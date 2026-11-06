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
them, not inside the <segment>."""

import os
import re

from lxml import etree
from translate.storage import factory, xliff2
from translate.storage.workflow import StateEnum

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
