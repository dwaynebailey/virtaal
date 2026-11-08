#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""memoQ's .mqxliff, which is XLIFF 1.2 that translate-toolkit's
factory doesn't know by its extension. Here the factory opens it as
XLIFF, and its units take the state, lock and target from
L{memoq_compat}. Which tool a file is from is known by its namespace,
so a memoQ file saved as .xlf gets the same.

A unit's state is written in the tool's own attribute alone, without
the XLIFF state and approved that translate-toolkit would add (the
tools don't read them). A locked unit keeps its target and state."""

import copy

from lxml import etree
from translate.misc.xml_helpers import getXMLspace
from translate.storage import factory, xliff
from translate.storage.placeables.lisa import strelem_to_xml, xml_to_strelem

from virtaal.support import memoq_compat, sdlxliff_compat

# (extension, the Open dialog's name, Virtaal's own MIME type). The
# names are in devsupport/tmp_strings.py.
FORMATS = [
    ("mqxliff", "memoQ XLIFF Translation File", "application/x-memoq-xliff"),
]

for _extension, _name, _mimetype in FORMATS:
    factory._classes_str.setdefault(_extension, ("xliff", "Xliff1File"))
    if not any(_extension in extensions for _n, extensions, _m in factory.supported):
        factory.supported.append((_name, [_extension], [_mimetype]))


def _flavour_of_root(root):
    for uri in root.nsmap.values():
        if uri == memoq_compat.NAMESPACE:
            return "memoq"
    return None


def flavour(unit):
    """The tool a unit's file is from, "memoq", or None."""
    if not isinstance(unit, xliff.xliffunit) or isinstance(unit, sdlxliff_compat.SegmentUnit):
        return None
    found = unit.__dict__.get("_cat_flavour", False)
    if found is False:
        found = _flavour_of_root(unit.xmlelement.getroottree().getroot())
        unit._cat_flavour = found
    return found


def store_flavour(store):
    """The tool a translate-toolkit store is from, "memoq", or None."""
    if not isinstance(store, xliff.xlifffile) or getattr(store, "document", None) is None:
        return None
    return _flavour_of_root(store.document.getroot())


_TOOLS = {"memoq": memoq_compat}


def _tool(unit):
    return _TOOLS.get(flavour(unit))


def is_locked(unit):
    """Whether C{unit} is a locked memoQ or Trados segment."""
    tool = _tool(unit)
    if tool is not None:
        return tool.is_locked(unit)
    return sdlxliff_compat.is_locked(unit)


class _State:
    """A unit's STATE: its tool's steps, or what it had before."""

    def __init__(self, inner):
        self.inner = inner

    def __get__(self, unit, owner=None):
        tool = _tool(unit) if unit is not None else None
        if tool is not None:
            return tool.STATE
        if hasattr(self.inner, "__get__"):
            return self.inner.__get__(unit, owner)
        return self.inner


_get_state_n = xliff.xliffunit.get_state_n
_set_state_n = xliff.xliffunit.set_state_n
_isapproved = xliff.xliffunit.isapproved
_markapproved = xliff.xliffunit.markapproved
_markfuzzy = xliff.xliffunit.markfuzzy
_settarget = xliff.xliffunit.settarget
_get_rich_target = xliff.xliffunit.get_rich_target
_set_rich_target = xliff.xliffunit.set_rich_target
_ensure_xml_space_preserve = xliff.xliffunit._ensure_xml_space_preserve


def get_state_n(self):
    tool = _tool(self)
    if tool is None:
        return _get_state_n(self)
    return tool.state_n(self, bool(self.target))


def set_state_n(self, value):
    tool = _tool(self)
    if tool is None:
        _set_state_n(self, value)
        return
    if tool.is_locked(self):
        return
    # memoQ's Proofread is a number between Virtaal's steps.
    if value not in getattr(tool, "STATE_STATUSES", ()):
        value = self.get_state_id(value)
    if value != self.get_state_n():
        tool.set_state_n(self, value)


def isapproved(self):
    if _tool(self) is None:
        return _isapproved(self)
    return self.get_state_n() >= self.S_TRANSLATED


def markapproved(self, value=True):
    # set_state_n() writes the tool's status, which carries approval.
    if _tool(self) is None:
        _markapproved(self, value)


def markfuzzy(self, value=True):
    if _tool(self) is None:
        _markfuzzy(self, value)
    elif value:
        self.set_state_n(self.S_NEEDS_REVIEW)
    elif self.get_state_n() < self.S_TRANSLATED:
        self.set_state_n(self.S_TRANSLATED)


def _ensure_preserve(self):
    # memoQ sets xml:space on <target>, not on the trans-unit.
    target = self.get_target_dom() if _tool(self) is not None else None
    if target is not None and getXMLspace(target) == "preserve":
        return
    _ensure_xml_space_preserve(self)


def get_rich_target(self, lang=None):
    target = self.get_target_dom(lang)
    if target is None or _tool(self) is None:
        return _get_rich_target(self, lang)
    if self._rich_target is None:
        xml_space = getXMLspace(self.xmlelement, self._default_xml_space)
        # xml_to_strelem() changes the whitespace of what it reads.
        self._rich_target = [xml_to_strelem(copy.deepcopy(target), xml_space)]
    return self._rich_target


def _content(node):
    """node's text and inline elements. strelem_to_xml() leaves tags out
    of the namespace until saving."""
    return [node.text or ""] + [
        (etree.QName(e).localname, dict(e.attrib), e.text or "", e.tail or "")
        for e in node.iterdescendants(etree.Element)
    ]


def _edited(unit, tool):
    """Before C{unit}'s target changes: an edited memoQ pre-translation."""
    if tool is memoq_compat:
        memoq_compat.mark_edited(unit)


def settarget(self, target, lang="xx", append=False):
    tool = _tool(self)
    if tool is not None and tool.is_locked(self):
        self._rich_target = None
        return
    if tool is not None and target is not None and not append:
        if (self.target or "") == str(target):
            self._rich_target = None
            return
        _edited(self, tool)
    _settarget(self, target, lang, append)


def set_rich_target(self, value, lang="xx", append=False):
    tool = _tool(self)
    if tool is not None and tool.is_locked(self):
        self._rich_target = None
        return
    if tool is not None and value is not None and not append:
        new = etree.Element("target")
        strelem_to_xml(new, value[0])
        target = self.get_target_dom()
        if target is not None and _content(target) == _content(new):
            # Showing a unit sets its target.
            self._rich_target = None
            return
        _edited(self, tool)
    _set_rich_target(self, value, lang, append)


xliff.xliffunit.STATE = _State(xliff.xliffunit.__dict__["STATE"])
xliff.xliffunit.get_state_n = get_state_n
xliff.xliffunit.set_state_n = set_state_n
xliff.xliffunit.isapproved = isapproved
xliff.xliffunit.markapproved = markapproved
xliff.xliffunit.markfuzzy = markfuzzy
xliff.xliffunit._ensure_xml_space_preserve = _ensure_preserve
xliff.xliffunit.get_rich_target = get_rich_target
xliff.xliffunit.settarget = settarget
xliff.xliffunit.set_rich_target = set_rich_target
