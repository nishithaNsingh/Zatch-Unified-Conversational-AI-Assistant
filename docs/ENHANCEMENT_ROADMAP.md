# Zatch AI Chatbot Assistant — Enhancement Roadmap

> Every enhancement that would bring the Zatch assistant up to the standard of
> leading e-commerce assistants, with the value each adds, the effort, and
> where the project stands today.
> Prepared **19 September 2026**.

**Effort key:** Tiny (hours) · Small (1–3 days) · Medium (1–2 weeks) · Large (weeks+)

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
| 1 | ~~**Faster replies**~~ ✅ | Done: reasoning effort set to "low", 12.9 s → 6.7 s on an order lookup. |
| 2 | ~~**Hindi / Hinglish**~~ ✅ | Done: replies follow the user's language, and correct Hindi answers are no longer refused as off-topic. |
| 3 | **Hand-off to a human agent** | An escape route whenever the bot can't help. |
| 4 | **Store policies and FAQ** | Return, shipping, COD and bargaining rules answered from one approved source. |
| 5 | **Tap-to-open cards in the app** | Turns every product card into a path to a sale. |

---

## 2. Revenue — helping people buy

| Enhancement | Value | Effort | Status today |
|---|---|---|---|
| **Tap-to-open product and Bit cards** in the app | Chat becomes a sales channel | Small (app team) | Server side done — see [app-integration.md](app-integration.md) |
| **Product images on cards** | Shoppers buy what they can see | Tiny — set `PRODUCT_IMAGE_QUERY` | ⛔ Blocked: Bunny Optimizer is NOT enabled (verified — 6 images returned byte-identical with the resize query; one answer would ship 8.6 MB) |
| **Deals / discounted products** | "Any offers?" is a top-5 question | Small — one new tool | ✅ Done — `get_discounted_products` |
| **Best-coupon finder** for the cart | Directly lifts conversion | Medium | Coupon validity check exists |
| **Bargaining coach** — "offer ₹X, likely accepted" | Unique to Zatch; no big marketplace has it | Medium | Bargain tools exist |
| **Compare two products** | Standard on Amazon / Flipkart | Small | ✅ Done — `compare_products` |
| **Complete the look** — bought-together items | Bigger baskets | Medium | Similar-products search exists |
| **Size help** — "which size for a 32-inch waist?" | Fewer returns, a major cost in fashion | Medium — needs seller size charts | Missing |
| **Personal picks** from browsing and purchase history | Better recommendations | Medium–Large | Basic version exists |
| **Search by photo** — "find something like this" | Standard in fashion shopping | Large | Missing |
| **Live session and Bit reminders** — "your seller goes live at 7" | Uses Zatch's social features | Medium — needs app notifications | Missing |

---

## 3. Support — fewer tickets

| Enhancement | Value | Effort | Status today |
|---|---|---|---|
| **Policy and FAQ answers** (returns, shipping, COD, bargaining rules) | Most support questions are about policy, not one order | Small–Medium | Per-product return policy only |
| **Refund / return status** — "has my refund been processed?" | Second most common support question | Medium — needs refund data | Cancellation eligibility only |
| **Hand-off to a human agent** with the conversation attached | No dead ends | Medium | Missing |
| **Report a problem** — damaged, wrong or missing item | Captures issues instantly with order context | Medium — first write action | Missing |
| **Proactive delay notices** — "your order is running 5 days late" | Bot already detects late orders; tell the customer first | Medium | Late detection exists |
| **Safe actions with confirmation** — cancel, add to cart, apply coupon | Leading assistants *do* things | Large — write access, confirmation step, audit log | Read-only by design |

---

## 4. Experience and trust

| Enhancement | Value | Effort | Status today |
|---|---|---|---|
| **Faster replies** — fewer AI round trips, a faster model, instant answers for common questions | Long waits lose customers | Medium | Streaming status messages hide part of the wait |
| **Hindi / Hinglish / regional languages** | Most Indian shoppers | Small–Medium — the model handles Hindi; needs testing | Untested |
| **Voice input** | Common on mobile in India | Medium (app side) | Missing |
| **👍 / 👎 on every reply** | The only real measure of answer quality | Small | ✅ Done — `POST /chat/feedback`, `scripts/read_feedback.py` |
| **Tap-to-choose order buttons** instead of typing "last 4 digits" | Removes the confusion seen in manager testing | Small | Text only |
| **Remember preferences across chats** — size, budget, style | Personal feel | Medium | Within one conversation only |

---

## 5. Business insight

| Enhancement | Value | Effort |
|---|---|---|
| **Analytics dashboard** — ratings, unmet demand, questions answered | Proves ROI to management; shows what to build next | ✅ First version done — `/insights`. Card taps and chat-to-purchase still need app-side events |
| **"Not found" demand report** — e.g. sleepwear searched, none in stock | Tells the business what to stock, from real demand | ✅ Done — `scripts/unmet_demand.py` |
| **Catalogue quality report** — listings named "test" or "hi", missing materials, ₹5 prices | Bad listings make the bot look bad | ✅ Done — `scripts/catalogue_quality.py` (54 of 143 flagged on first run) |
| **Seller assistant** — sales, payouts, pending bargains | Sellers matter as much as buyers | Medium — `seller_repo` partly built |

---

## 6. Production essentials

Required before a real launch — not extras.

| Item | Status |
|---|---|
| Real login tokens (JWT) from the Zatch backend | Pending |
| Company Azure account — current credit expires **25 September 2026** | Setup guide sent |
| Backup AI provider (`BACKUP_LLM_*`) | Not configured |
| `DEMO_UI_ENABLED=false` in production | Needed at launch |
| MongoDB Atlas network access restricted | Needed |
| Monitoring and alerts on errors and cost | Missing |
| Scheduled quality-suite runs | Manual today |
| Zatch's own embedding pipeline writing `text-embedding-3-small` vectors | Needed — see [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md) |

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
