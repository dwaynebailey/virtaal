#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Wordfast Pro's bilingual .txml files, which translate-toolkit can't
read (its wordfast module is Wordfast Classic's TM).

TXML has no public specification; Okapi's TXML filter is the reference.
A <txml> holds <translatable> blocks, each with <segment>s of a
<source>, maybe a <target>, and <ws> for what lies between segments;
<skeleton> holds the document's text between blocks. Inline codes are
<ut> elements holding the native code as text, shown here as
placeables, as XLIFF's <ph>. Each segment is a unit:

  - no target: Untranslated;
  - a machine translation (gtmt="true") not modified, or a segment
    marked unconfirmed="true": Needs review;
  - otherwise Translated.

A file is saved as it was read, apart from the segments that changed
(L{xmlinplace}). An edited segment is marked modified="true"; marking it
Translated drops unconfirmed (and marks an unmodified machine
translation modified), Needs review sets unconfirmed="true". Comments
(<comment> in a segment) are its notes."""

import copy
import os

from lxml import etree
from translate.misc.multistring import multistring
from translate.storage import base, factory
from translate.storage.placeables import StringElem
from translate.storage.placeables.lisa import strelem_to_xml, xml_to_strelem
from translate.storage.workflow import StateEnum

from virtaal.support import xmlinplace

NAME = "Wordfast TXML Translation File"
EXTENSION = "txml"
MIMETYPE = "application/x-wordfast-txml"

STATE = {
    StateEnum.EMPTY: (StateEnum.EMPTY, StateEnum.NEEDS_REVIEW),
    StateEnum.NEEDS_REVIEW: (StateEnum.NEEDS_REVIEW, StateEnum.UNREVIEWED),
    StateEnum.UNREVIEWED: (StateEnum.UNREVIEWED, StateEnum.MAX),
}

# The order Wordfast writes a segment's attributes in.
_ORDER = ("segmentId", "modified", "unconfirmed", "gtmt")

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, strip_cdata=False)


def _language(code):
    """Wordfast's "EN" or "EN-US" as "en" or "en_US"."""
    if not code:
        return None
    language, _sep, region = code.replace("_", "-").partition("-")
    return f"{language.lower()}_{region.upper()}" if region else language.lower()


def _wordfast_language(code):
    language, _sep, region = (code or "").replace("_", "-").partition("-")
    return f"{language}-{region}".upper() if region else language.upper()


def _set_attribute(element, name, value):
    """Set C{name} where Wordfast puts it among the segment's attributes."""
    if name in element.attrib:
        element.set(name, value)
        return
    items = list(element.attrib.items())
    rank = _ORDER.index(name)
    at = 0
    for index, (key, _value) in enumerate(items):
        if key in _ORDER and _ORDER.index(key) < rank:
            at = index + 1
    items.insert(at, (name, value))
    element.attrib.clear()
    for key, item in items:
        element.set(key, item)


# Inline codes: <ut x="1"> is read as a <ph id="1"> placeable.


def _rich(node):
    """The text of a <source> or <target> as a rich string."""
    if node is None:
        return StringElem("")
    node = copy.deepcopy(node)
    for child in node:
        if child.tag == "ut":
            child.tag = "ph"
            if child.get("x") is not None:
                child.set("id", child.get("x"))
    return xml_to_strelem(node, "preserve")


def _content(node):
    return [node.text or ""] + [
        (e.tag, dict(e.attrib), e.text or "", e.tail or "")
        for e in node.iterdescendants(etree.Element)
    ]


def _written(value, tag="target"):
    """A rich string as TXML: a C{tag} element with <ut> codes."""
    node = etree.Element(tag)
    strelem_to_xml(node, value)
    for child in node:
        if child.tag == "ph":
            child.tag = "ut"
            code = child.attrib.pop("id", None)
            if child.get("x") is None and code is not None:
                child.set("x", code)
    return node


class TxmlUnit(base.TranslationUnit):
    """A Wordfast <segment>."""

    STATE = STATE

    def __init__(self, source=None, segment=None):
        if segment is None:
            segment = etree.Element("segment")
            etree.SubElement(segment, "source").text = source or ""
        self.segment = segment
        self._rich_source = None
        self._rich_target = None
        super().__init__(source)

    # Text

    def _node(self, tag):
        return self.segment.find(tag)

    @property
    def source(self):
        return str(self.rich_source[0])

    @source.setter
    def source(self, source):
        # Only a new unit's: Wordfast's source doesn't change.
        if getattr(self, "segment", None) is not None and source is not None:
            node = self._node("source")
            node.text, node[:] = str(source), []
            self._rich_source = None

    @property
    def rich_source(self):
        if self._rich_source is None:
            self._rich_source = [_rich(self._node("source"))]
        return self._rich_source

    @rich_source.setter
    def rich_source(self, value):
        self.source = str(value[0]) if value else ""

    @property
    def target(self):
        node = self._node("target")
        return None if node is None else str(_rich(node))

    @target.setter
    def target(self, target):
        if isinstance(target, multistring):
            target = str(target)
        if (target or None) == self.target:
            # Its codes would be written as text.
            return
        self.rich_target = [StringElem(target)] if target else None

    def gettarget(self, lang=None):
        return self.target

    def settarget(self, target, lang="xx", append=False):
        self.target = target

    @property
    def rich_target(self):
        if self._rich_target is None:
            self._rich_target = [_rich(self._node("target"))]
        return self._rich_target

    @rich_target.setter
    def rich_target(self, value):
        form = value[0] if value else None
        if not isinstance(form, StringElem):
            form = StringElem(form or "")
        node = self._node("target")
        if node is None and not str(form):
            return
        if node is not None and _content(_written(_rich(node))) == _content(_written(form)):
            # Showing a unit sets its target: leave the file as it is then.
            return
        self._rich_target = None
        self._write_target(form)
        _set_attribute(self.segment, "modified", "true")

    def _write_target(self, form):
        node = self._node("target")
        if not str(form):
            self.segment.remove(node)
            return
        new = _written(form)
        if node is None:
            # After the source and the whitespace that follows it.
            index = self.segment.index(self._node("source")) + 1
            while index < len(self.segment) and self.segment[index].tag == "ws":
                index += 1
            self.segment.insert(index, new)
            return
        node.text = new.text
        node[:] = list(new)

    # State

    def _translated(self):
        node = self._node("target")
        return node is not None and bool(node.text or len(node))

    def get_state_n(self):
        if not self._translated():
            return StateEnum.EMPTY
        if self.segment.get("unconfirmed") == "true":
            return StateEnum.NEEDS_REVIEW
        if self.segment.get("gtmt") == "true" and self.segment.get("modified") != "true":
            return StateEnum.NEEDS_REVIEW
        return StateEnum.UNREVIEWED

    def set_state_n(self, value):
        if not self._translated() or self.get_state_id(value) == self.get_state_id():
            return
        if value >= StateEnum.UNREVIEWED:
            self.segment.attrib.pop("unconfirmed", None)
            if self.segment.get("gtmt") == "true":
                _set_attribute(self.segment, "modified", "true")
        elif value >= StateEnum.NEEDS_REVIEW:
            _set_attribute(self.segment, "unconfirmed", "true")

    def isfuzzy(self):
        return self.get_state_n() == StateEnum.NEEDS_REVIEW

    def markfuzzy(self, value=True):
        if value:
            self.set_state_n(StateEnum.NEEDS_REVIEW)
        elif self.isfuzzy():
            self.set_state_n(StateEnum.UNREVIEWED)

    def istranslated(self):
        return self.get_state_n() >= StateEnum.UNREVIEWED

    def marktranslated(self):
        self.set_state_n(StateEnum.UNREVIEWED)

    def isapproved(self):
        return False

    # The rest

    def getid(self):
        block = self.segment.getparent()
        block_id = block.get("blockId", "") if block is not None else ""
        return f"{block_id}:{self.segment.get('segmentId', '')}"

    def comments(self):
        """(author, text) of the segment's comments."""
        found = []
        for comment in self.segment.iter("comment"):
            if comment.text and not any(a.tag == "revisions" for a in comment.iterancestors()):
                found.append((comment.get("creationid") or comment.get("author"), comment.text))
        return found

    def getnotes(self, origin=None):
        if origin not in (None, "translator"):
            return ""
        return "\n".join(text for _author, text in self.comments())

    def is_machine_translation(self):
        return self.segment.get("gtmt") == "true"

    def match_score(self):
        """The target's score as a percentage, or None."""
        node = self._node("target")
        try:
            score = float(node.get("score")) if node is not None else 0
        except (TypeError, ValueError):
            return None
        return int(score) if score > 0 else None


class TxmlFile(base.TranslationStore):
    """A Wordfast Pro .txml file."""

    UnitClass = TxmlUnit
    Name = NAME
    Mimetypes = [MIMETYPE]
    Extensions = [EXTENSION]

    def __init__(self, inputfile=None, **kwargs):
        self.document = None
        self.encoding = "UTF-8"
        super().__init__(**kwargs)
        if inputfile is not None:
            self.parse(inputfile)

    def parse(self, data):
        if hasattr(data, "read"):
            data.seek(0)
            data = data.read()
        root = etree.fromstring(data, _PARSER)
        if root.tag != "txml":
            raise ValueError("Not a Wordfast TXML file")
        self.document = root.getroottree()
        self.encoding = self.document.docinfo.encoding or "UTF-8"
        self.units = []
        for block in root.iter("translatable"):
            for segment in block.iterchildren("segment"):
                unit = TxmlUnit(segment=segment)
                self.addunit(unit)
        self._head = data[: data.find(b"<txml")] if b"<txml" in data else b""
        xmlinplace.remember(self, data, "segment", "segmentId")

    def addunit(self, unit):
        unit._store = self
        self.units.append(unit)

    def serialize(self, out):
        xmlinplace.serialize(self, out, _serialize)

    def getsourcelanguage(self):
        return (
            _language(self.document.getroot().get("locale")) if self.document is not None else None
        )

    def gettargetlanguage(self):
        if self.document is None:
            return None
        return _language(self.document.getroot().get("targetlocale"))

    def setsourcelanguage(self, code):
        if self.document is not None and code:
            self.document.getroot().set("locale", _wordfast_language(code))

    def settargetlanguage(self, code):
        if self.document is not None and code:
            self.document.getroot().set("targetlocale", _wordfast_language(code))


def _serialize(store, out):
    """The whole file as lxml writes it, after what came before the root."""
    head = getattr(store, "_head", b"").lstrip(b"\xef\xbb\xbf")
    if not head:
        head = b'<?xml version="1.0" encoding="UTF-8"?>\n'
    out.write(head + etree.tostring(store.document.getroot(), encoding="UTF-8"))


# Registration

_getclass = factory.getclass


def getclass(
    storefile, localfiletype=None, ignore=None, classes=None, classes_str=None, hiddenclasses=None
):
    if localfiletype is None and not classes:
        name = (
            os.fspath(storefile)
            if isinstance(storefile, (str, os.PathLike))
            else getattr(storefile, "name", "")
        )
        if isinstance(name, str):
            if ignore and name.endswith(ignore):
                name = name[: -len(ignore)]
            if name.lower().endswith("." + EXTENSION):
                return TxmlFile
    elif localfiletype == EXTENSION:
        return TxmlFile
    return _getclass(storefile, localfiletype, ignore, classes, classes_str, hiddenclasses)


factory.getclass = getclass
if not any(EXTENSION in extensions for _n, extensions, _m in factory.supported):
    factory.supported.append((NAME, [EXTENSION], [MIMETYPE]))
