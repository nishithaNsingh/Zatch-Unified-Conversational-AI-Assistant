"""
WHAT:
    What gets recorded as "a shopper wanted this and we do not sell it",
    and - as much - what does not.

WHY BOTH DIRECTIONS MATTER:
    The report is only worth reading if a high count means a real gap. A
    successful search recorded as a miss inflates demand for things Zatch
    already stocks; a miss not recorded leaves the gap invisible, which is
    the whole problem this exists to fix.
"""

import pytest

from app.agent import tool_executor
from app.db import redis_client
from app.memory import demand_store


@pytest.fixture(autouse=True)
def _in_process():
    # Keep the real shared Redis out of the tests - see test_feedback.
    redis_client._client = None
    redis_client._checked = True
    demand_store.reset_for_tests()
    yield
    demand_store.reset_for_tests()


class TestNormalising:
    """Two spellings of one want are one line in the report."""

    def test_case_and_spacing_collapse(self):
        assert demand_store.normalise("  Sleep   Wear ") == "sleep wear"

    def test_plurals_are_left_alone(self):
        # "short"/"shorts" are not the same thing, so nothing is stemmed.
        assert demand_store.normalise("sleep wears") == "sleep wears"

    def test_a_very_long_query_is_capped(self):
        assert len(demand_store.normalise("x" * 500)) == demand_store.MAX_QUERY


class TestWhatIsRecorded:
    async def test_a_name_search_with_no_rows_is_demand(self, monkeypatch):
        async def nothing(**kwargs):
            return []
        monkeypatch.setitem(tool_executor.TOOL_REGISTRY,
                            "search_products_by_name", (nothing, False))
        await tool_executor.execute_tool("search_products_by_name", {"name": "nightwear"}, "u1")

        entries = await demand_store.recent()
        assert [e["query"] for e in entries] == ["nightwear"]
        assert entries[0]["reason"] == demand_store.EMPTY

    async def test_a_name_search_that_finds_something_is_not_demand(self, monkeypatch):
        async def found(**kwargs):
            return [{"_id": "a" * 24, "name": "Lenin shirts", "price": 599, "discountedPrice": 599}]
        monkeypatch.setitem(tool_executor.TOOL_REGISTRY,
                            "search_products_by_name", (found, False))
        await tool_executor.execute_tool("search_products_by_name", {"name": "Lenin shirts"}, "u1")
        assert await demand_store.recent() == []

    async def test_a_blank_query_is_not_demand(self, monkeypatch):
        async def nothing(**kwargs):
            return []
        monkeypatch.setitem(tool_executor.TOOL_REGISTRY,
                            "search_products_by_name", (nothing, False))
        await tool_executor.execute_tool("search_products_by_name", {"name": "   "}, "u1")
        assert await demand_store.recent() == []

    async def test_an_order_lookup_is_never_demand(self, monkeypatch):
        # Only searches carry a query; an empty order history is not a
        # gap in the catalogue.
        async def nothing(**kwargs):
            return []
        monkeypatch.setitem(tool_executor.TOOL_REGISTRY,
                            "get_order_history", (nothing, True))
        await tool_executor.execute_tool("get_order_history", {}, "u1")
        assert await demand_store.recent() == []

    async def test_the_query_travels_with_the_card_batch(self, monkeypatch):
        """_deliver needs it to record the rejected case."""
        async def found(**kwargs):
            return [{"_id": "a" * 24, "name": "Track Pants", "price": 450, "discountedPrice": 299}]
        monkeypatch.setitem(tool_executor.TOOL_REGISTRY,
                            "search_products_semantically", (found, False))
        batches: list = []
        await tool_executor.execute_tool(
            "search_products_semantically", {"query": "sleep wear"}, "u1", batches
        )
        assert batches[0]["query"] == "sleep wear"
        assert batches[0]["tool"] == "search_products_semantically"


class TestRanking:
    async def test_the_same_want_asked_twice_counts_twice(self):
        await demand_store.record("sleep wear", reason=demand_store.EMPTY, tool="t")
        await demand_store.record("Sleep Wear", reason=demand_store.REJECTED, tool="t")
        await demand_store.record("nightwear", reason=demand_store.EMPTY, tool="t")
        assert await demand_store.top() == [("sleep wear", 2), ("nightwear", 1)]

    async def test_nothing_recorded_is_an_empty_report_not_an_error(self):
        assert await demand_store.top() == []
        assert await demand_store.recent() == []
