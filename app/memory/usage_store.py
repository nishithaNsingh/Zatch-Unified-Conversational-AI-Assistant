"""
WHAT:
    Counts questions answered - the denominator everything else on the
    insights page is read against.

WHY IT IS A COUNTER AND NOT A LOG:
    "How many people asked something today" needs a number, not a
    transcript. Nothing here stores what was asked or who asked it: the
    conversation is already in the session store for 24 hours, the
    interesting failures are in the feedback and demand stores, and a
    third copy of customer text earns nothing.

WHY IT CAN BE WRONG BY A LITTLE:
    Counting happens after an answer is produced, outside the request's
    critical path, and a failure to count is swallowed. A dashboard that
    is off by a handful during a Redis blip is fine; an answer that 500s
    because a counter would not increment is not.
"""

from collections import Counter
from datetime import date, datetime, timezone

import structlog

from app.db.redis_client import get_redis

logger = structlog.get_logger()

COUNT_KEY = "zatch:usage:count"
TOTAL_FIELD = "questions"

# Per-day fields age out; the running total does not.
DAY_RETENTION_DAYS = 90

_local: Counter = Counter()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def record_question() -> None:
    """One answered turn. Never raises."""
    day = _today()
    try:
        client = await get_redis()
    except Exception as exc:
        # Resolving the client can fail too, and this runs right after a
        # user has been answered - nothing here may reach them.
        logger.warning("usage_count_failed", error=str(exc)[:200])
        client = None
    if client is None:
        _local[TOTAL_FIELD] += 1
        _local[day] += 1
        return
    try:
        pipe = client.pipeline()
        pipe.hincrby(COUNT_KEY, TOTAL_FIELD, 1)
        pipe.hincrby(COUNT_KEY, day, 1)
        pipe.expire(COUNT_KEY, DAY_RETENTION_DAYS * 24 * 3600)
        await pipe.execute()
    except Exception as exc:
        logger.warning("usage_count_failed", error=str(exc)[:200])
        _local[TOTAL_FIELD] += 1
        _local[day] += 1


async def totals() -> dict:
    """Questions answered in total, today, and per day for the last week."""
    client = await get_redis()
    if client is None:
        raw = dict(_local)
    else:
        try:
            raw = await client.hgetall(COUNT_KEY) or {}
        except Exception as exc:
            logger.error("usage_read_failed", error=str(exc)[:200])
            raw = {}

    counts = {k: int(v) for k, v in raw.items() if str(v).lstrip("-").isdigit()}
    days = sorted((k for k in counts if k != TOTAL_FIELD), reverse=True)[:7]
    return {
        "questions": counts.get(TOTAL_FIELD, 0),
        "today": counts.get(_today(), 0),
        "by_day": [{"day": d, "questions": counts[d]} for d in sorted(days)],
    }


def reset_for_tests() -> None:
    _local.clear()
