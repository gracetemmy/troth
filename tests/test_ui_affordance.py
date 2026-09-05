"""Guards against a bug that shipped once already.

Three of the five memory-map nodes had cursor:pointer and no click
handler, so they invited a click and did nothing. These tests pin the
invariant that made that possible: an element may only *look*
interactive if something actually wires it up.

They read the dashboard HTML as text rather than driving a browser, so
they run anywhere pytest does.
"""

import pathlib
import re

import pytest

HTML = pathlib.Path(__file__).resolve().parent.parent / "troth-v2-lethe-blue.html"
SOURCE = HTML.read_text(encoding="utf-8")
SCRIPT = re.search(r"<script>(.*)</script>", SOURCE, re.S).group(1)


def test_map_nodes_are_not_pointered_by_default():
    """The pointer must come from .acts, which is added beside the
    handler — never from a class every node already carries."""
    assert ".map-node.dyn{cursor:pointer}" not in SOURCE
    assert ".map-node.acts{cursor:pointer}" in SOURCE


def test_acts_class_is_only_added_with_a_handler():
    """`.acts`, the onclick and the keyboard handler must live in one
    block, so they cannot drift apart in a later edit."""
    block = re.search(r"if \(!act\) return;(.*?)\}\);", SCRIPT, re.S)
    assert block, "the single wiring block is gone — was it split up?"
    body = block.group(1)
    assert "el.onclick = act" in body
    assert "classList.add('acts')" in body
    assert "tabindex" in body


def test_every_map_slot_can_act():
    """Each slot the map renders must carry a record or a view. A slot
    with neither is a node that renders and does nothing."""
    slots = re.search(r"const slots = \[(.*?)\]\.filter\(Boolean\);", SCRIPT, re.S)
    assert slots, "renderMap's slot list moved"
    entries = re.findall(r"\{cls:'mn-[a-e]'.*?\}", slots.group(1), re.S)
    assert len(entries) == 5, f"expected 5 map slots, found {len(entries)}"
    for entry in entries:
        assert ("rec:" in entry) or ("view:" in entry), f"dead node: {entry[:60]}"


@pytest.mark.parametrize("label", ["VIEW ALL"])
def test_navigation_shaped_labels_are_wired(label):
    """An arrow promises navigation. It has to deliver some."""
    match = re.search(r"<[^>]*>([^<]*" + re.escape(label) + r"[^<]*)</", SOURCE)
    assert match, f"{label!r} not found"
    tag = SOURCE[SOURCE.rindex("<", 0, match.start(1)):match.start(1)]
    assert "onclick" in tag, f"{label!r} looks clickable but has no handler"


def test_keyboard_users_can_reach_an_acting_node():
    assert "role', 'button'" in SCRIPT
    assert "e.key === 'Enter'" in SCRIPT
