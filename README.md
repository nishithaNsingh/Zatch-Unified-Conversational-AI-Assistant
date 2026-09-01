# Zatch Unified Conversational AI Assistant

An in-app conversational assistant for **Zatch**, a social-commerce platform
combining e-commerce, live selling, short videos ("Bits") and price bargaining.

The mobile app sends a user message plus the user's existing Zatch JWT; this
service resolves it against the live MongoDB Atlas data and replies in natural
language — *"where's my order?"*, *"any blue jackets under ₹2000?"*,
*"can I bargain on this?"*

## Core guarantees

- **Read-only.** Enforced at the Atlas user role, not just in code. The
  assistant reports cancellation *eligibility*; it never cancels anything.
- **Identity comes only from the verified JWT.** `user_id` is not a parameter
  in any of the 34 tool schemas, so the model has no way to express a request
  for another user's data. It is injected server-side by `tool_executor.py`.
- **Three independent data-safety layers.** Read-only DB role → per-collection
  field allowlist used as the Mongo projection → an independent response
  sanitizer that re-strips anything not allowlisted.

## Layout

```
app/
├── agent/       LLM tool-calling loop, tool schemas, provider client
├── api/         FastAPI app, /chat + /health routes, request schemas
├── config/      Settings loader, structured logging with redaction
├── db/          Shared async MongoDB connection
├── memory/      Per-session conversation history (in-process)
├── repos/       Data access — one file per collection, allowlisted
├── security/    JWT verification, field allowlists, response sanitizer
└── tests/       Pytest suite (requires a live sandbox connection)
docs/            Implementation plan, requirements, schema reference
scripts/         Dev-only helpers, not part of the deployed app
```

### Deliberately not reachable from chat

Not everything in `repos/` is wired into a tool, and the gaps are
choices rather than oversights — each one is explained in the file that
owns it, so nobody has to guess:

| Code | Why it is not a tool |
|---|---|
| `seller_repo` (PDF §11) | The demo is buyer-side end to end. Four more schemas would be re-sent on every round for a capability no demo question reaches. The file documents exactly what wiring it up safely requires — chiefly that `seller_id` must never become a model parameter. |
| `categories_repo` | The assistant does need real category names; it gets them from the system prompt, which is cheaper than a tool round-trip. This backs `scripts/check_categories.py`. |

`app/tests/test_tool_registry.py` keeps the tool surface honest: a
schema with no implementation, an implementation with no schema, or any
schema that lets the model name *whose* data it wants all fail the
suite.

## Setup

```bash
uv sync
```

Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Purpose |
|---|---|---|
| `MONGODB_URI` | yes | Atlas connection string (read-only service account) |
| `LLM_API_KEY` | yes | Primary LLM provider (Groq) |
| `JWT_SECRET` | yes | Must match the main Zatch backend's signing secret |
| `GEMINI_API_KEY` | no | Fallback provider; app runs on Groq alone without it |
| `DEMO_UI_ENABLED` | no | Serves the demo chat page at `/demo`. Defaults to **true**; set false for public deployments |
| `BACKUP_LLM_*` | no | A second provider whose terms exclude training on prompts. The only thing that stops Azure being a single point of failure against real data — see HANDOFF.md |
| `REDIS_URL` | no | Shared conversation memory and rate-limit counts. Required for more than one worker — see below |

### Running more than one worker

`REDIS_URL` is optional, and the app starts and answers correctly
without it. Two pieces of state are then held **per process**, and both
are silently wrong the moment there is a second worker:

- conversation history — a follow-up landing on another worker reads as
  a new conversation, so "anything similar?" answers "similar to what?"
- the `/chat` rate limit — each worker grants the full allowance, so the
  real limit is `workers x CHAT_RATE_LIMIT_REQUESTS`

Both fall back deliberately rather than refusing to start: a cache
outage should degrade the assistant, not take it down. Startup logs
`redis_not_configured` (or `redis_unavailable`) with the consequence
spelled out. `scripts/check_redis.py` proves a configured URL actually
works.

## Running

```bash
uv run uvicorn app.api.main:app --reload
```

Interactive docs at http://127.0.0.1:8000/docs, and a chat surface for
demos at http://127.0.0.1:8000/demo

### Two endpoints, one conversation

| Endpoint | Shape | Use |
|---|---|---|
| `POST /chat` | JSON in, JSON out | The contract the mobile app was written against. Unchanged. |
| `POST /chat/stream` | JSON in, Server-Sent Events out | The same answer as it is produced. |

They are not two implementations — both call the same
`run_conversation`, and the only difference is that the streaming one
passes a callback. `/chat/stream` emits `status` events naming each
lookup as it runs, `token` events carrying the answer as the model
writes it, and a final `done` event with the authoritative reply.

Most of an answer's latency is tool rounds that produce no readable
text, so the `status` events matter more than the tokens: the wait is
filled by saying what is being looked up, rather than by a spinner.

Generate a token to try `/chat` while waiting on the real backend secret:

```bash
uv run python scripts/generate_test_token.py
```

Against `MONGODB_DATABASE=zatch` this refuses unless you pass
`--real-data`, because it mints a token for an arbitrary **real**
customer and everything you demo afterwards is that person's data. It
prints which account it handed you either way. Point at `zatch_demo` and
the buyers are invented.

## Tests

The suite runs against real sandbox data rather than fixtures, so it needs a
live `MONGODB_URI`.

```bash
uv run pytest -v
```

274 of the 375 tests need no database at all and run anywhere:

```bash
uv run pytest -m "not needs_db"
```

The `needs_db` marker is applied automatically by `conftest.py` to
anything requesting the `db` fixture — never written by hand, so it
cannot drift as tests are added. CI runs that subset plus a container
build; it cannot run the rest, because Atlas allow-lists IP addresses
and GitHub runners have none.

## Deployment

```bash
docker build -t zatch-assistant .
```

~270MB, unprivileged user, no `.env` or dev scripts inside. See
[docs/deployment.md](docs/deployment.md) for configuration, why
`WEB_CONCURRENCY` defaults to 1, how to read `/health`, and what the
mobile app needs.
