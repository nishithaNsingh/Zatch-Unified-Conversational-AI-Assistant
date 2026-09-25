"""
WHAT:
    Tests get_complete_the_look - products that go WITH one, not like it -
    and the co-purchase count that may rank them.

WHY:
    The one way this feature can go wrong quietly is by claiming more than
    the data holds: "frequently bought together" over a catalogue where
    131 of 134 orders carry one item. So the tests hold three lines:
    every suggestion is a real, active, in-stock listing of a pairing
    kind; a co-purchase count is only ever a count of two or more real
    shoppers; and a row without one is never described as bought by
    others.

FLOW:
    Repo tests run against the sandbox, finding their seed products
    dynamically like the rest of the suite. Trimmer tests are pure.
"""

import pytest

from app.agent.tool_executor import _product_cards, _trim_complete_the_look
from app.repos import orders_repo, products_repo
from app.repos.products_repo import COMPLEMENTS


async def _active_seed(db, sub_category):
    return await db["products"].find_one(
        {"subCategory": sub_category, "status": "active", "isSold": False},
        {"_id": 1, "name": 1, "category": 1, "subCategory": 1},
    )


class TestSuggestionsAreRealAndPaired:
    async def test_a_tee_gets_in_stock_items_of_pairing_kinds(self, db):
        seed = await _active_seed(db, "tshirts-polos")
        if seed is None:
            pytest.skip("no active t-shirt in the sandbox")
        result = await products_repo.get_complete_the_look(str(seed["_id"]), limit=4)

        assert result["kinds"] == COMPLEMENTS["tshirts-polos"]
        assert result["suggestions"], "menswear has jeans, shorts, caps and watches in stock"
        for s in result["suggestions"]:
            assert s["subCategory"] in COMPLEMENTS["tshirts-polos"]
            assert s["_id"] != seed["_id"]
            assert s["status"] == "active" and not s["isSold"]
            assert (s.get("totalStock") or 0) > 0

    async def test_kinds_are_taken_in_turn_not_all_one(self, db):
        seed = await _active_seed(db, "tshirts-polos")
        if seed is None:
            pytest.skip("no active t-shirt in the sandbox")
        result = await products_repo.get_complete_the_look(str(seed["_id"]), limit=4)
        kinds = [s["subCategory"] for s in result["suggestions"]]
        assert len(set(kinds)) >= min(2, len(kinds))

    async def test_menswear_never_pairs_into_womenswear(self, db):
        seed = await _active_seed(db, "casual-shirts")
        if seed is None:
            pytest.skip("no active casual shirt in the sandbox")
        result = await products_repo.get_complete_the_look(str(seed["_id"]), limit=6)
        assert all(s["category"] in ("Men", "men-accessories") for s in result["suggestions"])

    async def test_a_kind_with_no_rule_returns_nothing(self, db):
        seed = await db["products"].find_one({"subCategory": "refrigerators"}, {"_id": 1})
        if seed is None:
            pytest.skip("no refrigerator in the sandbox")
        result = await products_repo.get_complete_the_look(str(seed["_id"]))
        assert result["kinds"] == [] and result["suggestions"] == []
        assert result["seed"] is not None

    async def test_an_unknown_product_has_no_seed(self, db):
        result = await products_repo.get_complete_the_look("000000000000000000000000")
        assert result == {"seed": None, "kinds": [], "suggestions": []}


class TestCoBuyersAreCountsOnly:
    async def test_only_pairs_of_two_or_more_shoppers(self, db):
        # The product with the most distinct buyers is the likeliest to
        # have any pair at all.
        top = await db["orders"].aggregate([
            {"$unwind": "$items"},
            {"$group": {"_id": "$items.product", "b": {"$addToSet": "$buyerId"}}},
            {"$project": {"n": {"$size": "$b"}}},
            {"$sort": {"n": -1}}, {"$limit": 1},
        ]).to_list(1)
        if not top:
            pytest.skip("no orders in the sandbox")
        counts = await orders_repo.count_co_buyers(str(top[0]["_id"]))
        assert str(top[0]["_id"]) not in counts
        assert all(isinstance(n, int) and n >= orders_repo.MIN_CO_BUYERS for n in counts.values())

    async def test_a_bad_id_counts_nothing(self, db):
        assert await orders_repo.count_co_buyers("not-an-id") == {}


def _doc(i, name, sub, also=None, price=500):
    d = {"_id": f"{i:024d}", "name": name, "price": price, "discountedPrice": price,
         "subCategory": sub}
    if also:
        d["alsoBoughtBy"] = also
    return d


class TestWhatTheModelReads:
    RESULT = {
        "seed": {"name": "Anime Downshoulder Tees "},
        "kinds": ["jeans-trousers", "caps"],
        "suggestions": [
            _doc(1, "Baggy Blue Street", "jeans-trousers", also=2),
            _doc(2, "Zero Sleek Cap ", "caps"),
            _doc(3, "Zero Sleek Cap", "caps"),  # same line again
        ],
    }

    def test_rows_carry_kind_in_words_and_no_slug(self):
        out = _trim_complete_the_look(self.RESULT)
        assert out["forProduct"] == "Anime Downshoulder Tees"
        assert [r["kind"] for r in out["goesWith"]] == ["jeans and trousers", "caps"]
        assert "jeans-trousers" not in str(out)

    def test_only_a_real_co_purchase_is_carried(self):
        rows = _trim_complete_the_look(self.RESULT)["goesWith"]
        assert rows[0]["alsoBoughtBy"] == 2
        assert "alsoBoughtBy" not in rows[1]

    def test_identical_lines_collapse(self):
        assert len(_trim_complete_the_look(self.RESULT)["goesWith"]) == 2

    def test_empty_results_explain_themselves(self):
        no_rule = _trim_complete_the_look({"seed": {"name": "Fridge"}, "kinds": [], "suggestions": []})
        assert no_rule["goesWith"] == [] and "No pairing" in no_rule["note"]
        none_in_stock = _trim_complete_the_look({"seed": {"name": "x"}, "kinds": ["caps"], "suggestions": []})
        assert "in stock" in none_in_stock["note"]

    def test_unknown_product_is_an_error(self):
        assert _trim_complete_the_look({"seed": None})["error"]

    def test_suggestions_become_tappable_cards(self):
        cards = _product_cards("get_complete_the_look", _trim_complete_the_look(self.RESULT))
        assert [c["name"] for c in cards] == ["Baggy Blue Street", "Zero Sleek Cap"]
