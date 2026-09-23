"""
WHAT:
    All 7 product-related data lookups from PDF §4 (Product Discovery).
    This is the ONLY file in the whole app allowed to query the
    `products` collection directly. By agreed design (unlike orders),
    two functions here (get_recommendations, get_seller_info) also read
    from `users` directly, since products aren't owned by one user the
    way orders are, and splitting those two into a separate not-yet-built
    users_repo would leave PDF §4.6/§4.7 unfinished for no real benefit
    right now.

WHY THIS APPROACH:
    Unlike orders, products are public — any user can browse any
    product, so there's no per-user scoping/cross-user test here. The
    interesting design work is instead in accepting ALREADY-STRUCTURED
    filters (category, price range, color, size) rather than raw text —
    turning natural language like "shirts under 500" into these filters
    is Phase 6/7's job (the LLM), once it exists. This repo function
    just needs to query correctly once it receives clean filter values.

FLOW:
    Called by Phase 5's feature logic and, later, Phase 7's tool-calling
    layer, which will have already extracted structured filters from
    the user's natural-language message via the LLM.

LOGIC:
    - search_products() and get_trending_products() both exclude
      isSold=True products by default (ASSUMPTION — flagged for
      confirmation: showing a sold-out item in general browsing/trending
      would be a bad experience; direct lookups below do NOT apply this
      filter, since a direct question about a specific product should
      always get an answer even if it's sold).
    - Category/subCategory matching is case-insensitive (NL extraction
      might produce "men" vs "Men" — we shouldn't lose a match over
      casing).
    - Price filtering uses discountedPrice, matching the PDF §4.2
      example exactly ("products under 500" -> discountedPrice <= 500).

MECHANISM:
    Same two-layer safety pattern as orders_repo: every query uses the
    Step 3.3 allowlist as its MongoDB projection, and every result
    passes through the Step 3.4 sanitizer before returning — for BOTH
    `products` and `users` documents fetched in this file.
"""

import re
from time import monotonic

import structlog
from pymongo.errors import OperationFailure

from dataclasses import dataclass, field

from app.agent.embeddings import (
    EmbeddingModelMismatch,
    EmbeddingUnavailable,
    embed_query,
    embedding_model_tag,
    expected_dimensions,
)
from app.config.settings import get_settings
from app.db.connection import get_database
from app.repos.base import to_object_id
from app.security.field_allowlist import get_projection
from app.security.sanitizer import sanitize_document, sanitize_documents

logger = structlog.get_logger()

COLLECTION = "products"
USERS_COLLECTION = "users"

# ── Zatch's existing semantic-search layer ───────────────────────────
# These were already built, populated and indexed in the Atlas cluster
# (named "zatch-semantic-search") by the Zatch team - 143 product
# embeddings for 143 products, kept current by a change-stream sync.
# Nothing here provisions them; this repo just stops ignoring them.
EMBEDDINGS_COLLECTION = "product_embeddings"
TEXT_INDEX = "product_text_index"      # Atlas Search, full-text
VECTOR_INDEX = "product_vector_index"  # Atlas Vector Search, 384-dim cosine

# Fields the full-text index actually covers. Taken from the live index
# definition rather than guessed - searching a path the index does not
# map returns nothing, silently.
TEXT_SEARCH_PATHS = ["name", "tags", "searchKeywords", "category", "subCategory"]

# The category vocabulary the system prompt is built from, cached per
# process. See get_distinct_categories.
CATEGORY_CACHE_SECONDS = 600
_CATEGORY_CACHE: tuple[float, dict] | None = None


def _case_insensitive_exact(value: str) -> dict:
    """Builds a MongoDB regex query that matches a field exactly,
    ignoring case — so "men" and "Men" both match category "Men"."""
    return {"$regex": f"^{re.escape(value)}$", "$options": "i"}


async def _find_product(product_id: str) -> dict | None:
    """Internal helper — fetches one product by ID, applying both
    safety layers. Used by every direct-lookup function below."""
    object_id = to_object_id(product_id)
    if object_id is None:
        return None  # malformed ID -> nothing found, no crash

    db = get_database()
    projection = get_projection(COLLECTION)
    doc = await db[COLLECTION].find_one({"_id": object_id}, projection)
    return sanitize_document(COLLECTION, doc)


async def search_products(
    category: str | None = None,
    sub_category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    color: str | None = None,
    size: str | None = None,
    in_stock_only: bool = False,
    limit: int = 20,
) -> list[dict]:
    """PDF §4.1 (category search) + §4.2 (price filtering), combined
    into one flexible filter function — Phase 5.2.1's structured filter
    schema. All arguments are optional; only the ones actually passed
    narrow the search."""
    query: dict = {"isSold": False}  # see module docstring ASSUMPTION note

    if category:
        query["category"] = _case_insensitive_exact(category)
    if sub_category:
        query["subCategory"] = _case_insensitive_exact(sub_category)

    if min_price is not None or max_price is not None:
        price_query: dict = {}
        if min_price is not None:
            price_query["$gte"] = min_price
        if max_price is not None:
            price_query["$lte"] = max_price
        query["discountedPrice"] = price_query  # PDF §4.2 uses discountedPrice, not price

    if color or size:
        # $elemMatch requires BOTH conditions to match the SAME array
        # entry — without it, MongoDB would accept a product that has
        # the right color in one variant and the right size in another.
        variant_match: dict = {}
        if color:
            variant_match["color"] = _case_insensitive_exact(color)
        if size:
            variant_match["size"] = _case_insensitive_exact(size)
        if in_stock_only:
            variant_match["isOutOfStock"] = False
        query["variants"] = {"$elemMatch": variant_match}
    elif in_stock_only:
        query["totalStock"] = {"$gt": 0}

    db = get_database()
    projection = get_projection(COLLECTION)
    cursor = db[COLLECTION].find(query, projection).limit(limit)
    docs = await cursor.to_list(length=limit)
    return sanitize_documents(COLLECTION, docs)


async def get_variant_stock(product_id: str, color: str, size: str) -> dict | None:
    """PDF §4.3 — is this specific color/size in stock, and how many?"""
    doc = await _find_product(product_id)
    if doc is None:
        return None

    variants = doc.get("variants", []) or []
    # Search the variants array in Python (not MongoDB) since we already
    # fetched the full product — no need for a second query.
    match = next(
        (
            v for v in variants
            if v.get("color", "").lower() == color.lower()
            and v.get("size", "").lower() == size.lower()
        ),
        None,
    )
    if match is None:
        return {"productId": product_id, "color": color, "size": size, "found": False}

    return {
        "productId": product_id,
        "color": color,
        "size": size,
        "found": True,
        "stock": match.get("stock", 0),
        "isOutOfStock": match.get("isOutOfStock", True),
    }


async def get_product_detail(product_id: str) -> dict | None:
    """PDF §4.4 — full description, images, condition, return policy."""
    doc = await _find_product(product_id)
    if doc is None:
        return None
    return {
        "productId": product_id,
        "name": doc.get("name"),
        "description": doc.get("description"),
        "images": doc.get("images", []),
        "condition": doc.get("condition"),
        "price": doc.get("price"),
        "discountedPrice": doc.get("discountedPrice"),
        "shipping": doc.get("shipping"),
        "variants": doc.get("variants", []),
    }


MAX_COMPARED = 4


async def compare_products(product_ids: list | None = None) -> dict:
    """Two to four products side by side, from what their listings hold.

    ONE QUERY, NOT ONE PER PRODUCT - get_products_by_ids already batches,
    and a comparison is the one place where fetching serially would be
    most obviously wasteful.

    NOTHING IS INVENTED AND NOTHING IS SCORED. It returns the same fields
    get_product_detail returns, per product, and says which are missing.
    There is no "winner": the catalogue holds no attribute that ranks a
    watch against a watch, and a helpful-looking verdict would be this
    repo's opinion rather than Zatch's data. What CAN be said from the
    data - which is cheaper, which is discounted more deeply, which
    arrives sooner - is computed in the trimmer, where the arithmetic
    lives for every other tool.

    A product that does not exist is REPORTED rather than dropped:
    comparing two things and silently returning one reads as a working
    comparison of the wrong pair.
    """
    ids = [str(pid) for pid in (product_ids or []) if pid]
    if len(ids) < 2:
        return {
            "error": "A comparison needs at least two products.",
            "compared": [],
            "notFound": ids,
        }

    ids = ids[:MAX_COMPARED]
    object_ids = {pid: to_object_id(pid) for pid in ids}
    found = await get_products_by_ids([oid for oid in object_ids.values() if oid is not None])
    by_id = {str(doc["_id"]): doc for doc in found}

    compared = []
    for pid in ids:
        doc = by_id.get(pid)
        if doc is None:
            continue
        variants = doc.get("variants") or []
        compared.append({
            "productId": pid,
            "name": doc.get("name"),
            "description": doc.get("description"),
            "condition": doc.get("condition"),
            "category": doc.get("category"),
            "subCategory": doc.get("subCategory"),
            "price": doc.get("price"),
            "discountedPrice": doc.get("discountedPrice"),
            "totalStock": doc.get("totalStock"),
            "isSold": doc.get("isSold"),
            "shipping": doc.get("shipping"),
            # The shopper-facing half of a variant: what it comes in and
            # whether any of it is left. "NONE" is how this catalogue
            # spells "this product has no sizes", so it is dropped rather
            # than shown as a size called NONE.
            "colors": sorted({v.get("color") for v in variants if v.get("color")}),
            "sizes": sorted({
                v.get("size") for v in variants
                if v.get("size") and str(v.get("size")).upper() != "NONE"
            }),
            "inStock": any(not v.get("isOutOfStock") for v in variants) if variants else None,
        })

    return {
        "compared": compared,
        "notFound": [pid for pid in ids if pid not in by_id],
    }


async def get_trending_products(limit: int = 5) -> list[dict]:
    """PDF §4.5 — currently trending / top-pick products."""
    db = get_database()
    projection = get_projection(COLLECTION)
    query = {"isSold": False}
    cursor = (
        db[COLLECTION]
        .find(query, projection)
        # isTopPick first, then view/like count — matches "top-pick" and
        # "trending" both being covered by this one function per the PDF.
        .sort([("isTopPick", -1), ("viewCount", -1), ("likeCount", -1)])
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return sanitize_documents(COLLECTION, docs)


def _discounted_query(category: str | None) -> dict:
    """Products whose discountedPrice is genuinely below their price.

    $expr COMPARES TWO FIELDS OF THE SAME DOCUMENT, which an ordinary
    query cannot do. Without it the only options are "has a
    discountedPrice" - true of products sold at full price, where the two
    fields are equal - or a hardcoded threshold, which invents a rule the
    business never stated.
    """
    query: dict = {
        "isSold": False,
        "$expr": {"$lt": ["$discountedPrice", "$price"]},
    }
    if category:
        query["category"] = _case_insensitive_exact(category)
    return query


async def get_discounted_products(
    category: str | None = None, limit: int = 8
) -> list[dict]:
    """Products currently reduced, biggest reduction first.

    WHY PERCENTAGE AND NOT AMOUNT. Sorting by rupees saved ranks by how
    expensive a thing is - a 5% cut on a ₹29,000 fridge beats half price
    on a ₹200 hamper - which is not what "any offers?" asks.

    THE HONEST PART IS THE COUNT, NOT THE LIST. Measured on the real
    catalogue: 141 of 143 products carry some discount, so "these are our
    offers" would be a misleading way to describe any eight of them. The
    summariser sends the true total alongside, and the model is told to
    say so - see _summarise_deals in tool_executor.

    Sorting happens in the aggregation rather than in Python because the
    limit must be applied to the RANKED set, not to an arbitrary page of
    it.
    """
    db = get_database()
    pipeline = [
        {"$match": _discounted_query(category)},
        {
            "$addFields": {
                "_discountFraction": {
                    "$divide": [
                        {"$subtract": ["$price", "$discountedPrice"]},
                        # A product priced at 0 would divide by zero and
                        # abort the whole aggregation.
                        {"$cond": [{"$gt": ["$price", 0]}, "$price", 1]},
                    ]
                }
            }
        },
        {"$sort": {"_discountFraction": -1}},
        {"$limit": limit},
        # Same allowlist as every other read. _discountFraction is not in
        # it and is therefore dropped here: the percentage the model sees
        # is computed in the trimmer, from the two prices it also sees.
        {"$project": get_projection(COLLECTION)},
    ]
    docs = [doc async for doc in db[COLLECTION].aggregate(pipeline)]
    return sanitize_documents(COLLECTION, docs)


async def count_discounted_products(category: str | None = None) -> int:
    """How many products are reduced in total - the number that stops a
    capped list from reading as the whole of what is on offer."""
    return await get_database()[COLLECTION].count_documents(_discounted_query(category))


async def get_recommendations(user_id: str, limit: int = 5) -> list[dict]:
    """PDF §4.6 — based on the user's own saved/liked products. Falls
    back to trending if the user has no saved/liked history yet, or
    doesn't exist — never returns an error for a new user."""
    object_id = to_object_id(user_id)
    if object_id is None:
        return await get_trending_products(limit)

    db = get_database()
    user_projection = get_projection(USERS_COLLECTION)
    user_doc = await db[USERS_COLLECTION].find_one({"_id": object_id}, user_projection)
    user_doc = sanitize_document(USERS_COLLECTION, user_doc)

    if user_doc is None:
        return await get_trending_products(limit)  # unknown user -> generic fallback

    saved = user_doc.get("savedProducts", []) or []
    liked = user_doc.get("likedProducts", []) or []
    seed_ids = list(saved) + list(liked)

    if not seed_ids:
        return await get_trending_products(limit)  # no history yet -> generic fallback

    # Find what categories the user's saved/liked products belong to,
    # so we can recommend MORE of what they've already shown interest in.
    seed_cursor = db[COLLECTION].find({"_id": {"$in": seed_ids}}, {"category": 1})
    seed_docs = await seed_cursor.to_list(length=len(seed_ids))
    categories = list({d["category"] for d in seed_docs if d.get("category")})

    if not categories:
        return await get_trending_products(limit)

    projection = get_projection(COLLECTION)
    query = {
        "category": {"$in": categories},
        "_id": {"$nin": seed_ids},  # don't recommend what they already saved/liked
        "isSold": False,
    }
    cursor = db[COLLECTION].find(query, projection).sort("likeCount", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return sanitize_documents(COLLECTION, docs)


async def get_seller_info(product_id: str) -> dict | None:
    """PDF §4.7 — who's selling this, and are they trustworthy?"""
    doc = await _find_product(product_id)
    if doc is None or not doc.get("sellerId"):
        return None

    db = get_database()
    seller_projection = get_projection(USERS_COLLECTION)
    seller_doc = await db[USERS_COLLECTION].find_one(
        {"_id": doc["sellerId"]}, seller_projection
    )
    seller_doc = sanitize_document(USERS_COLLECTION, seller_doc)
    if seller_doc is None:
        return None

    seller_profile = seller_doc.get("sellerProfile", {}) or {}
    return {
        "productId": product_id,
        "businessName": seller_profile.get("businessName"),
        "customerRating": seller_doc.get("customerRating"),
        "followerCount": seller_doc.get("followerCount"),
    }

async def get_distinct_categories() -> dict:
    """Returns the REAL category/subCategory values actually used on
    products - not the categories collection's display menu, which we
    discovered uses different naming (e.g. "Home Decor" vs the real
    products.category value "Home"). This is what Phase 6/7 should
    ground the LLM's filter extraction against, since it matches
    exactly what search_products() filters on.

    CACHED FOR THE PROCESS, because the system prompt embeds these values
    and is rebuilt on EVERY message: two distinct() calls against Atlas,
    measured at ~60ms, on the critical path of every single answer. The
    catalogue's category vocabulary changes when a seller adds a product
    in a category nobody has used before - rare, and a restart or the TTL
    below picks it up.
    """
    global _CATEGORY_CACHE
    cached = _CATEGORY_CACHE
    if cached is not None and (monotonic() - cached[0]) < CATEGORY_CACHE_SECONDS:
        return cached[1]

    db = get_database()
    categories = await db[COLLECTION].distinct("category")
    sub_categories = await db[COLLECTION].distinct("subCategory")
    result = {
        "categories": sorted(c for c in categories if c),
        "subCategories": sorted(s for s in sub_categories if s),
    }
    _CATEGORY_CACHE = (monotonic(), result)
    return result


def reset_category_cache() -> None:
    """Drop the cached vocabulary - for tests, and for a caller that has
    just changed the catalogue and wants the next prompt to see it."""
    global _CATEGORY_CACHE
    _CATEGORY_CACHE = None

async def get_products_by_ids(product_ids: list) -> list[dict]:
    """Batch lookup - turns a list of product ObjectIds (e.g. from a
    live session's productSequence, or a Bit's tagged products) into
    real product data in ONE query, instead of one query per product.
    Used by the Live Shopping and Bits feature layers to enrich raw ID
    lists before responding to the user."""
    if not product_ids:
        return []

    db = get_database()
    projection = get_projection(COLLECTION)
    cursor = db[COLLECTION].find({"_id": {"$in": product_ids}}, projection)
    docs = await cursor.to_list(length=len(product_ids))
    return sanitize_documents(COLLECTION, docs)

async def _search_products_by_name_regex(name: str, limit: int) -> list[dict]:
    """The original substring match. Kept ONLY as a fallback for a
    cluster without the Atlas Search index (a fresh environment, a
    restored snapshot). It cannot use an index - a case-insensitive
    regex forces a collection scan - so it is the slow path by
    definition, not a preference."""
    db = get_database()
    projection = get_projection(COLLECTION)
    query = {"name": {"$regex": re.escape(name), "$options": "i"}, "isSold": False}
    cursor = db[COLLECTION].find(query, projection).limit(limit)
    docs = await cursor.to_list(length=limit)
    return sanitize_documents(COLLECTION, docs)


async def search_products_by_name(name: str, limit: int = 5) -> list[dict]:
    """Find a product the user named, using Zatch's Atlas Search index.

    WHY NOT THE REGEX IT REPLACES. The old query was
    {"name": {"$regex": ..., "$options": "i"}}, and a case-insensitive
    regex cannot use an index - every search scanned the whole
    collection. It also only ever matched the NAME, so "buddha statue"
    missed a product tagged "buddha" but named something else, and a
    typo missed everything.

    product_text_index was already built and READY on this cluster and
    covers name, tags, searchKeywords, category and subCategory. Using
    it is faster AND better recall, at no cost. Measured on the real
    catalogue: "watch" now also finds "Smartwatch" (the regex found it
    only by accident of substring matching, unranked), and "budha" finds
    Buddha, which the regex could not do at all.

    Falls back to the old scan if the index is absent, so a cluster
    without it degrades in speed rather than breaking outright.
    """
    db = get_database()
    pipeline = [
        # $search MUST be the first stage in the pipeline.
        {
            "$search": {
                "index": TEXT_INDEX,
                # THREE CLAUSES, EACH EARNING ITS PLACE - measured against
                # the real catalogue, not assumed:
                #   text     - ordinary token match, scores highest
                #   wildcard - the analyzer treats "Smartwatch" as ONE
                #              token, so a plain text search for "watch"
                #              misses it. This is the only clause that
                #              catches a word inside a compound word.
                #   fuzzy 1  - typo tolerance ("budha" -> Buddha,
                #              "shrit" -> Shirt). maxEdits is 1, NOT 2:
                #              at 2 the catalogue returned "Spider Fetch
                #              T Shirt" and "Bamboo towel" for "watch",
                #              and an assistant presents its top hits as
                #              real answers, so noise is worse than a
                #              miss.
                #
                # EVERY WORD MUST MATCH (matchCriteria "all"). With the
                # default "any", ONE word was enough: "sleep wear" matched
                # eight garments through subCategory slugs like
                # "fusion-wear", and "Titanium Nebula X9 smartwatch" (not
                # sold) returned a different smartwatch. Measured on 17
                # queries: both became empty, "cargo shorts" dropped six
                # unrelated shirts, and every real product still ranked
                # first. The fuzzy clause keeps it too, so "Lenin shirts"
                # still tolerates a one-letter slip per word.
                "compound": {
                    "should": [
                        {"text": {"query": name, "path": TEXT_SEARCH_PATHS, "matchCriteria": "all"}},
                        {
                            "wildcard": {
                                "query": f"*{name}*",
                                "path": TEXT_SEARCH_PATHS,
                                "allowAnalyzedField": True,
                            }
                        },
                        {
                            "text": {
                                "query": name,
                                "path": TEXT_SEARCH_PATHS,
                                "fuzzy": {"maxEdits": 1},
                                "matchCriteria": "all",
                            }
                        },
                    ],
                    "minimumShouldMatch": 1,
                },
            }
        },
        {"$match": {"isSold": False}},
        {"$limit": limit},
        # Same allowlist as every other read - the projection is not
        # optional just because this is an aggregation.
        {"$project": get_projection(COLLECTION)},
    ]

    try:
        docs = [doc async for doc in db[COLLECTION].aggregate(pipeline)]
    except OperationFailure as exc:
        logger.warning(
            "atlas_search_unavailable_falling_back_to_scan",
            index=TEXT_INDEX,
            detail=str(exc)[:200],
        )
        return await _search_products_by_name_regex(name, limit)

    return sanitize_documents(COLLECTION, docs)


async def find_similar_products(product_id: str, limit: int = 5) -> list[dict]:
    """Semantically similar products, via Zatch's existing vector index.

    HOW THIS WORKS WITHOUT KNOWING THEIR EMBEDDING MODEL. Normally
    $vectorSearch needs the QUERY embedded by the same model that
    produced the stored vectors - embed with a different model and
    cosine similarity is meaningless, silently. We do not know which
    model Zatch used (nothing in the database records it).

    So the query vector here is not computed at all: it is the STORED
    embedding of a product we already have. Stored-vs-stored is the same
    model by construction, so the comparison is valid whatever that
    model turns out to be. Free-text semantic search ("something cosy
    for winter") still needs their answer; "more like this one" does not.

    Sold products are dropped AFTER the vector stage rather than before,
    since $vectorSearch cannot be preceded by a $match.
    """
    object_id = to_object_id(product_id)
    if object_id is None:
        return []

    db = get_database()
    seed = await db[EMBEDDINGS_COLLECTION].find_one(
        {"_id": object_id}, get_projection(EMBEDDINGS_COLLECTION)
    )
    if not seed or not seed.get("embedding"):
        # Not every product necessarily has an embedding yet - the sync
        # is eventually consistent. Absence is normal, not an error.
        logger.info("no_embedding_for_product", product_id=product_id)
        return []

    pipeline = [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX,
                "path": "embedding",
                "queryVector": seed["embedding"],
                # numCandidates is the approximate-search breadth; the
                # +1 on limit is because the seed always returns itself
                # at similarity 1.0 and is dropped below.
                "numCandidates": max(100, (limit + 1) * 10),
                "limit": limit + 1,
            }
        },
        {"$project": {"score": {"$meta": "vectorSearchScore"}}},
    ]

    try:
        hits = [doc async for doc in db[EMBEDDINGS_COLLECTION].aggregate(pipeline)]
    except OperationFailure as exc:
        logger.warning(
            "vector_search_unavailable", index=VECTOR_INDEX, detail=str(exc)[:200]
        )
        return []

    ranked_ids = [h["_id"] for h in hits if h["_id"] != object_id][:limit]
    if not ranked_ids:
        return []

    # get_products_by_ids applies the products allowlist and sanitizer -
    # the embeddings collection never supplies content, only an ordering.
    products = await get_products_by_ids(ranked_ids)
    by_id = {p["_id"]: p for p in products if not p.get("isSold")}

    # $in does not preserve order, and here the order IS the relevance.
    return [by_id[pid] for pid in ranked_ids if pid in by_id]

@dataclass(frozen=True)
class EmbeddingAudit:
    """The state of the vector collection, counted rather than sampled."""

    total: int
    expected_dimensions: int
    expected_tag: str
    ok: int
    # THE TRUE TOTALS, kept apart from the samples below - and the
    # separation is not tidiness. The lists are capped so a wholly broken
    # collection does not print 143 ids, and reading their length as the
    # count made the report say "20 wrong" when every document was. A
    # number that under-reports the size of an incident is worse than no
    # number, because it is believed.
    wrong_width_count: int = 0
    wrong_tag_count: int = 0
    # A capped SAMPLE, for a human to start from.
    wrong_width: list = field(default_factory=list)   # [{_id, width}]
    wrong_tag: list = field(default_factory=list)     # [{_id, tag}]

    @property
    def truncated(self) -> bool:
        """Whether the samples leave anything out."""
        return (
            self.wrong_width_count > len(self.wrong_width)
            or self.wrong_tag_count > len(self.wrong_tag)
        )

    @property
    def healthy(self) -> bool:
        return (
            self.total > 0
            and not self.wrong_width_count
            and not self.wrong_tag_count
        )

    @property
    def level(self) -> str:
        """ok | empty | degraded | broken - what a monitor should act on."""
        if self.total == 0:
            return "empty"
        if self.healthy:
            return "ok"
        # EVERY document wrong is a different failure from SOME. All of
        # them means the catalogue belongs to another model and search is
        # meaningless; some of them means a rogue writer has been through
        # and those products have silently left the index.
        return "broken" if self.ok == 0 else "degraded"

    @property
    def summary(self) -> str:
        if self.level == "empty":
            return "no embeddings stored"
        if self.healthy:
            return f"{self.total} embeddings, all {self.expected_dimensions}-dim and tagged"
        return (
            f"{self.wrong_width_count} wrong-width and {self.wrong_tag_count} "
            f"wrong-tag of {self.total} embeddings"
        )


async def audit_embeddings(limit_reported: int = 20) -> EmbeddingAudit:
    """Every stored vector checked against the width and tag the index
    expects. Read-only, and the only thing that can see the failure.

    WHY A FULL SCAN RATHER THAN A SAMPLE. The failure mode is ONE
    document: Zatch's own change-stream pipeline re-embeds a product
    whenever a seller edits it, using the old 384-dim model and writing
    no tag. Sampling finds that document with probability 1/143. A scan
    finds it every time, and 143 vectors is a trivial read.

    IT REPORTS, IT DOES NOT REPAIR. This service holds a READ-ONLY
    credential by design - the same credential that once stopped an
    irreversible delete_many during this project's own migration. Fixing
    a poisoned vector means re-embedding it, which needs write access
    and belongs to scripts/reembed_catalogue.py under a temporarily
    granted role. Detection and repair are deliberately different
    privileges.

    THE PROJECTION IS HAND-WRITTEN, not the allowlist, and that is
    consistent with the existing guard in search_products_semantically:
    embeddingModel is an integrity field, read to check the collection
    against itself. Nothing here is ever returned to a user, which is
    what the allowlist governs.
    """
    db = get_database()
    expected = expected_dimensions()
    tag = embedding_model_tag()
    assumed = get_settings().legacy_embedding_assumed

    total = 0
    ok = 0
    wrong_width_count = 0
    wrong_tag_count = 0
    wrong_width: list = []
    wrong_tag: list = []

    cursor = db[EMBEDDINGS_COLLECTION].find(
        {}, {"embedding": 1, "embeddingModel": 1}
    )
    async for doc in cursor:
        total += 1
        width = len(doc.get("embedding") or [])
        stored = doc.get("embeddingModel")

        # An operator assertion stands in for a missing tag, exactly as
        # it does at search time - see LEGACY_EMBEDDING_ASSUMED. It only
        # counts when it names the model actually configured.
        effective = assumed if (stored is None and assumed) else stored

        bad = False
        if width != expected:
            bad = True
            wrong_width_count += 1
            if len(wrong_width) < limit_reported:
                wrong_width.append({"_id": str(doc.get("_id")), "width": width})
        if effective != tag:
            bad = True
            wrong_tag_count += 1
            if len(wrong_tag) < limit_reported:
                wrong_tag.append({
                    "_id": str(doc.get("_id")),
                    "tag": stored or "(untagged)",
                })
        if not bad:
            ok += 1

    return EmbeddingAudit(
        total=total, expected_dimensions=expected, expected_tag=tag, ok=ok,
        wrong_width_count=wrong_width_count, wrong_tag_count=wrong_tag_count,
        wrong_width=wrong_width, wrong_tag=wrong_tag,
    )


async def embedding_status() -> dict:
    """The same question as audit_embeddings, answered with counts.

    WHY BOTH EXIST. /health is polled - Render polls it, the container
    HEALTHCHECK polls it - so it must not walk the collection on every
    call. Three counted queries answer "is anything wrong" without
    reading a single vector; the deep scan is for the moments a human is
    actually looking, which are startup and the audit script.

    $not/$size is what makes the width check countable: it asks Mongo for
    documents whose embedding array is NOT the expected length, rather
    than measuring each one here.
    """
    db = get_database()
    expected = expected_dimensions()
    tag = embedding_model_tag()

    total = await db[EMBEDDINGS_COLLECTION].count_documents({})
    if total == 0:
        return {"level": "empty", "total": 0, "contaminated": 0}

    wrong_width = await db[EMBEDDINGS_COLLECTION].count_documents(
        {"embedding": {"$not": {"$size": expected}}}
    )
    wrong_tag = await db[EMBEDDINGS_COLLECTION].count_documents(
        {"embeddingModel": {"$ne": tag}}
    )

    # An operator assertion covers a wholly untagged legacy catalogue,
    # exactly as it does at search time.
    assumed = get_settings().legacy_embedding_assumed
    if wrong_tag == total and assumed == tag:
        untagged = await db[EMBEDDINGS_COLLECTION].count_documents(
            {"embeddingModel": {"$exists": False}}
        )
        if untagged == total:
            wrong_tag = 0

    contaminated = max(wrong_width, wrong_tag)
    if contaminated == 0:
        level = "ok"
    elif contaminated >= total:
        level = "broken"
    else:
        level = "degraded"
    return {"level": level, "total": total, "contaminated": contaminated}


async def log_embedding_preflight() -> None:
    """Says at STARTUP whether the vector collection is intact.

    THE RISK THIS EXISTS FOR IS DOCUMENTED AND DORMANT. Zatch's own
    change-stream pipeline re-embeds an edited product with the old
    384-dim model and no tag; the catalogue and the Atlas index are
    1536-dim. It has not fired only because nothing has been edited. The
    first seller who changes a price starts it, and the symptom is a
    product quietly missing from search - no error, no exception, no
    difference an operator could see.

    Startup is when someone is reading, so this is where it gets said.
    Never fatal: a service that refuses to boot because ONE product left
    the index is a worse outage than the one it is reporting.
    """
    try:
        audit = await audit_embeddings()
    except Exception as exc:
        logger.warning("embedding_preflight_failed", error=str(exc)[:200])
        return

    log = {
        "ok": logger.info,
        "empty": logger.warning,
        "degraded": logger.error,
        "broken": logger.error,
    }[audit.level]
    log(
        "embedding_preflight",
        level=audit.level,
        summary=audit.summary,
        total=audit.total,
        expected_dimensions=audit.expected_dimensions,
        wrong_width=audit.wrong_width[:5],
        wrong_tag=audit.wrong_tag[:5],
        consequence=(
            "affected products are absent from semantic search and nothing "
            "else reports it"
            if audit.level in ("degraded", "broken")
            else "semantic search is intact"
        ),
    )


async def search_products_semantically(query: str, limit: int = 5) -> list[dict]:
    """Free-text search by MEANING: "something cosy for winter".

    THE DIFFERENCE FROM find_similar_products. That one reuses a stored
    vector as its query, so it needs no model and works whatever embedded
    the catalogue. This one has to embed the QUERY, which means the model
    must match the one that embedded the documents.

    A MISMATCH IS THE DANGEROUS CASE, not a missing model. Comparing a
    query from model A against documents from model B returns numbers
    that look completely normal - ranked, plausible, in the usual 0-0.4
    range - and are noise. Nothing errors. The results read as merely
    mediocre, which is exactly how a broken search survives a review.

    So the stored documents carry an embeddingModel tag, and this refuses
    to run unless it matches. Untagged documents mean a catalogue from
    the original pipeline, which was never identified - also a refusal.
    """
    if not query or not query.strip():
        return []

    db = get_database()

    active_tag = embedding_model_tag()

    # WHY THIS NO LONGER SAMPLES ONE DOCUMENT.
    #
    # It used to be find_one({}) - take any embedding, check its tag. That
    # answers "was this catalogue embedded by our model", and misses the
    # failure this project actually faces: Zatch's own change-stream
    # pipeline re-embeds a product whenever a seller edits it, with the
    # OLD 384-dim model and no tag. That poisons ONE document out of 143,
    # and a sample finds it roughly one time in 143.
    #
    # So the query asks the opposite question - "is there ANY document
    # that is not ours" - which costs the same single round trip and
    # cannot miss. $ne matches a MISSING field too, which is exactly what
    # the rogue writer leaves behind.
    rogue = await db[EMBEDDINGS_COLLECTION].find_one(
        {"embeddingModel": {"$ne": active_tag}},
        {"embeddingModel": 1, "embedding": 1},
    )

    if rogue is None:
        # Everything carries our tag. Nothing to check, nothing to spend.
        stored_tag = active_tag
        sample = None
    else:
        total = await db[EMBEDDINGS_COLLECTION].count_documents({})
        if total == 0:
            logger.info("semantic_search_no_embeddings")
            return []

        foreign = await db[EMBEDDINGS_COLLECTION].count_documents(
            {"embeddingModel": {"$ne": active_tag}}
        )

        if foreign < total:
            # A MINORITY IS CONTAMINATION, NOT A WRONG CATALOGUE, and the
            # two deserve opposite responses. Refusing here would turn one
            # edited product into a total outage of semantic search, while
            # the other 142 comparisons are perfectly valid - and Atlas has
            # already dropped the bad vector from the index, which is why
            # nobody would otherwise notice.
            #
            # So the search proceeds and the incident is made loud: this
            # log line, the /health status, and scripts/check_embeddings.py
            # all name it. That is the difference between degrading and
            # degrading SILENTLY.
            logger.error(
                "embedding_collection_contaminated",
                contaminated=foreign,
                total=total,
                expected_tag=active_tag,
                example_id=str(rogue.get("_id")),
                example_tag=rogue.get("embeddingModel") or "(untagged)",
                example_width=len(rogue.get("embedding") or []),
                expected_width=expected_dimensions(),
                consequence="those products are missing from every semantic "
                            "search and nothing else reports it",
                fix="re-embed them with scripts/reembed_catalogue.py, and "
                    "stop the pipeline that wrote them",
            )
            stored_tag = active_tag
            sample = rogue
        else:
            # Every document disagrees - the catalogue belongs to another
            # model entirely, which is the original guard's case.
            sample = rogue
            stored_tag = rogue.get("embeddingModel")

    # THE UNTAGGED CASE. Documents from the original pipeline carry no
    # embeddingModel field at all, so they can never match any tag - and
    # with a read-only credential they cannot be tagged. LEGACY_EMBEDDING_
    # ASSUMED is the operator asserting which model produced them, backed
    # by scripts/identify_embedding_model.py reproducing those exact
    # vectors.
    #
    # It only helps when the assertion names the model we are ACTUALLY
    # running. Otherwise it falls through to the mismatch below, which is
    # the point: a stale assertion must not outlive the model it was
    # written for.
    if stored_tag is None:
        assumed = get_settings().legacy_embedding_assumed
        if assumed and assumed == active_tag:
            # Logged on every search, not once at startup. This is a
            # human claim standing in for a stored fact, and the log is
            # the only trace of that when results are later questioned.
            logger.info(
                "semantic_search_untagged_catalogue_assumed",
                assumed=assumed,
                basis="LEGACY_EMBEDDING_ASSUMED",
                caveat="documents carry no tag; match is asserted, not stored",
            )
            stored_tag = assumed

    if stored_tag != active_tag:
        logger.error(
            "semantic_search_model_mismatch",
            stored=stored_tag or "(untagged - original pipeline)",
            expected=active_tag,
            consequence="refusing to search rather than return ranked noise",
        )
        raise EmbeddingModelMismatch(
            f"The catalogue was embedded by {stored_tag or 'an unidentified model'}, "
            f"but queries would be embedded by {active_tag}. Either run "
            f"scripts/identify_embedding_model.py and configure the matching "
            f"model, run scripts/reembed_catalogue.py, or use "
            f"find_similar_products, which needs no model."
        )

    try:
        query_vector = await embed_query(query)
    except EmbeddingUnavailable as exc:
        logger.warning("semantic_search_unavailable", detail=str(exc)[:200])
        return []

    pipeline = [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX,
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": max(100, limit * 20),
                "limit": limit,
            }
        },
        {"$project": {"score": {"$meta": "vectorSearchScore"}}},
    ]

    try:
        hits = [doc async for doc in db[EMBEDDINGS_COLLECTION].aggregate(pipeline)]
    except OperationFailure as exc:
        logger.warning(
            "semantic_search_index_unavailable",
            index=VECTOR_INDEX,
            detail=str(exc)[:200],
        )
        return []

    ranked_ids = [h["_id"] for h in hits]
    if not ranked_ids:
        return []

    products = await get_products_by_ids(ranked_ids)
    by_id = {p["_id"]: p for p in products if not p.get("isSold")}
    # $in does not preserve order, and here the order IS the relevance.
    return [by_id[pid] for pid in ranked_ids if pid in by_id]
