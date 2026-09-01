# Zatch Assistant - Demo Run Sheet

Every answer below was produced by an actual run against the demo
database. Ask the questions in this order; the sequence builds.

**Use the same `session_id` throughout** - several questions depend
on the ones before them.

## Before you start

Run these in order. Each prevents a specific way the demo breaks.

```
uv run python scripts/inspect_data.py     # names the buyer you will demo as
uv run python scripts/check_redis.py      # must say: OK connected
uv run python scripts/warm_cache.py       # must say: CACHE WARM
uv run uvicorn app.api.main:app           # NO --reload
```

Then open **http://127.0.0.1:8000/demo** and click an account.
There is no token to paste - the picker signs you in.

| Step | Prevents |
|---|---|
| `inspect_data` | not noticing WHOSE data is about to be on screen |
| `check_redis` | memory vanishing mid-demo, so follow-ups lose context |
| `warm_cache` | the first question being slow and full price |
| no `--reload` | a file save restarting the server mid-answer |

Warm the cache a minute or two before you begin, not the night
before - it expires after 2 hours idle.

> **This run was against `zatch` - REAL customer data.** The
> accounts in the picker are real Zatch buyers, and the orders
> quoted below are theirs. Fine on a laptop you control; never
> behind a URL. Set `DEMO_UI_ENABLED=false` before deploying.

---

## 1. It knows the user's own data

**Ask:** `where is my order`

**It answered:**

> I found several in-progress orders. Which one do you mean? Reply with the last 4 digits or the item name.
> 
> digital clock, kurtha — 1378 — confirmed
> 8x10 size frame — 2662 — confirmed
> black — 7278 — confirmed
> black — 1467 — confirmed
> Shirt — 3238 — confirmed
> Shirt — 7520 — out_for_delivery

**Why this lands:** Real orders, straight from the database. It lists them and asks which - it does not guess.

*Cost: 4212 prompt tokens*

---

## 2. ...and remembers the conversation

**Ask:** `tell me about the digital clock one`

**It answered:**

> Order ending 1378 — status: confirmed (Payment verified on 20 Jul 2026).
> 
> Items: digital clock — qty 1 — ₹10  
> kurtha (green, M) — qty 1 — ₹10
> 
> Pricing: subtotal ₹20, shipping ₹10, tax ₹3.05, total ₹30.
> 
> Delivery city: Surat, Gujarat. Expected delivery: 27 Jul 2026 — this order is overdue (36 days late).
> 
> Tracking: no courier or AWB assigned yet, so tracking is not available.  
> Invoice: not available.  
> Cancellation: currently allowed.
> 
> For live updates, tracking or to cancel, open Orders → Order details in the app (select the order ending 1378).

**Why this lands:** Refers back to an item from the previous answer. It resolves which order that is from context alone.

*Cost: 5802 prompt tokens*

---

## 3. It knows the catalogue

**Ask:** `what do you have in Home?`

**It answered:**

> Here are Home-category items I found — one line each:
> 
> Home Decor-Buddha — ₹8,000 (now ₹7,700)  
> crochet flower bouquet keychain — ₹250 (now ₹200)  
> 8x10 size frame — ₹800 (now ₹500)  
> crochet sunflower bag charm — ₹150 (now ₹130)  
> 5X7 photo frame — ₹600 (now ₹399)  
> 16X20 marriage Frame — ₹1,500 (now ₹1,000)  
> digital clock — ₹2,000 (now ₹10)  
> Flat Paper Raffia — ₹150 (now ₹120)
> 
> Want details (price breakdown, stock, seller) on any of these? Tell me the item name.

**Why this lands:** Filters on the real category value 'Home' - the values are read from the database and embedded in the prompt, so it cannot invent one.

*Cost: 7347 prompt tokens*

---

## 4. ...including what things are LIKE

**Ask:** `anything similar to the Home Decor-Buddha?`

**It answered:**

> Here are items similar to Home Decor-Buddha — one line each:
> 
> Flat Paper Raffia — ₹150 (now ₹120)  
> Clock — ₹5,000 (now ₹4,500)  
> 8x10 size frame — ₹800 (now ₹500)  
> Photo frame — ₹999 (now ₹600)  
> 30×60 inchBamboo Towels — ₹800 (now ₹600)  
> Photo frames — ₹600 (now ₹399)  
> 8x12 — ₹600 (now ₹399)  
> Bouquet and hampers — ₹1,000 (now ₹700)
> 
> Want details (price breakdown, stock, seller) on any of these?

**Why this lands:** Vector search over Zatch's own embeddings - already in the database, previously unused by the assistant.

*Cost: 8595 prompt tokens*

---

## 5. Bargaining, the signature feature

**Ask:** `can I bargain on the Home Decor-Buddha?`

**It answered:**

> Yes — bargaining is allowed on Home Decor-Buddha (listed ₹8,000, now ₹7,700).
> 
> Bargain rules returned:
> - Auto-accept discount: 5% (offers at or below this may be auto‑accepted)  
> - Maximum discount allowed: 30%
> 
> Want me to suggest a reasonable offer amount to try? I can calculate one for you.

**Why this lands:** Resolves the product by name, then reads that seller's own bargain settings.

*Cost: 9455 prompt tokens*

---

## 6. ...and money questions

**Ask:** `is 11 still valid?`

**It answered:**

> Do you mean 11% or ₹11?
> 
> Quick answers for both:
> 
> - 11% — yes, that’s allowed (maximum 30%). It’s above the auto‑accept limit (5%), so it won’t be auto‑accepted; the seller would need to accept or counter. 11% off ₹7,700 = ₹847 off → offer price ₹6,853.
> 
> - ₹11 — no, that’s not valid. The seller allows up to 30% off the current price (₹7,700), so the lowest allowed price would be ₹5,390 (₹7,700 − 30%). ₹11 is far below that.
> 
> Which did you mean? Want me to suggest a reasonable offer?

**Why this lands:** Read-only coupon check. It reports validity; it never applies anything.

*Cost: 4869 prompt tokens*

---

## 7. It refuses to act, by design

**Ask:** `can I cancel order ORD1784536807731378?`

**It answered:**

> Yes — this order (ending 1378) can be cancelled. The system shows canCancel: true and the order is currently confirmed (payment verified 20 Jul 2026). 
> 
> Note: there’s no courier or AWB assigned yet and no invoice available. To cancel, open Orders → tap the order ending 1378 → use the Cancel option in the Order details screen. I can’t cancel it for you.

**Why this lands:** Reports eligibility, then points at the Orders screen. The assistant has no write access to anything.

*Cost: 5039 prompt tokens*

---

## 8. THE CLOSER: it cannot leak another customer's data

**Ask:** `what is the status of order ORD1772289684977375`

**It answered:**

> I couldn’t find any order with ID ORD1772289684977375.
> 
> Would you like me to:
> - check your recent orders for it (I can look through them), or  
> - try a different order ID or the last 4 digits/item name?

**Why this lands:** Order ORD1772289684977375 is REAL and belongs to a different buyer. The query is scoped by the verified token, so the database never returns it - the assistant is not choosing to withhold it, it structurally cannot see it.

*Cost: 10334 prompt tokens*

---

## Token budget

- Total for the full demo: **55,653 prompt tokens**
- Average per question: **6,956**
- Groq free tier: **8,000 per minute**, shared across all users

At the free tier this demo needs about **7.0 minutes** of
elapsed time no matter how fast anyone types. On a paid key it runs
at conversation speed.
