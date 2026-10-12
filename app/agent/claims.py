"""
WHAT:
    Two mechanical checks on a finished reply, both about saying
    something happened that did not:

      action_claim         the assistant says it did, or will do,
                           something it cannot - place, cancel, add,
                           apply, send, refund
      unsupported_statuses an order is given a status no lookup returned
                           for THAT order

WHY THESE EXIST:
    Measured on Nemotron, 12 October 2026. Asked "Place an offer of ₹900
    on the RL Black shirt for me", it answered "Your offer of ₹900 on the
    RL Black shirt has been submitted… You'll be notified if it's
    accepted." Nothing was placed - the database credential is read-only -
    but the shopper was told otherwise, which is worse than any refusal.
    grounding.ACTION_PROMISES only knew first-person wordings ("I've
    cancelled"), and a passive "has been submitted" walked past it.

    Asked for the details of a PENDING order, it wrote "The order is
    marked as delivered" - the details lookup carried no status, and the
    turn's order list did carry "delivered" for OTHER orders, so a check
    against "every status the turn returned" would have passed it too.
    Statuses are therefore judged per order.

WHY TWO TIERS FOR ACTIONS:
    "I've placed your offer" is false in any context: this assistant
    never acts. "Your order has been cancelled" is a correct status report
    when a buyer asks where their order is, and a false claim when they
    asked for it to BE cancelled. So passive completions and "you'll be
    notified" are judged only on turns where the shopper asked for an
    action; first-person claims and offers ("Shall I place it?") always.

    Both tiers let through a match that carries its own negation ("I
    can't place", "मैंने कुछ भी रद्द नहीं किया"), somebody else acting ("the
    seller has sent a counter-offer"), and a dated fact ("was placed on
    22 March").
"""

import re

from app.agent.grounding import DEVANAGARI, _SENTENCE, tool_names, tool_payloads

# ── Did the shopper ask for something to be DONE? ───────────────────

_ASK_EN = re.compile(
    r"\b(place|submit|make\s+an?\s+offer|send|cancel|add|apply|use\s+(?:the\s+)?(?:coupon|code)|"
    r"redeem|book|refund|return|buy|purchase|reorder|check\s*out|pay|accept|reject|"
    r"counter|negotiate\s+for\s+me|bargain\s+for\s+me)\b",
    re.IGNORECASE,
)
_ASK_HINGLISH = re.compile(
    r"\b(kar\s*do|kardo|kar\s+dijiye|kar\s+dena|karo|laga\s*do|lagao|bhej\s*do|bhejo|"
    r"daal\s*do|daalo|dal\s+do|wapas|refund|cancel)\b",
    re.IGNORECASE,
)
_ASK_HINDI = (
    "कर दो", "कर दीजिए", "कर दें", "करो", "रद्द", "कैंसल", "लगा दो", "लगाओ",
    "भेज दो", "जोड़ दो", "डाल दो", "रिफंड", "वापस", "प्लेस", "बुक",
)


def asks_for_action(message: str) -> bool:
    """Whether the shopper asked for something to be done, not looked up."""
    text = message or ""
    return bool(
        _ASK_EN.search(text)
        or _ASK_HINGLISH.search(text)
        or any(stem in text for stem in _ASK_HINDI)
    )


_QUESTION_START = re.compile(
    r"^\s*(?:can|could|is|are|does|do|did|will|would|should|what|where|when|how|why|which|"
    r"kya|kab|kahan|kaise|kyun|क्या|कब|कहाँ|कहां|कैसे|क्यों)\b",
    re.IGNORECASE,
)


def demands_action(message: str) -> bool:
    """A COMMAND to act - "cancel my order", "offer laga do", "कार्ट में जोड़
    दो" - as opposed to a question about one ("Can I cancel my order?").

    The difference decides what an honest answer must contain. A question
    is honestly answered "yes, it can still be cancelled in the app"; a
    command is only honestly answered by saying it was NOT done.
    """
    text = (message or "").strip()
    if not asks_for_action(text) or text.endswith("?") or text.endswith("؟"):
        return False
    return not _QUESTION_START.search(text)


# Saying it cannot act, in each language the shopper may write in.
_INABILITY = re.compile(
    r"\b(?:can't|can’t|cannot|can not|unable to|not able to|can only (?:look|check|read|see|find|show)|"
    r"don't have (?:the )?(?:ability|access|permission)|not allowed|isn't something i can|"
    r"have to do (?:that|it) (?:yourself|in the)|you(?:'ll| will) need to do (?:that|it))\b"
    r"|\bnahi(?:n)?\s+kar\s+(?:sakta|sakti|sakte|paunga|paungi)\b|\bsirf\s+(?:dekh|check)\b"
    r"|नहीं\s*कर\s*(?:सकता|सकती|सकते)|नहीं\s*(?:जोड़|लगा|भेज|डाल)\s*(?:सकता|सकती|सकते)"
    r"|(?:सिर्फ़|सिर्फ|केवल)\s*(?:जानकारी\s*)?देख\s*(?:सकता|सकती|सकते)|अनुमति\s*नहीं",
    re.IGNORECASE,
)


def states_inability(reply: str) -> bool:
    """Whether the reply says, in any of our languages, that it cannot act."""
    return bool(_INABILITY.search(reply or ""))


# ── Claims that something was or will be done ───────────────────────

# Base and past forms of what this assistant cannot do. "update" and
# "change" are left out of the base forms: "let me update you" is a
# figure of speech, not a claim.
_VERBS_EN = (
    r"(?:place[ds]?|submit(?:ted)?|sen[dt]|cancel(?:l?ed)?|add(?:ed)?|appl(?:y|ied)|"
    r"book(?:ed)?|process(?:ed)?|initiat(?:e|ed)|rais(?:e|ed)|request(?:ed)?|"
    r"order(?:ed)?|buy|bought|purchas(?:e|ed)|redeem(?:ed)?|refund(?:ed)?|"
    r"reserv(?:e|ed)|schedul(?:e|ed)|notify|forward(?:ed)?)"
)

# The VERB of a Hinglish or Hindi claim: the action word directly before
# "kar"/"कर", or a verb that is itself the action ("laga diya", "भेज दिया").
# Directly before, because "maine aapke orders check kar diye" (I checked
# your orders) names an order and claims nothing.
_DID_HINGLISH = (
    r"(?:(?:place|submit|cancel|add|apply|book|refund|return)\s+kar\s*(?:diya|di|diye|dia)"
    r"|(?:laga|bhej|daal)\s*(?:diya|di|diye|dia))"
)
_WILL_HINGLISH = (
    r"(?:(?:place|submit|cancel|add|apply|book|refund|return)\s+kar\s*(?:dunga|dungi|denge|deta|deti|raha|rahi)"
    r"|(?:laga|bhej|daal)\s*(?:dunga|dungi|denge|deta|deti))"
)
_DID_HINDI = (
    r"(?:(?:रद्द|कैंसल|प्लेस|सबमिट|लागू|बुक|रिफंड)\s*कर\s*(?:दिया|दी|दिए)"
    r"|(?:जोड़|भेज|लगा|डाल)\s*(?:दिया|दी|दिए))"
)
_WILL_HINDI = (
    r"(?:(?:रद्द|कैंसल|प्लेस|सबमिट|लागू|बुक|रिफंड)\s*कर\s*(?:दूँगा|दूंगा|दूँगी|दूंगी|देता|देती|रहा|रही)"
    r"|(?:जोड़|भेज|लगा|डाल)\s*(?:दूँगा|दूंगा|दूँगी|दूंगी|देता|देती))"
)

# Always a false claim: this assistant has no hands. First-person past or
# future, and offers to do it.
_ALWAYS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        rf"\b(?:i|we)(?:'ve|'ll|'m going to| have| will| am going to| just| now| already| successfully| went ahead and)+\s+(?:\w+\s+)?{_VERBS_EN}\b",
        r"\b(?:i|we)\s+(?:placed|submitted|sent|cancell?ed|added|applied|booked|processed|initiated|raised|requested|ordered|redeemed|refunded|reserved|scheduled|updated|changed|forwarded)\b",
        rf"\b(?:shall|should)\s+i\s+{_VERBS_EN}\b",
        rf"\b(?:want|would you like|do you want|should)\s+me\s+to\s+{_VERBS_EN}\b",
        rf"\blet\s+me\s+{_VERBS_EN}\b",
        rf"\bi\s+can\s+{_VERBS_EN}\b[^.?!\n]{{0,40}}\bfor\s+you\b",
        # "I can cancel one. Which one would you like to cancel?" - measured.
        r"\bi\s+can\s+(?:cancel|place|add|apply|submit|book|refund)\b",
        # Hindi capability and future: "मैं… सौदे लगा सकता हूं", "मैं… रद्द करूंगा"
        # (both measured). A negated form never matches: "नहीं" is checked
        # around every match.
        r"मैं[^।?!\n]{0,40}(?:रद्द|कैंसल|प्लेस|सबमिट|लागू|बुक|रिफंड|लगा|जोड़|भेज|डाल)\s*(?:कर\s*)?(?:सकता|सकती)\s*(?:हूँ|हूं)",
        r"मैं[^।?!\n]{0,40}(?:रद्द करूंगा|रद्द करूँगा|रद्द करूंगी|रद्द करूँगी|कैंसल करूंगा|कैंसल करूँगा|लगाऊंगा|लगाऊँगा|लगाऊंगी|जोड़ूंगा|जोड़ूँगा|जोड़ूंगी|भेजूंगा|भेजूँगा|प्लेस करूंगा|प्लेस करूँगा|सबमिट करूंगा)",
        # Hinglish capability and future: "main cancel kar sakta hoon", "main cancel karunga"
        r"\b(?:main|mai|hum)\b[^.?!\n]{0,40}\b(?:place|submit|cancel|add|apply|book|refund)\s+kar\s+(?:sakta|sakti|sakte)\b",
        r"\b(?:main|mai|hum)\b[^.?!\n]{0,40}\b(?:place|submit|cancel|add|apply|book|refund)\s+(?:karunga|karungi|karenge)\b",
        # Doing it right now: "I'm adding it to your cart".
        r"\b(?:i'm|i am|we're|we are)\s+(?:now\s+)?(?:adding|placing|submitting|applying|cancell?ing|booking|processing)\b",
        r"\bi(?:'ll| will)\s+(?:let you know|keep you (?:posted|updated)|notify you|send you)\b",
        # Hinglish: "maine offer place kar diya", "main cart mein add kar dunga"
        rf"\b(?:maine|mainne|hamne)\b[^.?!\n]{{0,40}}\b{_DID_HINGLISH}\b",
        rf"\b(?:main|mai|hum)\b[^.?!\n]{{0,40}}\b{_WILL_HINGLISH}\b",
        # Hindi: "मैंने ऑर्डर रद्द कर दिया", "मैं ऑफ़र प्लेस कर दूँगा"
        rf"मैंने[^।?!\n]{{0,40}}{_DID_HINDI}",
        rf"मैं[^।?!\n]{{0,40}}{_WILL_HINDI}",
    )
]

# False only when the shopper asked for that action this turn.
_WHEN_ASKED = [
    re.compile(p, re.IGNORECASE)
    for p in (
        # "has been submitted" - but not a dated fact, "was placed on 22 March"
        r"\b(?:has|have|had|is|was|were|are|'s)\s+(?:been\s+|now\s+|successfully\s+|just\s+|already\s+)*"
        r"(?:placed|submitted|sent|cancell?ed|added|applied|booked|processed|initiated|raised|"
        r"requested|created|redeemed|refunded|scheduled|reserved|forwarded|done)\b"
        r"(?!\s+(?:on|in|at|back)\b)",
        r"\bsuccessfully\s+(?:placed|submitted|sent|cancell?ed|added|applied|booked|processed)\b",
        r"\b(?:placed|submitted|sent|cancell?ed|added|applied|booked|processed)\s+successfully\b",
        r"\byou(?:'ll| will)\s+(?:be\s+notified|get\s+(?:a\s+)?(?:notification|confirmation|update|refund)|"
        r"receive\s+(?:a|an|your)\s+(?:notification|confirmation|update|email|sms|refund))\b",
        r"\bwe(?:'ll| will)\s+(?:notify|let you know|update you|process)\b",
        r"\bwould you like to\s+(?:place|submit|send|cancel|add|apply|book|go ahead|confirm)\b",
        r"\b(?:all set|it'?s done|done!)",
        # No auxiliary at all: "RL Black shirt added to your cart." - measured.
        r"\b(?:added|placed|submitted|applied|booked|sent)\s+(?:to|in|into|on|for)\s+(?:your\s+|the\s+)?(?:cart|bag|order|offer|seller)\b",
        r"\b(?:added|placed|submitted|applied|booked|sent)\s+(?:to|in|into|on|for)\s+(?:your|the)\b",
        # "RL Black shirt is in your cart now", "It's already in your cart" (measured).
        r"\b(?:is|are|'s)\s+(?:now\s+|already\s+)?in\s+your\s+(?:cart|bag)\b",
        r"\byour\s+(?:cart|bag)\s+now\s+(?:has|contains|includes)\b",
        r"\bcart\s+updated\b",
        # No subject either: "Adding the RL Black shirt to your cart -" (measured).
        r"(?:^|[.!\n]\s*)(?:adding|placing|submitting|applying|cancell?ing|booking)\b[^.?!\n]{0,50}"
        r"\b(?:to your cart|your offer|the offer|the coupon|your order|now)\b",
        # A bare "...order... cancelled" after a cancel request: "Blue shirt
        # order #...6826 cancelled (pending order)" (measured). Not "can be
        # cancelled", and not a list of "delivered or cancelled" orders.
        r"\border\b[^?!\n]{0,40}?(?<!\bbe )(?<!\bbeen )\bcancell?ed\b(?!\s+(?:orders?|ones?|or|and)\b)",
        # Hinglish: "offer place ho gaya", "order cancel kar diya gaya"
        r"\b(?:place|submit|cancel|add|apply|book|refund|return|bhej|laga|daal)\w*\s+"
        r"(?:ho|kar)\s+(?:gaya|gayi|gyi|gya|diya|di|chuka|chuki|diya gaya|di gayi)\b",
        r"\baapko\b[^.?!\n]{0,20}\b(?:notify|notification|suchit|bata)\w*\b[^.?!\n]{0,20}"
        r"\b(?:jayega|jaayega|jayegi|milega|milegi|kiya jayega)\b",
        # Hindi: "ऑर्डर रद्द हो गया", "आपको सूचित किया जाएगा"
        r"(?:रद्द|कैंसल|प्लेस|सबमिट|जोड़|लागू|भेज|बुक|रिफंड)\S*\s*(?:हो गया|हो गई|हो गयी|कर दिया गया|कर दी गई|हो चुका|हो चुकी)",
        # The passive without "कर": "ऑफर लगा दिया गया", "रद्द किया गया",
        # "जोड़ दिया गया", "प्रस्तुत किया गया" - all four measured.
        r"(?:रद्द|कैंसल|प्लेस|सबमिट|लागू|बुक|रिफंड|लगा|जोड़|भेज|डाल|प्रस्तुत|अप्लाई)\s*"
        r"(?:किया|की|दिया|दी|कर दिया|कर दी)\s*(?:गया|गई|गयी|जा चुका|जा चुकी)",
        # The one-word passive: "ऑफर लगाया गया है", "जोड़ा गया", "भेजा गया" (measured).
        r"(?:लगाया|लगाई|जोड़ा|जोड़ी|भेजा|भेजी|डाला|डाली)\s*(?:गया|गई|गयी|जा चुका|जा चुकी)",
        r"आपको[^।?!\n]{0,20}(?:सूचित|नोटिफ़ाई|नोटिफाई|सूचना|बता)[^।?!\n]{0,20}(?:जाएगा|जाएगी|मिलेगी|मिल जाएगी)",
    )
]

# A claim's own words saying it did NOT happen. Only "नहीं" for Hindi:
# "मत" also sits inside "कीमत" (price), and a lone "न" ends ordinary words.
_NEGATION = re.compile(
    r"\b(?:not|never|cannot|can't|can’t|couldn't|won't|don't|didn't|haven't|hasn't|isn't|"
    r"wasn't|unable|no)\b|n't\b|\bnahi\b|\bnahin\b|\bnhi\b|नहीं",
    re.IGNORECASE,
)

# Somebody else acting is a report, not a claim by the assistant.
_OTHER_ACTOR = re.compile(
    r"\b(?:seller|sellers|courier|shop|store|they|buyer|delivery partner)\b[^.?!\n]{0,15}$",
    re.IGNORECASE,
)


def _really_cancelled(reply: str, turn: list) -> bool:
    """Whether "cancelled" in this reply is a REPORT: the order it names
    was returned as cancelled - or, naming none, every order looked up
    was. Per order, because an order list routinely holds one cancelled
    order beside the pending one the shopper asked to cancel; measured,
    that is exactly the turn a false "order cancelled" came out of."""
    known = statuses_by_order(turn)
    if not known:
        return False
    named = _orders_named(reply, known)
    orders = named or set(known)
    return all("cancelled" in known[o] for o in orders)


def action_claim(reply: str, user_message: str, turn: list | None = None) -> str | None:
    """The words claiming an action happened or will happen, or None.

    A passive "has been cancelled" is let through when an order lookup
    this turn really returned that status: then it is a report.
    """
    text = reply or ""
    asked = asks_for_action(user_message)

    for tier, patterns in (("always", _ALWAYS), ("asked", _WHEN_ASKED if asked else [])):
        for pattern in patterns:
            for match in pattern.finditer(text):
                span = match.group(0)
                if _NEGATION.search(span):
                    continue
                # A few words back, within the same sentence: "you cannot
                # place", "it has not been", "the seller has".
                before = re.split(r"[.!?\n।]", text[max(0, match.start() - 30):match.start()])[-1]
                if _NEGATION.search(before):
                    continue
                if tier == "asked":
                    if _OTHER_ACTOR.search(before):
                        continue
                    if re.search(r"cancell?ed", span, re.I) and _really_cancelled(text, turn or []):
                        continue
                return span.strip()
    return None


def dishonest_about_action(reply: str, user_message: str, turn: list | None = None) -> str | None:
    """Why this reply is dishonest about acting, or None.

    TWO WAYS, and the second is the one that holds. Claim patterns catch
    what they know - and Nemotron found a new wording in every live round
    ("is in your cart now", "already in your cart", "added to cart",
    "रद्द करूंगा"). So a COMMAND to act is also judged positively: this
    assistant can never act, so an honest reply to "cancel my order" must
    say it cannot. One that does not is wrong however it is worded.
    """
    claim = action_claim(reply, user_message, turn)
    if claim:
        return f"claims an action: {claim!r}"
    if demands_action(user_message) and not states_inability(reply):
        return "asked to act, and the reply never says it cannot"
    return None


# What the assistant says when it still claims an action after being
# corrected once. Fixed, so nothing negotiable reaches the shopper.
READ_ONLY_REPLY = (
    "I can only look things up, so I haven't placed, changed or cancelled "
    "anything. You can do that yourself in the Zatch app."
)
READ_ONLY_REPLY_HI = (
    "मैं सिर्फ़ जानकारी देख सकता हूँ, इसलिए मैंने कुछ भी प्लेस, बदला या रद्द "
    "नहीं किया है। आप यह Zatch ऐप में खुद कर सकते हैं।"
)

READ_ONLY_INSTRUCTION = (
    "You can only read data. You cannot place, submit, cancel, add, apply, "
    "book, refund or send anything, and nothing has been done. Answer my "
    "previous message again: say plainly that you can't do it, then tell me "
    "how I can do it myself in the Zatch app. You may still tell me what the "
    "lookups returned. Do not say anything was or will be done, and do not "
    "offer to do it."
)


def read_only_reply(user_message: str) -> str:
    return READ_ONLY_REPLY_HI if DEVANAGARI.search(user_message or "") else READ_ONLY_REPLY


# ── A status no lookup returned for that order ──────────────────────

STATUS_PHRASE = re.compile(
    r"\b(out for delivery|ready to ship|in transit|delivered|shipped|dispatched|"
    r"cancell?ed|pending|confirmed|processing|returned|refunded)\b",
    re.IGNORECASE,
)
_STATUS_NEGATION = re.compile(r"\b(?:not|never|yet)\b|n't\b", re.IGNORECASE)

ORDER_TOOLS = frozenset({
    "get_order_status", "get_order_history", "get_order_detail",
    "get_delivery_estimate", "get_tracking", "get_invoice",
    "check_cancellation_eligibility",
})


def _norm_status(text: str) -> str:
    text = text.lower().replace("_", " ")
    return "cancelled" if text == "canceled" else text


def statuses_by_order(messages: list) -> dict[str, set]:
    """order id -> every status any lookup returned for it: its current
    status, and the stages in its history."""
    out: dict[str, set] = {}

    def walk(node):
        if isinstance(node, dict):
            order_id = node.get("orderId")
            if isinstance(order_id, str):
                statuses = out.setdefault(order_id, set())
                for key in ("status", "statusText"):
                    if isinstance(node.get(key), str):
                        statuses.add(_norm_status(node[key]))
                for stage in node.get("statusHistory") or []:
                    if isinstance(stage, dict) and isinstance(stage.get("status"), str):
                        statuses.add(_norm_status(stage["status"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for payload in tool_payloads(messages):
        walk(payload)
    return out


def _orders_named(text: str, known: dict) -> set:
    runs = re.findall(r"\d{4,}", text)
    named = set()
    for order_id in known:
        digits = re.sub(r"\D", "", order_id)
        if any(digits.endswith(run) for run in runs):
            named.add(order_id)
    return named


def _split_sentences(text: str) -> list[str]:
    parts = []
    for line in (text or "").split("\n"):
        parts.extend(s for s in _SENTENCE.split(line) if s.strip())
    return parts


def unsupported_statuses(reply: str, messages: list) -> list[tuple[str, str]]:
    """(order id, status) pairs the reply asserts and no lookup returned.

    Judged per order: each sentence's statuses are checked against the
    order that sentence names, or - when it names none - against the one
    order the whole reply is about. A sentence that cannot be tied to a
    single order is not judged, rather than guessed at. Negated mentions
    ("not delivered yet") are not assertions.
    """
    if not reply or not (ORDER_TOOLS & set(tool_names(messages).values())):
        return []
    known = {k: v for k, v in statuses_by_order(messages).items() if v}
    if not known:
        return []

    whole = _orders_named(reply, known)
    out: list[tuple[str, str]] = []
    for sentence in _split_sentences(reply):
        named = _orders_named(sentence, known)
        about = named if named else whole
        if len(about) != 1:
            continue
        order_id = next(iter(about))
        for match in STATUS_PHRASE.finditer(sentence):
            before = sentence[max(0, match.start() - 20):match.start()]
            if _STATUS_NEGATION.search(before):
                continue
            status = _norm_status(match.group(1))
            if status not in known[order_id] and (order_id, status) not in out:
                out.append((order_id, status))
    return out


def drop_unsupported_statuses(reply: str, messages: list) -> str:
    """The reply without the sentences that state an unsupported status -
    the last resort after asking again. A wrong status is worse than a
    missing sentence."""
    bad = {status for _, status in unsupported_statuses(reply, messages)}
    if not bad:
        return reply
    kept_lines = []
    for line in reply.split("\n"):
        kept = [
            s for s in _SENTENCE.split(line)
            if not any(_norm_status(m.group(1)) in bad for m in STATUS_PHRASE.finditer(s))
        ]
        joined = " ".join(kept).strip()
        if joined or not line.strip():
            kept_lines.append(joined)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()


def status_instruction(messages: list) -> str:
    """The correction, naming the statuses the lookups actually returned."""
    facts = "; ".join(
        f"order ending {re.sub(r'[^0-9]', '', oid)[-4:]}: {', '.join(sorted(s))}"
        for oid, s in statuses_by_order(messages).items() if s
    )
    return (
        "Answer my previous message again. Give an order's status only as the "
        "lookups returned it (its statusText) - never one they did not. The "
        f"lookups returned: {facts}."
    )
