#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""memoQ's .mqxliff and Phrase's .mxliff, which are XLIFF 1.2 that
translate-toolkit's factory doesn't know by their extensions. Here the
factory opens them as XLIFF, and their units take the state, lock and
target from L{memoq_compat} or L{phrase_compat}. Which tool a file is
from is known by its namespace, so a memoQ or Phrase file saved as .xlf
gets the same.

A unit's state is written in the tool's own attribute alone, without
the XLIFF state and approved that translate-toolkit would add (the
tools don't read them). A locked unit keeps its target and state.

Such a file is saved as it was read, apart from the trans-units that
changed: those are written by lxml in place of the original text, and
the rest, the byte order mark included, stays byte for byte. A file
whose trans-units can't be found in its text, or whose other content
changed, is written by translate-toolkit."""

import copy
import datetime
import sys

from lxml import etree
from translate.misc.xml_helpers import getXMLspace
from translate.storage import factory, xliff
from translate.storage.placeables.lisa import strelem_to_xml, xml_to_strelem

from virtaal.support import memoq_compat, phrase_compat, sdlxliff_compat, xmlinplace

# (extension, the Open dialog's name, Virtaal's own MIME type). The
# names are in devsupport/tmp_strings.py.
FORMATS = [
    ("mqxliff", "memoQ XLIFF Translation File", "application/x-memoq-xliff"),
    ("mxliff", "Phrase XLIFF Translation File", "application/x-phrase-xliff"),
]

for _extension, _name, _mimetype in FORMATS:
    factory._classes_str.setdefault(_extension, ("xliff", "Xliff1File"))
    if not any(_extension in extensions for _n, extensions, _m in factory.supported):
        factory.supported.append((_name, [_extension], [_mimetype]))


def _flavour_of_root(root):
    for uri in root.nsmap.values():
        if uri == memoq_compat.NAMESPACE:
            return "memoq"
        if uri and uri.startswith(phrase_compat.NAMESPACE_PREFIX):
            return "phrase"
    return None


def flavour(unit):
    """The tool a unit's file is from, "memoq" or "phrase", or None."""
    if not isinstance(unit, xliff.xliffunit) or isinstance(unit, sdlxliff_compat.SegmentUnit):
        return None
    found = unit.__dict__.get("_cat_flavour", False)
    if found is False:
        found = _flavour_of_root(unit.xmlelement.getroottree().getroot())
        unit._cat_flavour = found
    return found


def store_flavour(store):
    """The tool a translate-toolkit store is from, "memoq" or "phrase",
    or None."""
    if not isinstance(store, xliff.xlifffile) or getattr(store, "document", None) is None:
        return None
    return _flavour_of_root(store.document.getroot())


_TOOLS = {"memoq": memoq_compat, "phrase": phrase_compat}


def _tool(unit):
    return _TOOLS.get(flavour(unit))


def is_locked(unit):
    """Whether C{unit} is a locked memoQ, Phrase or Trados segment, or a
    unit of a monolingual project's source-language file."""
    if getattr(unit, "read_only", False):
        return True
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
_gettarget = xliff.xliffunit.gettarget
_settarget = xliff.xliffunit.settarget
_get_rich_target = xliff.xliffunit.get_rich_target
_set_rich_target = xliff.xliffunit.set_rich_target
_ensure_xml_space_preserve = xliff.xliffunit._ensure_xml_space_preserve
_serialize = xliff.xlifffile.serialize
_parse = xliff.xlifffile.parse


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


def _shown_target(unit, node):
    """The target node C{unit} shows: memoQ's with its tracked changes
    accepted, or None if C{node} is."""
    if node is None or flavour(unit) != "memoq" or not memoq_compat.has_tracked_changes(node):
        return None
    return memoq_compat.accepted(node)


def gettarget(self, lang=None):
    shown = _shown_target(self, self.get_target_dom(lang))
    if shown is None:
        return _gettarget(self, lang)
    return self.getNodeText(shown, getXMLspace(self.xmlelement, self._default_xml_space))


def get_rich_target(self, lang=None):
    target = self.get_target_dom(lang)
    shown = _shown_target(self, target)
    if shown is None and target is not None and _tool(self) is not None:
        # xml_to_strelem() changes the whitespace of what it reads.
        shown = copy.deepcopy(target)
    if shown is None:
        return _get_rich_target(self, lang)
    if self._rich_target is None:
        xml_space = getXMLspace(self.xmlelement, self._default_xml_space)
        self._rich_target = [xml_to_strelem(shown, xml_space)]
    return self._rich_target


def _content(node):
    """node's text and inline elements. strelem_to_xml() leaves tags out
    of the namespace until saving."""
    return [node.text or ""] + [
        (etree.QName(e).localname, dict(e.attrib), e.text or "", e.tail or "")
        for e in node.iterdescendants(etree.Element)
    ]


def tracking_user():
    """Whose memoQ tracked changes an edit is: the translator's name in
    Virtaal's settings, or "Virtaal" without one."""
    pan_app = sys.modules.get("virtaal.common.pan_app")
    translator = getattr(getattr(pan_app, "settings", None), "translator", None) or {}
    return (translator.get("name") or "").strip() or "Virtaal"


def tracking_time():
    """Now, as memoQ writes a tracked change's time: 2019-05-05T05:46:18Z."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _merge(unit, new):
    """Write C{new}'s content into a memoQ target whose edits are tracked,
    as tracked changes; False if they aren't."""
    target = unit.get_target_dom()
    if target is None or not memoq_compat.tracks_changes(unit):
        return False
    unit._rich_target = None
    memoq_compat.merge_target(target, new, tracking_user(), tracking_time())
    return True


def settarget(self, target, lang="xx", append=False):
    tool = _tool(self)
    if tool is not None and tool.is_locked(self):
        self._rich_target = None
        return
    if tool is not None and target is not None and not append:
        if (self.target or "") == str(target):
            self._rich_target = None
            return
        new = etree.Element("target")
        new.text = str(target) or None
        if _edit(self, tool, new):
            if target:
                self.marktranslated()
            return
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
        shown = _shown_target(self, target)
        if target is not None and _content(target if shown is None else shown) == _content(new):
            # Showing a unit sets its target.
            self._rich_target = None
            return
        if _edit(self, tool, new):
            return
    _set_rich_target(self, value, lang, append)


def _edit(unit, tool, new):
    """Before C{unit}'s target changes to C{new}'s content: mark an edited
    memoQ pre-translation, and write a target with tracked changes. True
    if the target is written."""
    if tool is not memoq_compat:
        return False
    memoq_compat.mark_edited(unit)
    return _merge(unit, new)


xliff.xliffunit.STATE = _State(xliff.xliffunit.__dict__["STATE"])
xliff.xliffunit.get_state_n = get_state_n
xliff.xliffunit.set_state_n = set_state_n
xliff.xliffunit.isapproved = isapproved
xliff.xliffunit.markapproved = markapproved
xliff.xliffunit.markfuzzy = markfuzzy
xliff.xliffunit._ensure_xml_space_preserve = _ensure_preserve
xliff.xliffunit.gettarget = gettarget
xliff.xliffunit.get_rich_target = get_rich_target
xliff.xliffunit.settarget = settarget
xliff.xliffunit.set_rich_target = set_rich_target


# Saving only what changed


def parse(self, xml, *args, **kwargs):
    data = xmlinplace.read_data(xml)
    _parse(self, xml, *args, **kwargs)
    self._inplace_original = None
    if store_flavour(self) is not None and data is not None:
        xmlinplace.remember(self, data, "trans-unit")


def serialize(self, out):
    if store_flavour(self) is None:
        _serialize(self, out)
        return
    xmlinplace.serialize(self, out, _serialize)


xliff.xlifffile.parse = parse
xliff.xlifffile.serialize = serialize
