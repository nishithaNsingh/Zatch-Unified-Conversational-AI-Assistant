"""
WHAT:
    The internal insights page: who can reach it, and whether the numbers
    it draws are the ones the stores hold.

WHY THE ACCESS TESTS COME FIRST:
    The page shows real customers' questions and the replies they rated
    unhelpful. Its protection is not "a code is checked" but "the routes
    do not exist without one", and a mounting rule is exactly the kind of
    thing a later refactor turns back on by accident.
"""

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.main import create_app
from app.config.settings import get_settings
from app.db import redis_client
from app.memory import demand_store, feedback_store, usage_store

CODE = "insights-test-code"


@pytest.fixture(autouse=True)
def _stores():
    redis_client._client = None
    redis_client._checked = True
    feedback_store.reset_for_tests()
    demand_store.reset_for_tests()
    usage_store.reset_for_tests()
    yield
    feedback_store.reset_for_tests()
    demand_store.reset_for_tests()
    usage_store.reset_for_tests()


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "insights_access_code", CODE, raising=False)
    transport = ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def unconfigured_client(monkeypatch):
    monkeypatch.setattr(get_settings(), "insights_access_code", None, raising=False)
    transport = ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestWhoCanReachIt:
    async def test_without_a_code_configured_the_page_does_not_exist(self, unconfigured_client):
        # Not "open without a gate" - absent. Forgetting to configure it
        # must fail closed.
        assert (await unconfigured_client.get("/insights")).status_code == 404
        assert (await unconfigured_client.get("/insights/data")).status_code == 404

    async def test_the_page_itself_carries_no_data(self, client):
        """An unauthorised visitor sees a code box and nothing else.

        Tested as the property that matters: the HTML is the same bytes
        whatever the stores hold, because everything is fetched with the
        code rather than rendered into the page.
        """
        before = (await client.get("/insights")).text
        await feedback_store.record(user_id="u", session_id="s", rating="down",
                                    question="MARKER-QUESTION", reply="MARKER-REPLY",
                                    verified=True)
        after = (await client.get("/insights")).text
        assert before == after
        assert "MARKER-QUESTION" not in after and "MARKER-REPLY" not in after
        assert "access code" in after

    async def test_data_needs_the_code(self, client):
        assert (await client.get("/insights/data")).status_code == 401
        assert (await client.get("/insights/data", headers={"X-Insights-Code": "wrong"})).status_code == 401

    async def test_the_right_code_opens_it(self, client):
        assert (await client.get("/insights/data", headers={"X-Insights-Code": CODE})).status_code == 200

    async def test_it_is_kept_out_of_the_public_api_docs(self, client):
        schema = (await client.get("/openapi.json")).json()
        assert not [p for p in schema["paths"] if p.startswith("/insights")]


class TestWhatItShows:
    async def _data(self, client):
        r = await client.get("/insights/data", headers={"X-Insights-Code": CODE})
        return r.json()

    async def test_it_reports_what_the_stores_hold(self, client):
        await usage_store.record_question()
        await usage_store.record_question()
        await feedback_store.record(user_id="u", session_id="s", rating="up",
                                    question="any offers?", reply="Here they are.", verified=True)
        await feedback_store.record(user_id="u", session_id="s", rating="down",
                                    question="sleep wear?", reply="Nothing found.", verified=True)
        await demand_store.record("sleep wear", reason=demand_store.REJECTED, tool="t")
        await demand_store.record("sleep wear", reason=demand_store.EMPTY, tool="t")
        await demand_store.record("yoga mat", reason=demand_store.EMPTY, tool="t")

        data = await self._data(client)
        assert data["usage"]["questions"] == 2
        assert data["usage"]["today"] == 2
        assert (data["feedback"]["up"], data["feedback"]["down"]) == (1, 1)
        assert data["feedback"]["positive_rate"] == 50.0
        assert [d["query"] for d in data["demand"]] == ["sleep wear", "yoga mat"]
        assert [d["count"] for d in data["demand"]] == [2, 1]

    async def test_the_thumbs_down_carries_its_exchange(self, client):
        await feedback_store.record(user_id="u", session_id="s", rating="down",
                                    question="sleep wear?", reply="Nothing found.", verified=True)
        downs = (await self._data(client))["recent_down"]
        assert len(downs) == 1
        assert downs[0]["question"] == "sleep wear?"
        assert downs[0]["reply"] == "Nothing found."

    async def test_thumbs_up_do_not_appear_in_the_review_list(self, client):
        await feedback_store.record(user_id="u", session_id="s", rating="up",
                                    question="q", reply="a", verified=True)
        data = await self._data(client)
        assert data["recent_down"] == []
        assert data["feedback"]["up"] == 1

    async def test_an_empty_store_is_an_empty_page_not_an_error(self, client):
        data = await self._data(client)
        assert data["feedback"]["total"] == 0
        assert data["feedback"]["positive_rate"] is None
        assert data["demand"] == [] and data["recent_down"] == []
        assert data["usage"]["questions"] == 0


class TestCounting:
    async def test_a_question_is_counted_once(self):
        await usage_store.record_question()
        totals = await usage_store.totals()
        assert totals["questions"] == 1
        assert totals["by_day"][-1]["questions"] == 1

    async def test_counting_never_raises(self, monkeypatch):
        async def broken():
            raise RuntimeError("redis is down")
        monkeypatch.setattr(usage_store, "get_redis", broken)
        await usage_store.record_question()  # must not raise
