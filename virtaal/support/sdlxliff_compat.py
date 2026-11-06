#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Trados Studio's segments in SDLXLIFF, which translate-toolkit reads
as plain XLIFF 1.2. Trados splits a trans-unit into sentence segments,
each a <mrk mtype="seg" mid> in <seg-source> and <target>, and keeps a
segment's confirmation level (conf), lock and origin in its <sdl:seg> in
<sdl:seg-defs>; comments are <mrk mtype="x-sdl-comment" sdl:cid>
pointing into <doc-info><cmt-defs>.

Each segment is shown as its own unit, a L{SegmentUnit} over the shared
trans-unit, whose source and target are the segment's markers (#4625):
Trados translates and confirms each one separately. Text and tags
between the segments stay as they are.

translate-toolkit takes the state from the XLIFF state and approved
attributes, which Trados doesn't write, so every translation showed as
needing work (#4624). A segment takes its state from conf, and a state
change writes conf alone. A locked segment keeps its target and state.

A trans-unit whose segments can't be told apart stays one
translate-toolkit unit, with Trados's state only for one segment; a
lock on any of its segments locks it."""

import copy
import re

from lxml import etree
from translate.misc.xml_helpers import clear_content
from translate.storage import lisa, xliff
from translate.storage.placeables import StringElem
from translate.storage.placeables.lisa import strelem_to_xml, xml_to_strelem
from translate.storage.workflow import StateEnum

from virtaal.support import lisa_compat, xliff_compat  # noqa: F401 - patch parse and settarget first

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


def seg_markers(unit, node):
    """The <mrk mtype="seg"> segment markers in C{node}, in order."""
    if node is None:
        return []
    return [m for m in node.iter(unit.namespaced("mrk")) if m.get("mtype") == "seg"]


def _seg_id(mid):
    # A marker's mid is the segment id as an XML name: "1 a" is "1_x0020_a".
    return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda match: chr(int(match[1], 16)), mid)


def segment_defs(unit):
    """The <sdl:seg> elements of C{unit}'s trans-unit, or of its own
    segment; none for a unit of another format."""
    if isinstance(unit, SegmentUnit):
        seg = unit.segment_def()
        return [] if seg is None else [seg]
    if not isinstance(unit, xliff.xliffunit):
        return []
    seg_defs = unit.xmlelement.find(SDL + "seg-defs")
    return [] if seg_defs is None else seg_defs.findall(SDL + "seg")


def segment_def(unit):
    """The <sdl:seg> of a unit with one Trados segment, or None."""
    if isinstance(unit, SegmentUnit):
        return unit.segment_def()
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
    """Whether C{unit}'s Trados segment is locked; for a trans-unit kept
    whole, whether any of its segments is, as they are edited together."""
    return any(segment_locked(seg) for seg in segment_defs(unit))


def segment_origin(seg):
    """(origin, origin-system, percent), each None when missing."""
    return seg.get("origin"), seg.get("origin-system"), seg.get("percent")


def comments(unit, elements=None):
    """(user, text) of each Trados comment in C{unit}, or only inside
    C{elements}, in document order. A segment's are those inside its
    markers."""
    if not isinstance(unit, xliff.xliffunit):
        return []
    if elements is None and isinstance(unit, SegmentUnit):
        elements = [e for e in (unit.get_source_dom(), unit.get_target_dom()) if e is not None]
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


# A line break and the indentation after it, and the spaces before it.
_INDENT = re.compile(r"[ \t]*\n[ \t]+")


def _unindent(text, start, end):
    if not text or "\n" not in text:
        return text
    if not text.strip():
        return None
    if start:
        text = re.sub(r"^[ \t]*\n[ \t]*", "", text)
    if end:
        text = re.sub(r"[ \t]*\n[ \t]*$", "", text)
    return _INDENT.sub(" ", text)


def _segment(node):
    """A copy of segment marker C{node} without a pretty-printed file's
    indentation, which the file keeps: a line break and indentation
    alone between two tags, or at the start or end of the segment, is
    left out, and one inside text is a space. Other whitespace in a
    segment is its text."""
    node = copy.deepcopy(node)
    node.tail = None
    last = node[-1] if len(node) else node
    node.text = _unindent(node.text, True, last is node)
    for element in node.iterdescendants():
        element.text = _unindent(element.text, False, False)
        element.tail = _unindent(element.tail, False, element is last)
    return node


def _text(node):
    return "" if node is None else str(_segment(node).xpath("string()"))


def _content(node):
    # strelem_to_xml() leaves tags out of the namespace until saving.
    return [
        (etree.QName(e).localname, dict(e.attrib), e.text, e.tail) for e in node.iter(etree.Element)
    ]


def _comment_markers(unit, node):
    mrk = unit.namespaced("mrk")
    return [m for m in node.iter(mrk) if m.get("mtype") == "x-sdl-comment" and m.get(SDL + "cid")]


def _keep_comments(unit, marker, old):
    """Put back around C{marker}'s new content the comment markers of
    C{old} it lost, so a retyped segment keeps its comments."""
    kept = {m.get(SDL + "cid") for m in _comment_markers(unit, marker)}
    # Innermost first, so they stay in document order.
    for comment in reversed(old):
        if comment.get(SDL + "cid") in kept:
            continue
        wrapper = marker.makeelement(comment.tag, dict(comment.attrib))
        wrapper.text, marker.text = marker.text, None
        for child in list(marker):
            wrapper.append(child)
        marker.append(wrapper)
        kept.add(comment.get(SDL + "cid"))


def _rich(node):
    return StringElem() if node is None else xml_to_strelem(_segment(node), "preserve")


class SegmentUnit(xliff.xliffunit):
    """One Trados segment of a trans-unit: its source and target are the
    segment's markers, its state, lock and origin its <sdl:seg>.

    The units of a trans-unit share its element, so notes, context and
    whether it is translatable are the trans-unit's."""

    STATE = STATE

    @classmethod
    def from_unit(cls, unit, mid, split):
        """The segment C{mid} of C{unit}'s trans-unit. C{split}: the
        trans-unit has other segments, so the id names the segment."""
        segment = cls.createfromxmlElement(unit.xmlelement)
        segment.namespace = unit.namespace
        segment._store = getattr(unit, "_store", None)
        segment.mid = mid
        segment.split = split
        return segment

    def __eq__(self, other):
        if isinstance(other, SegmentUnit) and other.xmlelement is self.xmlelement:
            return other.mid == self.mid
        if not isinstance(other, lisa.LISAunit):
            return NotImplemented
        return (self.source, self.target) == (other.source, other.target)

    __hash__ = None

    def copy(self):
        unit = xliff.xliffunit.createfromxmlElement(copy.deepcopy(self.xmlelement))
        unit.namespace = self.namespace
        return SegmentUnit.from_unit(unit, self.mid, self.split)

    def getid(self):
        uid = super().getid()
        return f"{uid}{xliff.ID_SEPARATOR}{self.mid}" if self.split else uid

    def _marker(self, name):
        node = self.xmlelement.find(self.namespaced(name))
        return next((m for m in seg_markers(self, node) if m.get("mid") == self.mid), None)

    def get_source_dom(self):
        return self._marker("seg-source")

    def set_source_dom(self, dom_node):
        raise NotImplementedError("A Trados segment's source is its marker")

    source_dom = property(get_source_dom, set_source_dom)

    def get_target_dom(self, lang=None):
        return self._marker("target")

    target_dom = property(get_target_dom)

    def getlanguageNodes(self):
        return [node for node in (self.get_source_dom(), self.get_target_dom()) if node is not None]

    @property
    def source(self):
        return _text(self.get_source_dom())

    @source.setter
    def source(self, source):
        self.setsource(source)

    def setsource(self, text, sourcelang=None):
        marker = self.get_source_dom()
        clear_content(marker)
        marker.text = text or None

    @property
    def rich_source(self):
        return [_rich(self.get_source_dom())]

    @rich_source.setter
    def rich_source(self, value):
        self.set_rich_source(value)

    def set_rich_source(self, value, sourcelang="en"):
        marker = self.get_source_dom()
        clear_content(marker)
        strelem_to_xml(marker, value[0])

    def gettarget(self, lang=None):
        marker = self.get_target_dom()
        return None if marker is None else _text(marker)

    @property
    def target(self):
        return self.gettarget()

    @target.setter
    def target(self, target):
        self.settarget(target)

    def get_rich_target(self, lang=None):
        if self._rich_target is None:
            self._rich_target = [_rich(self.get_target_dom())]
        return self._rich_target

    @property
    def rich_target(self):
        return self.get_rich_target()

    @rich_target.setter
    def rich_target(self, value):
        self.set_rich_target(value)

    def _new_target_marker(self):
        """An empty target marker for this segment, placed in the order of
        the segments in <seg-source>."""
        seg_source = self.xmlelement.find(self.namespaced("seg-source"))
        target = self.xmlelement.find(self.namespaced("target"))
        if target is None:
            # Trados's own layout: the source's, with empty segments.
            target = copy.deepcopy(seg_source)
            target.tag = self.namespaced("target")
            for marker in seg_markers(self, target):
                clear_content(marker)
            seg_source.addnext(target)
            target.tail = seg_source.tail
            return self.get_target_dom()
        mids = [m.get("mid") for m in seg_markers(self, seg_source)]
        index = mids.index(self.mid)
        existing = {m.get("mid"): m for m in seg_markers(self, target)}
        marker = target.makeelement(self.namespaced("mrk"), {"mtype": "seg", "mid": self.mid})
        before = next((existing[mid] for mid in reversed(mids[:index]) if mid in existing), None)
        after = next((existing[mid] for mid in mids[index + 1 :] if mid in existing), None)
        if before is not None:
            # The text after the segment before stays before this one.
            before.addnext(marker)
        elif after is not None:
            after.addprevious(marker)
        else:
            target.append(marker)
        return marker

    def set_rich_target(self, value, lang="xx", append=False):
        self._rich_target = None
        if is_locked(self):
            return
        marker = self.get_target_dom()
        new = etree.Element(self.namespaced("mrk"), {} if marker is None else dict(marker.attrib))
        if value is not None:
            strelem_to_xml(new, value[0])
        if marker is None:
            if new.text is None and not len(new):
                return
            marker = self._new_target_marker()
        elif _content(new) == _content(_segment(marker)):
            # Showing a unit sets its target: keep an indented segment.
            return
        comments = _comment_markers(self, marker)
        clear_content(marker)
        if value is not None:
            strelem_to_xml(marker, value[0])
        _keep_comments(self, marker, comments)

    def settarget(self, target, lang="xx", append=False):
        self._rich_target = None
        if is_locked(self) or (self.target or "") == (target or ""):
            return
        marker = self.get_target_dom()
        if marker is None:
            marker = self._new_target_marker()
        comments = _comment_markers(self, marker)
        clear_content(marker)
        marker.text = str(target) if target else None
        _keep_comments(self, marker, comments)
        if target:
            self.marktranslated()

    def segment_def(self):
        """This segment's <sdl:seg>, or None."""
        seg_defs = self.xmlelement.find(SDL + "seg-defs")
        segs = [] if seg_defs is None else seg_defs.findall(SDL + "seg")
        ids = (self.mid, _seg_id(self.mid))
        return next((seg for seg in segs if seg.get("id") in ids), None)

    def _new_segment_def(self):
        seg_defs = self.xmlelement.find(SDL + "seg-defs")
        if seg_defs is None:
            seg_defs = self.xmlelement.makeelement(SDL + "seg-defs")
            last = self.xmlelement.find(self.namespaced("target"))
            if last is None:
                last = self.xmlelement.find(self.namespaced("seg-source"))
            last.addnext(seg_defs)
        return etree.SubElement(seg_defs, SDL + "seg", id=_seg_id(self.mid))

    def get_state_n(self):
        seg = self.segment_def()
        if seg is None:
            return StateEnum.NEEDS_WORK if self.target else StateEnum.EMPTY
        return segment_state_n(seg, bool(self.target))

    def set_state_n(self, value):
        if is_locked(self):
            return
        if value not in STATE_CONFS:
            value = self.get_state_id(value)
        if value == self.get_state_n():
            return
        seg = self.segment_def()
        if seg is None and value in STATE_CONFS:
            seg = self._new_segment_def()
        if seg is not None:
            set_segment_state_n(seg, value)

    def isapproved(self):
        return self.get_state_n() >= StateEnum.UNREVIEWED

    def markapproved(self, value=True):
        # set_state_n() writes conf, which carries approval.
        pass


def segment_units(unit):
    """The units to show for C{unit}'s trans-unit: one per Trados segment,
    or C{unit} itself."""
    element = unit.xmlelement
    root = element.getroottree().getroot()
    if SDL[1:-1] not in root.nsmap.values() and element.find(SDL + "seg-defs") is None:
        return [unit]
    mids = [m.get("mid") for m in seg_markers(unit, element.find(unit.namespaced("seg-source")))]
    if not mids or None in mids or len(set(mids)) != len(mids):
        return [unit]
    target = element.find(unit.namespaced("target"))
    if target is not None:
        markers = seg_markers(unit, target)
        if not markers and (len(target) or _text(target).strip()):
            # A target Trados didn't segment: show it all.
            return [unit]
        if len(mids) == 1 and markers and xliff_compat.segment_marker(unit, target) is None:
            return [unit]
    return [SegmentUnit.from_unit(unit, mid, len(mids) > 1) for mid in mids]


_parse = xliff.xlifffile.parse


def parse(self, xml, *args, **kwargs):
    _parse(self, xml, *args, **kwargs)
    self.units = [segment for unit in self.units for segment in segment_units(unit)]


xliff.xlifffile.parse = parse
