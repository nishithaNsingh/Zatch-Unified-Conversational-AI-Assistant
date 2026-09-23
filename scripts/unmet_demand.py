"""
WHAT:
    What shoppers asked the assistant for and Zatch did not have - ranked,
    so buying decisions can be made from real demand rather than guesses.

WHY IT EXISTS:
    A search that finds nothing normally teaches nobody anything: the
    shopper leaves, and the gap stays invisible. Measured on the live
    catalogue, Zatch stocks no sleepwear at all - three subcategories
    exist for it with zero products in them - and the only sign of that
    was one person in a demo asking for it twice.

HOW A MISS IS DETECTED (two shapes, see app/memory/demand_store):
    empty      a name search returned no rows at all
    rejected   a meaning search returned its nearest neighbours and the
               ANSWER presented none of them - the sleepwear case, which
               no score threshold can catch

USAGE:
    uv run python scripts/unmet_demand.py                 # ranked summary
    uv run python scripts/unmet_demand.py --recent        # with examples
    uv run python scripts/unmet_demand.py --markdown -o demand.md
"""

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.redis_client import close_redis, get_redis  # noqa: E402
from app.memory import demand_store  # noqa: E402


def render_markdown(ranked, entries) -> str:
    reasons = Counter(e.get("reason") for e in entries)
    out = [
        "# What shoppers asked for and Zatch did not have",
        "",
        f"> Generated {date.today().isoformat()} by `scripts/unmet_demand.py`.",
        "> Every line is a real search by a real shopper that came back with nothing.",
        "",
        "## Summary",
        "",
        "| | |",
        "|---|---|",
        f"| Distinct things asked for | **{len(ranked)}** |",
        f"| Searches recorded (recent window) | **{len(entries)}** |",
        f"| — found nothing at all | {reasons.get(demand_store.EMPTY, 0)} |",
        f"| — nothing close enough to show | {reasons.get(demand_store.REJECTED, 0)} |",
        "",
        "## Most asked for, not stocked",
        "",
    ]
    if not ranked:
        out.append("_Nothing recorded yet._")
    else:
        out.extend(["| # | Searched for | Times asked |", "|---:|---|---:|"])
        for i, (query, count) in enumerate(ranked, 1):
            out.append(f"| {i} | {query} | {count} |")

    out.extend([
        "",
        "## How to read this",
        "",
        "- **A high count is a stocking decision waiting to be made** - people are "
        "asking for it by name and leaving empty-handed.",
        "- **A single odd line is usually a typo or a one-off**, not demand.",
        "- Counts are cumulative; the examples below are a recent window only.",
        "",
    ])
    if entries:
        out.extend(["## Recent searches", "", "| When | Searched for | Why nothing was shown |", "|---|---|---|"])
        human = {demand_store.EMPTY: "no match in the catalogue",
                 demand_store.REJECTED: "nothing close enough to show"}
        for e in entries[:40]:
            when = (e.get("at") or "")[:16].replace("T", " ")
            out.append(f"| {when} | {e.get('query', '')} | {human.get(e.get('reason'), e.get('reason', ''))} |")
    return "\n".join(out) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25, help="how many ranked rows")
    parser.add_argument("--recent", action="store_true", help="also list recent searches")
    parser.add_argument("--markdown", action="store_true", help="shareable report")
    parser.add_argument("-o", "--out", help="write the report to a file")
    args = parser.parse_args()

    if await get_redis() is None:
        print("No REDIS_URL configured - misses are only in this process and in "
              "the service logs (search for unmet_demand).\n")

    ranked = await demand_store.top(limit=args.limit)
    entries = await demand_store.recent(limit=200)

    if args.markdown or args.out:
        report = render_markdown(ranked, entries)
        if args.out:
            Path(args.out).write_text(report, encoding="utf-8")
            print(f"Wrote {args.out} - {len(ranked)} distinct searches")
        else:
            print(report)
    elif not ranked:
        print("Nothing recorded yet.")
    else:
        width = max(len(q) for q, _ in ranked)
        print(f"{len(ranked)} distinct things shoppers could not find:\n")
        for query, count in ranked:
            print(f"  {count:4}  {query:{width}}")
        if args.recent:
            print("\nRecent:")
            for e in entries[:20]:
                when = (e.get("at") or "")[:16].replace("T", " ")
                print(f"  {when}  {e.get('query', ''):30} ({e.get('reason')})")

    await close_redis()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
