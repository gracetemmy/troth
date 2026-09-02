"""Deadline extraction. Deterministic, so every case is pinned to a
fixed 'today' — Thursday 3 September 2026."""

from datetime import date

import pytest

from troth import commitments as C
from troth import memory as M

TODAY = date(2026, 9, 3)


@pytest.mark.parametrize("text,expected", [
    ("I'll send it by Friday", date(2026, 9, 4)),
    ("I'll get it to you tomorrow", date(2026, 9, 4)),
    ("We'll have it by end of week", date(2026, 9, 4)),
    ("I'll follow up next Tuesday", date(2026, 9, 15)),
    ("We'll deliver by 15 September", date(2026, 9, 15)),
    ("I'll send it on 2026-09-10", date(2026, 9, 10)),
    ("We'll circle back in 3 days", date(2026, 9, 6)),
    ("I'll get back to you next week", date(2026, 9, 10)),
    ("We'll wrap up by end of month", date(2026, 9, 30)),
])
def test_dates_resolve(text, expected):
    due, _, _ = C.extract_due(text, TODAY)
    assert due == expected


@pytest.mark.parametrize("text", [
    "I'll check in again in a few weeks",
    "I'll call you next quarter",
    "I'll get back to you at some point",
    "We'll sort it out in due course",
])
def test_vague_gets_no_invented_date(text):
    """A soft deadline must stay soft. Assigning it a Friday would be
    worse than admitting there is no date."""
    due, phrase, precision = C.extract_due(text, TODAY)
    assert due is None
    assert precision == C.VAGUE
    assert phrase


def test_no_time_expression_at_all():
    assert C.extract_due("I can offer you a 25% discount", TODAY) is None
    assert C.extract_due("Thanks, that sounds good", TODAY) is None


def test_same_weekday_means_next_week_not_today():
    """'Friday' said on a Friday means the Friday coming. A deadline
    already past by the time it is spoken is never what was meant."""
    friday = date(2026, 9, 4)
    due, _, _ = C.extract_due("I'll send it by Friday", friday)
    assert due == date(2026, 9, 11)


def test_past_calendar_date_rolls_to_next_year():
    due, _, _ = C.extract_due("We'll deliver by 15 January", TODAY)
    assert due == date(2027, 1, 15)


@pytest.mark.parametrize("due,expected", [
    (date(2026, 9, 1), "OVERDUE"),
    (date(2026, 9, 3), "DUE"),
    (date(2026, 9, 4), "DUE"),
    (date(2026, 9, 20), "OPEN"),
    (None, "UNDATED"),
])
def test_urgency(due, expected):
    assert C.status_for(due, C.EXACT, TODAY) == expected


def test_commitment_round_trips_through_memory(mem):
    rec = M.remember_commitment(mem, "acme", "I'll send the quote by Friday",
                                date(2026, 9, 4), "friday", C.EXACT, "Rep")
    stored = mem.get_entity("commitment", rec["id"])
    assert stored["status"] == M.OPEN
    assert stored["body"]["due"] == "2026-09-04"
    assert stored["body"]["made_by"] == "Rep"

    M.complete_commitment(mem, rec["id"], "sent")
    assert mem.get_entity("commitment", rec["id"])["status"] == M.KEPT
    assert M.list_commitments(mem, status=M.OPEN) == []
