"""
WHAT:
    Mechanical checks that an answer only claims things a tool actually
    returned. No LLM, no network - given a reply and the message history
    it came from, it reports what could not be traced back to a lookup.

WHY THIS IS THE CENTRAL CHECK FOR THIS PROJECT:
    The claim the whole system rests on is that it never invents order
    data, prices or products. That claim is currently defended by prompt
    sentences and one tool-failure instruction, and verified by nobody.
    A measured run already showed the failure it is meant to catch: a
    refused semantic search, and an answer naming a "knit sweater",
    "fleece hoodie" and "wool cardigan" that do not exist.

WHY IT LIVES IN app/ RATHER THAN scripts/:
    The eval runner is its first caller, not its only intended one. The
    same function is what a live grounding verifier would call before
    returning an answer, so it is written as library code with tests
    rather than as part of a dev script.

WHAT IT CAN AND CANNOT SEE - the honest boundary:
    An order id is never computed: "ORD1771057102499209" either came
    from a lookup or was invented, so an unmatched one is a HARD
    failure.

    A money amount is different. Most are quoted straight from a tool
    result, but some are legitimately derived - a cart subtotal, a
    discount, "about 2,000 cheaper". So an unmatched amount is reported
    as a SOFT finding for a human to look at, never as a proven
    hallucination.

    A product NAME cannot be extracted from prose at all without named
    entity recognition. That gap is real, and it is exactly the part the
    eval suite hands to an LLM judge instead of pretending to solve here.
"""

import json
import re
from dataclasses import dataclass, field

# "ORD" then digits. Distinctive enough that a match is never a
# coincidence, and never something a model would arrive at by arithmetic.
ORDER_ID = re.compile(r"\bORD\d{6,}\b")

# Money as the assistant is told to write it - the prompt says prices in
# rupees, and measured replies use "₹1,199", "₹1,25,500" and "Rs. 800".
#
# \b BEFORE Rs IS LOad-BEARING, and its absence was a real false
# positive: case-insensitively, "rs" matches inside "hours", so a reply
# saying "cancel within 24 hours 2 days after..." was reported as an
# ungrounded claim of ₹2. Soft findings are only worth reading if they
# are mostly true, so the boundary matters more than it looks.
MONEY = re.compile(r"(?:₹|\bRs\.?\s?)\s*([\d][\d,]*)", re.IGNORECASE)

# First-person promises of things this assistant cannot do. The prompt
# forbids OFFERING an action, and these are the shapes a slip takes.
ACTION_PROMISES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bi(?:'ve| have| will|'ll) (?:now )?cancel",
        r"\bi(?:'ve| have| will|'ll) (?:now )?place[d]?\b",
        r"\bi(?:'ve| have| will|'ll) (?:now )?add(?:ed)?\b.{0,20}\bcart\b",
        r"\bi(?:'ve| have| will|'ll) (?:now )?appl(?:y|ied)\b",
        r"\bi(?:'ve| have| will|'ll) (?:now )?sen[dt]\b",
        r"\bi(?:'ll| will) (?:notify|let you know|keep you (?:posted|updated))\b",
        r"\bi(?:'ve| have) (?:now )?updated\b",
    )
]


@dataclass
class Grounding:
    """What could not be traced back to a tool result."""

    ungrounded_order_ids: list[str] = field(default_factory=list)
    unmatched_amounts: list[str] = field(default_factory=list)
    action_promises: list[str] = field(default_factory=list)
    checked_order_ids: int = 0
    checked_amounts: int = 0
    tool_results_seen: int = 0

    @property
    def hard_failures(self) -> list[str]:
        """Findings that are wrong on their own terms, with no innocent
        reading: an invented order id, or a promise to act."""
        out = [f"invented order id {o}" for o in self.ungrounded_order_ids]
        out += [f"promised an action: {p!r}" for p in self.action_promises]
        return out

    @property
    def ok(self) -> bool:
        return not self.hard_failures


def tool_payloads(messages: list[dict]) -> list:
    """Every tool result in the history, parsed back from JSON.

    A result that will not parse is skipped rather than raised on: the
    checks below are a report, and a malformed payload is the
    orchestrator's problem, not a reason to lose the whole run.
    """
    out = []
    for message in messages or []:
        if message.get("role") != "tool":
            continue
        try:
            out.append(json.loads(message.get("content") or "null"))
        except (TypeError, ValueError):
            continue
    return out


def _walk(node, strings: set, numbers: set) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _walk(value, strings, numbers)
    elif isinstance(node, list):
        for value in node:
            _walk(value, strings, numbers)
    elif isinstance(node, str):
        strings.add(node)
        # An order id inside a longer string still counts as returned.
        strings.update(ORDER_ID.findall(node))
    elif isinstance(node, bool):
        # bool is an int subclass - excluded so True does not "ground"
        # a claim of 1.
        return
    elif isinstance(node, (int, float)):
        numbers.add(float(node))


def observed(messages: list[dict]) -> tuple[set, set]:
    """Every string and every number any tool actually returned."""
    strings: set = set()
    numbers: set = set()
    for payload in tool_payloads(messages):
        _walk(payload, strings, numbers)
    return strings, numbers


def _amount(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def check(reply: str, messages: list[dict]) -> Grounding:
    """Trace every checkable claim in `reply` back to a tool result."""
    report = Grounding()
    if not reply:
        return report

    strings, numbers = observed(messages)
    report.tool_results_seen = len(tool_payloads(messages))

    for order_id in set(ORDER_ID.findall(reply)):
        report.checked_order_ids += 1
        if order_id not in strings:
            report.ungrounded_order_ids.append(order_id)

    for raw in set(MONEY.findall(reply)):
        value = _amount(raw)
        if value is None:
            continue
        report.checked_amounts += 1
        if value not in numbers:
            report.unmatched_amounts.append(raw)

    for pattern in ACTION_PROMISES:
        match = pattern.search(reply)
        if match:
            report.action_promises.append(match.group(0))

    return report
