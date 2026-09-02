"""Deadlines — turning "I'll send it by Friday" into something Troth can
be held to.

A promise about price is checked against policy. A promise about *time*
needs a different treatment: there is nothing to check it against, it
just comes due. This module extracts the date, and `troth.memory` stores
it as a WARM commitment so the pre-call brief can say "you owe them a
quote, it was due two days ago".

Date parsing is deliberately deterministic — no model call, no guessing.
A phrase that does not resolve to a real date is kept with the phrase
intact and marked vague rather than being assigned an invented deadline.
"I'll get back to you at some point" is not a Friday, and pretending it
is would be worse than admitting the deadline is soft.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

# Resolution of the extracted deadline.
EXACT = "exact"    # a named day or calendar date
APPROX = "approx"  # "next week", "in 2 weeks" — real, but fuzzy
VAGUE = "vague"    # "in a few weeks" — no date at all; that is the finding

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})

# Phrases that sound like a deadline but name no date. Checked before the
# numeric patterns so "in a few weeks" is not read as "in 2 weeks".
VAGUE_RE = re.compile(
    r"\b(?:in\s+)?(?:a\s+)?(?:few|couple\s+of|several)\s+(?:days|weeks|months)\b"
    r"|\bnext\s+quarter\b|\bsometime\b|\bat\s+some\s+point\b|\bdown\s+the\s+line\b"
    r"|\bin\s+due\s+course\b|\bwhen\s+(?:i|we)\s+can\b|\bshortly\b|\bsoon\b",
    re.I,
)

# Something is only a dated commitment if it also commits to an action.
DEADLINE_CUE = re.compile(r"\b(by|before|on|this|next|in|end of|eow|eom|tomorrow|today)\b", re.I)


def _next_weekday(anchor: date, target: int, *, force_next_week: bool = False) -> date:
    """The next occurrence of a weekday. 'Friday' said on a Friday means
    the Friday coming, not today — a deadline already past by the time it
    is spoken is never what was meant."""
    ahead = (target - anchor.weekday()) % 7
    if ahead == 0:
        ahead = 7
    if force_next_week and ahead < 7:
        ahead += 7
    return anchor + timedelta(days=ahead)


def extract_due(text: str, today: date | None = None) -> tuple[date | None, str, str] | None:
    """Find a deadline. Returns (due_date, phrase, precision), or None if
    the line names no time at all."""
    today = today or date.today()
    low = text.lower()

    if not DEADLINE_CUE.search(low) and not VAGUE_RE.search(low):
        return None

    vague = VAGUE_RE.search(low)
    if vague:
        return (None, vague.group(0).strip(), VAGUE)

    if re.search(r"\btomorrow\b", low):
        return (today + timedelta(days=1), "tomorrow", EXACT)
    if re.search(r"\btoday\b|\btonight\b|\bend of (?:the )?day\b|\beod\b", low):
        return (today, "today", EXACT)

    m = re.search(r"\bend of (?:the )?month\b|\beom\b", low)
    if m:
        last = calendar.monthrange(today.year, today.month)[1]
        return (date(today.year, today.month, last), m.group(0), EXACT)

    m = re.search(r"\bend of (?:the )?week\b|\beow\b", low)
    if m:
        return (_next_weekday(today, 4), m.group(0), EXACT)

    # ISO and numeric dates: 2026-09-15, 15/09, 15/09/2026
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", low)
    if m:
        try:
            return (date(int(m.group(1)), int(m.group(2)), int(m.group(3))),
                    m.group(0), EXACT)
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", low)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3) or today.year)
        year += 2000 if year < 100 else 0
        try:
            return (date(year, month, day), m.group(0), EXACT)
        except ValueError:
            pass

    # "15 September", "September 15", "Sep 15th"
    names = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({names})\b", low) or \
        re.search(rf"\b({names})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", low)
    if m:
        a, b = m.group(1), m.group(2)
        day, month = (int(a), MONTHS[b]) if a.isdigit() else (int(b), MONTHS[a])
        try:
            due = date(today.year, month, day)
            # A date already gone means they meant next year.
            if due < today:
                due = date(today.year + 1, month, day)
            return (due, m.group(0), EXACT)
        except ValueError:
            pass

    # "in 3 days", "in 2 weeks", "in a month"
    m = re.search(r"\bin\s+(\d{1,2}|a|an)\s+(day|week|month)s?\b", low)
    if m:
        n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
        unit = m.group(2)
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
        return (today + timedelta(days=days), m.group(0),
                EXACT if unit == "day" else APPROX)

    # "next week"
    if re.search(r"\bnext week\b", low):
        return (today + timedelta(days=7), "next week", APPROX)

    # Weekday names, with "next friday" pushed a week out.
    names = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
    m = re.search(rf"\b(next\s+|this\s+)?({names})\b", low)
    if m:
        forced = bool(m.group(1) and m.group(1).strip() == "next")
        return (_next_weekday(today, WEEKDAYS[m.group(2)], force_next_week=forced),
                m.group(0).strip(), EXACT)

    return None


def describe(due: date | None, precision: str, today: date | None = None) -> str:
    """Plain-language urgency, for the brief and the spreadsheet."""
    today = today or date.today()
    if due is None:
        return "no date given"
    delta = (due - today).days
    hedge = "" if precision == EXACT else " (approx)"
    if delta < 0:
        return f"{abs(delta)} day{'s' if abs(delta) != 1 else ''} overdue{hedge}"
    if delta == 0:
        return f"due today{hedge}"
    if delta == 1:
        return f"due tomorrow{hedge}"
    return f"due in {delta} days{hedge}"


def status_for(due: date | None, precision: str, today: date | None = None) -> str:
    """OPEN, DUE, OVERDUE or UNDATED — what the dashboard colours by."""
    today = today or date.today()
    if due is None:
        return "UNDATED"
    delta = (due - today).days
    if delta < 0:
        return "OVERDUE"
    if delta <= 1:
        return "DUE"
    return "OPEN"


def parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
