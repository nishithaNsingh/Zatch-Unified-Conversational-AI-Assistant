"""
Permanent test suite for products_repo.py. Covers PDF §4's sub-features
and the real edge cases discovered during Phase 4 development (case-
insensitive category matching, exact variant lookup, graceful fallback
for recommendations).
"""

from app.repos import products_repo


class TestSearchProducts:
    async def test_category_filter_returns_matches(self, real_product_with_variants):
        category = real_product_with_variants["category"]
        results = await products_repo.search_products(category=category, limit=5)
        assert len(results) > 0
        assert all(p["category"].lower() == category.lower() for p in results)

    async def test_no_matches_returns_empty_list_not_error(self, db):
        results = await products_repo.search_products(category="NoSuchCategoryXYZ")
        assert results == []


class TestGetVariantStock:
    async def test_real_variant_is_found(self, real_product_with_variants):
        variant = real_product_with_variants["variants"][0]
        result = await products_repo.get_variant_stock(
            str(real_product_with_variants["_id"]), variant["color"], variant["size"]
        )
        assert result["found"] is True
        assert result["stock"] == variant.get("stock", 0)

    async def test_fake_variant_returns_found_false(self, real_product_with_variants):
        result = await products_repo.get_variant_stock(
            str(real_product_with_variants["_id"]), "nonexistent-color", "nonexistent-size"
        )
        assert result["found"] is False


class TestGetProductDetail:
    async def test_returns_real_product(self, real_product_with_variants):
        result = await products_repo.get_product_detail(str(real_product_with_variants["_id"]))
        assert result["name"] == real_product_with_variants["name"]

    async def test_fake_product_returns_none(self, db):
        result = await products_repo.get_product_detail("000000000000000000000000")
        assert result is None


class TestGetRecommendations:
    async def test_unknown_user_falls_back_to_trending_not_empty(self, db):
        results = await products_repo.get_recommendations("000000000000000000000000")
        assert len(results) > 0, "Fallback to trending must never return empty for a bad user_id"


class TestGetSellerInfo:
    async def test_returns_seller_for_real_product(self, real_product_with_variants):
        result = await products_repo.get_seller_info(str(real_product_with_variants["_id"]))
        assert result is not None
        assert "businessName" in result

class TestCategoryCache:
    """The system prompt embeds the real category vocabulary and is rebuilt
    on every message, so these two distinct() calls sat on the critical
    path of every answer (~60ms). Cached per process instead."""

    async def test_the_second_call_does_not_query_again(self, monkeypatch):
        from app.repos import products_repo
        products_repo.reset_category_cache()
        calls = []

        class FakeCollection:
            async def distinct(self, field):
                calls.append(field)
                return ["Men", "Women"] if field == "category" else ["shorts"]

        monkeypatch.setattr(products_repo, "get_database", lambda: {"products": FakeCollection()})
        first = await products_repo.get_distinct_categories()
        second = await products_repo.get_distinct_categories()
        assert first == second == {"categories": ["Men", "Women"], "subCategories": ["shorts"]}
        assert calls == ["category", "subCategory"]  # queried once, not twice
        products_repo.reset_category_cache()

    async def test_resetting_makes_it_query_again(self, monkeypatch):
        from app.repos import products_repo
        products_repo.reset_category_cache()
        calls = []

        class FakeCollection:
            async def distinct(self, field):
                calls.append(field)
                return []

        monkeypatch.setattr(products_repo, "get_database", lambda: {"products": FakeCollection()})
        await products_repo.get_distinct_categories()
        products_repo.reset_category_cache()
        await products_repo.get_distinct_categories()
        assert len(calls) == 4
        products_repo.reset_category_cache()


class TestDiscountedProducts:
    """'Any offers?' had no answer before this tool existed."""

    async def test_every_result_is_genuinely_reduced(self, db):
        from app.repos import products_repo
        rows = await products_repo.get_discounted_products(limit=8)
        assert rows, "the live catalogue has discounted products"
        for row in rows:
            assert row["discountedPrice"] < row["price"]
            assert row.get("isSold") is not True

    async def test_the_deepest_reduction_comes_first(self, db):
        from app.repos import products_repo
        rows = await products_repo.get_discounted_products(limit=8)
        pct = [(r["price"] - r["discountedPrice"]) / r["price"] for r in rows]
        assert pct == sorted(pct, reverse=True)

    async def test_the_count_is_at_least_what_the_page_shows(self, db):
        from app.repos import products_repo
        rows = await products_repo.get_discounted_products(limit=5)
        assert await products_repo.count_discounted_products() >= len(rows)

    async def test_a_category_narrows_both_the_list_and_the_count(self, db):
        from app.repos import products_repo
        rows = await products_repo.get_discounted_products(category="Men", limit=8)
        assert all(r.get("category", "").lower() == "men" for r in rows)
        assert (await products_repo.count_discounted_products("Men")
                <= await products_repo.count_discounted_products())

    async def test_an_unknown_category_is_empty_not_an_error(self, db):
        from app.repos import products_repo
        assert await products_repo.get_discounted_products(category="Spacesuits") == []


class TestCompareProducts:
    """Side by side, from what the listings actually hold."""

    WATCH = "6a1da6a761d3ca2f1475229e"       # Obsidian Gold Smart watch
    OTHER_WATCH = "6a1e6e267a12b194e15b3d6e"  # Noise Colorfit Icon Buzz
    MISSING = "6a0000000000000000000000"

    async def test_two_real_products_come_back_with_their_fields(self, db):
        from app.repos import products_repo
        out = await products_repo.compare_products([self.WATCH, self.OTHER_WATCH])
        assert len(out["compared"]) == 2 and out["notFound"] == []
        for row in out["compared"]:
            assert row["name"] and isinstance(row["price"], (int, float))
            assert row["shipping"] is not None

    async def test_the_order_asked_for_is_the_order_returned(self, db):
        from app.repos import products_repo
        out = await products_repo.compare_products([self.OTHER_WATCH, self.WATCH])
        assert [r["productId"] for r in out["compared"]] == [self.OTHER_WATCH, self.WATCH]

    async def test_a_product_that_does_not_exist_is_reported(self, db):
        # Silently comparing one product reads as a working comparison of
        # the wrong pair.
        from app.repos import products_repo
        out = await products_repo.compare_products([self.WATCH, self.MISSING])
        assert [r["productId"] for r in out["compared"]] == [self.WATCH]
        assert out["notFound"] == [self.MISSING]

    async def test_one_product_is_not_a_comparison(self, db):
        from app.repos import products_repo
        out = await products_repo.compare_products([self.WATCH])
        assert out["compared"] == [] and out["error"]

    async def test_an_unparseable_id_does_not_break_the_query(self, db):
        from app.repos import products_repo
        out = await products_repo.compare_products([self.WATCH, "not-an-id"])
        assert [r["productId"] for r in out["compared"]] == [self.WATCH]
        assert out["notFound"] == ["not-an-id"]

    async def test_it_never_compares_more_than_the_cap(self, db):
        from app.repos import products_repo
        ids = [self.WATCH, self.OTHER_WATCH] * 4
        out = await products_repo.compare_products(ids)
        assert len(out["compared"]) <= products_repo.MAX_COMPARED

    async def test_placeholder_sizes_are_dropped(self, db):
        # This catalogue spells "no sizes" as a size called NONE.
        from app.repos import products_repo
        out = await products_repo.compare_products([self.WATCH, self.OTHER_WATCH])
        for row in out["compared"]:
            assert all(str(s).upper() != "NONE" for s in row["sizes"])
