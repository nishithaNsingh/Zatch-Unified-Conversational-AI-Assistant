"""
Checks that every stored vector still matches the model and the width the
Atlas index was built for, and names the ones that do not.

    uv run python scripts/check_embeddings.py

WHY THIS EXISTS:
    Free-text semantic search depends on every vector in
    product_embeddings having been produced by the SAME model that embeds
    the query, at the SAME width as the index. The catalogue was migrated
    to azure:text-embedding-3-small:1536 on 2026-09-01.

    Zatch's own change-stream pipeline re-embeds a product whenever it is
    edited - with the OLD 384-dim model, and writing no tag. It has not
    fired since the migration only because nothing has been edited.

    THE FIRST PRODUCT ANYONE EDITS WRITES A 384-DIM VECTOR INTO A
    1536-DIM COLLECTION. Atlas cannot index a vector of the wrong width,
    so that product simply stops appearing in semantic search. Nothing
    errors. Nothing logs. The search keeps working and quietly returns
    one fewer product, and the only way to find out was to notice.

    This is how you find out instead.

WHAT IT CANNOT DO:
    Repair anything. The app's credential is read-only by design - the
    same credential that stopped an irreversible delete_many during this
    project's own migration. Fixing a poisoned vector means re-embedding
    it, which needs write access and belongs to
    scripts/reembed_catalogue.py under a temporarily granted role.
    Detection and repair are deliberately different privileges.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.embeddings import (  # noqa: E402
    embedding_model_tag,
    expected_dimensions,
)
from app.config.settings import get_settings  # noqa: E402
from app.db.connection import (  # noqa: E402
    close_mongo_connection,
    connect_to_mongo,
)
from app.repos.products_repo import audit_embeddings  # noqa: E402

WIDTH = 68


def line() -> None:
    print("-" * WIDTH)


async def main() -> int:
    settings = get_settings()

    line()
    print("WHAT THIS EXPECTS")
    line()
    print(f"  database    {settings.mongodb_database}")
    print(f"  backend     {settings.embedding_backend}")
    print(f"  tag         {embedding_model_tag()}")
    print(f"  dimensions  {expected_dimensions()}")
    if settings.legacy_embedding_assumed:
        print(f"  assuming    {settings.legacy_embedding_assumed}")
        print("              (operator assertion for UNTAGGED documents)")
    print()

    await connect_to_mongo()
    try:
        audit = await audit_embeddings()
    finally:
        await close_mongo_connection()

    line()
    print("WHAT IS STORED")
    line()
    print(f"  documents           {audit.total:>6}")
    print(f"  matching            {audit.ok:>6}")
    print(f"  wrong width         {audit.wrong_width_count:>6}")
    print(f"  wrong / missing tag {audit.wrong_tag_count:>6}")
    print()

    if audit.level == "ok":
        line()
        print("  OK - every vector matches the model and the index width.")
        print()
        print("  This is a snapshot, not a guarantee. Run it again after")
        print("  anyone edits a product, and before a demo.")
        line()
        return 0

    if audit.level == "empty":
        line()
        print("  NO EMBEDDINGS STORED.")
        print("  Free-text semantic search will return nothing. Run")
        print("  scripts/reembed_catalogue.py to populate the collection.")
        line()
        return 1

    line()
    print("  PROBLEM FOUND")
    line()

    if audit.wrong_width:
        print(f"  These vectors are not {audit.expected_dimensions} dims, so Atlas")
        print("  cannot index them. They are invisible to semantic search:")
        for row in audit.wrong_width:
            print(f"    {row['_id']}   {row['width']} dims")
        if audit.wrong_width_count > len(audit.wrong_width):
            print(f"    ... and {audit.wrong_width_count - len(audit.wrong_width)} more")
        print()

    if audit.wrong_tag:
        print("  These carry no tag, or one from another model. A vector")
        print("  from a different model is not slightly worse - comparing")
        print("  it returns plausible-looking noise:")
        for row in audit.wrong_tag:
            print(f"    {row['_id']}   {row['tag']}")
        if audit.wrong_tag_count > len(audit.wrong_tag):
            print(f"    ... and {audit.wrong_tag_count - len(audit.wrong_tag)} more")
        print()

    line()
    print("  WHAT TO DO, in this order")
    line()
    print("  1. STOP WHATEVER WROTE THEM. Almost certainly Zatch's")
    print("     change-stream re-embed pipeline, which still uses the old")
    print("     384-dim model and writes no tag. Re-embedding without")
    print("     stopping it first just loses the race again.")
    print()
    print("  2. Re-embed, with a credential that has readWrite on this")
    print("     database - the app's own account will refuse, by design:")
    print()
    print("       uv run python scripts/reembed_catalogue.py <URI> "
          f"{settings.mongodb_database}")
    print()
    print("  3. Run this again. It should report OK.")
    line()
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
