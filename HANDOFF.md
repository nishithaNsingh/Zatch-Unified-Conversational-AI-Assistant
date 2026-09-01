# Running the demo on another laptop

Three things go wrong on a fresh machine. They are all fixable in
minutes, but only if you know about them before the client is watching.

---

## 1. `.env` carries live credentials — send it separately

The zip must **not** be the way these travel. `.env` contains:

| Value | What it is |
|---|---|
| `MONGODB_URI` | login to the real Zatch customer database |
| `AZURE_OPENAI_API_KEY` | billable Azure key |
| `JWT_SECRET` | signs tokens that grant access to any customer's data |
| `DEMO_MONGODB_URI` | writable demo cluster |
| `REDIS_URL` | password to the Upstash session/rate-limit store |

Sent over WhatsApp, email or a file-share, those land in a third party's
systems permanently. Send the `.env` **contents** over something you
control and would be willing to rotate afterwards, or hand them over in
person.

If in doubt: rotate the Azure key and the demo cluster password after
the demo. Both take a minute.

---

## 2. MongoDB Atlas will refuse his laptop

Atlas only accepts connections from allow-listed IP addresses. His IP is
not on the list, so `inspect_data.py` will fail with something that
*reads* like an auth error:

```
bad auth : authentication failed
```

That message is misleading — the credential may be perfect. Add his IP:

**Atlas → Network Access → Add IP Address → Add Current IP Address**
(he must do this from his own machine, or you add the address he tells
you).

Do this **before** demo day. It is the single most likely thing to
derail the handover.

---

## 3. Redis is hosted, so it travels with the project

`REDIS_URL` points at an **Upstash** database over TLS
(`rediss://...giving-mouse-179550.upstash.io:6379`), not at anything
running on the original laptop. It works from any machine with outbound
internet, so there is nothing to install and nothing to start.

Two things depend on it, and both used to break silently without it:

- **conversation memory** — survives restarts and is shared across
  workers, so `--reload` and mid-demo restarts are no longer dangerous
- **the `/chat` rate limit** — counted in Redis, so the configured
  allowance is the real one no matter how many workers run

Verify it in one command before the demo:

```powershell
uv run python scripts/check_redis.py
```

It does a real write/read round trip. If it cannot connect, the app
still runs — it degrades to per-process state and logs
`redis_unavailable` — so this is a "fix before deploying", not a
"stop the demo".

**Why Upstash and not Redis Cloud.** Redis Cloud's free 30MB plan does
not allow TLS, and session history holds real order IDs, tracking
numbers and delivery cities. Upstash is TLS-only on its free tier, so
that data is encrypted in transit. If anyone moves this to another
provider, keep the `rediss://` scheme — `redis://` to a remote host
sends real customer data in the clear, and `check_redis.py` will say so.

---

## Setup on his machine

**Do not zip `.venv/`** — it is large and machine-specific. Also skip
`__pycache__/` and `.pytest_cache/`.

```powershell
# 1. Install uv (once)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. In the unzipped folder - recreates the environment from uv.lock
uv sync

# 3. Put .env in the folder root (sent separately - see section 1)

# 4. Verify, in this order
uv run python scripts/inspect_data.py     # data + connection
uv run python scripts/check_azure.py      # model + credit remaining
uv run pytest -q                          # 374 should pass
```

If `inspect_data.py` fails, it is almost certainly section 2.

---

## Running the demo

```powershell
uv run python scripts/warm_cache.py            # makes the first answer fast
uv run python scripts/generate_test_token.py --real-data   # copy the eyJ... line
uv run uvicorn app.api.main:app                # NO --reload
```

**Why `--real-data` is needed there.** That script mints a token for
whoever `db.orders.find_one({})` returns - against `zatch` that is an
arbitrary REAL customer, and everything you then demo is their purchase
history and delivery city. It now refuses unless you say so explicitly,
and prints the account it handed you:

```
database : zatch   << REAL CUSTOMER DATA
account  : Pavan
they have: 67 orders, delivering to Proddatur, Andhra Pradesh
```

Drop the flag by setting `MONGODB_DATABASE=zatch_demo`, where the buyers
are invented (`asha.demo`, Pune) and nothing real is on screen.

Then open **http://127.0.0.1:8000/demo**, paste the token, and click
**Start chatting**.

The page opens with six starter questions as tappable chips. They are
worth using rather than typing: every one leans on the signed-in user's
own records or a general listing, so none of them can miss because a
particular product is absent from the dataset. Under each answer it
shows the time to the first word and which sources it checked - that
line is the easiest way to make the "it reads real data" point without
saying it.

Questions to ask are in `docs/demo-script.md`, along with the answers
they produced on the last rehearsal.

---

## Rehearse once on his machine

```powershell
$env:PAUSE_SECONDS=0; uv run python scripts/rehearse_demo.py --real-data
```

~90 seconds, and it now drives the SAME streaming path the demo page
uses, so a pass means the thing the client will watch actually works.
Each question reports how many tokens streamed and which lookup ran.

**Run it once, and not twice in a row.** One rehearsal spends about
61,000 prompt tokens and Azure allows 100,000 a minute, so a second run
inside the same minute runs out part-way through and reports the last
questions as failures. That is the quota, not the code - the failure
line now says so. Wait a minute if you need to repeat it, and leave a
minute between the rehearsal and the demo itself.

It must end with:

```
All questions answered, and every answer actually mentioned what it was asked about.
```

If it does not, do not demo until it does — the failure message names
the question and the reason.

---

## Two things to say out loud during the demo

**The security question** returns *"I couldn't find any order with that
ID"*, which looks like a bug unless narrated:

> That order ID is real — it came from your database, and it belongs to a
> different customer. The assistant isn't declining to show it; the query
> is scoped by the login token, so the database never returned it.

**The prices look odd** — ₹10 products, ₹30 order totals. That is the
real staging data. Say so before anyone asks.

---

## Known limits, so he is not surprised

- Answers take **~8 seconds** for a single lookup and **~17 seconds**
  when the assistant needs two rounds ("where is my order and can I
  cancel it?"). The wait is no longer blank: `/demo` names the lookup
  in progress within about 5 seconds, then types the answer out word by
  word as the model writes it. The timing under each reply shows both
  numbers — time to first word, and total.

  You no longer have to talk over it, but the two-round questions are
  still worth narrating.
- Free-text semantic search **now works on real data** (migrated
  2026-09-01). *"Something cosy to wear in winter"* returns real
  products - it found a "Waffle Knit Oversized" shirt from the word
  *cosy*, which no keyword search would do. All 143 products are
  embedded with `azure:text-embedding-3-small:1536` and the Atlas vector
  index is READY at 1536 dims.

  Two caveats worth knowing before you lean on it:

  - **Vague queries are not deterministic.** On a broad phrase the model
    sometimes asks a clarifying question instead of searching. Measured
    across runs, *"show me something cosy to wear in winter"* searched
    and listed real products; *"I need a gift for a wedding"* came back
    asking about budget and recipient. Both are reasonable, but only the
    first is guaranteed - so `anything similar to X` is still the safer
    scripted moment.
  - **The catalogue limits the matches.** There is nothing genuinely
    wintry in 143 products, so "cosy" surfaces knitwear and shirts
    rather than coats. That is the data, not the search.
- The assistant is **read-only**. Asked to place an offer or add to cart,
  it points at the app. That is correct behaviour, not a limitation to
  apologise for.
- The Azure credit expires **2026-09-25** — see the runbook below.

---

## The product embeddings, and the one way they break

Free-text semantic search depends on every vector in `product_embeddings`
having been made by the SAME model that embeds the query. That is
enforced, not assumed: each document carries

    "embeddingModel": "azure:text-embedding-3-small:1536"

and `search_products_semantically` compares the stored tag to its own
before spending an embedding call. A mismatch RAISES rather than
searching, because cosine similarity across two vector spaces returns
numbers that look ranked and plausible and are noise.

**Migrated 2026-09-01.** All 143 products re-embedded at 1536 dims, the
Atlas `product_vector_index` dropped and rebuilt to match, verified
READY. Cost $0.0003.

### The risk that outlives the migration

Zatch's own change-stream pipeline re-embeds a product whenever it is
edited - and it uses the OLD 384-dim model and writes no tag. It has not
fired since the migration only because nothing has been edited (the
catalogue had no edits for five weeks beforehand).

**The first product anyone edits writes a 384-dim vector into a
1536-dim collection.** Atlas will reject it or the search will skip it,
and either way semantic search quietly degrades. Update that pipeline to
write 1536-dim vectors with the tag above, or expect this to break the
first time a seller changes a price.

### Redoing it

`scripts/reembed_catalogue.py` is the migration. It needs a credential
with `readWrite` on `zatch`; the app's own `chatbot-app-readonly` is
deliberately read-only and will refuse - that refusal is a feature, and
it stopped an irreversible `delete_many` during this work. Grant the
role for the migration, then take it away again.

---

## When the Azure credit runs out (2026-09-25)

While `MONGODB_DATABASE=zatch` (real customers), Groq and Gemini are
dropped from the provider chain automatically — their terms permit
training on prompts, and those prompts contain real orders. That leaves
Azure alone, so when its credit lapses every request answers *"I can't
reach my assistant service at the moment."*

**You will not be surprised by it.** The service now says so itself:

- at startup, a `llm_chain_preflight` log line — `warning` while the
  chain is one deep, `error` once the credit has lapsed
- `/health` reports `"llm": {"status": ..., "redundant": false}`;
  `unavailable` there degrades the overall status, which is what
  monitoring should alert on
- `uv run python scripts/check_azure.py` prints the days remaining and
  the fixes below

### The three fixes, best first

**1. Renew the credit.** Nothing else to change.

**2. Point `BACKUP_LLM_*` at another compliant provider.** This is a
`.env` change and a restart — no code change, which is the whole reason
those settings exist. Any OpenAI-compatible endpoint works (OpenAI's own
API, a second Azure resource, AWS Bedrock, Google Vertex):

```
BACKUP_LLM_BASE_URL=https://api.example.com/v1
BACKUP_LLM_API_KEY=...
BACKUP_LLM_MODEL=...
BACKUP_LLM_TRAINS_ON_PROMPTS=false
```

That last line is a claim about **someone else's contract**. Set it to
`false` only if their terms actually say submitted content is not used
for training. Leave it alone and the backup is treated exactly like Groq
— dropped the moment real customer data is configured, so it will not
help. Set it wrongly and real orders go somewhere that may learn from
them. Check the terms; it is a two-minute read and it is the whole
safety property.

Confirm it took effect — the chain should be two deep:

```bash
uv run python scripts/check_azure.py
```

**3. Demo only, same day: `MONGODB_DATABASE=zatch_demo`.** The filter is
keyed on the database, so pointing at the demo dataset re-enables Groq
and Gemini automatically and the assistant works again immediately. The
answers come from seeded data rather than real orders — fine for showing
the product, not for answering a real customer's question.
