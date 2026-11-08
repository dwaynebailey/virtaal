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
target back with the markup of the text that stayed, and the edit
tracked as the user's own insertions and deletions, as memoQ tracks a
reviewer's.

L{virtaal.support.catxliff_compat} puts these into translate-toolkit's
XLIFF units."""

import copy
import difflib
import re

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


def tracks_changes(unit):
    """Whether edits to C{unit}'s target are tracked: its target already
    has tracked changes, or memoQ exported the document with change
    tracking on (mq:changetrackingenabledstates holds an On)."""
    if has_tracked_changes(unit.get_target_dom()):
        return True
    root = unit.xmlelement.getroottree().getroot()
    info = next(root.iter(MQ + "docinformation"), None)
    states = "" if info is None else info.get(MQ + "changetrackingenabledstates") or ""
    return "On" in states.split(",")


def _key(atom):
    if isinstance(atom, str):
        return atom
    # strelem_to_xml() leaves tags out of the namespace until saving.
    return (etree.QName(atom).localname, atom.get("id"), etree.tostring(atom, method="text"))


def _detached(element):
    element = copy.deepcopy(element)
    element.tail = None
    return element


# The pieces an edit is tracked in, as a reviewer reads it: words, runs
# of spaces, and each other character.
_WORD = re.compile(r"\w+|\s+|[^\w\s]")

# Where a piece of the edited target goes, besides an existing marker.
_NEW_INS, _NEW_DEL, _ANCHOR = "ins", "del", "anchor"


class _Tracker:
    """Merges an edited target into one with memoQ's tracked changes,
    tracking the edit as C{user}'s, at C{time}."""

    def __init__(self, old, user, time):
        self.old, self.user, self.time = old, user, time

    def own(self, marker):
        return marker.get(MQ + "tcuser") == self.user

    def atoms(self, node, container, atoms, anchors):
        """Append the pieces of node's content, (word or element, the
        marker it is in), to atoms: what shows, and the user's own
        deletions, which an undo can bring back. Other users'
        deletions go to anchors as (position, deletion)."""
        atoms.extend((word, container) for word in _WORD.findall(node.text or ""))
        for child in node:
            kind = _tc_kind(child) if container is None else None
            if kind == "del" and not self.own(child):
                anchors.append((len(atoms), _detached(child)))
            elif kind is not None:
                self.atoms(child, child, atoms, anchors)
            else:
                atoms.append((_detached(child), container))
            atoms.extend((word, container) for word in _WORD.findall(child.tail or ""))

    def deleted(self, atom, container):
        """What deleting a piece does: the user's own insertion goes, a
        deletion stays, anything else becomes the user's deletion."""
        if container is None or _tc_kind(container) == "ins" and not self.own(container):
            return [(atom, _NEW_DEL)]
        if _tc_kind(container) == "del":
            return [(atom, container)]
        return []

    def place(self, new):
        old_atoms, anchors = [], []
        self.atoms(self.old, None, old_atoms, anchors)
        new_atoms = []
        self.atoms(new, None, new_atoms, [])
        deletions = {}
        for position, deletion in anchors:
            deletions.setdefault(position, []).append((deletion, _ANCHOR))
        matcher = difflib.SequenceMatcher(
            None, [_key(a) for a, _c in old_atoms], [_key(a) for a, _c in new_atoms], autojunk=False
        )
        placed = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            for i in range(i1, i2):
                placed.extend(deletions.pop(i, []))
                atom, container = old_atoms[i]
                if tag != "equal":
                    placed.extend(self.deleted(atom, container))
                elif container is not None and _tc_kind(container) == "del":
                    # The user's own deletion, typed back: undone.
                    placed.append((atom, None))
                else:
                    placed.append((atom, container))
            if tag != "equal":
                placed.extend((atom, _NEW_INS) for atom, _c in new_atoms[j1:j2])
        placed.extend(deletions.pop(len(old_atoms), []))
        return placed

    def kind(self, key):
        """ "ins" or "del" for the user's own markers, new or old."""
        if key in (_NEW_INS, _NEW_DEL):
            return key
        if key is None or isinstance(key, str) or not self.own(key):
            return None
        return _tc_kind(key)

    def marker(self, key):
        if isinstance(key, str):
            return self.old.makeelement(
                "{%s}mrk" % etree.QName(self.old).namespace,
                {
                    "mtype": "x-mq-tc",
                    MQ + "tctype": key,
                    MQ + "tcuser": self.user,
                    MQ + "tctime": self.time,
                },
            )
        return self.old.makeelement(key.tag, dict(key.attrib))

    def write(self, placed):
        old = self.old
        for child in list(old):
            old.remove(child)
        old.text = None
        group = None
        for atom, key in placed:
            if key == _ANCHOR:
                old.append(atom)
                group = None
                continue
            parent = old
            if key is None:
                group = None
            else:
                group = self.grouped(group, key)
                parent = group.element
            if isinstance(atom, str):
                _append_text(parent, atom)
            else:
                parent.append(atom)

    def grouped(self, group, key):
        """The marker a piece with C{key} goes in: C{group}'s, if it is
        the same marker, or the user's of the same kind with something
        new in it; else a new one."""
        new = key in (_NEW_INS, _NEW_DEL)
        if group is not None and (
            key is group.key
            or (self.kind(key) and self.kind(key) == self.kind(group.key) and (new or group.new))
        ):
            group.new = group.new or new
        else:
            group = _Group(key, self.marker(key), new)
            self.old.append(group.element)
        if new:
            # The user's marker, new or grown: their name, now.
            group.element.set(MQ + "tcuser", self.user)
            group.element.set(MQ + "tctime", self.time)
        return group


class _Group:
    def __init__(self, key, element, new):
        self.key, self.element, self.new = key, element, new


def merge_target(old, new, user, time):
    """Replace target C{old}'s content with the content of C{new} (an
    element holding the edited translation) as memoQ tracks it: text
    the edit took out is C{user}'s deletion, and text it added C{user}'s
    insertion, at C{time} (UTC, as 2019-05-05T05:46:18Z). Deleting the
    user's own insertion takes it out, and typing back the user's own
    deletion undoes it. Other users' deletions stay beside the text they
    were beside."""
    tracker = _Tracker(old, user, time)
    tracker.write(tracker.place(new))
