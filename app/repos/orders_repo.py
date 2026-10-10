"""
WHAT:
    All 7 order-related data lookups from PDF §3 (Order Management).
    This is the ONLY file in the whole app allowed to query the
    `orders` collection directly.

WHY THIS APPROACH:
    Every function here takes user_id as a REQUIRED first argument and
    folds it directly into the MongoDB query itself (as buyerId) -
    not as an afterthought filter applied to results. This makes it
    structurally impossible to accidentally return another user's
    order, since the database itself never even fetches it.

FLOW:
    Called by Phase 5's feature logic and, later, Phase 7's tool-calling
    layer - which will inject the verified user_id from the JWT
    (Phase 3.2), never from chat text.

LOGIC:
    Every lookup is scoped by BOTH order_id AND buyerId in the same
    query. If an order_id exists but belongs to someone else, the query
    simply finds nothing - same as if the order didn't exist at all.
    This is what proves cross-user access is blocked at the query level,
    not just hidden afterward.

MECHANISM:
    Uses the shared _find_one_scoped() helper, which applies the Step
    3.3 allowlist as a MongoDB projection AND runs the Step 3.4
    sanitizer on the result - two independent layers, every single call.
"""

import structlog

from app.db.connection import get_database
from app.repos.base import to_object_id
from app.security.field_allowlist import get_projection
from app.security.sanitizer import sanitize_document, sanitize_documents

logger = structlog.get_logger()

COLLECTION = "orders"

# PDF §3.7 — which order statuses still allow cancellation.
CANCELLABLE_STATUSES = {"pending", "confirmed"}

# An order in any other status is still in progress. The one definition,
# shared with tool_executor's inProgress flag so the window, the count and
# the flag cannot disagree about what "in progress" means.
COMPLETED_STATUSES = frozenset({"delivered", "cancelled", "returned", "refunded"})


async def _find_one_scoped(user_id: str, extra_query: dict) -> dict | None:
    """Internal helper - EVERY order lookup goes through this, so the
    buyerId scoping rule can never be skipped or forgotten in a single
    function below."""
    buyer_object_id = to_object_id(user_id)
    if buyer_object_id is None:
        return None  # malformed user_id -> nothing found, no crash

    db = get_database()
    query = {"buyerId": buyer_object_id, **extra_query}
    projection = get_projection(COLLECTION)
    doc = await db[COLLECTION].find_one(query, projection)
    return sanitize_document(COLLECTION, doc)  # belt-and-suspenders layer 2


async def get_order_status(user_id: str, order_id: str) -> dict | None:
    """PDF §3.1 — current status + full stage history."""
    doc = await _find_one_scoped(user_id, {"orderId": order_id})
    if doc is None:
        return None
    return {
        "orderId": doc.get("orderId"),
        "status": doc.get("status"),
        "statusHistory": doc.get("statusHistory", []),
        "tracking": doc.get("tracking"),
    }


async def get_delivery_estimate(user_id: str, order_id: str) -> dict | None:
    """PDF §3.2 — expected delivery date."""
    doc = await _find_one_scoped(user_id, {"orderId": order_id})
    if doc is None:
        return None
    dates = doc.get("dates", {})
    tracking = doc.get("tracking", {})
    return {
        "orderId": doc.get("orderId"),
        "expectedDelivery": dates.get("expectedDelivery"),
        "estimatedDelivery": tracking.get("estimatedDelivery"),
    }


async def count_orders(user_id: str) -> int:
    """How many orders this buyer has, in total.

    EXISTS BECAUSE THE LIST IS CAPPED AND THE CAP IS INVISIBLE. The
    assistant only ever sees the most recent handful of orders - eight,
    after tool_executor trims them - and with nothing else to go on it
    reports that as the whole picture. Measured: a buyer with 67 orders
    was told "you have 8 orders", which is not a hallucination but a
    faithful reading of a truncated list.

    A count is cheap, and it is the one number that turns the window
    into a window rather than the total.
    """
    buyer_object_id = to_object_id(user_id)
    if buyer_object_id is None:
        return 0
    return await get_database()[COLLECTION].count_documents(
        {"buyerId": buyer_object_id}
    )


async def count_in_progress(user_id: str) -> int:
    """How many of this buyer's orders are still in progress - the number
    the ORDERS rule branches on, counted over ALL their orders rather than
    over whichever few the history window happens to show."""
    buyer_object_id = to_object_id(user_id)
    if buyer_object_id is None:
        return 0
    return await get_database()[COLLECTION].count_documents(
        {"buyerId": buyer_object_id, "status": {"$nin": sorted(COMPLETED_STATUSES)}}
    )


async def get_order_history(user_id: str, limit: int = 5) -> list[dict]:
    """PDF §3.3 — the buyer's orders, IN-PROGRESS ONES FIRST.

    WHY NOT SIMPLY NEWEST FIRST. "Where is my order?" is a question about
    an order that has not arrived. Newest-first let completed orders push
    the open ones out of the window: measured, a buyer with 9 orders in
    progress - none among their 5 newest - was told nothing was in
    progress. So the window is filled with open orders first (newest of
    those first), and only the room left over goes to the most recent
    completed ones, which the ORDERS rule still needs when nothing is open.
    """
    buyer_object_id = to_object_id(user_id)
    if buyer_object_id is None:
        return []  # graceful empty list, not a crash

    db = get_database()
    projection = get_projection(COLLECTION)
    done = sorted(COMPLETED_STATUSES)

    open_docs = await (
        db[COLLECTION]
        .find({"buyerId": buyer_object_id, "status": {"$nin": done}}, projection)
        .sort("createdAt", -1)
        .limit(limit)
        .to_list(length=limit)
    )
    room = limit - len(open_docs)
    closed_docs = []
    if room > 0:
        closed_docs = await (
            db[COLLECTION]
            .find({"buyerId": buyer_object_id, "status": {"$in": done}}, projection)
            .sort("createdAt", -1)
            .limit(room)
            .to_list(length=room)
        )
    return sanitize_documents(COLLECTION, open_docs + closed_docs)


async def get_order_detail(user_id: str, order_id: str) -> dict | None:
    """PDF §3.4 — items, pricing breakdown, coarse delivery location."""
    doc = await _find_one_scoped(user_id, {"orderId": order_id})
    if doc is None:
        return None
    delivery = doc.get("deliveryAddress", {}) or {}
    return {
        "orderId": doc.get("orderId"),
        "items": doc.get("items", []),
        "pricing": doc.get("pricing"),
        "deliveryCity": delivery.get("city"),
        "deliveryState": delivery.get("state"),
    }


async def get_invoice(user_id: str, order_id: str) -> dict | None:
    """PDF §3.5 — invoice link, IF one has been generated yet.
    Real sandbox data confirms this ISN'T always the case (only 56/134
    orders have one) — every caller must handle invoiceAvailable=False."""
    doc = await _find_one_scoped(user_id, {"orderId": order_id})
    if doc is None:
        return None
    invoice = doc.get("invoice") or {}
    if not invoice.get("url"):
        return {"orderId": doc.get("orderId"), "invoiceAvailable": False}
    return {
        "orderId": doc.get("orderId"),
        "invoiceAvailable": True,
        "url": invoice.get("url"),
        "generatedAt": invoice.get("generatedAt"),
    }


async def get_tracking(user_id: str, order_id: str) -> dict | None:
    """PDF §3.6 — courier and tracking details."""
    doc = await _find_one_scoped(user_id, {"orderId": order_id})
    if doc is None:
        return None
    tracking = doc.get("tracking", {}) or {}
    return {
        "orderId": doc.get("orderId"),
        "courier": tracking.get("courier"),
        "awb": tracking.get("awb"),
        "trackingUrl": tracking.get("trackingUrl"),
    }


async def check_cancellation_eligibility(user_id: str, order_id: str) -> dict | None:
    """PDF §3.7 — informational ONLY. This function (and the chatbot as
    a whole) never performs the cancellation itself — read-only, always."""
    doc = await _find_one_scoped(user_id, {"orderId": order_id})
    if doc is None:
        return None
    status = doc.get("status")
    return {
        "orderId": doc.get("orderId"),
        "status": status,
        "canCancel": status in CANCELLABLE_STATUSES,
    }

# Fewer shoppers than this and "also bought" is one person's basket, not
# a pattern. Measured on the live data: 16 buyers, three of them test
# accounts holding 7-15 products each, so a single buyer pairs trousers
# with a wall clock. Two distinct buyers is the least that is not
# anecdote.
MIN_CO_BUYERS = 2


async def count_co_buyers(product_id: str) -> dict[str, int]:
    """For one product: every OTHER product, and how many distinct
    shoppers bought both. Only pairs reaching MIN_CO_BUYERS are returned.

    THE ONE DELIBERATELY UNSCOPED ORDER QUERY, and it is safe for the
    reason it returns nothing but numbers: no buyer id, order id, date or
    address leaves this function - only {product_id: count}. Every other
    function in this module is scoped to the caller's own orders, and
    anything that needs an order's CONTENTS still must be.

    Across a shopper's orders, not within one order: measured, 131 of the
    134 orders hold a single item, so "in the same basket" is a signal
    this catalogue does not have yet.
    """
    object_id = to_object_id(product_id)
    if object_id is None:
        return {}

    db = get_database()
    buyers = await db[COLLECTION].distinct("buyerId", {"items.product": object_id})
    if len(buyers) < MIN_CO_BUYERS:
        return {}

    pipeline = [
        {"$match": {"buyerId": {"$in": buyers}}},
        {"$unwind": "$items"},
        {"$match": {"items.product": {"$ne": object_id, "$exists": True}}},
        {"$group": {"_id": "$items.product", "buyers": {"$addToSet": "$buyerId"}}},
        {"$project": {"n": {"$size": "$buyers"}}},
        {"$match": {"n": {"$gte": MIN_CO_BUYERS}}},
    ]
    return {str(row["_id"]): row["n"] async for row in db[COLLECTION].aggregate(pipeline)}
