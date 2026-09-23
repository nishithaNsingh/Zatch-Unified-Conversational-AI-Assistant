"""
WHAT:
    Reads the thumbs-up / thumbs-down ratings shoppers left on answers.

WHY:
    The ratings are only worth collecting if somebody reads them. This is
    that: a positive rate, and the actual exchanges behind every
    thumbs-down, which is where the next fix usually is.

USAGE:
    uv run python scripts/read_feedback.py              # summary + recent
    uv run python scripts/read_feedback.py --down       # only the bad ones
    uv run python scripts/read_feedback.py --limit 200
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.redis_client import close_redis, get_redis  # noqa: E402
from app.memory import feedback_store  # noqa: E402


def _show(entry: dict) -> None:
    mark = "👍" if entry.get("rating") == "up" else "👎"
    stamp = (entry.get("at") or "")[:19].replace("T", " ")
    flag = "" if entry.get("verified") else "  [unverified]"
    print(f"\n{mark}  {stamp}{flag}")
    if entry.get("question"):
        print(f"    Q: {entry['question'][:300]}")
    reply = (entry.get("reply") or "").replace("\n", "\n       ")
    if reply:
        print(f"    A: {reply[:600]}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25, help="how many to print")
    parser.add_argument("--down", action="store_true", help="only thumbs-down")
    args = parser.parse_args()

    client = await get_redis()
    if client is None:
        print("No REDIS_URL configured - ratings are only in this process and in "
              "the service logs (search for chat_feedback).")

    totals = await feedback_store.summary()
    rate = f"{totals['positive_rate']}% positive" if totals["positive_rate"] is not None else "no ratings yet"
    print(f"{totals['up']} 👍   {totals['down']} 👎   ({totals['total']} total, {rate})")

    entries = await feedback_store.recent(limit=args.limit if not args.down else max(args.limit, 200))
    if args.down:
        entries = [e for e in entries if e.get("rating") == "down"][: args.limit]
    if not entries:
        print("\nNothing recorded yet.")
    for entry in entries:
        _show(entry)

    await close_redis()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
