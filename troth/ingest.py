"""Transcript ingestion — the deterministic tier router.

Sibyl does not auto-tier anything. It stores what you tell it to store,
where you tell it to. This module decides where each line of a raw
transcript belongs, and then writes it through the same wrapper functions
in `troth.memory` that a human typing manually would use. There is no
separate ingestion path and no bulk-write shortcut, so a line that
arrives from a transcript is checked exactly as strictly as one typed by
hand.

Routing is a pure function (`classify`), so it can be tested without
touching memory. Only `ingest_document` writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import commitments as C
from . import memory as M

# Below this, Troth will not promote a line into confirmed memory. An
# uncertain extraction becomes a flagged promise awaiting a human, never
# a silent WARM fact.
CONFIDENCE_FLOOR = 0.65

SPEAKER_RE = re.compile(r"^([A-Za-z][A-Za-z .'-]{1,24}):\s*(.+)$")

# A commitment is first-person and forward-looking: someone binding
# themselves to something. This is what separates "I can do 25% off"
# (a promise) from "our policy allows 15%" (a standing rule).
COMMITMENT_RE = re.compile(
    r"\b(i|we|i'll|we'll|i can|we can|i could|we could)\b.{0,40}?"
    r"\b(offer|give|do|get|waive|throw in|include|extend|guarantee|"
    r"promise|commit|send|deliver|ship|honou?r|match|cover|check in|"
    r"follow up|circle back|get back|call|email|review|confirm|write up|"
    r"put together|draft|revert|revise|prepare|share|loop back)\b",
    re.I,
)
POLICY_RE = re.compile(
    r"\b(policy|standard rate|list price|max(?:imum)? discount|pricing rule|"
    r"our standard|company policy|sign-?off|without approval)\b",
    re.I,
)
FACT_RE = re.compile(
    r"\b(we're|we are|i'm|i am|company|contact|ceo|cto|coo|vp|head of|"
    r"signed|scope|contract|headquartered|based in|team of)\b",
    re.I,
)
HEDGE_RE = re.compile(
    r"\b(maybe|might|could be|possibly|probably|not sure|unsure|"
    r"still deciding|thinking about|tentatively|we'll see|no promises|"
    r"don't quote me|off the record|i think|likely)\b",
    re.I,
)


@dataclass
class Item:
    """One classified line, before it touches memory."""

    kind: str
    text: str
    confidence: float
    speaker: str | None = None


@dataclass
class Routed:
    """Where an item went, and why. This is what the UI renders."""

    tier: str
    call: str
    reason: str
    text: str
    status: str | None = None
    record_id: str | None = None


@dataclass
class IngestResult:
    routed: list[Routed] = field(default_factory=list)

    @property
    def tally(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.routed:
            counts[r.tier] = counts.get(r.tier, 0) + 1
        return counts


def parse_lines(raw: str) -> list[tuple[str | None, str]]:
    """Split a transcript into (speaker, text) pairs."""
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        match = SPEAKER_RE.match(line)
        out.append((match.group(1).strip(), match.group(2).strip()) if match else (None, line))
    return out


def classify(text: str, speaker: str | None = None) -> Item:
    """Decide what a line is. Pure — no memory access.

    Order matters. Policy is checked before commitment because a sentence
    like "our standard policy allows a 10% discount" contains discount
    language but binds nobody; it is a standing rule, not a promise. The
    naive ordering routes it to WARM as a promise, which is wrong twice
    over: it invents a commitment nobody made, and it loses the rule that
    other promises need to be checked against.
    """
    hedged = bool(HEDGE_RE.search(text))

    if POLICY_RE.search(text):
        return Item("pricing_rule", text, 0.9, speaker)

    if COMMITMENT_RE.search(text):
        # A hedged commitment is the dangerous case: it sounds like a
        # promise and a client may well hold you to it, but the speaker
        # did not actually commit. It goes in at low confidence so the
        # floor sends it to a human rather than into confirmed memory.
        if hedged:
            return Item("promise", text, 0.5, speaker)

        # Split by what the commitment is *about*. A price claim can be
        # checked against standing policy. A dated deliverable cannot —
        # there is nothing to check it against, it simply comes due. They
        # need different handling, so they are different kinds here.
        if M.extract_discount_pct(text) is not None:
            return Item("promise", text, 0.85, speaker)
        if C.extract_due(text) is not None:
            return Item("commitment", text, 0.85, speaker)
        return Item("promise", text, 0.85, speaker)

    if hedged:
        # Hedged but not promise-shaped: "we might expand next quarter".
        # Real signal, no commitment. It belongs on the timeline, not in
        # confirmed facts, and not in front of a human either.
        return Item("timeline_event", text, 0.7, speaker)

    if FACT_RE.search(text):
        return Item("client_fact", text, 0.8, speaker)

    if len(text.split()) >= 4:
        return Item("timeline_event", text, 0.7, speaker)

    return Item("ambiguous", text, 0.3, speaker)


def ingest_document(
    memory, raw: str, client_id: str, deal_id: str | None = None
) -> IngestResult:
    """Route a raw transcript into Sibyl, line by line.

    Every write goes through troth.memory, so the REFERENCE policy check
    on promises happens here exactly as it does anywhere else.
    """
    deal_id = deal_id or client_id
    result = IngestResult()

    for speaker, text in parse_lines(raw):
        item = classify(text, speaker)

        if item.kind == "pricing_rule":
            pct = M.extract_discount_pct(item.text)
            if pct is not None:
                M.remember_pricing_rule(memory, M.MAX_DISCOUNT_KEY, pct)
                result.routed.append(Routed(
                    "REFERENCE", "remember_pricing_rule()",
                    f"standing policy — discount ceiling now {pct:g}%", item.text,
                ))
            else:
                M.log_interaction(memory, deal_id, "policy_note", item.text)
                result.routed.append(Routed(
                    "COLD", "log_interaction()",
                    "policy language with no figure to enforce", item.text,
                ))

        elif item.kind == "promise":
            if item.confidence < CONFIDENCE_FLOOR:
                # Hedged. Persist as flagged without pretending to judge
                # it — a human decides whether it was a commitment.
                pid = f"{deal_id}:{M._stable_id(item.text)}"
                memory.set_entity(  # SIBYL WARM
                    "promise", pid,
                    {"deal": deal_id, "text": item.text, "made_by": item.speaker,
                     "reason": "hedged language — unclear whether this was a commitment"},
                    status=M.FLAGGED,
                )
                M.log_interaction(memory, deal_id, "promise_flagged", item.text)
                result.routed.append(Routed(
                    "WARM", "flag_promise()",
                    f"hedged (confidence {item.confidence:.2f} < {CONFIDENCE_FLOOR}) — needs a human",
                    item.text, status=M.FLAGGED, record_id=pid,
                ))
            else:
                try:
                    checked = M.flag_promise(memory, deal_id, item.text, item.speaker)
                except M.MemoryUnavailable as exc:
                    # No standing policy in memory to check against. Troth
                    # still will not confirm the promise — but one
                    # uncheckable line must not abort the whole transcript
                    # and lose everything already written. Flag it and keep
                    # going.
                    pid = f"{deal_id}:{M._stable_id(item.text)}"
                    memory.set_entity(  # SIBYL WARM
                        "promise", pid,
                        {"deal": deal_id, "text": item.text, "made_by": item.speaker,
                         "reason": "no standing policy in memory to check against"},
                        status=M.FLAGGED,
                    )
                    M.log_interaction(memory, deal_id, "promise_flagged", item.text)
                    result.routed.append(Routed(
                        "WARM", "flag_promise()",
                        f"unverifiable — {exc.args[0].split('.')[0].lower()}",
                        item.text, status=M.FLAGGED, record_id=pid,
                    ))
                else:
                    result.routed.append(Routed(
                        "WARM", "flag_promise()", checked["reason"], item.text,
                        status=checked["status"], record_id=checked["id"],
                    ))

        elif item.kind == "commitment":
            due, phrase, precision = C.extract_due(item.text)
            rec = M.remember_commitment(memory, deal_id, item.text, due,
                                        phrase, precision, item.speaker)
            urgency = C.describe(due, precision)
            result.routed.append(Routed(
                "WARM", "remember_commitment()",
                f"deliverable — {phrase or 'no date'} ({urgency})",
                item.text, status=C.status_for(due, precision), record_id=rec["id"],
            ))

        elif item.kind == "client_fact":
            existing = M.get_client(memory, client_id) or {}
            details = dict(existing.get("body") or {})
            details.setdefault("facts", [])
            if item.text not in details["facts"]:
                details["facts"].append(item.text)
            M.remember_client(memory, client_id, details)
            result.routed.append(Routed(
                "WARM", "remember_client()", "confirmed client fact", item.text,
                status=M.CONFIRMED, record_id=client_id,
            ))

        elif item.kind == "timeline_event":
            M.log_interaction(memory, deal_id, "transcript_line", item.text)
            result.routed.append(Routed(
                "COLD", "log_interaction()", "timestamped timeline entry", item.text,
            ))

        else:
            M.log_interaction(memory, deal_id, "unclassified", item.text)
            result.routed.append(Routed(
                "COLD", "log_interaction()",
                "too short to classify — kept on the timeline, not promoted", item.text,
            ))

    return result
