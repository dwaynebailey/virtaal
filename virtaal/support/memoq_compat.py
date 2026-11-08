#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""memoQ's bilingual XLIFF 1.2 (.mqxliff, or memoQ's .xlf), in the
xmlns:mq="MQXliff" namespace: a row's status is mq:status, its lock
mq:locked, its match rate mq:percent, and its comments
<mq:comments>. memoQ doesn't write XLIFF's state or approved, so
translate-toolkit showed every translation as fuzzy (#4734).

memoQ keeps tracked changes in a target as <mrk mtype="x-mq-tc"
mq:tctype="ins|del">. A deletion isn't part of the translation, and an
insertion is plain text, until memoQ accepts or rejects them (#4737).
L{accepted} is the target as shown; L{merge_target} writes an edited
target back with the markup of the text that stayed.

L{virtaal.support.catxliff_compat} puts these into translate-toolkit's
XLIFF units."""

import copy
import difflib

from lxml import etree
from translate.storage.workflow import StateEnum

from virtaal.support import sdlxliff_compat

NAMESPACE = "MQXliff"
MQ = "{%s}" % NAMESPACE

# NotStarted is untranslated, or needs work if it has a target. memoQ
# documents no list; the ones not seen in a real file (Edited,
# AssembledFromFragments, Rejected, MachineTranslated) are from memoQ's
# UI.
STATUS_STATES = {
    "Rejected": StateEnum.REJECTED,
    "PreTranslated": StateEnum.NEEDS_REVIEW,
    "MachineTranslated": StateEnum.NEEDS_REVIEW,
    "AssembledFromFragments": StateEnum.NEEDS_REVIEW,
    "PartiallyEdited": StateEnum.NEEDS_REVIEW,
    "Edited": StateEnum.NEEDS_REVIEW,
    "ManuallyConfirmed": StateEnum.UNREVIEWED,
    "Reviewer1Confirmed": StateEnum.FINAL,
    "Proofread": StateEnum.FINAL + 1,
}
# What a state set in Virtaal writes.
STATE_STATUSES = {
    StateEnum.EMPTY: "NotStarted",
    StateEnum.NEEDS_WORK: "NotStarted",
    StateEnum.REJECTED: "Rejected",
    StateEnum.NEEDS_REVIEW: "PartiallyEdited",
    StateEnum.UNREVIEWED: "ManuallyConfirmed",
    StateEnum.FINAL: "Reviewer1Confirmed",
    StateEnum.FINAL + 1: "Proofread",
}
# Trados's steps: memoQ has the same.
STATE = sdlxliff_compat.STATE


def state_n(unit, has_target):
    state = STATUS_STATES.get(unit.xmlelement.get(MQ + "status"))
    if state is None:
        return StateEnum.NEEDS_WORK if has_target else StateEnum.EMPTY
    return state


def set_state_n(unit, value):
    """Write the status for C{value}, a step of L{STATE} or a number in
    L{STATE_STATUSES}."""
    if value not in STATE_STATUSES:
        value = unit.get_state_id(value)
    unit.xmlelement.set(MQ + "status", STATE_STATUSES[value])


# Statuses of a translation memoQ inserted, which an edit makes the
# translator's.
INSERTED = {"PreTranslated", "MachineTranslated", "AssembledFromFragments"}


def mark_edited(unit):
    """An inserted translation the user changed is PartiallyEdited, as
    memoQ marks it, still to confirm."""
    if unit.xmlelement.get(MQ + "status") in INSERTED:
        unit.xmlelement.set(MQ + "status", "PartiallyEdited")


def is_locked(unit):
    return unit.xmlelement.get(MQ + "locked") in ("locked", "true")


def match_rate(unit):
    """The match rate memoQ inserted the translation with, or None:
    101 is a context match."""
    percent = unit.xmlelement.get(MQ + "percent")
    return percent if percent and percent != "0" else None


def last_user(unit):
    return unit.xmlelement.get(MQ + "lastchanginguser") or None


def comments(unit):
    """(user, text) of the unit's memoQ comments that aren't deleted,
    leaving out those its notes already show."""
    notes = set(unit.getnotes().split("\n"))
    found = []
    for comment in unit.xmlelement.iterfind(f"{MQ}comments/{MQ}comment"):
        text = " ".join((comment.text or "").split())
        if text and comment.get("deleted") != "true" and text not in notes:
            found.append((comment.get("creatoruser"), text))
    return found


# Tracked changes


def _tc_kind(element):
    if not isinstance(element.tag, str) or etree.QName(element).localname != "mrk":
        return None
    if element.get("mtype") != "x-mq-tc":
        return None
    return element.get(MQ + "tctype")


def has_tracked_changes(node):
    return node is not None and any(_tc_kind(e) for e in node.iterdescendants())


def _append_text(parent, text):
    if not text:
        return
    if len(parent):
        parent[-1].tail = (parent[-1].tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _flatten(node):
    """node's content, in place, with deletions left out and insertions'
    content moved up into their parent."""
    for child in list(node):
        kind = _tc_kind(child)
        if kind is None:
            _flatten(child)
            continue
        index = node.index(child)
        node.remove(child)
        holder = node.makeelement("holder")
        if kind != "del":
            _flatten(child)
            holder.text = child.text
            holder.extend(list(child))
        _append_text(holder, child.tail)
        before = node[index - 1] if index else None
        if before is None:
            node.text = (node.text or "") + (holder.text or "")
        else:
            before.tail = (before.tail or "") + (holder.text or "")
        for offset, moved in enumerate(list(holder)):
            node.insert(index + offset, moved)


def accepted(node):
    """A copy of target C{node} as it reads with its tracked changes
    accepted: no deleted text, inserted text plain."""
    node = copy.deepcopy(node)
    tail = node.tail
    _flatten(node)
    node.tail = tail
    return node


def _key(atom):
    if isinstance(atom, str):
        return atom
    # strelem_to_xml() leaves tags out of the namespace until saving.
    return (etree.QName(atom).localname, atom.get("id"), etree.tostring(atom, method="text"))


def _detached(element):
    element = copy.deepcopy(element)
    element.tail = None
    return element


def _atoms(node, container, atoms, anchors):
    """Append the visible pieces of node's content, (char or element,
    the insertion it is in), to atoms, and (position, deletion) to
    anchors."""
    atoms.extend((char, container) for char in node.text or "")
    for child in node:
        kind = _tc_kind(child) if container is None else None
        if kind == "del":
            anchors.append((len(atoms), _detached(child)))
        elif kind is not None:
            _atoms(child, child, atoms, anchors)
        else:
            atoms.append((_detached(child), container))
        atoms.extend((char, container) for char in child.tail or "")


def _place(old, new):
    """The pieces of the content of new, in order: (piece, the insertion
    of old it stays in, or None), and old's deletions as (deletion,
    False), each beside the text it was beside."""
    old_atoms, anchors = [], []
    _atoms(old, None, old_atoms, anchors)
    new_atoms = []
    _atoms(new, None, new_atoms, [])
    deletions = {}
    for position, deletion in anchors:
        deletions.setdefault(position, []).append((deletion, False))
    matcher = difflib.SequenceMatcher(
        None, [_key(a) for a, _c in old_atoms], [_key(a) for a, _c in new_atoms], autojunk=False
    )
    placed = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        for i in range(i1, i2):
            placed.extend(deletions.pop(i, []))
            if tag == "equal":
                placed.append(old_atoms[i])
        if tag != "equal":
            placed.extend((atom, None) for atom, _c in new_atoms[j1:j2])
    placed.extend(deletions.pop(len(old_atoms), []))
    return placed


def merge_target(old, new):
    """Replace target C{old}'s content with the content of C{new} (an
    element holding the edited translation), keeping memoQ's tracked
    deletions and insertions around the text that didn't change."""
    placed = _place(old, new)
    for child in list(old):
        old.remove(child)
    old.text = None
    group, group_of = None, None
    for atom, container in placed:
        parent = old
        if container is False:
            old.append(atom)
            group_of = None
            continue
        if container is not None:
            if group_of is not container:
                group = old.makeelement(container.tag, dict(container.attrib))
                old.append(group)
                group_of = container
            parent = group
        else:
            group_of = None
        if isinstance(atom, str):
            _append_text(parent, atom)
        else:
            parent.append(atom)
