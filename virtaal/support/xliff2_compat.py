#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""XLIFF 2 files open with translate-toolkit's xliff2.Xliff2File, which its
factory never picks: it opens every .xlf as XLIFF 1 (#4366).

Xliff2Unit's state table has an empty range for "translated" and sorts
"reviewed" below it, and it never reads the segment's state attribute,
so here a segment's state maps to Virtaal's Untranslated, Needs work
(initial with a target), Translated and Reviewed (reviewed or final).
Its notes are read from and written to the <unit>, where XLIFF 2 keeps
them, not inside the <segment>.

A file is saved as it was read, apart from the <unit>s that changed
(L{xmlinplace}).

XLIFF 2.2's plural, gender and select module keeps a message's variants
as the segments of one <unit>: the unit's pgs:switch names the selectors
(plural:count gender:host), and each segment's pgs:case the values it is
for (one, few, feminine, 0, "feminine 0"). Such a unit is one plural unit
here (L{VariantUnit}), each case a form labelled with it, unless it has
more cases than Virtaal shows boxes: then each case is a unit of its
own, with the case as its context."""

import copy
import os
import re

from lxml import etree
from translate.misc.multistring import multistring
from translate.misc.xml_helpers import getXMLspace
from translate.storage import factory, xliff2
from translate.storage.placeables import StringElem
from translate.storage.placeables.lisa import strelem_to_xml, xml_to_strelem
from translate.storage.workflow import StateEnum

from virtaal.support import xmlinplace

_ROOT = re.compile(rb"<(?:[\w.-]+:)?xliff\b[^>]*>")
# Not a namespaced attribute: ITS tools put its:version="2.0" on XLIFF 1.2.
_VERSION_2 = re.compile(rb"""(?<![\w:.-])version\s*=\s*["']2\.""")
_NAMESPACE_2 = re.compile(rb"urn:oasis:names:tc:xliff:document:2\.")


def is_xliff2(head):
    """Whether C{head}, the start of a file, is an XLIFF 2 document."""
    root = _ROOT.search(head)
    if root is None:
        return False
    return bool(_VERSION_2.search(root.group()) or _NAMESPACE_2.search(root.group()))


def _head(storefile, size=2048):
    if isinstance(storefile, (str, os.PathLike)):
        try:
            with open(storefile, "rb") as f:
                return f.read(size)
        except OSError:
            return b""
    try:
        position = storefile.tell()
        head = storefile.read(size)
        storefile.seek(position)
    except (AttributeError, OSError, ValueError):
        return b""
    return head if isinstance(head, bytes) else b""


def _name(storefile, ignore):
    if isinstance(storefile, (str, os.PathLike)):
        name = os.fspath(storefile)
    else:
        name = getattr(storefile, "name", None)
        if not isinstance(name, str):
            return None
    if ignore and name.endswith(ignore):
        name = name[: -len(ignore)]
    return name


_getclass = factory.getclass


def getclass(
    storefile,
    localfiletype=None,
    ignore=None,
    classes=None,
    classes_str=None,
    hiddenclasses=None,
):
    if localfiletype is None and not classes and classes_str in (None, factory._classes_str):
        name = _name(storefile, ignore)
        ext = os.path.splitext(name)[1][1:].lower() if name else ""
        if ext in ("xlf", "xliff") and is_xliff2(_head(storefile)):
            return xliff2.Xliff2File
    return _getclass(storefile, localfiletype, ignore, classes, classes_str, hiddenclasses)


factory.getclass = getclass


Xliff2Unit = xliff2.Xliff2Unit

Xliff2Unit.STATE = {
    StateEnum.EMPTY: (StateEnum.EMPTY, StateEnum.NEEDS_WORK),
    StateEnum.NEEDS_WORK: (StateEnum.NEEDS_WORK, StateEnum.UNREVIEWED),
    StateEnum.UNREVIEWED: (StateEnum.UNREVIEWED, StateEnum.FINAL),
    StateEnum.FINAL: (StateEnum.FINAL, StateEnum.MAX),
}


def get_state_n(self):
    if not self.target:
        return StateEnum.EMPTY
    xmlstate = self.xmlelement.get("state")
    if xmlstate in ("reviewed", "final"):
        return StateEnum.FINAL
    if xmlstate == "initial":
        return StateEnum.NEEDS_WORK
    return StateEnum.UNREVIEWED


def set_state_n(self, value):
    before = self.xmlelement.get("state")
    if value >= StateEnum.FINAL:
        if before != "final":
            self.xmlelement.set("state", "reviewed")
    elif value >= StateEnum.UNREVIEWED:
        self.xmlelement.set("state", "translated")
    elif value >= StateEnum.NEEDS_WORK or before:
        self.xmlelement.set("state", "initial")
    if self.xmlelement.get("state") != before:
        # Its subState was for the state it had.
        self.xmlelement.attrib.pop("subState", None)


def _notes(self, create=False):
    unit = self.getunitelement()
    notes = unit.find(self.namespaced("notes"))
    if notes is None and create:
        notes = etree.Element(self.namespaced("notes"))
        unit.insert(0, notes)
    return notes


def getnotes(self, origin=None):
    notes = _notes(self)
    if notes is None:
        return ""
    return "\n".join(
        note.text
        for note in notes.iterchildren(self.namespaced("note"))
        if note.text and (origin is None or note.get("category") == origin)
    )


def addnote(self, text, origin=None, position="append"):
    if not text:
        return
    note = etree.SubElement(_notes(self, create=True), self.namespaced("note"))
    note.text = text
    if origin:
        note.set("category", origin)


def removenotes(self, origin=None):
    notes = _notes(self)
    if notes is None:
        return
    for note in list(notes.iterchildren(self.namespaced("note"))):
        if origin is None or note.get("category") == origin:
            notes.remove(note)
    if not len(notes):
        notes.getparent().remove(notes)


Xliff2Unit.get_state_n = get_state_n
Xliff2Unit.set_state_n = set_state_n
Xliff2Unit.getnotes = getnotes
Xliff2Unit.addnote = addnote
Xliff2Unit.removenotes = removenotes


# Showing a unit


def _rich(unit, node):
    # From a copy: xml_to_strelem() normalises the whitespace of what it
    # reads, which would change the file.
    if node is not None:
        node = copy.deepcopy(node)
    return [xml_to_strelem(node, getXMLspace(unit.xmlelement, unit._default_xml_space))]


def get_rich_source(self):
    return _rich(self, self.source_dom)


def get_rich_target(self, lang=None):
    if self._rich_target is None:
        self._rich_target = _rich(self, self.get_target_dom(lang))
    return self._rich_target


def _content(node):
    return [node.text or ""] + [
        (etree.QName(e).localname, dict(e.attrib), e.text or "", e.tail or "")
        for e in node.iterdescendants(etree.Element)
    ]


def _written(value):
    node = etree.Element("target")
    strelem_to_xml(node, value)
    return _content(node)


def _same_content(unit, value):
    """Whether C{unit}'s target already reads as C{value}, a StringElem,
    with its whitespace read as Virtaal shows it."""
    node = unit.get_target_dom()
    if node is None:
        return not str(value)
    return _written(_rich(unit, node)[0]) == _written(value)


def set_rich_target(self, value, lang="xx", append=False):
    # Showing a unit sets its target: leave the file as it is then.
    form = value[0] if value else None
    if not append and isinstance(form, StringElem) and _same_content(self, form):
        self._rich_target = None
        return
    _set_rich_target(self, value, lang, append)


_set_rich_target = Xliff2Unit.set_rich_target
Xliff2Unit.rich_source = property(get_rich_source, Xliff2Unit.rich_source.fset)
Xliff2Unit.get_rich_target = get_rich_target
Xliff2Unit.set_rich_target = set_rich_target


# Writing a target


def _blank(text):
    return text is not None and not text.strip()


def set_target_dom(self, dom_node, append=False):
    """As translate-toolkit, laying a new or removed target out as the
    segment's source is: on its own line, indented alike. A segment left
    without a target is initial."""
    source, old = self.source_dom, self.get_target_dom()
    _set_target_dom(self, dom_node, append)
    new = self.get_target_dom()
    if new is None and self.xmlelement.get("state") not in (None, "initial"):
        # Only an initial segment may have no target.
        self.set_state_n(StateEnum.NEEDS_WORK)
    if source is None or new is old or not _blank(source.tail):
        return
    if old is None and source.getnext() is new and _blank(self.xmlelement.text):
        new.tail, source.tail = source.tail, self.xmlelement.text
    elif old is not None:
        if new is None:
            source.tail = old.tail
        else:
            new.tail = old.tail


_set_target_dom = Xliff2Unit.set_target_dom
Xliff2Unit.set_target_dom = set_target_dom


# Plural, gender and select

PGS = "urn:oasis:names:tc:xliff:pgs:1.0"
SWITCH = "{%s}switch" % PGS
CASE = "{%s}case" % PGS
# UnitView.MAX_TARGETS: the target boxes Virtaal shows.
MAX_FORMS = 6


def _switch(segment_unit):
    """The pgs:switch of a segment's <unit>, or None."""
    parent = segment_unit.xmlelement.getparent()
    return parent.get(SWITCH) if parent is not None else None


def getcontext(self):
    switch = _switch(self)
    case = self.xmlelement.get(CASE)
    if switch is None or case is None:
        return _getcontext(self)
    return f"{switch} = {case}"


_getcontext = Xliff2Unit.getcontext
Xliff2Unit.getcontext = getcontext


class VariantUnit(Xliff2Unit):
    """The segments of a <unit> with a pgs:switch, one per pgs:case, as
    one plural unit: each case's source and target is a form. Its
    C{plural_tags} are the cases."""

    def __init__(self, segments):
        super().__init__(None, empty=True)
        self.segments = list(segments)
        self.namespace = self.segments[0].namespace
        self.xmlelement = self.segments[0].xmlelement
        switch = self.getunitelement().get(SWITCH) or ""
        self.selectors = [item.partition(":")[0] for item in switch.split()]
        self.plural_tags = [segment.xmlelement.get(CASE) for segment in self.segments]
        self.plural_ordinal = self.selectors == ["ordinal"]
        # Whether the cases are numbers' plural categories.
        self.plural_numbered = self.selectors in (["plural"], ["ordinal"])

    @staticmethod
    def fits(segments):
        """Whether C{segments}, a pgs <unit>'s, can be one unit."""
        cases = [segment.xmlelement.get(CASE) for segment in segments]
        return 0 < len(cases) <= MAX_FORMS and None not in cases and len(set(cases)) == len(cases)

    @property
    def plural_cldr(self):
        """Whether the cases are the target language's CLDR categories,
        in CLDR's order, as a monolingual file's plural forms are."""
        from virtaal.support.converters import plural_categories

        store = getattr(self, "_store", None)
        language = store.gettargetlanguage() if store is not None else None
        if not self.plural_numbered or not language:
            return False
        return self.plural_tags == list(plural_categories(language, self.plural_ordinal) or [])

    def hasplural(self):
        return True

    def getid(self):
        return self.getunitelement().get("id")

    def getcontext(self):
        return ""

    @property
    def source(self):
        return multistring([segment.source or "" for segment in self.segments])

    @source.setter
    def source(self, source):
        forms = getattr(source, "strings", [source])
        for index, segment in enumerate(self.segments):
            segment.source = forms[index] if index < len(forms) else ""

    @property
    def rich_source(self):
        return [segment.rich_source[0] for segment in self.segments]

    @rich_source.setter
    def rich_source(self, value):
        for segment, form in zip(self.segments, value, strict=False):
            segment.rich_source = [form]

    @property
    def target(self):
        return multistring([segment.target or "" for segment in self.segments])

    @target.setter
    def target(self, target):
        if isinstance(target, (list, tuple)):
            forms = list(target)
        else:
            forms = getattr(target, "strings", [target])
        for index, segment in enumerate(self.segments):
            form = str(forms[index]) if index < len(forms) and forms[index] else ""
            if (segment.target or "") != form:
                segment.target = form or None

    def gettarget(self, lang=None):
        return self.target

    def settarget(self, target, lang="xx", append=False):
        self.target = target

    def get_rich_target(self, lang=None):
        return [segment.rich_target[0] for segment in self.segments]

    def set_rich_target(self, value, lang="xx", append=False):
        forms = list(value or [])
        for index, segment in enumerate(self.segments):
            form = forms[index] if index < len(forms) else ""
            if not isinstance(form, StringElem):
                form = StringElem(form or "")
            if str(form):
                segment.rich_target = [form]
            elif segment.target:
                segment.target = None

    rich_target = property(get_rich_target, set_rich_target)

    def _translated_segments(self):
        return [segment for segment in self.segments if segment.target]

    def get_state_n(self):
        translated = self._translated_segments()
        if not translated:
            return StateEnum.EMPTY
        if len(translated) < len(self.segments):
            return StateEnum.NEEDS_WORK
        return min(segment.get_state_n() for segment in translated)

    def set_state_n(self, value):
        if self.get_state_id(value) == self.get_state_id():
            # Its cases keep their own states within the step.
            return
        for segment in self._translated_segments():
            if segment.get_state_id() != segment.get_state_id(value):
                segment.set_state_n(value)

    def isfuzzy(self):
        return self.get_state_n() == StateEnum.NEEDS_WORK

    def istranslated(self):
        return self.get_state_n() >= StateEnum.UNREVIEWED

    def isapproved(self):
        return self.get_state_n() >= StateEnum.FINAL

    def markfuzzy(self, value=True):
        if value:
            self.set_state_n(StateEnum.NEEDS_WORK)
        elif self.isfuzzy():
            self.set_state_n(StateEnum.UNREVIEWED)

    def marktranslated(self):
        self.set_state_n(StateEnum.UNREVIEWED)

    def markapproved(self, value=True):
        self.set_state_n(StateEnum.FINAL if value else StateEnum.UNREVIEWED)


def _variant_groups(units):
    """Each pgs <unit>'s segment units, by the <unit> element."""
    groups = {}
    for unit in units:
        if _switch(unit) is not None:
            groups.setdefault(unit.xmlelement.getparent(), []).append(unit)
    return groups


def group_variants(store):
    """Make each pgs <unit> of C{store} one L{VariantUnit} where it fits."""
    groups = _variant_groups(store.units)
    if not groups:
        return
    units = []
    for unit in store.units:
        segments = groups.get(unit.xmlelement.getparent())
        if segments is None or not VariantUnit.fits(segments):
            units.append(unit)
        elif unit is segments[0]:
            variant = VariantUnit(segments)
            variant._store = store
            units.append(variant)
    store.units = units
    store.id_index = {}


_parse = xliff2.Xliff2File.parse
_serialize = xliff2.Xliff2File.serialize


def parse(self, xml, *args, **kwargs):
    data = xmlinplace.read_data(xml)
    _parse(self, xml, *args, **kwargs)
    group_variants(self)
    self._inplace_original = None
    if data is not None:
        xmlinplace.remember(self, data, "unit")


def serialize(self, out):
    xmlinplace.serialize(self, out, _serialize)


xliff2.Xliff2File.parse = parse
xliff2.Xliff2File.serialize = serialize


# Size and length restriction

SLR = "urn:oasis:names:tc:xliff:sizerestriction:2.0"
SIZE_RESTRICTION = "{%s}sizeRestriction" % SLR
PROFILES = "{%s}profiles" % SLR
CODEPOINTS = "xliff:codepoints"
"""The standard profile that counts a text's code points, in NFC."""


def size_limit(unit):
    """The most characters (code points) a translation of C{unit}, an
    XLIFF 2 unit, may have: its <unit>'s slr:sizeRestriction, where its
    <file> measures in xliff:codepoints. None if it has none."""
    element = unit.getunitelement()
    limit = element.get(SIZE_RESTRICTION)
    if limit is None:
        return None
    file = next(element.iterancestors(unit.namespaced("file")), None)
    profiles = file.find(PROFILES) if file is not None else None
    if profiles is None or profiles.get("generalProfile") != CODEPOINTS:
        return None
    try:
        return int(limit) if int(limit) > 0 else None
    except ValueError:
        return None


def _codepoints(text):
    import unicodedata

    return len(unicodedata.normalize("NFC", text))


class SizeRestrictionCheck:
    """The "maxlength" extra check: a translation of an XLIFF 2 unit
    longer than its size restriction in code points (L{size_limit}),
    each form of a plural on its own."""

    name = "maxlength"
    in_child = False

    def __init__(self):
        from translate.filters.decorators import Category

        self.category = Category.FUNCTIONAL

    @staticmethod
    def applies_to(store):
        return isinstance(store, xliff2.Xliff2File)

    @staticmethod
    def available():
        return True

    def check(self, unit):
        if not isinstance(unit, Xliff2Unit) or not unit.target:
            return None
        limit = size_limit(unit)
        if limit is None:
            return None
        from virtaal.support.extrachecks import length_problem

        forms = getattr(unit.target, "strings", None) or [unit.target]
        return length_problem([str(form) for form in forms], limit, _codepoints)
