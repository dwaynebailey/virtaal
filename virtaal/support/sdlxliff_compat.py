#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Trados Studio's segment metadata in SDLXLIFF, which translate-toolkit
reads as plain XLIFF 1.2: a segment's <sdl:seg> in <sdl:seg-defs> holds
its confirmation level (conf), whether it is locked, and where its
translation came from (origin, percent); comments are <mrk
mtype="x-sdl-comment" sdl:cid> pointing into <doc-info><cmt-defs>.

translate-toolkit takes the state from the XLIFF state and approved
attributes, which Trados doesn't write, so every translation showed as
needing work (#4624). Here a trans-unit with one Trados segment takes
its state from conf, and a state change writes conf alone. A
trans-unit with several segments keeps translate-toolkit's state. A
trans-unit with a locked segment keeps its target and state.

The functions below that take a segment work on one <sdl:seg>, so they
also serve a trans-unit split into one unit per segment."""

from translate.storage import xliff
from translate.storage.workflow import StateEnum

from virtaal.support import xliff_compat  # noqa: F401 - patches settarget first

SDL = "{http://sdl.com/FileTypes/SdlXliff/1.0}"

# Okapi's order: rejected, draft, translated, approved. A missing conf
# is Unspecified.
CONF_STATES = {
    "RejectedTranslation": StateEnum.REJECTED,
    "RejectedSignOff": StateEnum.REJECTED + 1,
    "Draft": StateEnum.NEEDS_REVIEW,
    "Translated": StateEnum.UNREVIEWED,
    "ApprovedTranslation": StateEnum.FINAL,
    "ApprovedSignOff": StateEnum.FINAL + 1,
}
STATE_CONFS = {state_n: conf for conf, state_n in CONF_STATES.items()}

STATE = {
    StateEnum.EMPTY: (StateEnum.EMPTY, StateEnum.NEEDS_WORK),
    StateEnum.NEEDS_WORK: (StateEnum.NEEDS_WORK, StateEnum.REJECTED),
    StateEnum.REJECTED: (StateEnum.REJECTED, StateEnum.NEEDS_REVIEW),
    StateEnum.NEEDS_REVIEW: (StateEnum.NEEDS_REVIEW, StateEnum.UNREVIEWED),
    StateEnum.UNREVIEWED: (StateEnum.UNREVIEWED, StateEnum.FINAL),
    StateEnum.FINAL: (StateEnum.FINAL, StateEnum.MAX),
}


def segment_defs(unit):
    """The <sdl:seg> elements of C{unit}'s trans-unit; none for a unit of
    another format."""
    if not isinstance(unit, xliff.xliffunit):
        return []
    seg_defs = unit.xmlelement.find(SDL + "seg-defs")
    return [] if seg_defs is None else seg_defs.findall(SDL + "seg")


def segment_def(unit):
    """The <sdl:seg> of a trans-unit with one Trados segment, or None."""
    segs = segment_defs(unit)
    return segs[0] if len(segs) == 1 else None


def segment_state_n(seg, has_target):
    state_n = CONF_STATES.get(seg.get("conf"))
    if state_n is None:
        return StateEnum.NEEDS_WORK if has_target else StateEnum.EMPTY
    return state_n


def set_segment_state_n(seg, state_n):
    """Write the conf for C{state_n}, one of L{STATE}'s steps or a number
    in L{CONF_STATES}."""
    conf = STATE_CONFS.get(state_n)
    if conf is None:
        seg.attrib.pop("conf", None)
    else:
        seg.set("conf", conf)


def segment_locked(seg):
    return seg.get("locked") == "true"


def is_locked(unit):
    """Whether any of C{unit}'s Trados segments is locked: Virtaal edits a
    trans-unit's segments together."""
    return any(segment_locked(seg) for seg in segment_defs(unit))


def segment_origin(seg):
    """(origin, origin-system, percent), each None when missing."""
    return seg.get("origin"), seg.get("origin-system"), seg.get("percent")


def comments(unit, elements=None):
    """(user, text) of each Trados comment in C{unit}, or only inside
    C{elements}, in document order."""
    if not isinstance(unit, xliff.xliffunit):
        return []
    mrk = unit.namespaced("mrk")
    ids = []
    for element in [unit.xmlelement] if elements is None else elements:
        for marker in element.iter(mrk):
            cid = marker.get(SDL + "cid")
            if marker.get("mtype") == "x-sdl-comment" and cid and cid not in ids:
                ids.append(cid)
    if not ids:
        return []
    root = unit.xmlelement.getroottree().getroot()
    defs = {
        cmt_def.get("id"): cmt_def
        for cmt_def in root.iterfind(f"{SDL}doc-info/{SDL}cmt-defs/{SDL}cmt-def")
    }
    found = []
    for cid in ids:
        for comment in defs[cid].iter(SDL + "Comment") if cid in defs else ():
            text = " ".join((comment.text or "").split())
            if text:
                found.append((comment.get("user"), text))
    return found


class _State:
    """A unit's STATE: Trados's steps for a trans-unit with one segment,
    translate-toolkit's otherwise and on the class."""

    def __init__(self, ttk_state):
        self.ttk_state = ttk_state

    def __get__(self, unit, owner=None):
        if unit is not None and segment_def(unit) is not None:
            return STATE
        return self.ttk_state


_get_state_n = xliff.xliffunit.get_state_n
_set_state_n = xliff.xliffunit.set_state_n
_isapproved = xliff.xliffunit.isapproved
_markapproved = xliff.xliffunit.markapproved
_settarget = xliff.xliffunit.settarget
_set_rich_target = xliff.xliffunit.set_rich_target


def get_state_n(self):
    seg = segment_def(self)
    if seg is None:
        return _get_state_n(self)
    return segment_state_n(seg, bool(self.target))


def set_state_n(self, value):
    if is_locked(self):
        return
    seg = segment_def(self)
    if seg is None:
        _set_state_n(self, value)
        return
    if value not in STATE_CONFS:
        value = self.get_state_id(value)
    if value != self.get_state_n():
        set_segment_state_n(seg, value)


def isapproved(self):
    if segment_def(self) is None:
        return _isapproved(self)
    return self.get_state_n() >= StateEnum.UNREVIEWED


def markapproved(self, value=True):
    # set_state_n() writes conf, which carries approval.
    if segment_def(self) is None and not is_locked(self):
        _markapproved(self, value)


def settarget(self, target, lang="xx", append=False):
    if is_locked(self):
        # A caller may have changed the cached rich target in place.
        self._rich_target = None
        return
    _settarget(self, target, lang, append)


def set_rich_target(self, value, lang="xx", append=False):
    if is_locked(self):
        self._rich_target = None
        return
    _set_rich_target(self, value, lang, append)


xliff.xliffunit.STATE = _State(xliff.xliffunit.STATE)
xliff.xliffunit.get_state_n = get_state_n
xliff.xliffunit.set_state_n = set_state_n
xliff.xliffunit.isapproved = isapproved
xliff.xliffunit.markapproved = markapproved
xliff.xliffunit.settarget = settarget
xliff.xliffunit.set_rich_target = set_rich_target
