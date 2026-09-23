"""
WHAT:
    Records what shoppers looked for and did not find, so the business can
    see demand the catalogue is not meeting.

WHY THIS IS WORTH KEEPING:
    A search that finds nothing is normally a dead end - the shopper
    leaves and nobody learns anything. Measured on the live catalogue:
    Zatch stocks no sleepwear at all, and three subcategories exist for it
    with zero products in them. The assistant now says so honestly, which
    is right, and entirely invisible to whoever decides what to stock.

TWO SHAPES OF "NOT FOUND", because one alone misses the case that
prompted this:

    EMPTY       search_products_by_name or search_products returned no
                rows at all. Easy, and caught in tool_executor.

    REJECTED    search_products_semantically returned its eight nearest
                neighbours - it always does, however far away they are -
                and the ANSWER presented none of them. "sleep wear"
                scored 0.691 against a best of 0.672 for a query that
                does have real matches, so no threshold can tell those
                apart; the reply is where the judgement happens, so that
                is where this is recorded (orchestrator._deliver).

WHAT IS NOT STORED:
    No user id. Unmet demand is a question about the CATALOGUE, and the
    only thing needed to answer it is what was typed. The query itself is
    a shopper's words, so it is capped in length and expires.
"""

import json
import re
from collections import defaultdict, deque
from datetime import datetime, timezone

import structlog

from app.db.redis_client import get_redis

logger = structlog.get_logger()

LIST_KEY = "zatch:unmet"
COUNT_KEY = "zatch:unmet:count"

MAX_STORED = 2000
RETENTION_DAYS = 90
MAX_QUERY = 120

# Why the search found nothing. Kept apart in the report because they
# mean different things: EMPTY is usually a word the catalogue does not
# contain, REJECTED is a whole category nobody stocks.
EMPTY = "empty"
REJECTED = "rejected"

_local: deque = deque(maxlen=MAX_STORED)

# Queries that are not demand: a blank box, or the model passing along
# something that is not a product at all.
_NOISE = re.compile(r"^\W*$")


def normalise(query: str | None) -> str:
    """The form two spellings of the same want collapse into.

    "Sleep Wear", "sleep wear" and "sleep  wear" are one line in the
    report, not three. Plurals are deliberately NOT stemmed: "sleep
    wears" reads as the same want to a human reading the report, and
    guessing at English morphology here would merge things that are not
    the same ("saree" / "sarees" fine, "short" / "shorts" not).
    """
    return " ".join((query or "").lower().split())[:MAX_QUERY]


async def record(query: str | None, *, reason: str, tool: str) -> bool:
    """Notes one search that found nothing. Returns whether it stored."""
    text = normalise(query)
    if not text or _NOISE.match(text):
        return False

    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "query": text,
        "reason": reason,
        "tool": tool,
    }

    # The log line is the copy that survives a Redis that was never
    # provisioned or has been flushed.
    logger.info("unmet_demand", query=text, reason=reason, tool=tool)

    client = await get_redis()
    if client is None:
        _local.appendleft(entry)
        return False

    try:
        pipe = client.pipeline()
        pipe.lpush(LIST_KEY, json.dumps(entry))
        pipe.ltrim(LIST_KEY, 0, MAX_STORED - 1)
        pipe.expire(LIST_KEY, RETENTION_DAYS * 24 * 3600)
        pipe.hincrby(COUNT_KEY, text, 1)
        pipe.expire(COUNT_KEY, RETENTION_DAYS * 24 * 3600)
        await pipe.execute()
        return True
    except Exception as exc:
        # A search that found nothing has already disappointed somebody.
        # Failing the request on top of that helps no one.
        logger.error("unmet_demand_write_failed", error=str(exc)[:200])
        _local.appendleft(entry)
        return False


async def recent(limit: int = 100) -> list[dict]:
    client = await get_redis()
    if client is None:
        return list(_local)[:limit]
    try:
        raw = await client.lrange(LIST_KEY, 0, limit - 1)
    except Exception as exc:
        logger.error("unmet_demand_read_failed", error=str(exc)[:200])
        return list(_local)[:limit]

    out = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except (TypeError, ValueError):
            continue
    return out


async def top(limit: int = 25) -> list[tuple[str, int]]:
    """The most-wanted missing things, commonest first.

    COUNTED SEPARATELY FROM THE LIST. The list is capped at MAX_STORED
    and the counters are not, so "asked 40 times" stays true after the
    fortieth entry has aged out of the window.
    """
    client = await get_redis()
    if client is None:
        counts: dict = defaultdict(int)
        for entry in _local:
            counts[entry["query"]] += 1
    else:
        try:
            raw = await client.hgetall(COUNT_KEY) or {}
            counts = {k: int(v) for k, v in raw.items()}
        except Exception as exc:
            logger.error("unmet_demand_read_failed", error=str(exc)[:200])
            counts = {}

    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


def reset_for_tests() -> None:
    _local.clear()
