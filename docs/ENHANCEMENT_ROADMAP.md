# Zatch AI Chatbot Assistant — Enhancement Roadmap

> Every enhancement that would bring the Zatch assistant up to the standard of
> leading e-commerce assistants, with the value each adds, the effort, and
> where the project stands today.
> Prepared **19 September 2026**. Statuses updated **10 October 2026**.

**Effort key:** Tiny (hours) · Small (1–3 days) · Medium (1–2 weeks) · Large (weeks+)

**Status key:** ✅ Done · 🔨 Built but unverified · ⚠️ Built, but fails on the current model · ⛔ Blocked (needs someone outside this repo) · ⬜ Not started

**10 October 2026 re-test.** Since commit `c68f28b` the only chat model is NVIDIA
`nemotron-3.5-lightning-30b-a3b` (configured as `BACKUP_LLM_*`; Azure chat is no
longer configured and Groq/Gemini are excluded on real data). Every built feature
was re-asked 1–2 real questions through `run_conversation` on that model. Two
features that passed on Azure failed on it, and semantic search is down because the
Azure embedding key now returns HTTP 401 — see the notes in each row.

**Same day, after the fixes.** The failures were fixed mechanically rather than with
prompt sentences (`orchestrator._check_reply`, `grounding.py`;
`app/tests/test_nvidia_regressions.py`) and re-tested live with two real buyers:
order buttons read full and masked ids; replies in the wrong script, replies that
ignore the lookup, model self-disclosure, read-back tool notes, unsupported "other
shoppers paired" claims and bold are caught; a semantic-search outage is reported
honestly and no longer recorded as unmet demand; discounts round down.

**Implemented: 11 of 35** (9 done, 1 still unreliable on the current model, 1 partial).

**Open issues on NVIDIA, not yet fixed** (measured 10 October, after the fixes):

| Issue | Measured | Likely fix |
|---|---|---|
| In-scope questions refused as off-topic, on the first call, before any lookup | "Which is better, the pattu saree or the Fancy saree?" refused 2 of the 3 times it reached the model; "mera order kahan hai?" refused 2 of 5 | Ask once more, tools allowed, when a refusal came with no lookup — about one extra second on genuinely off-topic questions |
| Hinglish answered in English | 1 of 3 Hinglish answers that were not refused | Not checkable mechanically (same script); needs a model that follows the LANGUAGE rule |
| "Any offers?" answered with no lookup and an invented count ("12 products") | 1 of 3 | Re-ask when a reply states a number and no tool ran this turn |
| "Where is my order?" only sees the 5 newest orders | A buyer with 9 in-progress orders, none in the newest 5, was told none are in progress | Have `get_order_history` put in-progress orders first, or count them in the summary |

---

## Contents

1. [Top 5 by value for effort](#1-top-5-by-value-for-effort)
2. [Revenue — helping people buy](#2-revenue--helping-people-buy)
3. [Support — fewer tickets](#3-support--fewer-tickets)
4. [Experience and trust](#4-experience-and-trust)
5. [Business insight](#5-business-insight)
6. [Production essentials](#6-production-essentials)
7. [How this compares to the big firms](#7-how-this-compares-to-the-big-firms)
8. [Phased plan](#8-phased-plan)
9. [What matters more than any single feature](#9-what-matters-more-than-any-single-feature)

---

## 1. Top 5 by value for effort

| # | Enhancement | Why first |
|---|---|---|
| 1 | ~~**Faster replies**~~ ✅ | Done: reasoning effort set to "low", 12.9 s → 6.7 s on an order lookup (Azure). On NVIDIA single lookups take 3.6–6.5 s. |
| 2 | **Hindi / Hinglish** ⚠️ | Built and passed on Azure. On NVIDIA, after the 10 Oct fixes, Devanagari gives a correct Hindi answer 3 times in 5 (one English, one apology) and never the "my name is Nemotron" reply. Hinglish is still unreliable — see open issues. |
| 3 | **Hand-off to a human agent** ⛔ | An escape route whenever the bot can't help. Needs a support team and channel. |
| 4 | **Store policies and FAQ** ⛔ | Return, shipping, COD and bargaining rules answered from one approved source. Needs approved policy text. |
| 5 | **Tap-to-open cards in the app** ⛔ | Turns every product card into a path to a sale. Server side done; app team. |

---

## 2. Revenue — helping people buy

| Enhancement | Value | Effort | Status today |
|---|---|---|---|
| **Tap-to-open product and Bit cards** in the app | Chat becomes a sales channel | Small (app team) | ⛔ Blocked on the app team. Server side done (`6efc6f4`, `bb55fed`) and still emitting cards on NVIDIA — see [app-integration.md](app-integration.md) |
| **Product images on cards** | Shoppers buy what they can see | Tiny — set `PRODUCT_IMAGE_QUERY` | ⛔ Blocked: Bunny Optimizer is NOT enabled (verified — 6 images returned byte-identical with the resize query; one answer would ship 8.6 MB). Guard in `6175b41` |
| **Deals / discounted products** | "Any offers?" is a top-5 question | Small — one new tool | ✅ Done — `get_discounted_products` (`33fa303`). NVIDIA 4/5 (once answered with no lookup — see open issues). Discounts now round down: the ₹1,000 → ₹5 listing reads 99%, not 100% |
| **Best-coupon finder** for the cart | Directly lifts conversion | Medium | ⬜ Not started — coupon validity check exists, but it does not check `applicableProducts` |
| **Bargaining coach** — "offer ₹X, likely accepted" | Unique to Zatch; no big marketplace has it | Medium | ⬜ Not started — bargain tools exist |
| **Compare two products** | Standard on Amazon / Flipkart | Small | ✅ Done — `compare_products` (`759e1ad`). On NVIDIA every comparison that ran was correct, with no winner declared; it resolves the two names in separate rounds, so it always uses the synthesis turn. The saree comparison is often refused before it runs — see open issues |
| **Complete the look** — bought-together items | Bigger baskets | Medium | ✅ First version done — `get_complete_the_look` (`6d19596`). First real-model test, NVIDIA: 1 of 4 invented "what other shoppers have paired". Fixed 10 Oct: the result states `rowsBoughtByOtherShoppers: 0`, the claim is caught and re-asked, bold and read-back notes are stripped — with the final code 3 of 3 live replies clean, one claim caught and corrected on the way |
| **Size help** — "which size for a 32-inch waist?" | Fewer returns, a major cost in fashion | Medium — needs seller size charts | ⛔ Blocked — needs size charts from sellers |
| **Personal picks** from browsing and purchase history | Better recommendations | Medium–Large | ⬜ Not started — basic saved/liked version exists |
| **Search by photo** — "find something like this" | Standard in fashion shopping | Large | ⬜ Not started |
| **Live session and Bit reminders** — "your seller goes live at 7" | Uses Zatch's social features | Medium — needs app notifications | ⛔ Blocked — needs app notifications |

---

## 3. Support — fewer tickets

| Enhancement | Value | Effort | Status today |
|---|---|---|---|
| **Policy and FAQ answers** (returns, shipping, COD, bargaining rules) | Most support questions are about policy, not one order | Small–Medium | ⛔ Blocked — needs approved policy text. Per-product return policy only |
| **Refund / return status** — "has my refund been processed?" | Second most common support question | Medium — needs refund data | ⬜ Not started — no order in the database has a refunded or returned status yet |
| **Hand-off to a human agent** with the conversation attached | No dead ends | Medium | ⛔ Blocked — needs a support team and channel |
| **Report a problem** — damaged, wrong or missing item | Captures issues instantly with order context | Medium — first write action | ⬜ Not started |
| **Proactive delay notices** — "your order is running 5 days late" | Bot already detects late orders; tell the customer first | Medium | ⛔ Blocked — needs a push channel in the app. Late detection exists |
| **Safe actions with confirmation** — cancel, add to cart, apply coupon | Leading assistants *do* things | Large — write access, confirmation step, audit log | ⬜ Not started — read-only by design |

---

## 4. Experience and trust

| Enhancement | Value | Effort | Status today |
|---|---|---|---|
| **Faster replies** — fewer AI round trips, a faster model, instant answers for common questions | Long waits lose customers | Medium | ✅ Done (`fabda16`, Azure-only setting). On NVIDIA: single lookup 3.6–6.5 s, compare / complete-the-look 5–10 s, outliers 12.6 s and 20.2 s |
| **Hindi / Hinglish / regional languages** | Most Indian shoppers | Small–Medium — the model handles Hindi; needs testing | ⚠️ Built (`fabda16`). On NVIDIA it failed 0/2 for Devanagari. Fixed 10 Oct: a reply in the wrong script is asked again in the shopper's script; a reply that ignores the lookup (the "my name is Nemotron" case) is asked again, then replaced with an apology; self-disclosure lines are removed. Live after the fixes: 3 of 5 correct Hindi, 1 English with correct data, 1 apology. Hinglish still unreliable (refused 2 of 5, English 1 of 3). Hinglish off-topic refusal still works |
| **Voice input** | Common on mobile in India | Medium (app side) | ⛔ Blocked — app side |
| **👍 / 👎 on every reply** | The only real measure of answer quality | Small | ✅ Done — `POST /chat/feedback`, `scripts/read_feedback.py` (`94a5f8c`). No model dependency; tests pass, script reads production ratings |
| **Tap-to-choose order buttons** instead of typing "last 4 digits" | Removes the confusion seen in manager testing | Small | ✅ Done (`6d19596`). Failed 0 of 4 on NVIDIA because the model prints full order ids. Fixed 10 Oct: the matcher reads full, masked ("ORD****6819") and short ids, and each order carries a computed `inProgress` flag. Live: a three-order reply drew 3 buttons, and tapping one opened that order |
| **Remember preferences across chats** — size, budget, style | Personal feel | Medium | ⬜ Not started — within one conversation only |

---

## 5. Business insight

| Enhancement | Value | Effort | Status today |
|---|---|---|---|
| **Analytics dashboard** — ratings, unmet demand, questions answered | Proves ROI to management; shows what to build next | Small | ✅ First version done — `/insights` (`3770b62`). No model dependency; tests pass. Card taps and chat-to-purchase still need app-side events |
| **"Not found" demand report** — e.g. sleepwear searched, none in stock | Tells the business what to stock, from real demand | Small | ✅ Done — `scripts/unmet_demand.py` (`925149f`). Fixed 10 Oct: a semantic search that cannot run (embedding 401, index down) is no longer recorded as unmet demand, and the shopper is told search by description is temporarily unavailable — verified live, nothing recorded |
| **Catalogue quality report** — listings named "test" or "hi", missing materials, ₹5 prices | Bad listings make the bot look bad | Small | ✅ Done — `scripts/catalogue_quality.py` (`c61aa40`). Re-run 10 Oct: still 54 of 143 flagged |
| **Seller assistant** — sales, payouts, pending bargains | Sellers matter as much as buyers | Medium | ⬜ Not started — `seller_repo` built and tested, not wired to any tool |

---

## 6. Production essentials

Required before a real launch — not extras.

| Item | Status |
|---|---|
| Real login tokens (JWT) from the Zatch backend | ⛔ Blocked — backend team |
| Company Azure account — credit expired **25 September 2026** | ⛔ Blocked — Azure chat is no longer configured, and the embedding key returns HTTP 401, so semantic search is down (the assistant now says so honestly) |
| Backup AI provider (`BACKUP_LLM_*`) | 🔨 Partial — NVIDIA Nemotron configured (`c68f28b`), but it is now the *only* provider, so the chain is not redundant (`/health`: `at_risk`). Render's environment not verified |
| `DEMO_UI_ENABLED=false` in production | ⬜ Not started — `render.yaml` still sets `true` |
| MongoDB Atlas network access restricted | ⬜ Not started (not verifiable from the repo) |
| Monitoring and alerts on errors and cost | ⬜ Not started |
| Scheduled quality-suite runs | ⬜ Not started — manual today |
| Zatch's own embedding pipeline writing `text-embedding-3-small` vectors | ⛔ Blocked — Zatch team; see [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md) |

---

## 7. How this compares to the big firms

With this roadmap done, Zatch would match **roughly 80–90% of the features** of
leading shopping assistants (e.g. Amazon Rufus, Flipkart's assistant). It would
not match them on **scale, speed and data depth** — those come from years of
data and large teams, not from code.

### Where Zatch would match

| Area | Big-firm assistants | Zatch after roadmap |
|---|---|---|
| Order tracking, returns, policies | ✅ | ✅ |
| Search by meaning, recommendations | ✅ | ✅ |
| Product Q&A from listings | ✅ | ✅ (stricter grounding) |
| Compare, deals, coupons | ✅ | ✅ |
| Hindi / regional languages | ✅ | ✅ |
| Hand-off to a human | ✅ | ✅ |
| Tappable cards with images | ✅ | ✅ |

### Where they stay ahead

| Gap | Why they have it | Can Zatch close it? |
|---|---|---|
| **Speed** (1–2 s replies) | Their own model infrastructure, heavy caching | Partly — 3–5 s is realistic |
| **Personalisation** | Years of browsing and purchase data from millions of users | Only as Zatch's own data grows |
| **Product knowledge** | Millions of reviews and Q&As, clean structured specs | Needs better catalogue data from sellers |
| **Custom-trained models** | Trained on their own shopping data | Not needed at Zatch's scale |
| **Quality testing at scale** | Thousands of test cases, live A/B tests, dedicated teams | 48 cases today; a few hundred is realistic |
| **Reliability** | Multiple providers and regions, 24/7 on-call | A backup provider plus monitoring gets most of the way |
| **Actions** | Buy, cancel, return and reorder in chat | Possible — the largest engineering step |

### Where Zatch can lead

- **Bargaining assistant** — no big marketplace negotiates prices; nothing to compare against.
- **Live selling and Bits in chat** — "what's live now?", "show me the products in this video". Closer to TikTok Shop than Amazon.
- **Strict grounding** — every price and order traces to a database lookup, with mechanical checks against invented products.
- **Speed of change** — fixes reach users in a day.

> **The realistic goal:** not "as good as Amazon Rufus", but
> **a best-in-class assistant for a social-commerce marketplace.**

---

## 8. Phased plan

| Phase | Enhancements |
|---|---|
| **Before launch** | All [production essentials](#6-production-essentials) · product images · tap-to-open cards |
| **Month 1** | Faster replies · Hindi / Hinglish · policies and FAQ · 👍/👎 feedback · "not found" demand report · deals |
| **Next quarter** | Human hand-off · refund status · best-coupon finder · bargaining coach · analytics dashboard · catalogue quality report · tap-to-choose order buttons |
| **Later** | Safe actions with confirmation · size help · search by photo · personalisation · seller assistant · voice · live reminders |

---

## 9. What matters more than any single feature

1. **Catalogue data quality.** The assistant can only be as good as the listings:
   real materials, size charts, and no listings named "test" or "hi".
2. **Real usage data.** Once the assistant is inside the app, what customers
   actually ask shows which of these features to build first.
