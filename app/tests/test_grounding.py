"""
WHAT:
    Tests the mechanical grounding checks - the ones that decide whether
    an answer's claims trace back to a tool result.

WHY THESE MATTER MORE THAN THE EVAL SUITE AROUND THEM:
    The eval runner is a script; if it miscounts, someone notices a
    strange number. These functions are the thing a live grounding
    verifier would call before an answer reaches a customer, so a false
    NEGATIVE here is a hallucination shipped, and a false POSITIVE is a
    correct answer blocked.

    The second one is why the money check is deliberately soft: a cart
    subtotal is arithmetic over tool results rather than a quote from
    one, and a checker that cannot tell those apart would flag honest
    answers forever until someone turned it off.

FLOW:
    Pure unit tests. No database, no LLM.
"""

import json

from app.evals import grounding


def tool(payload) -> dict:
    return {"role": "tool", "tool_call_id": "t1", "content": json.dumps(payload)}


ORDERS = tool({
    "orders": [
        {"orderId": "ORD1771057102499209", "status": "shipped",
         "items": [{"name": "Lenin shirts", "qty": 1}], "total": 1061},
    ],
    "showing": 1, "totalOrders": 9,
})


class TestOrderIdsAreHardEvidence:
    """An order id is never arrived at by arithmetic - it was returned
    or it was invented."""

    def test_a_quoted_order_id_is_grounded(self):
        report = grounding.check(
            "Your order ORD1771057102499209 has shipped.", [ORDERS]
        )
        assert report.checked_order_ids == 1
        assert report.ungrounded_order_ids == []
        assert report.ok

    def test_an_invented_order_id_is_a_hard_failure(self):
        report = grounding.check("Your order ORD9999999999999999 shipped.", [ORDERS])
        assert report.ungrounded_order_ids == ["ORD9999999999999999"]
        assert not report.ok
        assert "invented order id" in report.hard_failures[0]

    def test_an_id_inside_a_longer_string_still_counts_as_returned(self):
        """Tool results carry ids inside sentences too - a note, a
        tracking blurb. Returned is returned."""
        messages = [tool({"note": "Refund raised for ORD1771057102499209 today"})]
        report = grounding.check("That's ORD1771057102499209.", messages)
        assert report.ok


class TestMoneyIsSoftEvidence:
    def test_a_quoted_price_is_grounded(self):
        report = grounding.check("The total was ₹1,061.", [ORDERS])
        assert report.checked_amounts == 1
        assert report.unmatched_amounts == []

    def test_indian_digit_grouping_is_normalised(self):
        messages = [tool({"price": 125500})]
        report = grounding.check("It is ₹1,25,500.", messages)
        assert report.unmatched_amounts == []

    def test_rupees_written_as_rs_is_read_too(self):
        messages = [tool({"price": 800})]
        assert grounding.check("Rs. 800 for that one.", messages).unmatched_amounts == []

    def test_an_unmatched_amount_is_reported_but_does_not_fail(self):
        """It could be a hallucinated price, or an honest subtotal. The
        checker says what it saw and lets a human decide."""
        report = grounding.check("That comes to ₹4,999.", [ORDERS])
        assert report.unmatched_amounts == ["4,999"]
        assert report.ok, "a derived amount must not fail an answer"

    def test_a_boolean_does_not_ground_a_claim_of_one(self):
        """bool is an int subclass in Python, so isTopPick=True would
        otherwise silently vouch for '₹1'."""
        messages = [tool({"isTopPick": True, "isSold": False})]
        report = grounding.check("It costs ₹1.", messages)
        assert report.unmatched_amounts == ["1"]


class TestPromisingAnActionIsAlwaysWrong:
    """The assistant is read-only at the database role. A sentence
    claiming otherwise is wrong no matter what the tools returned."""

    def test_claiming_a_cancellation_fails(self):
        report = grounding.check("I've cancelled that order for you.", [ORDERS])
        assert not report.ok
        assert "promised an action" in report.hard_failures[0]

    def test_offering_to_notify_fails(self):
        report = grounding.check("I'll let you know when it ships.", [ORDERS])
        assert not report.ok

    def test_reporting_what_the_user_can_do_is_fine(self):
        report = grounding.check(
            "It can still be cancelled - you can do that from the order "
            "screen in the app.",
            [ORDERS],
        )
        assert report.ok


class TestItSurvivesMessyInput:
    def test_an_empty_reply_reports_nothing(self):
        assert grounding.check("", [ORDERS]).ok

    def test_a_malformed_tool_payload_is_skipped_not_raised(self):
        messages = [{"role": "tool", "tool_call_id": "x", "content": "not json"}]
        report = grounding.check("Order ORD1771057102499209.", messages)
        assert report.tool_results_seen == 0
        assert report.ungrounded_order_ids == ["ORD1771057102499209"]

    def test_non_tool_messages_are_not_treated_as_evidence(self):
        """The USER can name an order id. That is not the assistant
        having looked it up."""
        messages = [{"role": "user", "content": "where is ORD1771057102499209"}]
        report = grounding.check("ORD1771057102499209 has shipped.", messages)
        assert report.ungrounded_order_ids == ["ORD1771057102499209"]

    def test_no_messages_at_all(self):
        assert grounding.check("Hello.", []).ok


class TestTheMoneyPatternDoesNotSeeThingsThatAreNotMoney:
    """Every false positive here costs the soft findings a little
    credibility, and a report nobody trusts is a report nobody reads."""

    def test_rs_inside_an_ordinary_word_is_not_a_price(self):
        """Measured: "cancel within 24 hours 2 days after delivery" was
        reported as an ungrounded claim of Rs 2, because "rs" sits
        inside "hours" and the pattern was case-insensitive."""
        report = grounding.check(
            "You can cancel within 24 hours 2 days after it ships.", [ORDERS]
        )
        assert report.unmatched_amounts == []
        assert report.checked_amounts == 0

    def test_the_same_trap_in_orders_and_sellers(self):
        for word in ("orders 5", "sellers 9", "yours 3"):
            report = grounding.check(f"About your {word} of them.", [ORDERS])
            assert report.checked_amounts == 0, f"{word!r} read as money"

    def test_a_real_rs_price_is_still_read(self):
        messages = [tool({"price": 800})]
        assert grounding.check("Rs. 800 for that.", messages).checked_amounts == 1
        assert grounding.check("Rs 800 for that.", messages).unmatched_amounts == []
