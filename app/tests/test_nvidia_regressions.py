"""
WHAT:
    Pins the fixes for what the 10 October 2026 re-test found when the
    only chat model became NVIDIA Nemotron instead of Azure gpt-5-mini.

WHY ONE FILE:
    Each failure was invisible on the old model and measured on the new
    one, and each fix is mechanical rather than a prompt sentence - so a
    future model swap is exactly when these need to keep holding. Kept
    together so the next swap has one place to look:

      order buttons     the model writes full order ids, not "ending 1378"
      wrong script      a Devanagari question answered in English
      unrelated reply   get_cart found three items; reply: "my name is
                        Nemotron, made by NVIDIA"
      social proof      "what other shoppers have paired" with no
                        co-purchase data behind it, and **bold** text
      search outage     an embedding 401 reported as "Zatch doesn't have
                        that" and recorded as unmet demand
      deals rounding    99.5% off shown as "100% off"

FLOW:
    Pure unit tests, plus run_conversation driven by a scripted model.
    No database, no LLM.
"""

import json

import pytest

from app.agent import grounding, orchestrator, tool_executor
from app.agent.grounding import order_choices_offered
from app.agent.llm_client import Completion, ToolCall
from app.agent.orchestrator import FALLBACK_GENERIC, OUT_OF_SCOPE_REPLY
from app.agent.tool_executor import (
    SEARCH_UNAVAILABLE,
    _order_choices,
    _trim_comparison,
    _trim_complete_the_look,
    _trim_deals,
    _trim_order_history,
    execute_tool,
)
from app.memory import demand_store
from app.repos import products_repo

# ── Helpers ──────────────────────────────────────────────────────────

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


@pytest.fixture
def scripted(monkeypatch):
    """Installs a scripted model and a scripted tool. Returns the list of
    (tool_choice, last message) each model call received, so a test can
    see whether and how it was asked again."""

    def install(replies, tool_result):
        seen = []

        async def fake_llm(messages, user_id, tool_choice="auto", on_text=None):
            seen.append((tool_choice, messages[-1].get("content")))
            return replies[len(seen) - 1]

        async def fake_tool(name, args, user_id, collector=None):
            return json.dumps(tool_result)

        monkeypatch.setattr(orchestrator, "_complete_with_failover", fake_llm)
        monkeypatch.setattr(orchestrator, "execute_tool", fake_tool)
        return seen

    return install


async def converse(question):
    return await orchestrator.run_conversation("u1", question, history=list(SYSTEM))


CART = {
    "items": [
        {"name": "Jeans", "variant": {"color": "blue", "size": "32"}, "qty": 1, "price": 800},
        {"name": "Formal Shirts", "variant": {"color": "green"}, "qty": 3, "price": 400},
    ],
    "itemCount": 2, "subtotal": 2000, "discount": 0, "total": 2000,
}

NEMOTRON_IDENTITY = "मेरा नाम नेमोट्रॉन है। मुझे एनवीडिया के शोधकर्ताओं द्वारा बनाया गया है।"
HINDI_CART = "आपके कार्ट में 2 आइटम हैं: Jeans ₹800 और Formal Shirts ₹400 × 3. कुल ₹2,000."


def lookup_turn(tool_name, payload, question="q"):
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": tool_name, "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps(payload)},
    ]


# ── 1. Order buttons ─────────────────────────────────────────────────

def order(order_id, name, status="pending"):
    return {"orderId": order_id, "status": status,
            "items": [{"name": name, "qty": 1}], "total": 452}


CHOICES = _order_choices("get_order_history", {"orders": [
    order("ORD1774196577926819", "US Polo black shirt"),
    order("ORD1774143822658262", "blue shirt"),
]})


class TestOrderButtonsFindTheOrderHoweverItIsWritten:
    def test_full_order_ids_are_recognised(self):
        """The measured Nemotron reply, which drew no buttons before."""
        reply = (
            "- ORD1774196577926819 - US Polo black shirt, overdue by 194 days\n"
            "- ORD1774143822658262 - blue shirt, overdue by 195 days\n"
            "Which one would you like to check? Tap it below or reply with the item name."
        )
        assert [c["last4"] for c in order_choices_offered(reply, CHOICES)] == ["6819", "8262"]

    @pytest.mark.parametrize("first, second", [
        ("ORD****6819", "ORD****8262"),
        ("ORD…6819", "ORD…8262"),
        ("xxxx6819", "xxxx8262"),
        ("#6819", "#8262"),
        ("ORD177419…6819", "ORD177414…8262"),
        ("…7926819", "…3822658262"),
    ])
    def test_masked_and_partial_ids_are_recognised(self, first, second):
        reply = f"Which one - {first} or {second}?"
        assert len(order_choices_offered(reply, CHOICES)) == 2

    def test_the_short_form_still_works(self):
        assert len(order_choices_offered("ending 6819 or ending 8262?", CHOICES)) == 2

    def test_a_reply_that_contradicts_itself_still_offers_what_it_lists(self):
        """Measured: "no orders in progress", then two pending orders and
        "which one?". The list is what the shopper acts on."""
        reply = (
            "There are no orders currently in progress.\n"
            "You have two that are overdue:\n"
            "- ORD1774196577926819 - US Polo black shirt\n"
            "- ORD1774143822658262 - blue shirt\n"
            "Which one would you like to check?"
        )
        assert len(order_choices_offered(reply, CHOICES)) == 2

    def test_a_different_number_ending_differently_does_not_match(self):
        """5926819 is not the end of ...77926819, so it names nothing."""
        assert order_choices_offered("Orders 5926819 and 1158262?", CHOICES) == []

    def test_one_full_id_is_still_an_answer_not_a_question(self):
        reply = "Your order ORD1774196577926819 is pending."
        assert order_choices_offered(reply, CHOICES) == []

    def test_a_full_id_does_not_also_name_a_sibling_sharing_its_last_four(self):
        twins = _order_choices("get_order_history", {"orders": [
            order("ORD1111111116819", "a"), order("ORD2222222226819", "b"),
            order("ORD3333333338262", "c"),
        ]})
        reply = "ORD1111111116819 or ORD3333333338262?"
        assert [c["item"] for c in order_choices_offered(reply, twins)] == ["a", "c"]


class TestInProgressIsComputedNotInferred:
    def test_each_order_says_whether_it_is_in_progress(self):
        rows = _trim_order_history([
            order("ORD1", "a", status="pending"),
            order("ORD2", "b", status="out_for_delivery"),
            order("ORD3", "c", status="delivered"),
            order("ORD4", "d", status="cancelled"),
        ])
        assert [r["inProgress"] for r in rows] == [True, True, False, False]

    async def test_the_summary_counts_them(self, monkeypatch):
        async def history(user_id, limit=5):
            return [order("ORD1", "a"), order("ORD2", "b", status="delivered")]

        async def count(user_id):
            return 2

        async def in_progress(user_id):
            return 1

        monkeypatch.setitem(tool_executor.TOOL_REGISTRY, "get_order_history", (history, True))
        monkeypatch.setattr(tool_executor.orders_repo, "count_orders", count)
        monkeypatch.setattr(tool_executor.orders_repo, "count_in_progress", in_progress)
        result = json.loads(await execute_tool("get_order_history", {}, "u1"))
        assert result["inProgressShown"] == 1


# ── 2. Language and relevance ────────────────────────────────────────

class TestTheReplyMustUseTheLookup:
    def test_the_measured_nemotron_reply_is_caught(self):
        turn = lookup_turn("get_cart", CART)
        assert grounding.unrelated_to_findings(NEMOTRON_IDENTITY, turn)

    def test_an_english_identity_reply_is_caught(self):
        turn = lookup_turn("get_cart", CART)
        reply = "I'm a large language model trained by NVIDIA researchers."
        assert grounding.unrelated_to_findings(reply, turn)

    @pytest.mark.parametrize("reply", [
        HINDI_CART,
        "आपका कार्ट खाली है।",
        "Your cart has 2 items: Jeans and Formal Shirts.",
        "Jeans and Formal Shirts.",
        "Total 2000.",
        "हाँ।",
        "Which size did you mean?",
        # Presenting what the cards show is using the lookup.
        "Here is what I found.",
        "A few picks for you.",
    ])
    def test_real_answers_pass(self, reply):
        assert grounding.unrelated_to_findings(reply, lookup_turn("get_cart", CART)) is None

    def test_a_date_from_an_iso_string_counts(self):
        turn = lookup_turn("get_delivery_estimate", {"expectedDelivery": "2026-10-12T00:00:00"})
        assert grounding.unrelated_to_findings("It should arrive by 12 October.", turn) is None

    def test_a_script_it_cannot_read_is_left_alone(self):
        tamil = "உங்கள் வண்டியில் இரண்டு பொருட்கள் உள்ளன"
        assert grounding.unrelated_to_findings(tamil, lookup_turn("get_cart", CART)) is None


class TestTheReplyMustUseTheShoppersScript:
    def test_devanagari_answered_in_english_is_a_mismatch(self):
        assert grounding.script_mismatch("मेरे कार्ट में क्या है?", "Your cart has 3 items") == (
            "Hindi, written in Devanagari script"
        )

    def test_devanagari_answered_in_devanagari_is_fine(self):
        assert grounding.script_mismatch("मेरे कार्ट में क्या है?", HINDI_CART) is None

    def test_hinglish_and_english_are_not_judged(self):
        assert grounding.script_mismatch("mera order kahan hai?", "Your order is pending") is None
        assert grounding.script_mismatch("where is my order", "आपका ऑर्डर") is None


class TestTheLoopCorrectsThem:
    async def test_an_unrelated_reply_is_asked_again(self, scripted):
        seen = scripted([tool_call("get_cart"), text(NEMOTRON_IDENTITY), text(HINDI_CART)], CART)
        reply, messages = await converse("मेरे कार्ट में क्या है?")

        assert reply == HINDI_CART
        assert seen[-1][0] == "none", "the re-ask withholds the tools"
        assert seen[-1][1] == f"[{orchestrator.ANSWER_FROM_LOOKUP}]"
        contents = [m.get("content") for m in messages]
        assert NEMOTRON_IDENTITY not in contents
        assert f"[{orchestrator.ANSWER_FROM_LOOKUP}]" not in contents, (
            "the one-off instruction must not be stored in the history"
        )

    async def test_unrelated_twice_becomes_an_apology_not_the_nonsense(self, scripted):
        scripted([tool_call("get_cart"), text(NEMOTRON_IDENTITY), text(NEMOTRON_IDENTITY)], CART)
        reply, messages = await converse("मेरे कार्ट में क्या है?")
        assert reply == FALLBACK_GENERIC
        assert messages[-1]["content"] == FALLBACK_GENERIC

    async def test_an_empty_lookup_does_not_trigger_the_relevance_check(self, scripted):
        seen = scripted([tool_call("search_products_by_name"), text("I couldn't find that.")], [])
        reply, _ = await converse("do you have a flux capacitor")
        assert reply == "I couldn't find that."
        assert len(seen) == 2

    async def test_an_english_reply_to_devanagari_is_asked_again_in_hindi(self, scripted):
        english = "Your cart has 2 items: Jeans ₹800 and Formal Shirts ₹400."
        seen = scripted([tool_call("get_cart"), text(english), text(HINDI_CART)], CART)
        reply, messages = await converse("मेरे कार्ट में क्या है?")

        assert reply == HINDI_CART
        assert "Devanagari" in seen[-1][1]
        assert english not in [m.get("content") for m in messages]

    async def test_still_english_after_one_retry_keeps_the_first_answer(self, scripted):
        english = "Your cart has 2 items: Jeans ₹800 and Formal Shirts ₹400."
        seen = scripted([tool_call("get_cart"), text(english), text("Still English: Jeans ₹800.")], CART)
        reply, messages = await converse("मेरे कार्ट में क्या है?")
        assert reply == english
        assert messages[-1]["content"] == english
        assert len(seen) == 3, "one retry, not a loop"

    async def test_the_english_refusal_to_a_devanagari_question_stands(self, scripted):
        seen = scripted([text(OUT_OF_SCOPE_REPLY)], [])
        reply, _ = await converse("फ्रांस की राजधानी क्या है?")
        assert reply == OUT_OF_SCOPE_REPLY
        assert len(seen) == 1


class TestSelfTalkIsRemoved:
    """Measured inside otherwise-correct Nemotron replies: the model
    naming itself, quoting its own scope rule, and reading a tool's note
    back to the shopper."""

    LEAK = (
        "आपका कार्ट:\n- jeans (blue, size 32) – ₹800\nकुल: ₹2,100\n\n"
        "मैं नेमोट्रॉन हूँ, Zatch के लिए शॉपिंग असिस्टेंट। ... मुझे exactly कहना होगा: "
        "\"I'm built specifically for Zatch-related queries. Please ask me something about Zatch.\""
    )

    def test_the_identity_and_prompt_quote_go_and_the_data_stays(self):
        cleaned = grounding.drop_self_talk(self.LEAK, [], OUT_OF_SCOPE_REPLY)
        assert "नेमोट्रॉन" not in cleaned
        assert "built specifically" not in cleaned
        assert "₹800" in cleaned and "₹2,100" in cleaned

    def test_the_refusal_itself_is_untouched(self):
        assert grounding.drop_self_talk(OUT_OF_SCOPE_REPLY, [], OUT_OF_SCOPE_REPLY) == OUT_OF_SCOPE_REPLY

    def test_a_tools_note_read_back_is_removed(self):
        turn = lookup_turn("get_complete_the_look", _trim_complete_the_look({
            "seed": SEED, "kinds": ["handbags"], "suggestions": [suggestion("a", "Handbags")],
        }))
        reply = "Lenin Mix Sarees goes well with:\n- Handbags – ₹1,100\n\nSay they go well with it."
        cleaned = grounding.drop_self_talk(reply, turn, OUT_OF_SCOPE_REPLY)
        assert cleaned == "Lenin Mix Sarees goes well with:\n- Handbags – ₹1,100"

    def test_saying_what_an_instruction_asked_for_is_not_an_echo(self):
        """SEARCH_UNAVAILABLE tells the model to say search is not working;
        the reply that does is obeying, and must survive."""
        turn = lookup_turn("search_products_semantically", SEARCH_UNAVAILABLE)
        reply = "Searching by description isn't working right now. Try a product name instead."
        assert grounding.drop_self_talk(reply, turn, OUT_OF_SCOPE_REPLY) == reply

    def test_a_fact_from_a_note_is_kept(self):
        """Digits exempt a sentence: "of 67 orders" is the answer."""
        turn = lookup_turn("get_order_history", {
            "orders": [], "note": "Showing the 8 most recent of 67 orders.",
        })
        reply = "Showing the 8 most recent of 67 orders."
        assert grounding.drop_self_talk(reply, turn, OUT_OF_SCOPE_REPLY) == reply

    async def test_the_loop_sends_the_answer_without_the_leak(self, scripted):
        seen = scripted([tool_call("get_cart"), text(self.LEAK)], CART)
        reply, messages = await converse("मेरे कार्ट में क्या है?")
        assert "नेमोट्रॉन" not in reply and "₹800" in reply
        assert messages[-1]["content"] == reply
        assert len(seen) == 2, "removing lines costs no extra call"

    async def test_a_reply_that_is_only_self_talk_is_asked_again(self, scripted):
        seen = scripted([text("I'm Nemotron, a language model from NVIDIA."),
                         text("I'm the Zatch shopping assistant - ask me about orders.")], [])
        reply, _ = await converse("who are you?")
        assert reply == "I'm the Zatch shopping assistant - ask me about orders."
        assert seen[-1][1] == f"[{orchestrator.ABOUT_THE_ASSISTANT}]"


# ── 3. Complete the look ─────────────────────────────────────────────

SEED = {"_id": "s1", "name": "Lenin mix Sarees", "subCategory": "sarees"}


def suggestion(pid, name, also=None):
    row = {"_id": pid, "name": name, "price": 1200, "discountedPrice": 1100,
           "subCategory": "handbags"}
    if also:
        row["alsoBoughtBy"] = also
    return row


class TestTheToolResultSaysThereIsNoShopperData:
    def test_with_no_co_purchase_counts_it_says_so(self):
        out = _trim_complete_the_look({
            "seed": SEED, "kinds": ["handbags"],
            "suggestions": [suggestion("a", "Handbags"), suggestion("b", "Clutch")],
        })
        assert out["rowsBoughtByOtherShoppers"] == 0
        assert "do NOT say other shoppers" in out["note"]

    def test_with_a_co_purchase_count_it_permits_the_claim(self):
        out = _trim_complete_the_look({
            "seed": SEED, "kinds": ["handbags"],
            "suggestions": [suggestion("a", "Handbags", also=3)],
        })
        assert out["rowsBoughtByOtherShoppers"] == 1
        assert "with that number" in out["note"]


LOOK = {"forProduct": "Lenin mix Sarees", "goesWith": [
    {"product_id": "a", "name": "Handbags", "price": 1200, "discountedPrice": 1100, "kind": "handbags"},
], "rowsBoughtByOtherShoppers": 0}

CLAIMING = (
    "Based on what other shoppers have paired with the Lenin Mix Sarees, here are a few options:\n"
    "- **Handbags** - ₹1,100"
)


class TestSocialProofIsCaught:
    def test_the_measured_claim_is_found(self):
        turn = lookup_turn("get_complete_the_look", LOOK)
        assert grounding.unsupported_social_proof(CLAIMING, turn)

    @pytest.mark.parametrize("phrase", [
        "Customers who bought this also bought these.",
        "These are frequently bought together.",
        "A popular pairing is the handbag.",
        "Shoppers often pair it with a handbag.",
    ])
    def test_other_phrasings_are_found(self, phrase):
        assert grounding.unsupported_social_proof(phrase, lookup_turn("get_complete_the_look", LOOK))

    def test_a_real_count_licenses_the_claim(self):
        backed = {**LOOK, "goesWith": [{**LOOK["goesWith"][0], "alsoBoughtBy": 3}]}
        turn = lookup_turn("get_complete_the_look", backed)
        assert grounding.unsupported_social_proof("3 other shoppers bought the Handbags too.", turn) is None

    def test_other_tools_are_not_judged(self):
        turn = lookup_turn("get_product_reviews", {"reviewCount": 4})
        assert grounding.unsupported_social_proof("Shoppers who bought it rated it 4.", turn) is None

    def test_the_honest_wording_passes(self):
        turn = lookup_turn("get_complete_the_look", LOOK)
        assert grounding.unsupported_social_proof("Handbags - ₹1,100 - goes well with it.", turn) is None

    def test_the_last_resort_drops_the_sentence_and_keeps_the_rows(self):
        cleaned = grounding.drop_social_proof(grounding.plain_text(CLAIMING))
        assert "shoppers" not in cleaned
        assert "Handbags - ₹1,100" in cleaned


class TestBoldIsStripped:
    def test_plain_text(self):
        assert grounding.plain_text("**Handbags** ₹1,100") == "Handbags ₹1,100"

    async def test_the_loop_sends_and_stores_it_without_asterisks(self, scripted):
        scripted([tool_call("get_cart"), text("**Jeans** - ₹800")], CART)
        reply, messages = await converse("what is in my cart")
        assert reply == "Jeans - ₹800"
        assert messages[-1]["content"] == "Jeans - ₹800"


class TestTheLoopRemovesTheClaim:
    async def test_it_is_asked_again(self, scripted):
        honest = "These go well with it:\n- Handbags - ₹1,100"
        seen = scripted([tool_call("get_complete_the_look"), text(CLAIMING), text(honest)], LOOK)
        reply, _ = await converse("Complete the look for the Lenin mix Sarees")
        assert reply == honest
        assert seen[-1][1] == f"[{orchestrator.NO_SOCIAL_PROOF}]"

    async def test_if_it_insists_the_sentence_is_dropped(self, scripted):
        scripted([tool_call("get_complete_the_look"), text(CLAIMING), text(CLAIMING)], LOOK)
        reply, _ = await converse("Complete the look for the Lenin mix Sarees")
        assert "shoppers" not in reply
        assert "Handbags - ₹1,100" in reply


# ── 4. Semantic search outage ────────────────────────────────────────

class TestASearchThatCouldNotRunIsNotUnmetDemand:
    @pytest.fixture(autouse=True)
    def _no_redis(self, monkeypatch):
        async def none():
            return None
        monkeypatch.setattr(demand_store, "get_redis", none)
        demand_store.reset_for_tests()
        yield
        demand_store.reset_for_tests()

    async def test_an_embedding_failure_is_raised_not_returned_empty(self, monkeypatch):
        from app.agent.embeddings import EmbeddingUnavailable

        class Collection:
            async def find_one(self, *a, **k):
                return None  # no foreign tag: the catalogue matches

        class Db:
            def __getitem__(self, name):
                return Collection()

        async def fails(text):
            raise EmbeddingUnavailable("embedding endpoint returned HTTP 401")

        monkeypatch.setattr(products_repo, "get_database", lambda: Db())
        monkeypatch.setattr(products_repo, "embed_query", fails)
        with pytest.raises(products_repo.SemanticSearchUnavailable):
            await products_repo.search_products_semantically("something cosy")

    async def test_the_tool_says_unavailable_and_records_nothing(self, monkeypatch):
        async def unavailable(query, limit=5):
            raise products_repo.SemanticSearchUnavailable("401")

        monkeypatch.setitem(tool_executor.TOOL_REGISTRY, "search_products_semantically",
                            (unavailable, False))
        batches: list = []
        result = await execute_tool(
            "search_products_semantically", {"query": "sleepwear"}, "u1", collector=batches
        )

        assert json.loads(result) == SEARCH_UNAVAILABLE
        assert "doesn't have it" in SEARCH_UNAVAILABLE["instruction"]
        assert list(demand_store._local) == [], "a search that never ran is not demand"
        assert batches == []

    async def test_a_real_empty_result_is_still_recorded(self, monkeypatch):
        async def nothing(name, limit=5):
            return []

        monkeypatch.setitem(tool_executor.TOOL_REGISTRY, "search_products_by_name",
                            (nothing, False))
        await execute_tool("search_products_by_name", {"name": "sleepwear"}, "u1")
        assert [e["query"] for e in demand_store._local] == ["sleepwear"]

    def test_an_unavailable_search_is_not_a_finding(self):
        assert not orchestrator._found_something(json.dumps(SEARCH_UNAVAILABLE))


# ── 5. Deals rounding ────────────────────────────────────────────────

class TestDiscountsRoundDown:
    @pytest.mark.parametrize("price, discounted, percent", [
        (1000, 5, 99),       # 99.5 - was shown as "100% off"
        (2000, 10, 99),      # 99.5
        (1499, 650, 56),     # 56.6
        (1000, 700, 30),     # exactly 30, despite float error
        (100, 70, 30),
        (999, 699, 30),      # 30.03
    ])
    def test_deals(self, price, discounted, percent):
        [row] = _trim_deals([{"_id": "a" * 24, "name": "x", "price": price,
                              "discountedPrice": discounted}])
        assert row["discountPercent"] == percent

    def test_comparisons_use_the_same_rule(self):
        out = _trim_comparison({"compared": [
            {"productId": "a", "name": "A", "price": 1000, "discountedPrice": 5},
            {"productId": "b", "name": "B", "price": 1499, "discountedPrice": 650},
        ]})
        assert [r["discountPercent"] for r in out["compared"]] == [99, 56]

    def test_never_100_unless_it_is_free(self):
        [row] = _trim_deals([{"_id": "a" * 24, "name": "x", "price": 1000,
                              "discountedPrice": 1}])
        assert row["discountPercent"] == 99
