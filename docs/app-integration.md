# Making the cards navigate

Every answer that surfaces a product or a Bit already carries the ids
needed to open them. The demo page does not open anything — it renders
`Opens this product in the Zatch app.` as placeholder text, because a
web page has nowhere to send you. **The server side is finished. What is
missing is one tap handler in the app.**

This document is the contract: what arrives, what the app does with it,
and what it must not do.

---

## 1. What already arrives

No flag to enable, no parameter to send. Ask about products or Bits and
the fields are populated.

### `POST /chat` — the JSON contract

```json
{
  "reply": "Lenin shirts — ₹599, Men.\n16X20 marriage Frame — ₹1000, Home.",
  "session_id": "demo-mtsc0u6i-a74s42",
  "products": [
    {
      "product_id": "69a846974e2d6530993ab1c1",
      "name": "Lenin shirts",
      "price": 599.0,
      "discountedPrice": 599.0,
      "image": null
    }
  ],
  "bits": [],
  "attribution": [
    {"start": 15, "end": 19, "text": "₹599", "kind": "price",
     "tool": "get_trending_products"}
  ]
}
```

A Bit answer is the same envelope with the other list filled:

```json
{
  "reply": "Here are the trending Bits.",
  "session_id": "demo-b-1789122241",
  "products": [],
  "bits": [
    {"bit_id": "69aac90f4e2d6530993b50a6", "title": "Lenin shirts"},
    {"bit_id": "698e33892c63bfbc0476b11f", "title": "Leather jacket"}
  ],
  "attribution": []
}
```

Both lists are always present. Both default to `[]`, so a client written
against the older response shape keeps deserialising this one unchanged.

### `POST /chat/stream` — the SSE contract

The same data, as it is produced. Frames are `event:` + `data:` +
a blank line, and `data` is the JSON object **without** the type:

```
event: status
data: {"tool": "get_trending_bits", "label": "Finding trending videos"}

event: bits
data: {"bits": [{"bit_id": "69aac90f...", "title": "Lenin shirts"}]}

event: done
data: {"reply": "Here are the trending Bits.", "session_id": "doc-sse-1"}
```

`products` carries `{"products": [...]}` with the identical row shape.

---

## 2. The ids, and the one thing not to do

| Field | Is exactly | Verified |
|---|---|---|
| `product_id` | `products._id`, as a 24-char hex string | resolves to the named product |
| `bit_id` | `bits._id`, as a 24-char hex string | resolves to the named Bit |

They are the raw Mongo ObjectIds the rest of the Zatch backend already
uses. Nothing is hashed, prefixed or re-encoded, so whatever the app
already does with a product id from any other endpoint works here.

**Never match a card to a record by name.** The two namespaces do not
overlap as ids — a `product_id` is never a `bits._id` and vice versa —
but the **titles collide**. In the live catalogue right now, `Lenin
shirts` is both a product and a Bit:

```
product_id 69a846974e2d6530993ab1c1  ->  products._id  "Lenin shirts"
bit_id     69aac90f4e2d6530993b50a6  ->  bits._id      "Lenin shirts"
```

Route on the id in the row you were handed. The `name`/`title` is for
display only.

---

## 3. What the app has to add

Four things, none of them large.

### 3.1 Decode the two lists

Add `products` and `bits` to whatever model the chat response already
deserialises into. Both are optional and both default to empty.

```
ChatResponse
  reply         String
  sessionId     String
  products      [ProductCard]   // default []
  bits          [BitCard]       // default []
  attribution   [Attribution]   // default []

ProductCard
  productId        String       // products._id, 24-char hex
  name             String?      // display only
  price            Double?
  discountedPrice  Double?      // may equal price; may be null
  image            String?      // see 5.2 - currently always null

BitCard
  bitId            String       // bits._id, 24-char hex
  title            String?      // display only
```

On the streaming endpoint, handle the `products` and `bits` events the
same way you already handle `status` and `done`.

### 3.2 Render the rows under the reply

The demo draws them between the answer bubble and the timing footnote.
Match whatever the app's existing product row looks like — the point of
sending structured rows rather than a formatted string is that the app
styles them natively.

A Bit row is **title only**. It deliberately carries no price, no view
count and no like count: a video has no price, and the counts are not
sent to the model at all any more, so nothing downstream can display
them.

### 3.3 The tap handler — the actual missing piece

This is the whole task. Replace the placeholder with a push onto the
existing navigation stack:

```
onTapProduct(card)  ->  navigate to the existing PRODUCT screen, card.productId
onTapBit(card)      ->  navigate to the existing BIT / video player, card.bitId
```

The app already has both screens. Nothing new needs building — the chat
just needs permission to push onto them.

**What the app team has to confirm back:** the route or deep link each
screen is reachable by, and whether it accepts a bare ObjectId. That is
the only open question in this document, and it belongs to whoever owns
those routes.

### 3.4 Handle the empty and broken cases

| Case | What arrives | What to do |
|---|---|---|
| Answer surfaced nothing tappable | both lists `[]` | render the reply alone |
| A row has no name/title | `name`/`title` is `null` | show a neutral label; the demo uses "Unnamed product" / "Untitled Bit" |
| Product was deleted after the answer was built | the id resolves to nothing | the destination screen's own not-found path — the chat cannot know |
| Assistant could not answer | both lists `[]` | see 4.3 |

A row with no id never arrives. Rows are dropped server-side when the id
did not survive trimming, because a card that cannot be opened is worse
than no card.

---

## 4. Guarantees worth relying on

### 4.1 Only one kind per turn

A turn delivers products **or** Bits, never both. The answer is written
from the last lookup that surfaced something tappable — search then open
a detail ends on the detail; search then find-similar ends on the
similars — and that batch is the one sent.

So `products` and `bits` are never both non-empty in the same response.
The app can branch on whichever is populated.

### 4.2 At most once, before `done`

On the streaming endpoint, `products`/`bits` arrives at most once per
turn and always before `done`. `done` carries the authoritative reply,
so a client that renders tokens as they arrive should replace what it
drew with `done.reply`.

### 4.3 Nothing tappable under an apology

When the assistant cannot answer — provider down, rate limited,
malformed — the reply is a plain sentence and **both lists are empty**.
Rows are suppressed deliberately: offering things to tap under "I can't
reach my assistant service" contradicts the sentence above them.

### 4.4 The model never composes a link

The ids travel beside the prose, never inside it. The model is never
shown a URL and never writes one, and the system prompt forbids it from
printing internal ids. This is why a card cannot open the wrong product:
the row is lifted verbatim from the tool result rather than written by
the model.

If the app ever renders the reply text as markdown, there is nothing in
it to linkify — by design.

---

## 5. Two things that are currently off

### 5.1 There is no web URL, on purpose

`zatch.shop` is a marketing site with no product pages — `/product/<id>`
renders its router's 404 — so there is nowhere for a web link to point.
Both card builders therefore emit an id and no URL.

If a web surface ever needs real links, it is a few lines in one place
per type: `_product_cards` and `_bit_cards` in
`app/agent/tool_executor.py`. Nothing else in the pipeline would change,
and the app path would be unaffected.

### 5.2 `image` is always `null` today

Product rows carry an `image` field that is currently always null. This
is a deliberate guard, not a bug: the catalogue holds full-resolution
originals — one measured image is **8.5 MB**, a 6144x8192 photograph,
rendered into a 46-pixel square — and eight of those is roughly 10 MB
for a single answer, on a phone.

It turns on with no code change once Bunny Optimizer is enabled on the
pull zone: set `PRODUCT_IMAGE_QUERY='?width=96&quality=75'` and the
field populates. **Build the row to render without an image**, and it
will simply start showing one later.

Bit rows have no image field at all.

---

## 6. What is NOT needed

- **No new endpoint.** The data is already in `/chat` and
  `/chat/stream`.
- **No extra auth.** The same Zatch JWT already sent with the message.
  The rows are scoped by that token like everything else.
- **No CORS work.** CORS is a browser rule; a native client is
  unaffected. See `docs/deployment.md`.
- **No change to the reply text.** The prose stands on its own; the rows
  are an addition to it, not a replacement.

---

## 7. Checklist

- [ ] `products` and `bits` decoded from the chat response
- [ ] `products` and `bits` SSE events handled (streaming clients only)
- [ ] Rows rendered under the reply, matching existing app styling
- [ ] Bit rows show the title only — no price, no counts
- [ ] Tap pushes the existing product screen with `productId`
- [ ] Tap pushes the existing Bit player with `bitId`
- [ ] Rows matched by **id**, never by name — the titles collide
- [ ] Missing `name`/`title` renders a neutral label
- [ ] Product rows render correctly with `image == null`
- [ ] No rows rendered when the reply is an error sentence
- [ ] Route/deep-link contract for both screens confirmed by the app team
