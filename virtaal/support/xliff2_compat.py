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
(L{xmlinplace})."""

import copy
import os
import re

from lxml import etree
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
    if value >= StateEnum.FINAL:
        if self.xmlelement.get("state") != "final":
            self.xmlelement.set("state", "reviewed")
    elif value >= StateEnum.UNREVIEWED:
        self.xmlelement.set("state", "translated")
    elif value >= StateEnum.NEEDS_WORK or self.xmlelement.get("state"):
        self.xmlelement.set("state", "initial")


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


_parse = xliff2.Xliff2File.parse
_serialize = xliff2.Xliff2File.serialize


def parse(self, xml, *args, **kwargs):
    data = xmlinplace.read_data(xml)
    _parse(self, xml, *args, **kwargs)
    self._inplace_original = None
    if data is not None:
        xmlinplace.remember(self, data, "unit")


def serialize(self, out):
    xmlinplace.serialize(self, out, _serialize)


xliff2.Xliff2File.parse = parse
xliff2.Xliff2File.serialize = serialize
