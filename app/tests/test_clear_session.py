"""
WHAT:
    The two halves of ending a conversation on purpose: the 24-hour TTL
    that makes it necessary, and DELETE /chat/session that does it.

WHY THE TTL MAKES IT NECESSARY:
    History holds real order ids, courier tracking numbers and delivery
    cities. At 30 minutes an abandoned conversation was gone before
    anyone thought about it; at 24 hours it is not. Waiting out a TTL is
    not a delete, so there is now a way to actually delete.

WHY THESE RUN WITHOUT A DATABASE:
    The route touches no Mongo at all - it verifies a JWT, counts
    against the rate limiter, and removes a key from the session store.
    conftest pins that store to the in-process backend, so the whole
    file is hermetic and runs in CI, where most of this repo's endpoint
    tests cannot.
"""

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.main import app
from app.memory import session_store
from app.security.auth import create_test_token

USER = "6a1111111111111111111111"
OTHER = "6a2222222222222222222222"
SESSION = "demo-abc123"

HISTORY = [
    {"role": "system", "content": "stub"},
    {"role": "user", "content": "where is my order"},
    {"role": "assistant", "content": "Order ending 1378 is confirmed."},
]


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        yield ac


def headers(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_test_token(user_id=user_id)}"}


class TestTheTimeoutIsADay:
    def test_it_is_twenty_four_hours(self):
        assert session_store.SESSION_TIMEOUT_MINUTES == 24 * 60

    async def test_the_stored_session_outlives_the_old_half_hour(self):
        """The behaviour the number exists for: put the phone down after
        lunch and the conversation is still yours."""
        from datetime import datetime, timedelta, timezone

        await session_store.save_session(USER, SESSION, list(HISTORY))
        stored = session_store._sessions[f"{USER}:{SESSION}"]
        stored["last_active"] = datetime.now(timezone.utc) - timedelta(hours=6)

        assert await session_store.get_session(USER, SESSION) is not None

    async def test_it_still_expires_eventually(self):
        """A longer window is not an unbounded one."""
        from datetime import datetime, timedelta, timezone

        await session_store.save_session(USER, SESSION, list(HISTORY))
        stored = session_store._sessions[f"{USER}:{SESSION}"]
        stored["last_active"] = datetime.now(timezone.utc) - timedelta(hours=25)

        assert await session_store.get_session(USER, SESSION) is None


class TestClearingActuallyDeletes:
    async def test_the_session_is_gone_afterwards(self, client):
        await session_store.save_session(USER, SESSION, list(HISTORY))
        assert await session_store.get_session(USER, SESSION) is not None

        response = await client.delete(
            f"/chat/session/{SESSION}", headers=headers(USER)
        )

        assert response.status_code == 204
        assert await session_store.get_session(USER, SESSION) is None, (
            "the transcript is still on the server after being cleared"
        )

    async def test_clearing_something_that_was_never_there_is_fine(self, client):
        """The desired state is "this conversation does not exist", and
        it does not. Erroring would make the UI report a failure for an
        outcome it already has."""
        response = await client.delete(
            "/chat/session/never-existed", headers=headers(USER)
        )
        assert response.status_code == 204

    async def test_it_leaves_other_conversations_alone(self, client):
        await session_store.save_session(USER, SESSION, list(HISTORY))
        await session_store.save_session(USER, "demo-other", list(HISTORY))

        await client.delete(f"/chat/session/{SESSION}", headers=headers(USER))

        assert await session_store.get_session(USER, SESSION) is None
        assert await session_store.get_session(USER, "demo-other") is not None


class TestItCanOnlyClearYourOwn:
    """The property that makes an unguarded delete safe. Nothing in the
    route checks ownership because nothing in the route can EXPRESS
    somebody else's session: the key is the verified user_id joined to
    the session_id, so a borrowed id resolves to a key the caller does
    not own."""

    async def test_the_same_session_id_under_two_users_is_two_sessions(
        self, client
    ):
        await session_store.save_session(USER, SESSION, list(HISTORY))
        await session_store.save_session(OTHER, SESSION, list(HISTORY))

        response = await client.delete(
            f"/chat/session/{SESSION}", headers=headers(OTHER)
        )
        assert response.status_code == 204

        assert await session_store.get_session(OTHER, SESSION) is None
        assert await session_store.get_session(USER, SESSION) is not None, (
            "SECURITY: one user's clear removed another user's conversation"
        )

    async def test_an_unauthenticated_caller_cannot_clear(self, client):
        await session_store.save_session(USER, SESSION, list(HISTORY))

        response = await client.delete(f"/chat/session/{SESSION}")

        assert response.status_code == 401
        assert await session_store.get_session(USER, SESSION) is not None

    async def test_a_forged_token_cannot_clear(self, client):
        await session_store.save_session(USER, SESSION, list(HISTORY))

        response = await client.delete(
            f"/chat/session/{SESSION}",
            headers={"Authorization": "Bearer not.a.real.token"},
        )

        assert response.status_code == 401
        assert await session_store.get_session(USER, SESSION) is not None


class TestTheButtonIsWiredCorrectly:
    """The page source, the same way test_demo_ui_session.py checks the
    rest of this screen - there is no JS runtime in the suite."""

    @property
    def page(self):
        from app.api.routes import demo_ui

        return demo_ui.PAGE

    def test_the_button_exists(self):
        assert 'id="clearBtn"' in self.page
        assert 'onclick="clearChat()"' in self.page

    def test_it_calls_the_delete_endpoint(self):
        body = self.page.split("async function clearChat()")[1].split("\n}")[0]
        assert '"/chat/session/"' in body
        assert 'method: "DELETE"' in body

    def test_the_server_is_cleared_before_the_screen(self):
        """THE ORDERING IS THE CORRECTNESS. Wiping the transcript first
        would show an empty screen while the model still held every turn,
        so the next answer would use context the user believes they
        deleted."""
        body = self.page.split("async function clearChat()")[1].split("\n}")[0]
        fetch_at = body.index("fetch(")
        wipe_at = body.index("thread.messages = []")
        assert fetch_at < wipe_at, "the UI is cleared before the server confirms"

    def test_a_failed_clear_leaves_the_transcript_alone(self):
        body = self.page.split("async function clearChat()")[1].split("\n}")[0]
        catch = body.split("catch (e)")[1].split("}")[0]
        assert "return" in catch, "a failed clear falls through and wipes anyway"
        assert "thread.messages = []" not in catch

    def test_the_greeting_survives_a_clear(self):
        """It is rendered rather than stored, so emptying the messages
        cannot remove it - renderChat draws it every time."""
        body = self.page.split("async function clearChat()")[1].split("\n}")[0]
        assert "renderChat()" in body
