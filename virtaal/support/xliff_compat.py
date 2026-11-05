#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""An XLIFF 1.2 target that holds one segment, <mrk mtype="seg">, as
Trados Studio's SDLXLIFF files do, is edited inside that marker.
translate-toolkit reads the marker as an UnknownXML placeable, so text
typed into a blank target lands before it, and replacing the whole
target drops it (#4623). Trados reads a segment's translation only from
inside its marker.

A target with several segment markers, or with text outside its
marker, is left to translate-toolkit."""

from translate.misc.xml_helpers import clear_content, getXMLspace
from translate.storage import xliff
from translate.storage.placeables.lisa import strelem_to_xml, xml_to_strelem


def segment_marker(unit, node=None):
    """The one <mrk mtype="seg"> in C{unit}'s target that all of its text
    belongs in, or None."""
    if node is None:
        node = unit.get_target_dom()
    if node is None:
        return None
    mrk = unit.namespaced("mrk")
    markers = [m for m in node.iter(mrk) if m.get("mtype") == "seg"]
    if len(markers) != 1:
        return None
    marker = markers[0]
    inside = set(marker.iterdescendants())
    outside_text = [node.text, marker.tail]
    for element in node.iterdescendants():
        if element is not marker and element not in inside:
            outside_text += [element.text, element.tail]
    if any(text and text.strip() for text in outside_text):
        return None
    return marker


_get_rich_target = xliff.xliffunit.get_rich_target
_set_rich_target = xliff.xliffunit.set_rich_target
_settarget = xliff.xliffunit.settarget


def get_rich_target(self, lang=None):
    marker = segment_marker(self, self.get_target_dom(lang))
    if marker is None:
        return _get_rich_target(self, lang)
    if self._rich_target is None:
        xml_space = getXMLspace(self.xmlelement, self._default_xml_space)
        self._rich_target = [xml_to_strelem(marker, xml_space)]
    return self._rich_target


def set_rich_target(self, value, lang="xx", append=False):
    marker = None if value is None else segment_marker(self)
    if marker is None:
        _set_rich_target(self, value, lang, append)
        return
    self._rich_target = None
    self._ensure_xml_space_preserve()
    clear_content(marker)
    strelem_to_xml(marker, value[0])


def settarget(self, target, lang="xx", append=False):
    marker = None if target is None else segment_marker(self)
    if marker is None:
        _settarget(self, target, lang, append)
        return
    self._rich_target = None
    if self.target == target:
        return
    clear_content(marker)
    marker.text = str(target)
    if target:
        self._ensure_xml_space_preserve()
        self.marktranslated()


xliff.xliffunit.get_rich_target = get_rich_target
xliff.xliffunit.set_rich_target = set_rich_target
xliff.xliffunit.settarget = settarget

# translate-toolkit reindents <seg-source> on save like a container, so
# the text between segments, which Trados keeps there, became newlines
# and indentation.
_ttk_xml_indent = xliff.xlifffile.XMLindent
xliff.xlifffile.XMLindent = {
    **_ttk_xml_indent,
    "leaves": set(_ttk_xml_indent.get("leaves", ())) | {"seg-source"},
}
