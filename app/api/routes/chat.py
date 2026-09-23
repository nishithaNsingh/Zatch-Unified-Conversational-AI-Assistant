"""
WHAT:
    The two ways to hold a conversation. /chat returns the finished
    reply in one JSON response; /chat/stream sends the same reply as it
    is produced, over Server-Sent Events.

WHY BOTH:
    /chat is the contract Phase 12's mobile app was written against, and
    a streaming endpoint is a different shape of client code. Changing
    it would mean the integration has to land at the same moment as this
    does. So /chat is untouched and /chat/stream is added beside it -
    the mobile app adopts streaming when it is ready to, not when we
    are.

    They are NOT two implementations. Both call the same
    run_conversation; the only difference is that the streaming one
    passes a callback. There is no second orchestration loop to keep in
    step, which is the failure mode this shape exists to avoid.

WHY STREAMING AT ALL:
    A measured answer takes 8-10 seconds, and the handover notes advise
    talking over the silence. Most of that is spent on tool rounds that
    produce no readable text, so streaming TOKENS alone would not fill
    it - the first token cannot arrive until the lookups are done. What
    fills it is the status events: the assistant says what it is looking
    up while it looks it up, and then types the answer out.

MECHANISM:
    run_conversation runs as a task and pushes events into a queue; the
    response generator drains that queue into SSE frames. A queue rather
    than yielding directly because the callback is called from deep
    inside the loop, which knows nothing about HTTP.
"""

import asyncio
import json

import structlog
from fastapi import APIRouter, Depends, Path
from fastapi.responses import StreamingResponse

from app.agent.orchestrator import FALLBACK_GENERIC, run_conversation
from app.api.dependencies import rate_limited_user_id
from app.api.schemas import ChatRequest, ChatResponse, FeedbackRequest
from app.memory import feedback_store, session_store, usage_store

logger = structlog.get_logger()

router = APIRouter()

# Sentinel closing the queue. An object() rather than None, so a genuine
# event can never be mistaken for the end of the stream.
_STREAM_END = object()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user_id: str = Depends(rate_limited_user_id)):
    # user_id comes from the verified JWT, never the request body - and it
    # scopes the SESSION as well as the data. session_id alone is
    # client-supplied and guessable; see session_store's OWNERSHIP note.
    history = await session_store.get_session(user_id, request.session_id)
    # The mobile app calls THIS endpoint, not the streaming one, so the
    # product rows have to arrive in the response body rather than only
    # as an SSE event.
    products: list = []
    attribution: list = []
    bits: list = []
    reply, messages = await run_conversation(
        user_id, request.message, history=history,
        products_out=products, attribution_out=attribution,
        bits_out=bits,
    )
    await session_store.save_session(user_id, request.session_id, messages)
    await usage_store.record_question()
    return ChatResponse(
        reply=reply, session_id=request.session_id,
        products=products, bits=bits, attribution=attribution,
    )


@router.post("/chat/feedback", status_code=204)
async def chat_feedback(
    request: FeedbackRequest,
    user_id: str = Depends(rate_limited_user_id),
):
    """One thumbs-up or thumbs-down on one reply.

    WHY THIS IS THE ONLY REAL QUALITY SIGNAL. The eval suite scores
    questions we chose; grounding proves an answer traced to a lookup.
    Neither says whether a shopper was helped. A rating does, and it is
    the one number that improves as the assistant is used rather than as
    we test it.

    THE EXCHANGE IS CHECKED, NOT TRUSTED. The client sends the question
    and reply it is rating; this looks for both in that user's stored
    session and records whether they were found. Unverified feedback is
    still kept - a session expires in 24 hours while the app may still be
    showing the reply - but a reviewer can tell the difference between
    "the server saw this exchange" and "a client said so".

    204 EVEN WHEN THE STORE IS DOWN. The user tapped a thumb; an error
    toast teaches them not to bother again, and the rating is in the log
    line regardless. Storage failure is our problem, not theirs.
    """
    history = await session_store.get_session(user_id, request.session_id) or []
    verified = _exchange_in_history(history, request.question, request.reply)

    await feedback_store.record(
        user_id=user_id,
        session_id=request.session_id,
        rating=request.rating,
        question=request.question,
        reply=request.reply,
        verified=verified,
    )
    return None


def _normalise(text: str | None) -> str:
    r"""Text as it was MEANT, not as it was transported.

    Line endings are normalised because a client that renders "\n" and
    posts back "\r\n" has not changed the answer - measured on the
    first live test, where exactly that marked a genuine exchange
    unverified.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def _exchange_in_history(history: list, question: str | None, reply: str | None) -> bool:
    """Whether this user's own stored session contains what is being rated."""
    if not history or not reply:
        return False
    said = {
        _normalise(message.get("content"))
        for message in history
        if message.get("role") in ("user", "assistant")
    }
    if _normalise(reply) not in said:
        return False
    return not question or _normalise(question) in said


@router.delete("/chat/session/{session_id}", status_code=204)
async def clear_chat_session(
    session_id: str = Path(..., min_length=1, max_length=200),
    user_id: str = Depends(rate_limited_user_id),
):
    """Deletes one conversation from the server, for good.

    WHY IT EXISTS. History expires on its own, but the TTL is now 24
    hours (see session_store.SESSION_TIMEOUT_MINUTES) and that history
    holds real order ids, tracking numbers and delivery cities. "Clear
    my chat" should mean the data is gone, not that it stops being shown
    while it sits in Redis for the rest of the day.

    IT CAN ONLY EVER CLEAR THE CALLER'S OWN. The store is keyed by the
    VERIFIED user_id together with the session_id, so a session_id
    belonging to somebody else simply hashes to a key this user does not
    own - the same property that stops one user reading another's
    history. Nothing here needs to check ownership because nothing here
    can express somebody else's session.

    RATE LIMITED like every other route, per the note in
    api/dependencies.rate_limited_user_id: endpoints depend on that
    rather than on get_current_user_id so a new one cannot quietly skip
    the limiter. The cost is that a user who has exhausted their
    allowance cannot clear until the window rolls - an accepted
    annoyance against an endpoint that cannot be added without the
    limiter by accident.

    204 AND NOT A BODY. Deleting a session that was never there is not
    an error - the desired state is "this conversation does not exist",
    and it does not.
    """
    await session_store.clear_session(user_id, session_id)
    logger.info("chat_session_cleared", user_id=user_id, session_id=session_id)


def _frame(event: dict) -> str:
    """One SSE frame. The event name goes on its own line so a client can
    addEventListener per kind instead of switching on a field."""
    payload = {k: v for k, v in event.items() if k != "type"}
    return f"event: {event['type']}\ndata: {json.dumps(payload)}\n\n"


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest, user_id: str = Depends(rate_limited_user_id)
):
    """The same conversation, delivered as it happens.

    Events, in the order a client sees them:
      status    {"tool": "...", "label": "Looking up your orders"}
      token     {"text": "..."} - fragments of the answer
      products  {"products": [{"product_id": "...", "name": "...", ...}]}
      bits      {"bits": [{"bit_id": "...", "title": "..."}]} - same
                contract as products, for short videos
      attribution {"claims": [{"start": 12, "end": 18, "text": "₹1,199",
                   "kind": "price", "tool": "get_order_history"}]}
      done      {"reply": "...", "session_id": "..."}
      error     {"message": "..."} - a sentence, never a stack trace

    products ARRIVES BEFORE done, and at most once. It is sent only when
    the reply is a real answer - never under one of the fallbacks, where
    rows to tap would contradict the apology above them. A client that
    ignores the event renders exactly what it rendered before.

    done CARRIES THE WHOLE REPLY, not just a terminator. A client can
    render the tokens as they arrive and then replace what it drew with
    the authoritative text - which is what makes the fallback paths work
    unchanged. When the model never produced a token (a rate limit, a
    dead provider), those replies are still real sentences and still
    need to reach the user; they arrive in done rather than as tokens,
    and the client needs no special case for them.
    """
    history = await session_store.get_session(user_id, request.session_id)
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(event: dict) -> None:
        # Called synchronously from inside the orchestration loop, on
        # this same event loop - so put_nowait is both safe and correct.
        # An unbounded queue because the producer is the LLM's own token
        # rate: applying backpressure here would slow the answer down to
        # the speed of the slowest reader.
        queue.put_nowait(event)

    async def converse() -> None:
        try:
            reply, messages = await run_conversation(
                user_id, request.message, history=history, on_event=on_event
            )
            await session_store.save_session(user_id, request.session_id, messages)
            await usage_store.record_question()
            queue.put_nowait(
                {"type": "done", "reply": reply, "session_id": request.session_id}
            )
        except asyncio.CancelledError:
            # The client hung up. Nothing to report to nobody.
            raise
        except Exception:
            # run_conversation answers every provider failure in words,
            # so reaching here means something genuinely unexpected. The
            # user still gets a sentence rather than a severed stream.
            logger.exception("chat_stream_failed", user_id=user_id)
            queue.put_nowait({"type": "error", "message": FALLBACK_GENERIC})
        finally:
            queue.put_nowait(_STREAM_END)

    async def events():
        task = asyncio.create_task(converse())
        try:
            while True:
                event = await queue.get()
                if event is _STREAM_END:
                    break
                yield _frame(event)
        finally:
            # A disconnected client should not leave an LLM call and a
            # session write running behind it.
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nginx and several PaaS proxies buffer responses by default,
            # which would hold every frame until the answer finished -
            # turning a streaming endpoint back into a slow blocking one,
            # and only once it is deployed.
            "X-Accel-Buffering": "no",
        },
    )
