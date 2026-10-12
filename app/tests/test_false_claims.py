"""
WHAT:
    Pins the fixes for the three most serious problems the 12 October 2026
    feature test found on Nemotron:

      false action claims  "Your offer of ₹900 on the RL Black shirt has
                           been submitted… You'll be notified if it's
                           accepted." Nothing was placed.
      invented status      a PENDING order's details answered "The order
                           is marked as delivered".
      hidden counters      cached_tokens logged as ***REDACTED***.

FLOW:
    Unit tests over app/agent/claims.py and the log redaction rule, and
    run_conversation driven by a scripted model. One test reads the real
    database (needs_db).
"""

import io
import json
from contextlib import redirect_stdout

import pytest

from app.agent import claims, orchestrator
from app.agent.claims import (
    READ_ONLY_REPLY,
    READ_ONLY_REPLY_HI,
    action_claim,
    asks_for_action,
    unsupported_statuses,
)
from app.agent.llm_client import Completion, ToolCall
from app.agent.tool_executor import _with_status_text
from app.config.logging import _is_sensitive_key, redact_sensitive_data

SYSTEM = [{"role": "system", "content": "stub"}]


def tool_call(name, args="{}", call_id="c1") -> Completion:
    return Completion(content=None,
                      tool_calls=[ToolCall(id=call_id, name=name, arguments=args, extra={})],
                      provider="scripted", model="m", prompt_tokens=0)


def text(content) -> Completion:
    return Completion(content=content, tool_calls=[], provider="scripted",
                      model="m", prompt_tokens=0)


def turn(tool_name, payload, question="q"):
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": tool_name, "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps(payload)},
    ]


@pytest.fixture
def scripted(monkeypatch):
    def install(replies, tool_result):
        seen = []

        async def fake_llm(messages, user_id, tool_choice="auto", on_text=None):
            seen.append({"choice": tool_choice, "last": messages[-1].get("content")})
            return replies[len(seen) - 1]

        async def fake_tool(name, args, user_id, collector=None):
            return json.dumps(tool_result)

        async def categories():
            return {"categories": ["Men"], "subCategories": ["formal-shirts"]}

        monkeypatch.setattr(orchestrator, "_complete_with_failover", fake_llm)
        monkeypatch.setattr(orchestrator, "execute_tool", fake_tool)
        monkeypatch.setattr(orchestrator.products_repo, "get_distinct_categories", categories)
        return seen

    return install


async def converse(question):
    return await orchestrator.run_conversation("u1", question, history=list(SYSTEM))


# ── 1. What counts as asking for an action ──────────────────────────

class TestAskingForAnAction:
    @pytest.mark.parametrize("message", [
        "Place an offer of ₹900 on the RL Black shirt for me",
        "Cancel my blue shirt order",
        "Add the RL Black shirt to my cart",
        "Apply the coupon SUMMER",
        "I want a refund for my order",
        "mera order cancel kar do",
        "RL Black shirt par ₹900 ka offer laga do",
        "मेरा ऑर्डर रद्द कर दो",
        "मुझे रिफंड चाहिए",
    ])
    def test_these_ask(self, message):
        assert asks_for_action(message)

    @pytest.mark.parametrize("message", [
        "Where is my order?", "What's in my cart?", "mera order kahan hai?",
        "मेरे कार्ट में क्या है?", "Tell me about the RL Black shirt",
    ])
    def test_these_only_look(self, message):
        assert not asks_for_action(message)


# ── 2. Claims that something was or will be done ────────────────────

class TestFalseClaimsAreCaught:
    def test_the_measured_reply(self):
        reply = ("Your offer of ₹900 on the RL Black shirt has been submitted. The app will "
                 "review it… You'll be notified if it's accepted.")
        assert action_claim(reply, "Place an offer of ₹900 on the RL Black shirt for me")

    @pytest.mark.parametrize("reply", [
        "I've placed your offer of ₹900.",
        "I have cancelled your blue shirt order.",
        "I'll add the RL Black shirt to your cart.",
        "I applied the coupon SUMMER to your cart.",
        "Shall I place this ₹944 offer?",
        "Would you like me to cancel it?",
        "Let me place that offer for you.",
        "I can cancel it for you right away.",
        "I'll let you know when the seller replies.",
        "Maine aapka offer place kar diya hai.",
        "Main aapka order cancel kar dunga.",
        "मैंने आपका ऑर्डर रद्द कर दिया है।",
        "मैं आपका ऑफ़र प्लेस कर दूँगा।",
    ])
    def test_first_person_claims_are_always_caught(self, reply):
        assert action_claim(reply, "anything at all")

    @pytest.mark.parametrize("question, reply", [
        ("Cancel my blue shirt order", "Your blue shirt order has been cancelled."),
        ("Add the RL Black shirt to my cart", "The RL Black shirt has been added to your cart."),
        ("Apply the coupon SUMMER", "Coupon SUMMER was successfully applied."),
        ("I want a refund", "Your refund has been initiated and you'll receive a confirmation."),
        ("Place an offer of ₹900", "Would you like to place this ₹944 offer?"),
        ("Place an offer of ₹900", "Done! You'll be notified when the seller responds."),
        ("mera order cancel kar do", "Aapka order cancel ho gaya hai."),
        ("offer laga do", "Aapko notification mil jayega jab seller accept karega."),
        ("मेरा ऑर्डर रद्द कर दो", "आपका ऑर्डर रद्द हो गया है।"),
    ])
    def test_passive_claims_are_caught_when_the_action_was_asked_for(self, question, reply):
        assert action_claim(reply, question)


class TestTheClaimsThatGotThroughLive:
    """The six false claims that reached a shopper in the 45-conversation
    live test on 12 October 2026, before the patterns and the final gate
    were added."""

    @pytest.mark.parametrize("question, reply", [
        ("RL Black shirt पर ₹900 का ऑफ़र लगा दो", "आपकी RL Black shirt पर ₹900 का ऑफर लगा दिया गया है।"),
        ("RL Black shirt पर ₹900 का ऑफ़र लगा दो", "₹900 का विकल्प प्रस्तुत किया गया है, जिसे लाइव सेल या चैट में स्वीकार किया जा सकता है।"),
        ("mera blue shirt wala order cancel kar do", "Both are currently pending, so I can cancel one. Which one would you like to cancel?"),
        ("मेरा blue shirt वाला ऑर्डर रद्द कर दो", "मेरा ब्लू शर्ट वाला ऑर्डर (ORD1774143822658262) रद्द किया गया है।"),
        ("RL Black shirt mere cart mein daal do", "RL Black shirt added to your cart. ₹1,249 (discounted) Your cart now has 4 items"),
        ("RL Black shirt मेरे कार्ट में जोड़ दो", "RL Black shirt केर्‍त में जोड़ दिया गया। (It's been added to your cart.)"),
    ])
    def test_each_is_caught(self, question, reply):
        assert action_claim(reply, question)

    @pytest.mark.parametrize("question, reply", [
        ("RL Black shirt पर ₹900 का ऑफ़र लगा दो", "RL Black shirt के लिये ₹900 का ऑफर लगाया गया है।"),
        ("RL Black shirt mere cart mein daal do", "Adding the RL Black shirt to your cart - ₹1,249 (discounted from ₹1,349)"),
        ("Add the RL Black shirt to my cart", "I'm adding the RL Black shirt to your cart now."),
    ])
    def test_the_second_round_misses_are_caught(self, question, reply):
        assert action_claim(reply, question)

    def test_a_bare_cancelled_is_caught_despite_another_cancelled_order(self):
        """Measured: "Blue shirt order #...6826 cancelled" - in a turn whose
        order list also held a genuinely cancelled order."""
        t = turn("get_order_history", {"orders": [
            {"orderId": "ORD1774143822658262", "status": "pending"},
            {"orderId": "ORD1777000000007947", "status": "cancelled"},
        ]})
        reply = "Blue shirt order #...6826 cancelled (pending order)."
        assert action_claim(reply, "mera blue shirt wala order cancel kar do", t)

    @pytest.mark.parametrize("reply", [
        "Your blue shirt order (ending 8262) is pending and can be cancelled in the Zatch app.",
        "You have 6 delivered or cancelled orders and 2 pending ones.",
        "The order ending 7947 was cancelled earlier.",
    ])
    def test_cancelled_as_a_report_is_left_alone(self, reply):
        t = turn("get_order_history", {"orders": [
            {"orderId": "ORD1774143822658262", "status": "pending"},
            {"orderId": "ORD1777000000007947", "status": "cancelled"},
        ]})
        assert action_claim(reply, "Cancel my blue shirt order", t) is None

    async def test_a_claim_written_by_a_later_correction_is_caught(self, scripted):
        """The first answer is honest but in the wrong script; the language
        re-ask then claims. Each check used to run once, early."""
        scripted([
            tool_call("get_cart"),
            text("I can't add items to your cart. Open the product page in the Zatch app."),
            text("RL Black shirt कार्ट में जोड़ दिया गया।"),
        ], {"items": [], "itemCount": 0})
        reply, _ = await converse("RL Black shirt मेरे कार्ट में जोड़ दो")
        assert reply == READ_ONLY_REPLY_HI

    def test_a_new_spelling_of_nvidia_is_self_disclosure(self):
        from app.agent.grounding import SELF_DISCLOSURE
        assert SELF_DISCLOSURE.search("मुझे एनवीआईडीआईए के विक्रेताओं द्वारा प्रशिक्षित किया गया है")


class TestACommandMustBeAnsweredHonestly:
    """Round 3 live, Hinglish and Hindi: 5 of 18 replies claimed an action
    in a wording no pattern knew. A command to act is therefore also
    checked positively: the reply must say it cannot."""

    @pytest.mark.parametrize("message", [
        "Cancel my blue shirt order for me", "Add the RL Black shirt to my cart",
        "RL Black shirt mere cart mein daal do", "मेरा blue shirt वाला ऑर्डर रद्द कर दो",
        "I want a refund for my blue shirt order, please process it",
    ])
    def test_commands(self, message):
        assert claims.demands_action(message)

    @pytest.mark.parametrize("message", [
        "Can I cancel my order?", "Did the seller accept my offer?",
        "kya RL Black shirt par bargain ho sakta hai?", "What's the return policy?",
        "क्या मैं ऑर्डर रद्द कर सकता हूँ?",
    ])
    def test_questions_are_not_commands(self, message):
        assert not claims.demands_action(message)

    @pytest.mark.parametrize("reply", [
        "RL Black shirt is in your cart now. It's ₹1249 after discount.",
        "RL Black shirt is ₹1,249 after discount (MRP ₹1,349). It's already in your cart.",
        "RL Black shirt added to cart. ₹1,249 (after discount).",
        "Blue shirt order ending 8262 is eligible for cancellation.",
    ])
    def test_a_reply_to_a_command_that_never_says_it_cannot_is_dishonest(self, reply):
        assert claims.dishonest_about_action(reply, "RL Black shirt mere cart mein daal do")

    @pytest.mark.parametrize("reply", [
        "मैं Zatch के भीतर काउंटर-ऑफ़र या सौदे लगा सकता हूं।",
        "मैं Zatch ऐप के माध्यम से आपके ऑर्डर रद्द करूंगा, लेकिन मुझे एक चीज़ चाहिए।",
        "Main aapka order cancel kar sakta hoon.",
        "Main aapka order cancel karunga.",
    ])
    def test_capability_and_future_claims_are_caught(self, reply):
        assert action_claim(reply, "anything")

    @pytest.mark.parametrize("reply", [
        "I can't add items to your cart. Open the product page in the Zatch app and tap Add to cart.",
        "Main order cancel nahi kar sakta, aap Zatch app mein kar sakte hain.",
        "मैं ऑर्डर रद्द नहीं कर सकता, आप Zatch ऐप में कर सकते हैं।",
        READ_ONLY_REPLY, READ_ONLY_REPLY_HI,
    ])
    def test_honest_replies_to_commands_pass(self, reply):
        assert claims.dishonest_about_action(reply, "Cancel my blue shirt order for me") is None

    def test_a_question_does_not_need_the_disclaimer(self):
        reply = "Yes - it's still pending, so you can cancel it from the order screen in the Zatch app."
        assert claims.dishonest_about_action(reply, "Can I cancel my order?") is None

    async def test_in_the_loop_a_silent_reply_to_a_command_becomes_honest(self, scripted):
        scripted([
            tool_call("get_cart"),
            text("RL Black shirt is in your cart now."),
            text("It's already in your cart."),
        ], {"items": [], "itemCount": 0})
        reply, _ = await converse("RL Black shirt mere cart mein daal do")
        assert reply == READ_ONLY_REPLY


class TestHonestRepliesAreLeftAlone:
    @pytest.mark.parametrize("question, reply", [
        ("Place an offer of ₹900", "I can't place offers. You can make one from the product page in the Zatch app."),
        ("Place an offer of ₹900", "I cannot place an offer for you - open the RL Black shirt in the Zatch app and tap Make offer."),
        ("Cancel my blue shirt order", "It can still be cancelled - open the order in the Zatch app and tap Cancel."),
        ("Cancel my order", "Your order was placed on 22 March 2026 and is pending."),
        ("Did the seller send a counter-offer?", "The seller has sent a counter-offer of ₹340."),
        ("Where is my order?", "Your order has been cancelled."),
        ("mera order cancel kar do", "Main order cancel nahi kar sakta, aap Zatch app mein kar sakte hain."),
        ("मेरा ऑर्डर रद्द कर दो", "मैं ऑर्डर रद्द नहीं कर सकता, आप Zatch ऐप में कर सकते हैं।"),
        ("Where is my order?", "Let me check your orders."),
        ("Where is my order?", "Maine aapke orders check kar diye, dono pending hain."),
    ])
    def test_these_are_not_claims(self, question, reply):
        assert action_claim(reply, question) is None

    def test_a_returned_cancelled_status_is_a_report(self):
        t = turn("check_cancellation_eligibility", {"orderId": "ORD1", "status": "cancelled"})
        assert action_claim("That order has been cancelled already.", "Cancel my order", t) is None

    @pytest.mark.parametrize("message", ["Place an offer for me", "मेरा ऑर्डर रद्द कर दो"])
    def test_the_fixed_sentences_are_never_claims_themselves(self, message):
        """If the honest sentence read as a claim, the correction would
        flag its own output."""
        assert action_claim(READ_ONLY_REPLY, message) is None
        assert action_claim(READ_ONLY_REPLY_HI, message) is None

    def test_the_fixed_sentence_follows_the_script(self):
        assert claims.read_only_reply("मेरा ऑर्डर रद्द कर दो") == READ_ONLY_REPLY_HI
        assert claims.read_only_reply("mera order cancel kar do") == READ_ONLY_REPLY


class TestTheLoopNeverSendsAClaim:
    async def test_a_claim_is_asked_again_and_the_honest_answer_kept(self, scripted):
        honest = "I can't place offers - you can make one on the product page in the Zatch app."
        seen = scripted([
            tool_call("suggest_offer_amount"),
            text("Your offer of ₹900 on the RL Black shirt has been submitted."),
            text(honest),
        ], {"productId": "p", "suggestionAvailable": True, "suggestedOffer": 944})
        reply, messages = await converse("Place an offer of ₹900 on the RL Black shirt for me")
        assert reply == honest
        assert seen[-1]["last"] == f"[{claims.READ_ONLY_INSTRUCTION}]"
        assert "has been submitted" not in json.dumps(messages)

    async def test_claiming_twice_sends_the_fixed_sentence(self, scripted):
        scripted([
            tool_call("get_cart"),
            text("Added! The RL Black shirt is in your cart."),
            text("I've added the RL Black shirt to your cart."),
        ], {"items": [], "itemCount": 0})
        reply, messages = await converse("Add the RL Black shirt to my cart")
        # "Added!" alone is not a pattern; the second, first-person one is.
        # Either way nothing claiming an action may go out.
        assert action_claim(reply, "Add the RL Black shirt to my cart") is None

    async def test_two_explicit_claims_end_in_the_fixed_sentence(self, scripted):
        scripted([
            tool_call("get_order_history"),
            text("I have cancelled your blue shirt order."),
            text("I've cancelled it for you."),
        ], {"orders": [{"orderId": "ORD1774143822658262", "status": "pending"}]})
        reply, messages = await converse("Cancel my blue shirt order for me")
        assert reply == READ_ONLY_REPLY
        assert messages[-1]["content"] == READ_ONLY_REPLY

    async def test_in_hindi_the_fixed_sentence_is_hindi(self, scripted):
        scripted([
            tool_call("get_order_history"),
            text("मैंने आपका ऑर्डर रद्द कर दिया है।"),
            text("मैंने आपका ऑर्डर रद्द कर दिया।"),
        ], {"orders": [{"orderId": "ORD1774143822658262", "status": "pending"}]})
        reply, _ = await converse("मेरा ब्लू शर्ट वाला ऑर्डर रद्द कर दो")
        assert reply == READ_ONLY_REPLY_HI


# ── 3. Statuses no lookup returned ──────────────────────────────────

HISTORY = {"orders": [
    {"orderId": "ORD1774143822658262", "status": "pending", "statusText": "pending"},
    {"orderId": "ORD1781270783657447", "status": "delivered", "statusText": "delivered"},
]}
DETAIL = {"orderId": "ORD1774143822658262", "status": "pending", "statusText": "pending",
          "items": [{"name": "blue shirt"}], "pricing": {"total": 452}}


def detail_turn():
    return [
        {"role": "user", "content": "Show me the details of my blue shirt order"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "get_order_history", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "get_order_detail", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": json.dumps(HISTORY)},
        {"role": "tool", "tool_call_id": "b", "content": json.dumps(DETAIL)},
    ]


MEASURED = ("Your recent order ORD1774143822658262 (ending 8262) was for a blue shirt. "
            "Total ₹452. The order is marked as delivered.")


class TestStatusesAreJudgedPerOrder:
    def test_the_measured_reply_is_caught(self):
        """The turn DID return "delivered" - for another order. A check
        over all statuses would have passed this."""
        assert unsupported_statuses(MEASURED, detail_turn()) == [("ORD1774143822658262", "delivered")]

    @pytest.mark.parametrize("reply", [
        "Your blue shirt order (ending 8262) is pending.",
        "Your blue shirt order (ending 8262) hasn't been delivered yet.",
        "Order ending 8262 is pending; order ending 7447 was delivered.",
        "Ending 8262 – pending. Ending 7447 – delivered.",
    ])
    def test_correct_statuses_pass(self, reply):
        assert unsupported_statuses(reply, detail_turn()) == []

    def test_a_stage_in_the_history_counts(self):
        t = turn("get_order_status", {"orderId": "ORD1", "status": "delivered",
                                       "statusHistory": [{"status": "confirmed"}, {"status": "shipped"}]})
        assert unsupported_statuses("Order ORD1 was shipped, then delivered.", t) == []

    def test_an_invented_stage_is_caught(self):
        t = turn("get_order_status", {"orderId": "ORD1774143822658262", "status": "pending"})
        assert unsupported_statuses("Order ending 8262 is in transit.", t) == [("ORD1774143822658262", "in transit")]

    def test_no_order_lookup_no_judgement(self):
        t = turn("get_cart", {"items": []})
        assert unsupported_statuses("It was delivered.", t) == []

    def test_the_last_resort_drops_only_the_wrong_sentence(self):
        cleaned = claims.drop_unsupported_statuses(MEASURED, detail_turn())
        assert "delivered" not in cleaned
        assert "Total ₹452." in cleaned


class TestDetailsCarryTheStatus:
    def test_the_trimmer_adds_status_text(self):
        out = _with_status_text({"orderId": "ORD1", "status": "out_for_delivery"})
        assert out["statusText"] == "out for delivery"

    def test_nothing_is_invented_when_there_is_no_status(self):
        assert _with_status_text({"orderId": "ORD1"}) == {"orderId": "ORD1"}

    async def test_the_repo_returns_it(self, db, real_order):
        from app.repos import orders_repo
        detail = await orders_repo.get_order_detail(str(real_order["buyerId"]), real_order["orderId"])
        assert detail["status"] == real_order["status"]


class TestTheLoopCorrectsAStatus:
    async def test_a_wrong_status_is_asked_again(self, scripted):
        right = "Your blue shirt order (ending 8262) is pending. Total ₹452."
        seen = scripted([tool_call("get_order_detail"), text(MEASURED), text(right)], DETAIL)
        reply, _ = await converse("Show me the details of my blue shirt order")
        assert reply == right
        assert "ending 8262: pending" in seen[-1]["last"]

    async def test_if_it_insists_the_sentence_is_removed(self, scripted):
        scripted([tool_call("get_order_detail"), text(MEASURED), text(MEASURED)], DETAIL)
        reply, messages = await converse("Show me the details of my blue shirt order")
        assert "delivered" not in reply
        assert "₹452" in reply
        assert messages[-1]["content"] == reply


# ── 4. Counters readable, secrets redacted ──────────────────────────

class TestTokenCountsStayReadable:
    @pytest.mark.parametrize("key", [
        "prompt_tokens", "cached_tokens", "completion_tokens", "reasoning_tokens",
        "max_response_tokens", "tokens_used", "token_count", "cachedTokens",
    ])
    def test_a_count_is_readable(self, key):
        assert not _is_sensitive_key(key, 2096)

    @pytest.mark.parametrize("key, value", [
        ("token", "eyJhbGciOiJIUzI1NiJ9.x.y"),
        ("access_token", "eyJ..."),
        ("refresh_tokens", 3),
        ("cached_tokens", "not-a-number"),
        ("token", 482913),
        ("verification_token", 482913),
        ("api_key", "sk-live-123"),
        ("password", "hunter2"),
        ("auth_token", 7),
        ("cached_tokens", True),
    ])
    def test_a_secret_is_still_redacted(self, key, value):
        assert _is_sensitive_key(key, value)

    def test_end_to_end_in_a_log_line(self):
        out = redact_sensitive_data(None, "info", {
            "event": "llm_call_served", "prompt_tokens": 5533, "cached_tokens": 4192,
            "usage": {"reasoning_tokens": 12, "access_token": "eyJ..."},
            "calls": [{"tokens_used": 40, "token": "abc"}],
        })
        assert out["cached_tokens"] == 4192
        assert out["prompt_tokens"] == 5533
        assert out["usage"] == {"reasoning_tokens": 12, "access_token": "***REDACTED***"}
        assert out["calls"] == [{"tokens_used": 40, "token": "***REDACTED***"}]

    def test_through_the_configured_logger(self):
        import structlog
        from app.config.logging import configure_logging

        buf = io.StringIO()
        with redirect_stdout(buf):
            configure_logging()
            structlog.get_logger().info("probe", cached_tokens=2096, token="secret-value")
        line = json.loads(buf.getvalue().strip().splitlines()[-1])
        assert line["cached_tokens"] == 2096
        assert line["token"] == "***REDACTED***"
