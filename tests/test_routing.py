"""Classification is pure, so it can be tested without touching memory."""

import pytest

from troth import ingest as I
from troth import memory as M


@pytest.mark.parametrize("line,kind", [
    ("Our standard policy allows a 15% discount", "pricing_rule"),
    ("Company policy caps discounts at 10% without sign-off", "pricing_rule"),
    ("I can offer you a 25% bundle discount", "promise"),
    ("I'll send the revised quote by Friday", "commitment"),
    ("I'll check in again in a few weeks", "commitment"),
    ("We'll waive the setup fee", "promise"),
    ("I might be able to waive the setup fee, not sure yet", "promise"),
    ("We might expand our data team next quarter, still deciding", "timeline_event"),
    ("We're Meridian Labs and I'm the CTO", "client_fact"),
    ("Thanks for making the time today", "timeline_event"),
    ("Ok.", "ambiguous"),
])
def test_classify(line, kind):
    assert I.classify(line).kind == kind


def test_policy_beats_promise_wording():
    """A policy sentence contains discount language but binds nobody. If
    it routes to promise, the rule that every other promise is checked
    against is lost."""
    item = I.classify("Our standard policy allows a 15% early-renewal discount")
    assert item.kind == "pricing_rule"


def test_hedged_commitment_drops_below_the_floor():
    item = I.classify("I might be able to waive the setup fee, not sure yet")
    assert item.confidence < I.CONFIDENCE_FLOOR


def test_hedge_without_commitment_stays_on_the_timeline():
    """'We might expand next quarter' hedges but commits to nothing.
    Flagging it would just make the review queue noisy."""
    item = I.classify("We might expand our data team next quarter, still deciding")
    assert item.kind == "timeline_event"
    assert item.confidence >= I.CONFIDENCE_FLOOR


def test_speaker_is_captured():
    speaker, text = I.parse_lines("Dara: We signed in July")[0]
    assert speaker == "Dara"
    assert text == "We signed in July"


def test_unparsed_line_keeps_its_text():
    assert I.parse_lines("no speaker here") == [(None, "no speaker here")]


def test_empty_transcript_routes_nothing(seeded):
    assert I.ingest_document(seeded, "   \n\n  ", "ghost").routed == []


def test_garbage_is_kept_not_promoted(seeded):
    result = I.ingest_document(seeded, "%%%%\n::::\n12345", "garbage")
    assert {r.tier for r in result.routed} == {"COLD"}
    assert seeded.list_entities("client") == []


def test_uncheckable_promise_does_not_abort_the_transcript(mem):
    """No policy in memory. The promise cannot be confirmed, but the rest
    of the transcript must still be written."""
    text = "Rep: I can offer 25% off.\nDara: We are Acme and I am the CTO."
    result = I.ingest_document(mem, text, "acme")

    assert len(result.routed) == 2
    assert M.get_client(mem, "acme") is not None
    assert len(M.flagged_promises(mem)) == 1


def test_conflicting_rules_last_one_wins(mem):
    text = ("Rep: Company policy caps discounts at 10% without sign-off.\n"
            "Rep: Actually our standard policy allows a 20% discount.")
    I.ingest_document(mem, text, "orbital")
    assert M.current_max_discount(mem) == 20
