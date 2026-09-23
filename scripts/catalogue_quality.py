"""
WHAT:
    Reads the live catalogue and reports the listings a shopper should
    never be shown - test rows, placeholder names, prices that cannot be
    real, empty descriptions, missing images.

WHY THIS EXISTS:
    The assistant answers from the catalogue, so a bad listing becomes a
    bad answer. Measured, the day get_discounted_products shipped: asked
    "any offers?", the honest top of the list was "digital clock 2000 ->
    10", "black 1000 -> 5", "test 10000 -> 100" and "hi 101 -> 10". Every
    figure was true. Every one of them reads as broken.

    Nothing here is fixable from this repo - the database credential is
    read-only and these are sellers' own listings. So this is a REPORT,
    addressed to whoever owns catalogue data.

WHAT IT DELIBERATELY DOES NOT DO:
    It does not decide what a "real" price is. A ₹5 listing is FLAGGED,
    not hidden: a marketplace may legitimately sell something for ₹5, and
    inventing a floor in this repo would quietly bury the problem instead
    of fixing it. Every check below is a question for a human, not a rule
    the assistant enforces.

USAGE:
    uv run python scripts/catalogue_quality.py            # summary
    uv run python scripts/catalogue_quality.py --markdown # shareable report
    uv run python scripts/catalogue_quality.py --markdown -o report.md
"""

import argparse
import asyncio
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.connection import close_mongo_connection, connect_to_mongo, get_database  # noqa: E402

# Names that are somebody testing the upload form. Whole-name matches
# only: "new" is a placeholder, "New Balance Shoes" is a product.
PLACEHOLDER_NAMES = {
    "test", "testing", "test1", "test123", "hi", "hello", "hey", "abc",
    "asdf", "qwerty", "xyz", "aaa", "new", "demo", "sample", "product",
    "untitled", "na", "n/a", "none", "null", "undefined", "temp", "tmp",
    "123", "1234", "check", "checking", "dummy", "item",
}

# A letter repeated three or more times at the end of a word: "shirtzzz",
# "shirttt". Nobody names a product that way on purpose.
KEYBOARD_MASH = re.compile(r"([a-z])\1{2,}\b", re.IGNORECASE)

# Fabric words, for apparel listings that never say what they are made of
# - the "Premium maslin silk" case, where the only clue was the name.
FABRIC_WORDS = {
    "cotton", "silk", "linen", "lenin", "wool", "denim", "leather", "satin",
    "chiffon", "georgette", "rayon", "viscose", "polyester", "nylon",
    "velvet", "crepe", "khadi", "jute", "muslin", "maslin", "blend",
    "spandex", "lycra", "cashmere", "fleece", "knit", "canvas", "suede",
}
APPAREL_CATEGORIES = {"men", "women"}

MIN_DESCRIPTION_CHARS = 25
SUSPICIOUS_PRICE = 20        # rupees, at or below
EXTREME_DISCOUNT_PCT = 80


def _name(doc) -> str:
    step1 = (doc.get("stepData") or {}).get("step1") or {}
    return (step1.get("name") or doc.get("name") or "").strip()


def _field(doc, key):
    step1 = (doc.get("stepData") or {}).get("step1") or {}
    value = step1.get(key)
    return doc.get(key) if value in (None, "") else value


def audit(doc) -> list[str]:
    """Every problem this listing has. Empty means it looks fine."""
    problems = []
    raw_name = _name(doc)
    name = raw_name.lower()
    price = _field(doc, "price")
    discounted = _field(doc, "discountedPrice")
    description = (_field(doc, "description") or "").strip()
    category = (_field(doc, "category") or "").strip()
    sub_category = (_field(doc, "subCategory") or "").strip()
    images = doc.get("images") or (doc.get("stepData", {}).get("step3", {}) or {}).get("images") or []

    if not raw_name:
        problems.append("no name")
    elif name in PLACEHOLDER_NAMES:
        problems.append(f"placeholder name ({raw_name!r})")
    elif len(raw_name) <= 2:
        problems.append(f"name too short to mean anything ({raw_name!r})")
    elif KEYBOARD_MASH.search(raw_name):
        problems.append(f"name looks mistyped ({raw_name!r})")

    if raw_name != raw_name.strip() or "  " in raw_name:
        problems.append("name has stray spaces")

    if not isinstance(price, (int, float)) or price <= 0:
        problems.append(f"no usable price ({price!r})")
    elif isinstance(discounted, (int, float)):
        if discounted <= 0:
            problems.append(f"discounted price is {discounted}")
        elif discounted <= SUSPICIOUS_PRICE:
            problems.append(f"sells for ₹{discounted:g}")
        if discounted > price:
            problems.append(f"'discount' raises the price (₹{price:g} -> ₹{discounted:g})")
        elif price > 0:
            pct = (price - discounted) / price * 100
            if pct >= EXTREME_DISCOUNT_PCT:
                problems.append(f"{pct:.1f}% off (₹{price:g} -> ₹{discounted:g})")

    if not description:
        problems.append("no description")
    elif len(description) < MIN_DESCRIPTION_CHARS:
        problems.append(f"description is {len(description)} characters")

    if not images:
        problems.append("no images")
    if not category:
        problems.append("no category")
    if not sub_category:
        problems.append("no subCategory")

    if (
        category.lower() in APPAREL_CATEGORIES
        and description
        and not ({w.strip(".,()") for w in f"{name} {description}".lower().split()} & FABRIC_WORDS)
    ):
        problems.append("clothing with no fabric named")

    if not doc.get("isSold") and (doc.get("totalStock") or 0) <= 0:
        problems.append("on sale with zero stock")

    return problems


async def collect():
    db = get_database()
    rows, by_problem, names = [], defaultdict(list), defaultdict(list)
    total = 0
    async for doc in db.products.find({}, {"embedding": 0}):
        total += 1
        name = _name(doc)
        names[name.lower()].append(str(doc["_id"]))
        problems = audit(doc)
        if problems:
            rows.append({
                "id": str(doc["_id"]),
                "name": name or "(no name)",
                "seller": str(doc.get("sellerId") or "unknown"),
                "problems": problems,
            })
            for p in problems:
                by_problem[p.split(" (")[0].split(" — ")[0]].append(name or doc["_id"])

    duplicates = {n: ids for n, ids in names.items() if n and len(ids) > 1}
    return total, rows, by_problem, duplicates


def render_markdown(total, rows, by_problem, duplicates) -> str:
    from datetime import date

    def bucket(key_words):
        return [r for r in rows if any(any(k in p.lower() for k in key_words) for p in r["problems"])]

    junk = bucket(["placeholder name", "name too short", "no name", "mistyped"])
    price = bucket(["sells for", "no usable price", "% off", "raises the price", "discounted price is"])
    thin = bucket(["no description", "description is", "fabric"])
    media = bucket(["no images", "no category", "no subcategory"])
    stock = bucket(["zero stock"])

    out = [
        "# Zatch catalogue quality report",
        "",
        f"> Generated {date.today().isoformat()} from the live `products` collection "
        f"by `scripts/catalogue_quality.py`.",
        "> Read-only: nothing here has been changed. Every item is a listing a "
        "shopper can be shown today, and that the AI assistant may quote in an answer.",
        "",
        "## Summary",
        "",
        "| | |",
        "|---|---|",
        f"| Products in catalogue | **{total}** |",
        f"| Listings with at least one problem | **{len(rows)}** ({len(rows) * 100 // max(total, 1)}%) |",
        f"| Junk or placeholder names | **{len(junk)}** |",
        f"| Price problems | **{len(price)}** |",
        f"| Missing or thin description | **{len(thin)}** |",
        f"| Missing images or category | **{len(media)}** |",
        f"| Listed but out of stock | **{len(stock)}** |",
        f"| Duplicate names | **{len(duplicates)}** |",
        "",
        "### Most common problems",
        "",
        "| Problem | Listings |",
        "|---|---:|",
    ]
    for problem, items in sorted(by_problem.items(), key=lambda kv: -len(kv[1])):
        out.append(f"| {problem} | {len(items)} |")

    # WHO TO TALK TO. Measured on the first run: the junk listings were
    # not spread across the marketplace, they belonged to two sellers.
    # That turns "54 listings are bad" into two conversations.
    by_seller = defaultdict(list)
    for r in rows:
        by_seller[r["seller"]].append(r["name"])
    worst = sorted(by_seller.items(), key=lambda kv: -len(kv[1]))[:10]
    if worst:
        out.extend(["", "### Where the problems are concentrated", "",
                    "| Seller ID | Flagged listings | Examples |", "|---|---:|---|"])
        for seller, names_ in worst:
            examples = ", ".join(sorted(set(names_))[:4]) or "-"
            out.append(f"| `{seller}` | {len(names_)} | {examples} |")

    def section(title, why, items):
        out.extend(["", f"## {title}", "", why, ""])
        if not items:
            out.append("_None._")
            return
        out.extend(["| Product | Problem | Product ID | Seller ID |", "|---|---|---|---|"])
        for r in sorted(items, key=lambda r: r["name"].lower()):
            problems = "; ".join(r["problems"])
            out.append(f"| {r['name'] or '(no name)'} | {problems} | `{r['id']}` | `{r['seller']}` |")

    section(
        "1. Junk and placeholder listings — fix first",
        "These look like somebody testing the upload form. They appear in search "
        "results, in 'trending', and at the top of 'any offers?' because their "
        "discounts are the deepest in the catalogue.",
        junk,
    )
    section(
        "2. Price problems",
        "Prices a shopper would not believe, or discounts so deep they read as a "
        "mistake. **Not necessarily wrong** — a ₹5 item may be genuine — but each "
        "one is worth a look, because the assistant will quote it as a real offer.",
        price,
    )
    section(
        "3. Missing or thin descriptions",
        "The assistant answers product questions strictly from the listing. With no "
        "description it can only say what the name says — measured: asked what "
        "'Premium maslin silk' is made from, the honest answer was that the listing "
        "does not say.",
        thin,
    )
    section("4. Missing images, category or subCategory", "Affects search, filtering and card display.", media)
    section("5. Listed but out of stock", "Shown to shoppers as available while nothing can be bought.", stock)

    out.extend(["", "## 6. Duplicate names", ""])
    if duplicates:
        out.extend(["Several listings share a name, so answers repeat themselves.", "",
                    "| Name | Copies | Product IDs |", "|---|---:|---|"])
        for name, ids in sorted(duplicates.items(), key=lambda kv: -len(kv[1])):
            out.append(f"| {name} | {len(ids)} | {', '.join(f'`{i}`' for i in ids)} |")
    else:
        out.append("_None._")

    out.extend([
        "",
        "---",
        "",
        "## How this was decided",
        "",
        "| Check | Rule |",
        "|---|---|",
        "| Placeholder name | Whole name is one of a fixed list (`test`, `hi`, `new`, `abc`, …) |",
        "| Mistyped name | A letter repeated 3+ times at the end of a word (`shirtzzz`) |",
        f"| Suspicious price | Sells for ₹{SUSPICIOUS_PRICE} or less |",
        f"| Extreme discount | {EXTREME_DISCOUNT_PCT}% off or more |",
        f"| Thin description | Under {MIN_DESCRIPTION_CHARS} characters |",
        "| Clothing with no fabric named | Category Men/Women and no fabric word in the name or description |",
        "",
        "No listing was changed or hidden. The assistant still reports exactly what "
        "the catalogue says — that is the point of it — so the fix belongs in the "
        "catalogue.",
        "",
    ])
    return "\n".join(out)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", action="store_true", help="shareable report")
    parser.add_argument("-o", "--out", help="write the report to a file")
    args = parser.parse_args()

    await connect_to_mongo()
    try:
        total, rows, by_problem, duplicates = await collect()
    finally:
        await close_mongo_connection()

    if args.markdown or args.out:
        report = render_markdown(total, rows, by_problem, duplicates)
        if args.out:
            Path(args.out).write_text(report, encoding="utf-8")
            print(f"Wrote {args.out} - {len(rows)} of {total} listings flagged")
        else:
            print(report)
    else:
        print(f"{len(rows)} of {total} listings flagged\n")
        for problem, items in sorted(by_problem.items(), key=lambda kv: -len(kv[1])):
            print(f"  {len(items):4}  {problem}")

    return 1 if rows else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
