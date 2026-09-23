"""
WHAT:
    The thumbs-up / thumbs-down path end to end: the store, the endpoint,
    and what the endpoint is willing to vouch for.

WHY IT MATTERS:
    This is the only signal that says whether an answer HELPED. Everything
    else this project measures - eval scores, grounding, tool choice -
    measures whether the answer was defensible.

WHY THESE RUN WITHOUT A DATABASE:
    Feedback touches no Mongo. conftest pins the session store to the
    in-process backend, and Redis is absent in CI, which is also the
    degraded path worth testing: a rating must never 500 because a cache
    is missing.
"""

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.main import app
from app.db import redis_client
from app.memory import feedback_store, session_store
from app.security.auth import create_test_token

USER = "6a1111111111111111111111"
OTHER = "6a2222222222222222222222"
SESSION = "demo-feedback-1"

QUESTION = "where is my order"
REPLY = "Order ending 1378 is confirmed."

HISTORY = [
    {"role": "system", "content": "stub"},
    {"role": "user", "content": QUESTION},
    {"role": "assistant", "content": REPLY},
]


def _pin_in_process():
    """Keep Redis out of these tests.

    conftest pins it for every test, but session_store.reset_for_tests()
    calls redis_client.reset_for_tests(), which un-pins it - so a local
    .env with REDIS_URL would send test ratings to the real shared store.
    Measured the hard way: the first run of this file wrote 11 entries
    into live Redis.
    """
    redis_client._client = None
    redis_client._checked = True


@pytest_asyncio.fixture
async def client():
    session_store.reset_for_tests()
    feedback_store.reset_for_tests()
    _pin_in_process()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    session_store.reset_for_tests()
    feedback_store.reset_for_tests()
    _pin_in_process()


def auth(user_id=USER):
    return {"Authorization": f"Bearer {create_test_token(user_id)}"}


async def _rate(client, rating="up", **body):
    payload = {"session_id": SESSION, "rating": rating,
               "question": QUESTION, "reply": REPLY}
    payload.update(body)
    return await client.post("/chat/feedback", json=payload, headers=auth(body.pop("user", USER)))


class TestRecording:
    async def test_a_thumbs_up_is_stored_with_the_exchange(self, client):
        await session_store.save_session(USER, SESSION, HISTORY)
        assert (await _rate(client, "up")).status_code == 204

        entries = await feedback_store.recent()
        assert len(entries) == 1
        assert entries[0]["rating"] == "up"
        assert entries[0]["question"] == QUESTION
        assert entries[0]["reply"] == REPLY

    async def test_a_thumbs_down_is_stored_the_same_way(self, client):
        await session_store.save_session(USER, SESSION, HISTORY)
        assert (await _rate(client, "down")).status_code == 204
        assert (await feedback_store.recent())[0]["rating"] == "down"

    async def test_the_totals_count_both(self, client):
        await session_store.save_session(USER, SESSION, HISTORY)
        await _rate(client, "up")
        await _rate(client, "up")
        await _rate(client, "down")
        totals = await feedback_store.summary()
        assert (totals["up"], totals["down"], totals["total"]) == (2, 1, 3)
        assert totals["positive_rate"] == 66.7

    async def test_an_empty_store_has_no_verdict(self, client):
        # 0% positive would read as a verdict nothing has earned.
        assert (await feedback_store.summary())["positive_rate"] is None


class TestVerification:
    """The client sends the exchange; the server says whether it saw it."""

    async def test_an_exchange_from_the_session_is_verified(self, client):
        await session_store.save_session(USER, SESSION, HISTORY)
        await _rate(client, "up")
        assert (await feedback_store.recent())[0]["verified"] is True

    async def test_text_the_session_never_contained_is_not_verified(self, client):
        await session_store.save_session(USER, SESSION, HISTORY)
        await _rate(client, "down", reply="Something the assistant never said.")
        entry = (await feedback_store.recent())[0]
        assert entry["verified"] is False
        # Kept anyway: the rating is still a real user's opinion.
        assert entry["reply"] == "Something the assistant never said."

    async def test_an_expired_session_still_accepts_the_rating(self, client):
        # The app may still be showing a reply whose 24h session has gone.
        assert (await _rate(client, "up")).status_code == 204
        assert (await feedback_store.recent())[0]["verified"] is False

    async def test_another_users_session_is_not_vouched_for(self, client):
        # OTHER rates an exchange that belongs to USER's session. The store
        # is keyed by the verified user, so it hashes to a key OTHER does
        # not own - the same property that stops one user reading another's
        # history.
        await session_store.save_session(USER, SESSION, HISTORY)
        response = await client.post(
            "/chat/feedback",
            json={"session_id": SESSION, "rating": "up", "question": QUESTION, "reply": REPLY},
            headers=auth(OTHER),
        )
        assert response.status_code == 204
        entry = (await feedback_store.recent())[0]
        assert entry["verified"] is False
        assert entry["user_id"] == OTHER


class TestRefusals:
    async def test_an_unknown_rating_is_rejected(self, client):
        response = await client.post(
            "/chat/feedback",
            json={"session_id": SESSION, "rating": "sideways", "reply": REPLY},
            headers=auth(),
        )
        assert response.status_code == 422
        assert await feedback_store.recent() == []

    async def test_no_token_means_no_rating(self, client):
        response = await client.post(
            "/chat/feedback", json={"session_id": SESSION, "rating": "up", "reply": REPLY}
        )
        assert response.status_code in (401, 403)
        assert await feedback_store.recent() == []

    async def test_the_store_refuses_an_invalid_rating_directly(self, client):
        with pytest.raises(ValueError):
            await feedback_store.record(
                user_id=USER, session_id=SESSION, rating="meh",
                question=None, reply=None, verified=False,
            )


class TestLimits:
    async def test_long_text_is_clipped_rather_than_stored_whole(self, client):
        feedback_store.reset_for_tests()
        await feedback_store.record(
            user_id=USER, session_id=SESSION, rating="down",
            question="q" * 5000, reply="r" * 9000, verified=False,
        )
        entry = (await feedback_store.recent())[0]
        assert len(entry["question"]) == feedback_store.MAX_TEXT
        assert len(entry["reply"]) == feedback_store.MAX_TEXT

    async def test_the_request_body_is_capped_before_it_reaches_us(self, client):
        response = await client.post(
            "/chat/feedback",
            json={"session_id": SESSION, "rating": "up", "reply": "x" * 9000},
            headers=auth(),
        )
        assert response.status_code == 422

    async def test_a_missing_cache_does_not_fail_the_request(self, client):
        # Redis is absent in CI; this IS the degraded path.
        await session_store.save_session(USER, SESSION, HISTORY)
        assert (await _rate(client, "up")).status_code == 204
        assert len(await feedback_store.recent()) == 1

    async def test_line_endings_do_not_make_it_a_different_answer(self, client):
        # A client that renders \n and posts back \r\n has not changed the
        # reply. Found on the first live test, which marked a genuine
        # exchange unverified for exactly this.
        await session_store.save_session(
            USER, SESSION,
            [{"role": "user", "content": "any offers?"},
             {"role": "assistant", "content": "Line one\nLine two"}],
        )
        response = await client.post(
            "/chat/feedback",
            json={"session_id": SESSION, "rating": "up",
                  "question": "any offers?", "reply": "Line one\r\nLine two\r\n"},
            headers=auth(),
        )
        assert response.status_code == 204
        assert (await feedback_store.recent())[0]["verified"] is True
