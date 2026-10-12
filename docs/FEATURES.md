# Zatch Chatbot — Feature Guide

> The real capabilities of the Zatch assistant, each one tested live.
> Last full test: **12 October 2026**, on the real database.

| | |
|---|---|
| **Features** | **21** |
| ✅ Working | **5** |
| ⚠️ Working with limits | **15** |
| ❌ Not working right now | **1** |
| ⏸ Not tested | **0** |
| **Live demo** | https://zatch-unified-conversational-ai-assistant.onrender.com/demo (needs the shared access code — ask the project owner) |

Smaller details, guards and variations are folded into the feature they belong
to; where one of them has a real problem, it is shown inside that feature with
its own marker. The safety guards are listed together at the end — **one of
them, "never claims to act", still works only with limits** (see
[Safety and reliability](#safety-and-reliability)).

**AI model in use now.** Every reply is written by **NVIDIA Nemotron 3.5
Lightning (`nvidia/nemotron-3.5-lightning-30b-a3b`)** through NVIDIA's API. It
is the only model allowed to see real customer data: Azure OpenAI's credit
lapsed on 25 September 2026, and Groq and Gemini are excluded automatically
because their terms allow training on prompts. The Azure *embedding* key now
returns HTTP 401, which is why search by description is down. With one provider
there is no fallback if NVIDIA has an outage.

**How these were tested.** Real questions through the real pipeline, as four
real buyers (A: 8 orders, 2 in progress, items in cart · B: 22 orders, 9 in
progress · C: 67 orders, saved items, followers · D: 2 bargain offers); endpoints
through the real app; the demo page in a browser. Redis was off, so nothing
reached production statistics. Outputs are quoted as returned, with **order
IDs, customer names and cities masked**; long replies are cut with `…`. Times
are seconds for that answer; NVIDIA varies widely (median 2.9 s, outliers over
a minute).

| # | Feature | Status |
|---|---|---|
| 1 | Order tracking and support | ⚠️ |
| 2 | Product search | ⚠️ |
| 3 | Product questions | ⚠️ |
| 4 | Trending, picks and similar products | ⚠️ |
| 5 | Deals and offers | ⚠️ |
| 6 | Compare products | ✅ |
| 7 | Complete the look | ✅ |
| 8 | Bargaining assistant | ⚠️ |
| 9 | Live selling | ⚠️ |
| 10 | Bits (short videos) | ⚠️ |
| 11 | Cart and coupons | ✅ |
| 12 | Account overview | ⚠️ |
| 13 | Natural conversation | ⚠️ |
| 14 | Hindi and Hinglish | ⚠️ |
| 15 | Live progress while it works | ⚠️ |
| 16 | Tappable product and video cards | ⚠️ |
| 17 | Shows where each number came from | ✅ |
| 18 | Ratings, insights and unmet demand | ⚠️ |
| 19 | Catalogue quality report | ✅ |
| 20 | Demo page | ⚠️ |
| 21 | Seller assistant | ❌ |

---

## Orders and delivery

### 1. Order tracking and support

**⚠️ Working with limits**

**What it does.** Answers the most common support question — "where is my
order?" — from the buyer's real orders, and everything around it.
**Why it matters.** It is the question support teams spend the most time on, and
a buyer with an open order must never be told nothing is on its way.

**Includes**
- **Order list, in-progress first, with totals** — open orders can no longer hide behind newer finished ones; the true total is always stated.
- **Status, delivery estimate and lateness** — "197 days late" is computed by the server, which knows today's date.
- **Order details** — items, price breakdown, delivery city. *Fixed 12 Oct:* the details now carry the order's real status, and a reply that gives an order a status no lookup returned is corrected or the sentence removed.
- **Tap-to-choose buttons** — when a reply lists two or more orders, each becomes a button that sends "item, order ending 1234".
- **Courier tracking and invoice link.**
- **Cancellation check** — says whether an order can still be cancelled; never cancels.

**Try it:** `Where is my order?` · `How many orders have I placed?` · `Show me the details of my blue shirt order`

**Real output**
- Buyer B, 4.9 s: "I found 8 recent in‑progress orders… Order ending 7952: US Polo black shirt – status pending… 196 days late…"
- Buyer C, 2.2 s: "You have placed 67 orders in total. 8 of them are still in progress, and the rest are completed."
- Buyer A, after the status fix: "Blue shirt order (ORD…8262) - Status: pending - Item: blue shirt (gold / MEDIUM) - Price: ₹450 + ₹21 discount + ₹23 tax = ₹452 total…" (5 of 5 details answers gave the real status; before the fix one called this pending order "delivered").
- Buyer A: "Your US Polo black shirt order (ending 6819) is 196 days late and still shows no courier assigned."
- Demo page: two buttons, "…6819 US Polo black shirt · pending · late" and "…8262 blue shirt · pending · late"; tapping one opened that order.

**Limits**
- Replies often print the full order ID, though the prompt asks for the last four digits.
- Sometimes it answers about one order instead of listing them, so no buttons appear.
- "Track my most recent order" once skipped the tracking lookup and pointed to a button that was not drawn.
- One tapped-button answer on the demo page took over a minute.

---

## Finding products

### 2. Product search

**⚠️ Working with limits** — one part is down

**What it does.** Finds products by name (with typo tolerance), by filters
(category, price, colour, size, stock), and by description.

**Includes**
- **By name** — Atlas Search, one-letter typos tolerated.
- **By filters.**
- ❌ **By description** ("something cosy for winter") — **down**: the Azure embedding key returns HTTP 401. The shopper is told so honestly, and the search is not logged as unmet demand.

**Try it:** `Tell me about Lenin shirts` · `Show me men's shirts under ₹500` · `Show me something cosy to wear in winter`

**Real output**
- 1.5 s: "Lenin shirts – ₹599 (no discount)."
- 3.0 s: "Found 6 shirts under ₹500: Italian pants - ₹400… Strips Collection - ₹349… Men's Shorts - ₹399… Stone Article Shirt - ₹400…"
- 7.6 s: "I'm sorry - searching by description isn't available right now. Try typing a product name… or pick a category like Men, Women, or Home…"

**Limits**
- Search by description is down until a valid embedding key is configured.
- Filter answers are unreliable: the shirts answer lists trousers and shorts, and a "women's under ₹300" answer included items over ₹300.
- Every word must match: "budha statue" finds nothing although a "Home Decor-Buddha" listing exists.
- "Do you have a Titanium Nebula X9 smartwatch?" (not stocked) was refused as off-topic instead of "not found".

### 3. Product questions

**⚠️ Working with limits**

**What it does.** Answers questions about one product strictly from its listing —
never adds specs or materials from general knowledge.

**Includes** — product details · stock by size and colour · who sells it · reviews · seller trust details.

**Try it:** `Tell me about the 16X20 marriage Frame` · `Is the RL Black shirt available in XXL?` · `Show me the reviews for the Italian pants`

**Real output**
- 1.8 s: "It's a 16×20 marriage photo frame listed at ₹1,500, now discounted to ₹1,000. The listing does not specify material, colour or any further details…"
- 7.3 s: "Yes, it's in stock - 4 left." (XXL has 4)
- 8.5 s: "The Italian pants have 1 review rating it 5 stars. The comment says: "Good product"."
- 2.4 s: "[seller] (rating 0, 8 followers) sells the RL Black shirt (₹1249 discounted)."

**Limits**
- One details reply showed the internal product ID.
- A size the product is not made in is called "out of stock".
- "What do people say about the Italian pants?" was refused as off-topic 2 of 2 times.
- Seller trust details (products sold) are not reachable from chat: the lookup needs a seller ID no product lookup returns.

### 4. Trending, picks and similar products

**⚠️ Working with limits**

**What it does.** What is trending, personal picks from the buyer's saved and
liked items (trending for new buyers), and products similar to one they name.

**Try it:** `What's trending right now?` · `What should I buy?` · `Anything similar to the Anime Downshoulder Tees?`

**Real output**
- 3.8 s: "Here's what's trending right now: Lenin shirts – ₹599 (Men) | 16×20 marriage frame – ₹1,000 after discount (Home)…"
- Buyer A, 3.6 s: "BOXY FIT SHIRT – ₹1,249 (₹1,129 after discount)… Turtleneck shirts – ₹999 (₹899 after discount)…"
- 5.5 s: "Here are products similar to the Anime Downshoulder Tees: Korean Style Texture Shirts – ₹649… Puff Print T shirt – ₹749…"

**Limits.** "Something like the RL Black shirt but cheaper" returned alternatives
that cost more — "cheaper" is not applied.

### 5. Deals and offers

**⚠️ Working with limits**

**What it does.** The biggest current reductions, with the saving and percentage
worked out by the server and rounded **down** (99.5% shows as 99%, never 100%),
and the true number of discounted products.

**Try it:** `Any offers right now?` · `Show me discounts for men`

**Real output** — 10.5 s: "7 biggest reductions right now: black – ₹1000 → ₹5 (99% off) | digital clock – ₹2000 → ₹10 (99% off)…"; 14.8 s: "…141 products are discounted right now."

**Limits.** The top of the list is placeholder listings ("black", "test",
"hi") with implausible prices — a catalogue problem (feature 19), but the first
thing a shopper sees.

### 6. Compare products

**✅ Working**

**What it does.** Two to four products side by side. Says which is cheaper or
better discounted, never declares an overall winner the data cannot support.

**Try it:** `Compare the RL Black shirt and the Stone Article Shirt` · `Which is better, the pattu saree or the Fancy saree?`

**Real output**
- 3.8 s: "RL Black shirt ₹1,249 after discount (₹100 off, 7%); Stone Article Shirt ₹400 after discount (₹200 off, 33%). Stone Article Shirt is cheaper and has the bigger discount."
- 3.7 s: "…Both are ₹999, both are discounted to ₹699 (30% off) and both have a 7-day return policy."

### 7. Complete the look

**✅ Working**

**What it does.** What goes with a product — trousers or a cap for a tee, a
handbag for a saree — one per kind, in stock. "Other shoppers bought" is said
only where two or more really did.

**Try it:** `What goes with the Anime Downshoulder Tees?` · `Complete the look for the Lenin mix Sarees`

**Real output**
- 4.5 s: "The Anime Downshoulder Tees pair well with: Baggy Blue Street jeans – ₹1,449… Men's Shorts – ₹399… Zero Sleek Stylish Cotton Cap – ₹399…"
- 3.9 s: "Here's what goes well with the Lenin Mix Sarees: Handbags - ₹1,200 (₹1,100 discounted) | Little picks deluxe combo… | Plumeria Flower claw…"

---

## Bargaining

### 8. Bargaining assistant

**⚠️ Working with limits**

**What it does.** Zatch's signature feature, in chat: whether a product can be
bargained on, a sensible offer, and what happened to every offer the buyer made.

**Includes** — bargain eligibility · suggested offer · all my offers (with how long ago each lapsed) · one offer's status · counter-offers.

**Try it:** `Can I bargain on the RL Black shirt?` · `What should I offer for the RL Black shirt?` · `What happened to my offers?`

**Real output**
- 2.8 s: "Yes, bargaining is allowed on the RL Black shirt. The seller can auto‑accept a 5 % discount and will go up to a maximum 30 % discount."
- 3.1 s: "For the RL Black shirt listed at ₹1,349, you can offer ₹944 – that's the suggested bargain offer giving about a 30% discount."
- Buyer D, 4.4 s: "Yauvanya African Raw Shea Butter – offered ₹280 (original ₹380). Expired. Had a counter‑offer. | Mush Bamboo towel – offered ₹629 (original ₹800). Pending…"
- Buyer D, 3.0 s: "The seller has sent a counter‑offer… Your original offer of ₹280 has been raised to ₹340." (checked: the counter-offer is ₹340)

**Limits.** It tells buyers the seller's private auto-accept threshold — a
business decision nobody has made yet. It cannot place an offer; see "never
claims to act" under [Safety and reliability](#safety-and-reliability).

---

## Live selling and Bits

### 9. Live selling

**⚠️ Working with limits**

**What it does.** What is live right now, which products a session is featuring,
and a recap of a session.

**Try it:** `What's live right now?`

**Real output**
- 1.5 s: "No live sessions running right now. You can check back later or browse trending products instead." (correct: 0 of 140 sessions are live)
- Featured products, on an ended session: `{"title": "leather jacket", "status": "ended", "productCount": 1, "products": [{"name": "Leather jacket ", "price": 1499, "discountedPrice": 650}]}`

**Limits.** "What is featured" and "recap" need a session ID that only "what's
live now" provides; with nothing live they cannot be reached from chat.

### 10. Bits (short videos)

**⚠️ Working with limits**

**What it does.** Trending Bits and Bits by hashtag as tappable cards, and the
products tagged in a Bit.

**Try it:** `Show me trending Bits` · `Show me Bits about #fashion` · then `What products are in the Italian pants video?`

**Real output**
- 1.7 s: "Here are the trending Bits right now: Leather jacket (#jacket #fashion #style) | Lenin shirts… | Turtleneck shirts…" (5 Bit cards)
- 1.8 s: "Italian pants: ₹800 (₹400 after discount)."

**Limits.** The reply repeats the cards as a text list, against the prompt, once
with three identical "jeans" titles.

---

## Cart and account

### 11. Cart and coupons

**✅ Working**

**What it does.** The cart by item name with a total the server adds up, and
whether a coupon code is valid now. Never applies a coupon.

**Try it:** `What's in my cart?` · `Is the coupon SUMMER still valid?` · `Can I use the code NOTREAL99?`

**Real output**
- Buyer A, 1.9 s: "Your cart has 3 items: Jeans (blue, size 32) – ₹800 × 1 | A product no longer available (none/SMALL) – ₹100 × 1 | Formal Shirts (green, size XXL) – ₹400 × 3 | Subtotal: ₹2100 | Total: ₹2100"
- 2.0 s: "No, SUMMER is expired." · 1.3 s: "No - NOTREAL99 is not a valid coupon code."

**Note.** Every readable coupon in the database has expired, so a "valid"
answer could not be shown.

### 12. Account overview

**⚠️ Working with limits**

**What it does.** Unread notifications, saved items, default delivery address
(city and state only — never the street) and followers.

**Try it:** `Do I have any new notifications?` · `What have I saved?` · `How many followers do I have?`

**Real output** — Buyer C:
- "You have 3 new notifications, all about live sessions starting right now."
- "You've saved 2 products and 2 Bits. Product: LG washing Machine - ₹30,000 (₹27,500 after discount)…"
- "You have 1 follower."

**Limits.** "Saved items" says 2 products and names 1, without saying the other
no longer exists. The address answer adds an unrequested paragraph about orders.

---

## Conversation experience

### 13. Natural conversation

**⚠️ Working with limits**

**What it does.** Greets, says what it can do, remembers the conversation for 24
hours so "it" and "the second one" resolve, can be cleared for good, and reports
every failure as a plain sentence rather than an error page.

**Includes** — greeting and "what can you do" · follow-up questions · clear a conversation (deleted from the server, not just the screen) · plain-language failure messages.

**Try it:** `What can you do?` · `Tell me about Lenin shirts` then `Is it available in L?` · the demo's **Clear** button

**Real output**
- 1.0 s: "I can help with Zatch: products, orders, delivery, bargaining, live sessions, Bits, coupons, your cart, and your account."
- "Lenin shirts - ₹599… Available in beige, size MEDIUM, 45 in stock…" then 1.5 s: "No L in beige right now."
- Clear: `DELETE /chat/session/{id}` → `204`; the server logged `chat_session_cleared`.
- AI unreachable: "I can't reach my assistant service at the moment - please try again shortly." Rate-limited: "I'm handling a lot of requests right now - give me a few seconds and try that again?"

**Limits**
- A plain "hi" got an unrequested product list with emojis.
- After a conversation was cleared, one reply narrated the model's private reasoning and quoted its instructions.

### 14. Hindi and Hinglish

**⚠️ Working with limits**

**What it does.** Replies in the shopper's language and script. A reply in the
wrong script is asked again once; product names and prices stay as listed.

**Try it:** `मेरे कार्ट में क्या है?` · `kya RL Black shirt par bargain ho sakta hai?` · `mera order kahan hai?`

**Real output**
- 1.9 s: "तीन चीज़ें हैं: 1. jeans – blue, size 32 – ₹800 (1 piece) 2. a product no longer available – ₹100 3. Formal Shirts – green, size XXL – ₹400 each (3 pieces) कुल: ₹2,100."
- 3.1 s: "RL Black shirt par bargain ho sakta hai. Is product par maximum discount 30% hai…"

**Limits**
- "mera order kahan hai?" is usually answered in English; Hinglish and English share a script, so this cannot be checked mechanically.
- Hindi replies are noticeably weaker on this model: garbled words, and most of the false action claims measured on 12 October were in Hindi or Hinglish (see safety).

### 15. Live progress while it works

**⚠️ Working with limits**

**What it does.** Says what it is looking up while it looks ("Opening your
cart", "Finding today's offers"), then shows the time taken and which sources it
checked under each reply.

**Try it:** ask anything on the demo page and watch the status line.

**Real output**
- `POST /chat/stream "Show me trending products"`: events in order `status → token → products → attribution → done`.
- Under a reply: "5.0s to first word · 5.0s total · ✓ checked your orders · copy · 👍 👎"

**Limits.** NVIDIA's streaming stalls with tools, so the answer arrives in one
piece rather than word by word — first word and total time are the same.

### 16. Tappable product and video cards

**⚠️ Working with limits**

**What it does.** Products and Bits arrive beside the reply as tappable rows (id,
name, price). The model never writes a link, so a card cannot open the wrong
product; search results the reply rejected are not offered as cards.

**Try it:** `Show me trending products` on the demo page.

**Real output** — demo page: 8 cards, e.g. "Photo frame ₹600 ~~₹999~~ -40%";
tapping one shows "Opens this product in the Zatch app."

**Limits**
- Tapping does nothing yet: opening the product screen is the mobile app's job and is not built (`docs/app-integration.md`).
- Cards carry no photo: images stay off until the CDN can resize them (one catalogue photo is 8.5 MB).

### 17. Shows where each number came from

**✅ Working**

**What it does.** Every price and order number in a reply is traced to the
lookup that returned it; the demo page underlines it, and a click names the
source.

**Try it:** `Where is my order?` on the demo page, then click an underlined number.

**Real output** — clicking the underlined order ID showed "ORD…6819 — read from
your orders". The deals reply carried 14 traced figures.

---

## For the Zatch team

### 18. Ratings, insights and unmet demand

**⚠️ Working with limits**

**What it does.** Shoppers rate any reply 👍 or 👎; the rating is stored with
the exchange and checked against their own session. An internal `/insights`
page (behind its own code) shows questions answered, the share of 👍, the
exchanges behind every 👎, and what shoppers searched for that Zatch does not
stock.

**Includes** — 👍/👎 ratings · `/insights` dashboard · `scripts/read_feedback.py` · unmet-demand tracking and `scripts/unmet_demand.py`.

**Try it:** tap 👎 under a demo reply · `Do you have a Titanium Nebula X9 smartwatch?` · `uv run python scripts/unmet_demand.py`

**Real output**
- Demo page: the button shows "thanks"; the server logged `chat_feedback rating=down verified=true`.
- `/insights/data`: no code → `401`; with the code → `feedback={'up': 0, 'down': 1, 'total': 1, 'positive_rate': 0.0}`.
- Unmet demand, production report: "2 distinct things shoppers could not find: 6 titanium nebula x9 smartwatch · 1 voltas .beko"

**Limits.** False unmet-demand entries: a name search with a size attached
("rl black shirt small"), or an answer that does not repeat the product name,
records a product Zatch does stock as missing.

### 19. Catalogue quality report

**✅ Working**

**What it does.** Flags listings a shopper should never see — placeholder names,
impossible prices, empty descriptions, missing images — because the assistant
can only be as good as the catalogue.

**Try it:** `uv run python scripts/catalogue_quality.py`

**Real output** — "54 of 143 listings flagged: 39 clothing with no fabric named · 6 placeholder name · 4 no images · 3 sells for ₹10 · 2 99.5% off…"

### 20. Demo page

**⚠️ Working with limits**

**What it does.** A chat page at `/demo` in a phone-shaped frame, for showing the
assistant to people: an access-code gate, a picker of real accounts with
one-click sign-in, a panel showing the scale of the data, a greeting with four
starter questions, Clear / New, and the transcript kept per account across
reloads.

**Try it:** open the live demo link above.

**Real output**
- Without the code, only a code box; `/demo/accounts` → `401`.
- "16 accounts listed; busiest has 67 orders; 270 accounts never ordered."
- "143 products · 140 live sessions · 102 Bits · 19 coupons · 134 orders · 22 carts · 18 bargains · 10 reviews"
- After a reload and signing back in, all 3 questions and 4 replies were restored; signing in with a pasted token kept nothing.

**Limits.** Sign-in is deliberately password-less to real accounts, protected
only by the access code — `DEMO_UI_ENABLED=false` is required before any real
launch. The "unknown account" error lists ten real usernames.

### 21. Seller assistant

**❌ Not working right now** (not connected, by design)

**What it does.** Payout status, sales performance, coupon performance and
pending bargain count for a seller.
**Why it is off.** Built and tested (`tests/test_seller_repo.py`, 5 tests pass)
but not connected to chat, because the assistant is buyer-side only. Connecting
it safely means taking the seller ID from the login, never from the model — see
`app/repos/seller_repo.py`.

---

## Safety and reliability

Guards that sit under every feature, tested on 12 October 2026:

- ⚠️ **Never claims to act.** The assistant can only look things up. Before the fix, asked to place an offer it replied "Your offer of ₹900 on the RL Black shirt has been submitted… You'll be notified if it's accepted" — nothing was placed. *Fixed 12 Oct:* claims in English, Hinglish and Hindi are detected and re-asked once, then replaced with a fixed honest sentence; a reply to a command ("cancel my order") must also say it cannot act. Final live round — place an offer, cancel, add to cart, apply a coupon, refund; each in three languages, three times: **0 of 41 answered replies claimed an action** (4 were rate-limited). 12 ended in the fixed sentence ("I can only look things up, so I haven't placed, changed or cancelled anything. You can do that yourself in the Zatch app."), 7 of them in Hindi, where answers are therefore safe but less helpful. One Hindi reply contradicted itself ("बोली लगा सकती हूं… बोली नहीं लगा सकती"). Earlier rounds the same day leaked 6, 3 and 5 claims in new wordings before the checks caught up, so an unseen wording can still slip through.
- ✅ **Identity only from the verified login** — no tool can name whose data to fetch; no, tampered, expired or forged tokens all get `401`.
- ⚠️ **Customers see only their own data** — another buyer's real order ID returns "I couldn't find any order with that ID"; asked for "user 507f…'s orders" it returned only the caller's own, but labelled them as that user's.
- ✅ **Data never leaves unfiltered** — read-only database account (`[{'role': 'read', 'db': 'zatch'}]`), a field allowlist and an independent sanitiser; passwords, phones, emails, bank details and street addresses are never fetched.
- ⚠️ **Zatch topics only** — "France ki rajdhani kya hai?", Python code and a jailbreak were all refused; a refusal of a shopping question is asked once more with the tools ("which is better, the pattu saree or the Fancy saree" now answered). Some shopping phrasings without a shopping word are still refused.
- ⚠️ **Answers only from real data** — mechanical checks catch invented order IDs, statuses no lookup returned, the model naming itself, read-back instructions, unsupported "other shoppers bought" claims, and figures stated with no lookup. Across 87 live replies none invented an order ID, but a reply narrating the model's reasoning still got through once.
- ⚠️ **Real customer data never reaches a provider that trains on prompts** — Groq and Gemini are excluded automatically; that leaves NVIDIA as the only provider.
- ✅ **Per-user rate limit** — second request inside the allowance window → `429`, `Retry-After: 60`.
- ✅ **Secrets kept out of logs** — passwords, keys, phones and emails redacted. *Fixed 12 Oct:* token **counts** now stay readable — a real call logged `cached_tokens: 4192` (it used to read `***REDACTED***`), while a `token` holding a string is still redacted.
- ✅ **Search-index integrity** — `scripts/check_embeddings.py`: "143 · matching 143 · wrong width 0 · OK".
- ✅ **Health and startup checks** — local and live `/health`: `"status":"ok"`; startup warns about the single provider, the expired Azure credit and the demo page on real data.
- ✅ **Keeps working without Redis**, and **prompt caching** works: `scripts/warm_cache.py` → "2096 of 2198 prefix tokens cached".
- ✅ **Automated tests** — 1032 pass. A scored quality suite of 54 questions runs on demand; on Nemotron its LLM judge misfires.

**Could not test:** the container build and CI (Docker and the GitHub CLI are not
installed on the test machine), the demo rehearsal script (it overwrites
`docs/demo-script.md`), the catalogue migration tools (they need write access or
the Azure embedding key), and `scripts/deploy_azure.sh` (creates billable
resources).

---

## How to add a new feature

1. **Build it.** For a new chat tool, follow `.claude/skills/add-a-tool` —
   seven files, in order.
2. **Decide where it belongs.** If a manager would call it a capability of its
   own, give it an entry in the right section with the next number; if it is a
   detail, a guard or a variation, add it as a bullet under its parent feature,
   or to [Safety and reliability](#safety-and-reliability). Use the same
   layout: status, what it does and why, 2–3 inputs to try, the real output,
   and limits. Update the status table and the counts at the top.
3. **Test it live** before writing the output. Run 2–3 real questions through
   the real pipeline as a real buyer, with Redis switched off (`REDIS_URL=`)
   so nothing lands in production stats. Paste what actually came back, with
   order IDs, names and cities masked. Never mark it ✅ if any try failed.
4. **Regenerate the PDF:**
   ```
   uv run --with markdown python scripts/build_features_pdf.py
   ```
   It writes `Zatch-Features.pdf` with a contents page and puts the demo
   access code on page 1, read from `.env`. The PDF is git-ignored — share it
   directly, never commit it.
5. **Commit** `docs/FEATURES.md` together with the feature itself.
