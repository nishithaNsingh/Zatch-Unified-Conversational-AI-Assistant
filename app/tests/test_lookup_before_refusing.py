"""
WHAT:
    Pins the second round of fixes from the 10 October 2026 NVIDIA
    re-test: questions refused before any lookup, a figure answered from
    memory, and in-progress orders hidden behind newer finished ones.

WHY:
    Measured on Nemotron, on the FIRST call, before any lookup:

      "Which is better, the pattu saree or the Fancy saree?"  refused 6/6
      "mera order kahan hai?"                                  refused 1/6
      "Compare the pattu saree and the Fancy saree"            refused 0/5

    The trigger is the "which is X, A or B?" shape, which reads to the
    model as general knowledge - not the products. The SCOPE wording now
    names the case (2/5 left over), and a refusal with no lookup, for a
    message that reads like shopping, is asked once more with the tools.

    "Any offers right now?" was once answered "I found 12 products..."
    with no tool called. And a buyer with 9 orders in progress - none
    among their 5 newest - was told nothing was in progress.

FLOW:
    Unit tests, run_conversation driven by a scripted model, a fake orders
    collection, and one test against the real database (needs_db).
"""

import json

import pytest

from app.agent import grounding, orchestrator, tool_executor
from app.agent.llm_client import Completion, ToolCall
from app.agent.orchestrator import OUT_OF_SCOPE_REPLY
from app.agent.tool_executor import _trim_order_history

SYSTEM = [{"role": "system", "content": "stub"}]


def tool_call(name, args="{}", call_id="c1") -> Completion:
    return Completion(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args, extra={})],
        provider="scripted", model="m", prompt_tokens=0,
    )


def text(content) -> Completion:
    return Completion(content=content, tool_calls=[], provider="scripted",
                      model="m", prompt_tokens=0)


def lookup_turn(tool_name, payload, question="q"):
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": tool_name, "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps(payload)},
    ]


def order(order_id, name, status="pending"):
    return {"orderId": order_id, "status": status,
            "items": [{"name": name, "qty": 1}], "total": 452}


async def converse(question):
    return await orchestrator.run_conversation("u1", question, history=list(SYSTEM))


CATALOGUE = grounding.catalogue_words({
    "categories": ["Men", "Women", "Home"],
    "subCategories": ["sarees", "formal-shirts", "watches", "photo-frames"],
})


# ── What reads as shopping ───────────────────────────────────────────

class TestWhatReadsAsShopping:
    def test_catalogue_kinds_are_singularised(self):
        assert {"saree", "shirt", "formal", "watch", "photo", "frame"} <= CATALOGUE

    @pytest.mark.parametrize("message", [
        "Which is better, the pattu saree or the Fancy saree?",
        "Which is cheaper, the RL Black shirt or the Stone Article Shirt?",
        "mera order kahan hai?",
        "मेरे कार्ट में क्या है?",
        "Any offers right now?",
        "kitne ka hai ye daam batao",
    ])
    def test_shopping_questions(self, message):
        assert grounding.looks_like_shopping(message, CATALOGUE)

    @pytest.mark.parametrize("message", [
        "France ki rajdhani kya hai?",
        "Write Python code to reverse a list",
        "What's the weather in Mumbai today?",
        "फ्रांस की राजधानी क्या है?",
        "Who won the cricket match yesterday?",
    ])
    def test_off_topic_questions(self, message):
        assert not grounding.looks_like_shopping(message, CATALOGUE)

    def test_no_catalogue_still_knows_orders_carts_and_offers(self):
        assert grounding.looks_like_shopping("mera order kahan hai?")
        assert not grounding.looks_like_shopping("Which is better, the pattu saree or the Fancy saree?")


class TestFiguresNobodyLookedUp:
    def test_an_invented_count_is_found(self):
        reply = "I found 12 products with the biggest current reductions right now."
        assert grounding.unsourced_figures(reply, SYSTEM) == ["12"]

    def test_a_figure_from_an_earlier_lookup_is_sourced(self):
        history = SYSTEM + lookup_turn("get_discounted_products", {"totalDiscounted": 141})
        assert grounding.unsourced_figures("141 products are discounted.", history) == []

    def test_a_figure_the_user_gave_is_sourced(self):
        history = SYSTEM + [{"role": "user", "content": "anything under 500?"}]
        assert grounding.unsourced_figures("Under ₹500, let me check.", history) == []

    def test_list_numbering_is_not_a_figure(self):
        reply = "I can help with:\n1. your orders\n2. products\n3. offers"
        assert grounding.unsourced_figures(reply, SYSTEM) == []


# ── The second look, in the loop ─────────────────────────────────────

@pytest.fixture
def scripted(monkeypatch):
    """A scripted model and tool. Records, per model call, the tool_choice
    and the last message, so a test can see the nudge and that tools were
    allowed with it."""

    async def categories():
        return {"categories": ["Women"], "subCategories": ["sarees", "formal-shirts"]}

    monkeypatch.setattr(orchestrator.products_repo, "get_distinct_categories", categories)

    def install(replies, tool_result):
        seen = []

        async def fake_llm(messages, user_id, tool_choice="auto", on_text=None):
            seen.append({"choice": tool_choice, "last": messages[-1].get("content")})
            return replies[len(seen) - 1]

        async def fake_tool(name, args, user_id, collector=None):
            return json.dumps(tool_result)

        monkeypatch.setattr(orchestrator, "_complete_with_failover", fake_llm)
        monkeypatch.setattr(orchestrator, "execute_tool", fake_tool)
        return seen

    return install


class TestTheSecondLook:
    async def test_a_refused_comparison_is_asked_again_with_tools(self, scripted):
        found = [{"product_id": "1", "name": "pattu saree", "price": 999, "discountedPrice": 699}]
        answer = "Both are ₹699 after discount; the pattu saree has maggam work."
        seen = scripted([
            text(OUT_OF_SCOPE_REPLY),
            tool_call("search_products_by_name", call_id="a"),
            tool_call("search_products_by_name", call_id="b"),
            tool_call("compare_products", call_id="c"),
            text(answer),
        ], found)

        reply, messages = await converse("Which is better, the pattu saree or the Fancy saree?")

        assert reply == answer
        assert seen[1]["last"] == f"[{orchestrator.LOOK_BEFORE_REFUSING}]"
        # The nudge must not cost a tool round. A comparison with no nudge
        # is three tool rounds (search, search, compare) and then the
        # synthesis turn; nudged, it is exactly that plus the refused call.
        assert [s["choice"] for s in seen] == ["auto", "auto", "auto", "auto", "none"]
        contents = [m.get("content") for m in messages]
        assert OUT_OF_SCOPE_REPLY not in contents
        assert f"[{orchestrator.LOOK_BEFORE_REFUSING}]" not in contents, (
            "the nudge travels on one call and is never stored"
        )

    async def test_an_off_topic_refusal_is_not_asked_again(self, scripted):
        seen = scripted([text(OUT_OF_SCOPE_REPLY)], [])
        reply, _ = await converse("France ki rajdhani kya hai?")
        assert reply == OUT_OF_SCOPE_REPLY
        assert len(seen) == 1

    async def test_a_second_refusal_stands(self, scripted):
        seen = scripted([text(OUT_OF_SCOPE_REPLY), text(OUT_OF_SCOPE_REPLY)], [])
        reply, _ = await converse("mera order kahan hai?")
        assert reply == OUT_OF_SCOPE_REPLY
        assert len(seen) == 2, "asked again once, never more"

    async def test_an_invented_figure_is_asked_again_with_tools(self, scripted):
        deals = {"deals": [{"product_id": "1", "name": "black", "price": 1000,
                            "discountedPrice": 5, "discountPercent": 99}],
                 "totalDiscounted": 141}
        answer = "The biggest reduction is black - ₹5, was ₹1000 (99% off)."
        seen = scripted([
            text("I found 12 products with the biggest current reductions right now."),
            tool_call("get_discounted_products"),
            text(answer),
        ], deals)

        reply, _ = await converse("Any offers right now?")
        assert reply == answer
        assert seen[1]["last"] == f"[{orchestrator.LOOK_IT_UP}]"

    async def test_a_greeting_is_left_alone(self, scripted):
        seen = scripted([text("Hi! I can help with your Zatch orders and products.")], [])
        await converse("hi")
        assert len(seen) == 1

    async def test_a_reply_after_a_lookup_is_never_nudged(self, scripted):
        """The nudge is for answers written from nothing. Once a tool has
        run, the existing checks own the reply."""
        seen = scripted([tool_call("get_cart"), text("Your cart has 12 items.")],
                        {"items": [], "itemCount": 0})
        await converse("what's in my cart")
        assert len(seen) == 2


class TestAnEmptySynthesisTriesADifferentShape:
    """Measured: 2 of ~20 order questions came back empty on both identical
    synthesis attempts. The third attempt sends the lookups as plain text."""

    async def test_the_third_attempt_is_flattened_and_answers(self, monkeypatch):
        calls = []
        answer = "Your US Polo black shirt is pending, 194 days late."
        replies = [
            tool_call("get_order_history", call_id="a"),
            tool_call("get_order_detail", call_id="b"),
            tool_call("get_delivery_estimate", call_id="c"),
            text(""), text(""), text(answer),
        ]

        async def fake_llm(messages, user_id, tool_choice="auto", on_text=None):
            calls.append(messages)
            return replies[len(calls) - 1]

        async def fake_tool(name, args, user_id, collector=None):
            return json.dumps({"orders": [order("ORD1774196577926819", "US Polo black shirt")]})

        monkeypatch.setattr(orchestrator, "_complete_with_failover", fake_llm)
        monkeypatch.setattr(orchestrator, "execute_tool", fake_tool)

        reply, messages = await converse("Where is my order?")

        assert reply == answer
        assert any(m.get("role") == "tool" for m in calls[4]), "attempt 2 is the same shape"
        assert not any(m.get("role") == "tool" for m in calls[5]), "attempt 3 is flattened"
        assert any(m.get("role") == "tool" for m in messages), (
            "the stored history keeps the real tool exchanges"
        )


class TestTheScopeWordingNamesTheCase:
    async def test_it_says_when_to_refuse_and_what_is_never_refused(self, monkeypatch):
        async def categories():
            return {"categories": ["Women"], "subCategories": ["sarees"]}

        monkeypatch.setattr(orchestrator.products_repo, "get_distinct_categories", categories)
        prompt = await orchestrator.build_system_prompt()
        assert "Refuse ONLY when the message clearly has nothing to do with shopping on Zatch" in prompt
        assert "which is better, X or Y" in prompt
        assert "mera order kahan hai" in prompt
        assert OUT_OF_SCOPE_REPLY in prompt, "the refusal itself is unchanged"


# ── In-progress orders first ─────────────────────────────────────────

class FakeOrders:
    """Just enough of a Motor collection for get_order_history: find with
    a status $in / $nin, sort, limit, to_list."""

    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection=None):
        status = query.get("status", {})
        rows = [r for r in self.rows if r["buyerId"] == query["buyerId"]]
        if "$in" in status:
            rows = [r for r in rows if r["status"] in status["$in"]]
        if "$nin" in status:
            rows = [r for r in rows if r["status"] not in status["$nin"]]
        return _Cursor(rows)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction):
        self.rows = sorted(self.rows, key=lambda r: r[key], reverse=direction < 0)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, length=None):
        return self.rows


class TestInProgressFirst:
    BUYER = "698e25132c63bfbc047683d8"

    def install(self, monkeypatch, statuses):
        from bson import ObjectId

        from app.repos import orders_repo

        buyer = ObjectId(self.BUYER)
        # Index 0 is the most recent order.
        rows = [
            {"_id": i, "orderId": f"ORD{1000 + i}", "buyerId": buyer,
             "status": status, "createdAt": 100 - i}
            for i, status in enumerate(statuses)
        ]
        monkeypatch.setattr(orders_repo, "get_database", lambda: {"orders": FakeOrders(rows)})
        return orders_repo

    async def test_older_open_orders_come_before_newer_finished_ones(self, monkeypatch):
        """The measured buyer: the newest five finished, older ones open."""
        repo = self.install(monkeypatch, ["delivered"] * 5 + ["confirmed", "processing", "shipped"])
        history = await repo.get_order_history(self.BUYER, limit=5)
        assert [o["status"] for o in history] == [
            "confirmed", "processing", "shipped", "delivered", "delivered",
        ]

    async def test_open_orders_keep_newest_first_among_themselves(self, monkeypatch):
        repo = self.install(monkeypatch, ["delivered", "pending", "delivered", "shipped"])
        history = await repo.get_order_history(self.BUYER, limit=5)
        assert [o["orderId"] for o in history] == ["ORD1001", "ORD1003", "ORD1000", "ORD1002"]

    async def test_more_open_orders_than_room_shows_only_open_ones(self, monkeypatch):
        repo = self.install(monkeypatch, ["delivered"] + ["pending"] * 9)
        history = await repo.get_order_history(self.BUYER, limit=5)
        assert [o["status"] for o in history] == ["pending"] * 5

    async def test_with_nothing_open_the_most_recent_come_first(self, monkeypatch):
        repo = self.install(monkeypatch, ["delivered", "cancelled", "delivered"])
        history = await repo.get_order_history(self.BUYER, limit=5)
        assert [o["orderId"] for o in history] == ["ORD1000", "ORD1001", "ORD1002"]

    def test_the_executor_and_the_repo_share_one_definition(self):
        from app.repos import orders_repo

        assert tool_executor.DELIVERED_OR_DONE is orders_repo.COMPLETED_STATUSES


class TestTheStatusIsTheDatasNotThePromptsExample:
    """Measured: two PENDING orders reported as "out for delivery" in 5 of
    5 live replies - the example the ORDERS rule used for "plain words"."""

    def test_each_order_carries_its_status_in_words(self):
        rows = _trim_order_history([
            order("ORD1", "a", status="pending"),
            order("ORD2", "b", status="out_for_delivery"),
            order("ORD3", "c", status="ready_to_ship"),
        ])
        assert [r["statusText"] for r in rows] == ["pending", "out for delivery", "ready to ship"]

    def test_each_order_carries_its_last_four_digits(self):
        """Measured: asked for "the last 4 digits", the model wrote 8479,
        74721 and 7792 for ids ending 7952, 1940 and 6819."""
        [row] = _trim_order_history([order("ORD1774190779847952", "US Polo black shirt")])
        assert row["last4"] == "7952"

    async def test_the_prompt_no_longer_offers_an_example_status(self, monkeypatch):
        async def categories():
            return {"categories": ["Women"], "subCategories": ["sarees"]}

        monkeypatch.setattr(orchestrator.products_repo, "get_distinct_categories", categories)
        prompt = await orchestrator.build_system_prompt()
        assert "out for delivery" not in prompt
        assert "statusText exactly as given" in prompt


class TestTheSummarySaysTheTotals:
    async def summarise(self, monkeypatch, rows, total, open_total):
        async def count(user_id):
            return total

        async def open_count(user_id):
            return open_total

        monkeypatch.setattr(tool_executor.orders_repo, "count_orders", count)
        monkeypatch.setattr(tool_executor.orders_repo, "count_in_progress", open_count)
        return await tool_executor._summarise_orders(_trim_order_history(rows), "u1")

    async def test_more_open_orders_than_shown(self, monkeypatch):
        out = await self.summarise(monkeypatch, [order(f"ORD{i}", "x") for i in range(5)], 22, 9)
        assert out["inProgressTotal"] == 9
        assert out["note"] == (
            "The user has 22 orders in total, 9 in progress. "
            "Showing 5 of the in-progress ones, newest first."
        )

    async def test_every_open_order_shown(self, monkeypatch):
        rows = [order("ORD1", "a"), order("ORD2", "b", status="delivered")]
        out = await self.summarise(monkeypatch, rows, 67, 1)
        assert "67 orders in total, 1 in progress" in out["note"]
        assert "every in-progress order first" in out["note"]

    async def test_all_orders_fit(self, monkeypatch):
        out = await self.summarise(monkeypatch, [order("ORD1", "a", status="delivered")], 1, 0)
        assert out["note"] == "These are all 1 of the user's orders; 0 in progress."


class TestInProgressFirstOnRealData:
    async def test_no_open_order_is_hidden_behind_a_finished_one(self, db):
        """Against the real collection: for buyers with an open order, the
        window lists open orders before any finished one, and shows as many
        open ones as fit."""
        from app.repos import orders_repo

        buyers = await db.orders.distinct(
            "buyerId", {"status": {"$nin": sorted(orders_repo.COMPLETED_STATUSES)}}
        )
        assert buyers, "the sandbox has no open orders to test with"
        for buyer in buyers[:5]:
            history = await orders_repo.get_order_history(str(buyer), limit=5)
            flags = [o["status"] not in orders_repo.COMPLETED_STATUSES for o in history]
            assert flags == sorted(flags, reverse=True), "an open order came after a finished one"
            open_total = await orders_repo.count_in_progress(str(buyer))
            assert sum(flags) == min(open_total, 5)
