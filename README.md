# Zatch Unified Conversational AI Assistant

An in-app conversational assistant for **Zatch**, a social-commerce platform
combining e-commerce, live selling, short videos ("Bits") and price bargaining.

The mobile app sends a user message plus the user's existing Zatch JWT; this
service resolves it against the live MongoDB Atlas data and replies in natural
language — *"where's my order?"*, *"any blue jackets under ₹2000?"*,
*"can I bargain on this?"*

**New to the project?** Start with
[docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md) — the
overview, architecture, features, deployment and remaining work in one
place.

## Core guarantees

- **Read-only.** Enforced at the Atlas user role, not just in code. The
  assistant reports cancellation *eligibility*; it never cancels anything.
- **Identity comes only from the verified JWT.** `user_id` is not a parameter
  in any of the 36 tool schemas, so the model has no way to express a request
  for another user's data. It is injected server-side by `tool_executor.py`.
- **Three independent data-safety layers.** Read-only DB role → per-collection
  field allowlist used as the Mongo projection → an independent response
  sanitizer that re-strips anything not allowlisted.
- **Zatch topics only, in two layers.** A system-prompt rule states the scope
  and quotes the exact refusal. A mechanical post-check then reads the
  finished reply and suppresses it on either of two signals: nothing vouches
  for any of it (no lookup, no mention of Zatch), or it asserts a proper noun
  no tool returned — which is what catches an off-topic answer *wearing* a
  domain word, like "Zatch doesn't sell those, but the capital of France is
  Paris". The prompt is the layer that can read a follow-up in context, and
  the layer a jailbreak argues with; the post-check has nothing to negotiate
  with.

  **Suppressing is narrower than detecting**, drawn that way by measurement:
  a finding inside a reply that rests on real tool results is recorded and
  left alone, because every such finding measured across ~24 live
  conversations was a correct answer flagged for its own formatting (`Men`,
  `Jul`, `Total`). Only a reply grounded in nothing is replaced — and the
  user's own words never count as grounding, or a jailbreak would vouch for
  itself by naming its subject. A refusal that contradicts a *successful*
  lookup is rejected outright and re-asked, since a search that returned a
  product proves the question was about the catalogue.

  Greetings, "what can you do", follow-ups carrying no Zatch words ("the
  second one"), and products whose names collide with people or places
  ("Lenin shirts") are deliberately still answered: over-refusal is the
  failure a user actually feels. See the SCOPE note in
  `agent/orchestrator.py` and `grounding.unexplained_answer`, pinned by
  `tests/test_scope.py` — including its remaining blind spot — and by eval
  cases in both directions.

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

### The vector collection checks itself

Free-text semantic search is only meaningful while every stored vector
came from the model that embeds the query, at the width the Atlas index
was built for. Zatch's own change-stream pipeline still re-embeds edited
products with the old 384-dim model and no tag, so that assumption can
be broken by a seller changing a price — and Atlas simply stops indexing
the affected product, which looks like nothing at all.

Three layers, because the write side and the read side fail differently:

| Layer | What it does |
|---|---|
| `embeddings.validate_dimensions` | No producer in this repo can emit a wrong-width vector — enforced inside `embed_texts`, so it is not a step anyone can forget |
| `products_repo.audit_embeddings` | Scans every stored vector and names the offending ids. Read-only: it reports, it never repairs |
| The search guard | Asks for any document that is *not* ours, rather than sampling one and hoping — a single poisoned document was previously found with probability 1/143 |

Reported at startup (`embedding_preflight`), through `/health`, on every
affected search, and by `scripts/check_embeddings.py`.

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
| `JWT_SECRET` | yes | Must match the main Zatch backend's signing secret |
| `AZURE_OPENAI_*` | on real data | Primary LLM provider — the only eligible one when `MONGODB_DATABASE=zatch` |
| `LLM_API_KEY` / `GEMINI_API_KEY` | no | Groq / Gemini fallbacks, used only against demo data (they may train on prompts) |
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

### Three endpoints, one conversation

| Endpoint | Shape | Use |
|---|---|---|
| `POST /chat` | JSON in, JSON out | The contract the mobile app was written against. Unchanged. |
| `POST /chat/stream` | JSON in, Server-Sent Events out | The same answer as it is produced. |
| `DELETE /chat/session/{id}` | 204, no body | Ends one conversation for good — removes it from the server, not just from the screen. |
| `POST /chat/feedback` | 204, no body | A 👍 or 👎 on one reply, stored with the exchange it refers to. `scripts/read_feedback.py` reads them back. |
| `GET /insights` | HTML | Internal dashboard: ratings, the exchanges behind each 👎, unmet demand, questions answered. Served ONLY when `INSIGHTS_ACCESS_CODE` is set. |

The `/demo` page keeps a **named account's** transcript across reloads
(mirrored to `localStorage` under that account's own key) and keeps
**nothing** for the pasted-token path, which has no identity behind it.
A saved transcript is pruned once it passes the server's own session TTL
— the page reads that number from `/demo/stats` rather than hardcoding
it — so the screen can never show a conversation the model has already
forgotten. Persisting real buyers' order history to disk is the cost;
sign in as a named account only on a machine you would trust with it.

Answers also carry `products` and `bits` — `(id, name)` rows the client
renders as tappable cards. The id travels beside the prose rather than
inside it, so the model never composes a link and a card cannot open the
wrong thing. Wiring those taps to real screens is one handler per type:
see [docs/app-integration.md](docs/app-integration.md).

Conversation history expires after **24 hours** of inactivity. That is
long enough that a shopper who puts their phone down after lunch is
still in the same conversation, and long enough that waiting it out is
not a delete — which is why the endpoint above exists, and why `/demo`
has a **Clear** button that calls it. The store is keyed by the verified
`user_id` joined to the session id, so a caller can only ever clear
their own.

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

560 of the 670 tests need no database at all and run anywhere:

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
