#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""translate-toolkit 3.18+ takes a TMX unit's source from the tuv in the
header's srclang and gives None when no tuv has it, so a Trados
Workbench export with srclang EN-GB and tuvs in EN-US opened as an empty
file (#4626). Such a unit's first tuv is its source here, as in
translate-toolkit 3.17 and earlier and in Virtaal's TM import.

TMX 1.1 and 1.2 give a tuv's language in lang, not xml:lang, and
translate-toolkit only reads xml:lang (#4726). Here a tuv without
xml:lang has the language in its lang, and a new tuv uses lang when the
file's tuvs do."""

from translate.misc import xml_helpers
from translate.storage import lisa, tmx

_get_source_dom = tmx.tmxunit.get_source_dom
_createlanguageNode = tmx.tmxunit.createlanguageNode


def _is_tuv(node):
    tag = node.tag
    return isinstance(tag, str) and tag.rpartition("}")[2] == "tuv"


def tuv_lang(node):
    """C{node}'s xml:lang, or for a TMX tuv without one its lang."""
    lang = xml_helpers.getXMLlang(node)
    if lang is None and _is_tuv(node):
        lang = node.get("lang")
    return lang


def _uses_lang(unit):
    """Whether C{unit}'s file gives its tuvs' languages in lang."""
    tuvs = unit.getlanguageNodes()
    if not tuvs:
        root = unit.xmlelement.getroottree().getroot()
        tuvs = root.iter(unit.namespaced(unit.languageNode))
    tuv = next(iter(tuvs), None)
    return tuv is not None and xml_helpers.getXMLlang(tuv) is None and tuv.get("lang") is not None


def get_source_dom(self):
    node = _get_source_dom(self)
    if node is None:
        node = next(iter(self.getlanguageNodes()), None)
    return node


def createlanguageNode(self, lang, text, purpose):
    node = _createlanguageNode(self, lang, text, purpose)
    if _uses_lang(self):
        del node.attrib["{%s}lang" % xml_helpers.XML_NS]
        node.set("lang", lang)
    return node


tmx.tmxunit.get_source_dom = get_source_dom
tmx.tmxunit.source_dom = property(get_source_dom, tmx.tmxunit.set_source_dom)
tmx.tmxunit.createlanguageNode = createlanguageNode
for _module in (lisa, tmx):
    if hasattr(_module, "getXMLlang"):
        _module.getXMLlang = tuv_lang
