"""
WHAT:
    Stores a thumbs-up or thumbs-down on one reply, with the exchange it
    refers to, so answer quality can be read from real use rather than
    guessed at.

WHY THIS EXISTS:
    Everything else this project measures is a proxy. The eval suite
    scores 53 questions WE chose; the grounding checks prove an answer
    traced back to a lookup, not that it helped. Nothing tells us which
    answers a shopper was actually glad to get. One tap does.

WHY REDIS AND NOT MONGO:
    The database credential is READ-ONLY, deliberately and at the Atlas
    role - the assistant cannot write to Zatch's data, and that guarantee
    is worth more than the convenience of a feedback collection. Redis is
    already here for sessions and rate limits, and this is the same kind
    of data: ours, operational, disposable.

    Without Redis it degrades to a per-process list rather than refusing:
    losing feedback is a smaller failure than 500ing on a thumbs-up. The
    log line is written either way, so a Render log search finds the
    ratings even if the store is gone.

WHAT IS STORED, AND THE PRIVACY COST:
    The question and the reply, verbatim. That is the whole point - a
    rating with no exchange attached says "something was wrong" and
    nothing else. But a reply can name a delivery city or an order id, so
    this is real customer data living longer than the 24-hour session:
    FEEDBACK_RETENTION_DAYS caps it, the list is capped by count as well,
    and the user id stored is the opaque Mongo id, never a name.
"""

import json
from collections import deque
from datetime import datetime, timezone

import structlog

from app.db.redis_client import get_redis

logger = structlog.get_logger()

LIST_KEY = "zatch:feedback"
COUNT_KEY = "zatch:feedback:count"

# HOW MUCH IS KEPT, on two axes because either alone fails. A count cap
# stops a busy week from filling a free-tier Redis; a TTL stops a quiet
# quarter from holding customer text indefinitely.
MAX_STORED = 2000
FEEDBACK_RETENTION_DAYS = 90

# Long enough to see what was asked and answered, short enough that one
# rogue client cannot push megabytes into the store.
MAX_TEXT = 2000

RATINGS = ("up", "down")

# The fallback when Redis is not configured. Bounded for the same reason
# the Redis list is capped, and per-process, so it is for development and
# tests rather than for production.
_local: deque = deque(maxlen=MAX_STORED)


def _clip(text: str | None) -> str:
    return (text or "").strip()[:MAX_TEXT]


async def record(
    *,
    user_id: str,
    session_id: str,
    rating: str,
    question: str | None,
    reply: str | None,
    verified: bool,
) -> bool:
    """Saves one rating. Returns whether it reached Redis.

    `verified` says whether the question and reply the client sent were
    found in that user's stored session. A false there is not a reason to
    drop the rating - a session expires in 24 hours and somebody may rate
    an older reply the app still shows - but a review that cannot tell
    the two apart is reading unverified client text as though the server
    had vouched for it.
    """
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {RATINGS}, got {rating!r}")

    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "session_id": session_id,
        "rating": rating,
        "question": _clip(question),
        "reply": _clip(reply),
        "verified": verified,
    }

    # WRITTEN EVEN WHEN THE STORE WORKS. Render's logs outlive a Redis
    # that was never provisioned, was flushed, or aged the entry out, and
    # this line is the only copy in any of those cases. No question or
    # reply text here: the logs are a different retention regime, and the
    # counts are what a log search is for.
    logger.info(
        "chat_feedback",
        rating=rating,
        session_id=session_id,
        verified=verified,
        question_chars=len(entry["question"]),
    )

    client = await get_redis()
    if client is None:
        _local.appendleft(entry)
        return False

    try:
        pipe = client.pipeline()
        pipe.lpush(LIST_KEY, json.dumps(entry))
        pipe.ltrim(LIST_KEY, 0, MAX_STORED - 1)
        pipe.expire(LIST_KEY, FEEDBACK_RETENTION_DAYS * 24 * 3600)
        pipe.hincrby(COUNT_KEY, rating, 1)
        await pipe.execute()
        return True
    except Exception as exc:
        # A thumbs-up that fails to store must not fail the request the
        # user is looking at. It is already in the log line above.
        logger.error("feedback_write_failed", error=str(exc)[:200])
        _local.appendleft(entry)
        return False


async def recent(limit: int = 50) -> list[dict]:
    """The most recent ratings, newest first."""
    client = await get_redis()
    if client is None:
        return list(_local)[:limit]
    try:
        raw = await client.lrange(LIST_KEY, 0, limit - 1)
    except Exception as exc:
        logger.error("feedback_read_failed", error=str(exc)[:200])
        return list(_local)[:limit]

    out = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except (TypeError, ValueError):
            continue
    return out


async def summary() -> dict:
    """Totals since the counters were created.

    COUNTED SEPARATELY FROM THE LIST, because the list is capped and the
    counters are not: after 2000 ratings the list is a recent window, and
    "how are we doing overall" must not silently become "how are we doing
    lately".
    """
    client = await get_redis()
    if client is None:
        counts = {r: sum(1 for e in _local if e.get("rating") == r) for r in RATINGS}
    else:
        try:
            raw = await client.hgetall(COUNT_KEY) or {}
            counts = {r: int(raw.get(r, 0) or 0) for r in RATINGS}
        except Exception as exc:
            logger.error("feedback_read_failed", error=str(exc)[:200])
            counts = {r: 0 for r in RATINGS}

    total = counts["up"] + counts["down"]
    return {
        **counts,
        "total": total,
        # None rather than 0 for an empty store: "0% positive" reads as a
        # verdict, and no verdict has been earned yet.
        "positive_rate": round(counts["up"] / total * 100, 1) if total else None,
    }


def reset_for_tests() -> None:
    _local.clear()
