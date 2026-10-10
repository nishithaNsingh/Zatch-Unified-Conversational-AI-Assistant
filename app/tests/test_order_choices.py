"""
WHAT:
    Tests the tap-to-choose order buttons: the rows lifted from
    get_order_history, and the rule for when a reply actually offers them.

WHY:
    Shoppers in manager testing were asked to "reply with the item name or
    the last 4 digits" and got it wrong. A button removes the typing, but a
    button under the wrong reply - one order answered, or orders the reply
    never listed - is a question nobody asked. Most of what is tested here
    is when NOT to draw one.

FLOW:
    Pure unit tests over _order_choices and order_choices_offered, plus one
    that execute_tool fills the collector. No database, no LLM.
"""

from app.agent import tool_executor
from app.agent.grounding import order_choices_offered
from app.agent.tool_executor import _order_choices, execute_tool


def order(order_id, *names, status="shipped", overdue=False):
    row = {
        "orderId": order_id, "status": status,
        "items": [{"name": n, "qty": 1} for n in names], "total": 999,
    }
    if overdue:
        row["isOverdue"] = True
        row["daysLate"] = 3
    return row


SUMMARY = {
    "orders": [
        order("ORD20260001378", "Blue Kurta"),
        order("ORD20260002291", "Silk Saree", "Dupatta", status="pending", overdue=True),
    ],
    "showing": 2, "totalOrders": 2, "note": None,
}


class TestWhatBecomesAChoice:
    def test_rows_come_from_the_summarised_history(self):
        choices = _order_choices("get_order_history", SUMMARY)
        assert [c["last4"] for c in choices] == ["1378", "2291"]
        assert choices[0] == {
            "order_id": "ORD20260001378", "last4": "1378", "item": "Blue Kurta",
            "status": "shipped", "late": False,
            "message": "Blue Kurta, order ending 1378",
        }

    def test_several_items_are_summarised_and_lateness_travels(self):
        second = _order_choices("get_order_history", SUMMARY)[1]
        assert second["item"] == "Silk Saree + 1 more"
        assert second["late"] is True
        # The tap sends what a typing shopper was asked for: item and digits.
        assert second["message"] == "Silk Saree + 1 more, order ending 2291"

    def test_an_unsummarised_list_is_read_too(self):
        assert len(_order_choices("get_order_history", SUMMARY["orders"])) == 2

    def test_an_order_with_no_id_is_not_offered(self):
        assert _order_choices("get_order_history", {"orders": [order(None, "x")]}) == []

    def test_an_order_with_no_items_still_has_a_message(self):
        [c] = _order_choices("get_order_history", {"orders": [order("ORD20260004444")]})
        assert c["item"] is None
        assert c["message"] == "The order ending 4444"

    def test_names_are_stripped_before_they_are_sent(self):
        [c] = _order_choices("get_order_history", {"orders": [order("ORD20260006819", "US Polo black shirt ")]})
        assert c["message"] == "US Polo black shirt, order ending 6819"

    def test_other_tools_never_produce_choices(self):
        assert _order_choices("get_order_status", SUMMARY) == []
        assert _order_choices("get_trending_products", [{"orderId": "ORD20260001378"}]) == []


class TestWhenTheReplyOffersThem:
    CHOICES = _order_choices("get_order_history", SUMMARY)

    def test_a_reply_listing_both_offers_both(self):
        reply = ("You have two orders:\n- Blue Kurta, ending 1378, shipped\n"
                 "- Silk Saree, ending 2291, pending (late)\nWhich one?")
        assert [c["last4"] for c in order_choices_offered(reply, self.CHOICES)] == ["1378", "2291"]

    def test_the_digits_are_found_whatever_the_wording(self):
        reply = "Blue Kurta (…1378) or Silk Saree (#2291)?"
        assert len(order_choices_offered(reply, self.CHOICES)) == 2

    def test_one_order_named_is_an_answer_not_a_question(self):
        reply = "Your Blue Kurta, order ending 1378, is out for delivery."
        assert order_choices_offered(reply, self.CHOICES) == []

    def test_digits_inside_a_longer_number_do_not_count(self):
        reply = "Orders 21378 and 22910 are not yours."
        assert order_choices_offered(reply, self.CHOICES) == []

    def test_an_order_the_reply_leaves_out_gets_no_button(self):
        third = _order_choices("get_order_history", {"orders": [order("ORD20260009999", "Shoes")]})
        reply = "Which one - ending 1378 or ending 2291?"
        offered = order_choices_offered(reply, self.CHOICES + third)
        assert [c["last4"] for c in offered] == ["1378", "2291"]

    def test_an_empty_reply_offers_nothing(self):
        assert order_choices_offered("", self.CHOICES) == []


class TestTheCollectorIsFilled:
    async def test_execute_tool_appends_an_orders_batch(self, monkeypatch):
        async def fake_history(user_id, limit=5):
            return [
                {"orderId": "ORD20260001378", "status": "shipped",
                 "items": [{"name": "Blue Kurta", "qty": 1}]},
                {"orderId": "ORD20260002291", "status": "pending",
                 "items": [{"name": "Silk Saree", "qty": 1}]},
            ]

        async def fake_count(user_id):
            return 2

        monkeypatch.setitem(
            __import__("app.agent.tools", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY,
            "get_order_history", (fake_history, True),
        )
        monkeypatch.setattr(tool_executor.orders_repo, "count_orders", fake_count)
        monkeypatch.setattr(tool_executor.orders_repo, "count_in_progress", fake_count)

        batches: list = []
        await execute_tool("get_order_history", {}, "u1", collector=batches)

        assert [b["kind"] for b in batches] == ["orders"]
        assert [c["last4"] for c in batches[0]["items"]] == ["1378", "2291"]
