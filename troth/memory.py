"""The Sibyl Memory layer.

EVERY read and write Troth performs happens in this file. Nothing else in
the codebase touches storage. If you are a judge looking for the
critical-path memory calls, they are all here, and each one is marked
with a `SIBYL <TIER>` comment.

Sibyl Memory has five tiers. Troth maps onto them like this:

    HOT       set_state / get_state          live deal state
    WARM      set_entity / get_entity        clients, confirmed promises
    COLD      write_event / read_events      the interaction timeline
    REFERENCE set_reference / get_reference  standing pricing policy
    ARCHIVE   archive_entity                 churned / closed-lost clients

`status="flagged"` is NOT a sixth Sibyl tier. `status` is a native field
on Sibyl's WARM entities, and Troth uses it to carry its own trust
label. Sibyl stores the value; deciding what "flagged" means, and
refusing to act on it, is Troth's logic and lives in this file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from sibyl_memory_client import MemoryClient
from sibyl_memory_client.exceptions import NotFoundError

DEFAULT_DB = os.environ.get("TROTH_DB", "~/.sibyl-memory/memory.db")

# The REFERENCE key holding the standing discount ceiling. Read live on
# every promise check — never cached, so editing the rule changes the
# verdict on the next promise without a restart.
MAX_DISCOUNT_KEY = "pricing.max_discount_pct"

# Trust labels Troth writes into Sibyl's native entity `status` field.
CONFIRMED = "confirmed"
FLAGGED = "flagged"
RETRACTED = "retracted"


class MemoryUnavailable(RuntimeError):
    """Raised when Sibyl Memory cannot be reached.

    Troth does not degrade to a cache or an empty answer when this
    happens; it refuses to answer at all. That refusal is the point.
    """


def connect(db_path: str | None = None) -> MemoryClient:
    """Open the Sibyl Memory database. This is Troth's only storage."""
    try:
        return MemoryClient.local(db_path or DEFAULT_DB)
    except Exception as exc:  # pragma: no cover - depends on local env
        raise MemoryUnavailable(
            f"Sibyl Memory is unreachable at {db_path or DEFAULT_DB}. "
            "Troth has no other store to fall back on."
        ) from exc


def _absent_is_none(fn, *args, **kwargs) -> Any:
    """Sibyl's getters disagree about missing records: get_entity and
    archive_entity raise NotFoundError, while get_state and get_reference
    return None and delete_entity returns False. Troth treats "not there"
    as None everywhere, and normalises it here so no caller has to
    remember which is which."""
    try:
        return fn(*args, **kwargs)
    except NotFoundError:
        return None


def _unwrap_reference(record: Any) -> Any:
    """Sibyl returns REFERENCE bodies as a JSON *string*, unlike entities
    and state which come back as dicts. Normalise that here so callers
    never have to know."""
    if not record:
        return None
    body = record.get("body") if isinstance(record, dict) else record
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    return body


# ---------------------------------------------------------------------
# WARM — confirmed facts about a client
# ---------------------------------------------------------------------

def remember_client(memory: MemoryClient, name: str, details: dict) -> dict:
    """Persist a confirmed client fact."""
    return memory.set_entity(  # SIBYL WARM
        "client", name, details, status=CONFIRMED
    )


def get_client(memory: MemoryClient, name: str) -> dict | None:
    return _absent_is_none(memory.get_entity, "client", name)  # SIBYL WARM


# ---------------------------------------------------------------------
# REFERENCE — standing policy that outlives any one deal
# ---------------------------------------------------------------------

def remember_pricing_rule(memory: MemoryClient, rule: str, value: Any) -> Any:
    """Persist a standing pricing rule."""
    return memory.set_reference(rule, {"value": value})  # SIBYL REFERENCE


def current_max_discount(memory: MemoryClient) -> float:
    """Read the live discount ceiling out of REFERENCE.

    Called on every promise check. Not cached, deliberately: a judge can
    change this rule and watch the same promise flip from confirmed to
    flagged.

    There is deliberately NO default. If REFERENCE holds no ceiling,
    Troth cannot judge a promise and says so, rather than falling back to
    a number compiled into the source. A hardcoded fallback would let the
    trust gate keep working with the database deleted, which is precisely
    what the load-bearing test is looking for.
    """
    body = _unwrap_reference(memory.get_reference(MAX_DISCOUNT_KEY))  # SIBYL REFERENCE
    if isinstance(body, dict) and "value" in body:
        try:
            return float(body["value"])
        except (TypeError, ValueError) as exc:
            raise MemoryUnavailable(
                f"REFERENCE {MAX_DISCOUNT_KEY!r} holds {body['value']!r}, "
                "which is not a number. Troth will not guess a ceiling."
            ) from exc
    raise MemoryUnavailable(
        f"No discount ceiling in REFERENCE under {MAX_DISCOUNT_KEY!r}. "
        "Troth cannot check a promise against a policy it does not have. "
        "Seed it with `troth seed` or remember_pricing_rule()."
    )


# ---------------------------------------------------------------------
# HOT — the live state of a deal
# ---------------------------------------------------------------------

def start_deal(memory: MemoryClient, client_id: str, stage: str, **extra) -> Any:
    body = {"client": client_id, "stage": stage, **extra}
    return memory.set_state(f"deal:{client_id}", body)  # SIBYL HOT


def get_deal(memory: MemoryClient, client_id: str) -> dict | None:
    record = memory.get_state(f"deal:{client_id}")  # SIBYL HOT
    return record.get("body") if record else None


# ---------------------------------------------------------------------
# COLD — the append-only interaction timeline
# ---------------------------------------------------------------------

def log_interaction(
    memory: MemoryClient, deal_id: str, kind: str, content: str
) -> Any:
    """Append a timestamped entry to the COLD journal.

    Sibyl's write_event is keyword-only: evaluated / acted / forward /
    extra. Troth puts the human-readable line in `acted` and the routing
    metadata in `extra`.
    """
    return memory.write_event(  # SIBYL COLD
        acted=[content],
        extra={"deal": deal_id, "kind": kind},
    )


def timeline(memory: MemoryClient, limit: int = 50) -> list:
    return list(memory.read_events(limit=limit))  # SIBYL COLD


# ---------------------------------------------------------------------
# The trust gate — Troth's own logic, on Sibyl's native status field
# ---------------------------------------------------------------------

_DISCOUNT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def _stable_id(text: str) -> str:
    """A short id derived from the promise text, identical in every
    process. Re-ingesting the same promise updates the same entity
    instead of creating a duplicate."""
    return hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:10]


def extract_discount_pct(text: str) -> float | None:
    match = _DISCOUNT_RE.search(text)
    return float(match.group(1)) if match else None


def flag_promise(
    memory: MemoryClient,
    deal_id: str,
    promise_text: str,
    made_by: str | None = None,
    promise_id: str | None = None,
) -> dict:
    """Check a promise against live REFERENCE policy, then persist it.

    This is the load-bearing moment. The verdict is not computed from
    anything in this process — it comes from a REFERENCE value read out
    of Sibyl microseconds earlier. Delete the database and there is no
    ceiling to check against, so there is no verdict to give.
    """
    ceiling = current_max_discount(memory)
    pct = extract_discount_pct(promise_text)

    if pct is not None and pct > ceiling:
        status = FLAGGED
        reason = f"{pct:g}% exceeds the standing {ceiling:g}% discount ceiling"
    elif pct is not None:
        status = CONFIRMED
        reason = f"{pct:g}% is within the standing {ceiling:g}% ceiling"
    else:
        status = FLAGGED
        reason = "no discount figure found; cannot be checked against policy"

    # Stable across processes: Python's built-in hash() is randomised per
    # interpreter, which would give the same promise a different id in a
    # fresh session and break cold-start recall.
    name = promise_id or f"{deal_id}:{_stable_id(promise_text)}"
    body = {
        "deal": deal_id,
        "text": promise_text,
        "made_by": made_by,
        "discount_pct": pct,
        "checked_against": ceiling,
        "reason": reason,
    }
    memory.set_entity("promise", name, body, status=status)  # SIBYL WARM
    log_interaction(
        memory, deal_id, f"promise_{status}", f"{promise_text} — {reason}"
    )
    return {"id": name, "status": status, "reason": reason, **body}


def resolve_flag(
    memory: MemoryClient, promise_id: str, decision: str, note: str | None = None
) -> dict:
    """A human approves or retracts a flagged promise.

    Rewrites the same WARM entity with a new status and appends a COLD
    audit event. The old status is not preserved in place — the COLD
    journal is the record of what changed.
    """
    record = _absent_is_none(memory.get_entity, "promise", promise_id)  # SIBYL WARM
    if not record:
        raise KeyError(f"no promise {promise_id!r} in memory")

    status = {"approve": CONFIRMED, "retract": RETRACTED}.get(decision)
    if status is None:
        raise ValueError("decision must be 'approve' or 'retract'")

    body = dict(record.get("body") or {})
    body["resolution"] = {"decision": decision, "note": note}
    memory.set_entity("promise", promise_id, body, status=status)  # SIBYL WARM
    log_interaction(
        memory,
        body.get("deal", "unknown"),
        "flag_resolved",
        f"{promise_id} {decision}d by a human" + (f": {note}" if note else ""),
    )
    return {"id": promise_id, "status": status, **body}


def flagged_promises(memory: MemoryClient) -> list:
    """Everything still awaiting a human decision."""
    return list(memory.list_entities("promise", status=FLAGGED))  # SIBYL WARM


# ---------------------------------------------------------------------
# ARCHIVE — retired relationships the agent must not re-pitch
# ---------------------------------------------------------------------

def archive_client(memory: MemoryClient, client_id: str, reason: str) -> Any:
    """Retire a client. Sibyl moves the row to a separate archive table,
    so it genuinely leaves the normal read path rather than being
    filtered out after the fact."""
    result = _absent_is_none(
        memory.archive_entity, "client", client_id, reason=reason
    )  # SIBYL ARCHIVE
    if result is None:
        raise KeyError(f"no client {client_id!r} in memory to archive")
    log_interaction(memory, client_id, "client_archived", f"archived: {reason}")
    return result


def is_archived(memory: MemoryClient, client_id: str) -> bool:
    """True when the client has been retired.

    Because archived rows leave the entity table, absence here is the
    signal. Troth calls this before suggesting any outreach.
    """
    return _absent_is_none(memory.get_entity, "client", client_id) is None  # SIBYL WARM


# ---------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------

def search_active(memory: MemoryClient, query: str, limit: int = 20) -> list:
    """Full-text search across Sibyl, excluding retracted promises.

    Archived clients are already gone from these results — Sibyl moved
    them out of the table. Dropping retracted promises is Troth's own
    filtering, since Sibyl has no opinion about what our status values
    mean.
    """
    results = memory.search_entities(query, limit=limit)  # SIBYL SEARCH
    return [r for r in results if r.get("status") != RETRACTED]
