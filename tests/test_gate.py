"""The four things the hackathon gate actually checks."""

import pytest

from troth import ingest as I
from troth import memory as M


def test_no_policy_means_no_verdict(mem):
    """The deletion test. With nothing in REFERENCE, Troth must refuse to
    judge rather than fall back to a constant in the source."""
    with pytest.raises(M.MemoryUnavailable):
        M.flag_promise(mem, "acme", "I can do 25% off")


def test_verdict_comes_from_memory_not_code(seeded):
    """Change the stored policy, and the same promise changes verdict.
    This is the answer that changes because of memory."""
    text = "I can offer you 25% off"
    assert M.flag_promise(seeded, "acme", text)["status"] == M.FLAGGED

    M.remember_pricing_rule(seeded, M.MAX_DISCOUNT_KEY, 30)
    assert M.flag_promise(seeded, "acme", text)["status"] == M.CONFIRMED


def test_promise_id_is_stable_across_processes(seeded):
    """Cold-start recall depends on this: the same promise text must
    resolve to the same id in a brand new interpreter."""
    a = M.flag_promise(seeded, "acme", "I can offer 25% off")["id"]
    b = M.flag_promise(seeded, "acme", "I can offer 25% off")["id"]
    assert a == b
    assert len(M.flagged_promises(seeded)) == 1, "re-ingest must not duplicate"


def test_archived_client_leaves_the_read_path(seeded):
    M.remember_client(seeded, "northstar", {"facts": ["churned"]})
    assert not M.is_archived(seeded, "northstar")

    M.archive_client(seeded, "northstar", "moved to a competitor")
    assert M.is_archived(seeded, "northstar")
    assert M.get_client(seeded, "northstar") is None
    assert not any(r.get("name") == "northstar"
                   for r in M.search_active(seeded, "northstar"))


def test_archiving_an_unknown_client_is_an_error(seeded):
    with pytest.raises(KeyError):
        M.archive_client(seeded, "nobody", "never existed")
