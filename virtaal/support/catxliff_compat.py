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
tools don't read them). A locked unit keeps its target and state.

Such a file is saved as it was read, apart from the trans-units that
changed: those are written by lxml in place of the original text, and
the rest, the byte order mark included, stays byte for byte. A file
whose trans-units can't be found in its text, or whose other content
changed, is written by translate-toolkit."""

import codecs
import copy
import difflib
import io
import re

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


# Saving only what changed

# A trans-unit's text: an empty one, or up to its end tag. Trans-units
# don't nest; the count is checked against the parsed file.
_TRANS_UNIT = re.compile(r"<trans-unit\b[^>]*?/>|<trans-unit\b.*?</trans-unit\s*>", re.DOTALL)


_TOKEN = re.compile(r"<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>|<[^>]*>|[^<]+", re.DOTALL)
_ATTRIBUTE = re.compile(r"""([^\s=/<>]+)\s*=\s*("[^"]*"|'[^']*')""")
_REFERENCE = re.compile(r"&(?:#x([0-9A-Fa-f]+)|#([0-9]+)|(lt|gt|amp|quot|apos));")
_ENTITIES = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def _unescape(text):
    def replace(match):
        hexadecimal, decimal, name = match.groups()
        if name:
            return _ENTITIES[name]
        return chr(int(hexadecimal, 16) if hexadecimal else int(decimal))

    return _REFERENCE.sub(replace, text.replace("\r\n", "\n"))


def _token_key(token):
    """What XML token C{token} means, however it is written: a tag by
    its name and attributes, text by its characters."""
    if not token.startswith("<") or token.startswith(("<![CDATA[", "<!--", "<?")):
        return ("text", _unescape(token) if token[:1] != "<" else token)
    name = re.match(r"</?([^\s/>]+)", token).group(1)
    if token.startswith("</"):
        return ("end", name)
    attributes = tuple((k, _unescape(v[1:-1])) for k, v in _ATTRIBUTE.findall(token))
    return ("empty" if token.endswith("/>") else "start", name, attributes)


def _tokens(text):
    """(token text, key) of C{text}, with a start tag and its end tag
    with nothing between them taken as one empty element."""
    tokens = []
    for raw in _TOKEN.findall(text):
        key = _token_key(raw)
        if key[0] == "end" and tokens and tokens[-1][1][:2] == ("start", key[1]):
            start, start_key = tokens.pop()
            raw, key = start + raw, ("empty", *start_key[1:])
        tokens.append((raw, key))
    return tokens


def _minimal(old, new):
    """C{new}, lxml's text of a changed trans-unit, keeping C{old}'s text
    where they mean the same."""
    old_tokens, new_tokens = _tokens(old), _tokens(new)
    crlf = "\r\n" in old
    matcher = difflib.SequenceMatcher(
        None, [k for _t, k in old_tokens], [k for _t, k in new_tokens], autojunk=False
    )
    pieces = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pieces.extend(t for t, _k in old_tokens[i1:i2])
            continue
        for token, _key in new_tokens[j1:j2]:
            pieces.append(token.replace("\n", "\r\n") if crlf else token)
    return "".join(pieces)


def _canonical(element):
    """element's content, to tell whether it changed. Not C14N, which
    takes no relative namespace URI such as memoQ's."""
    return [
        (
            node.tag,
            tuple(node.attrib.items()),
            node.text or "",
            "" if node is element else node.tail or "",
        )
        for node in element.iter()
    ]


def _outside_units(root, tag):
    """What isn't inside a trans-unit: each other node's tag, attributes,
    text and tail, and each trans-unit's tail."""
    found = []
    pending = [root]
    while pending:
        node = pending.pop()
        if node.tag == tag:
            found.append((None, node.tail))
            continue
        found.append((node.tag, dict(node.attrib), node.text, node.tail))
        pending.extend(reversed(node))
    return found


class _Original:
    """The text a file was read from, and where each trans-unit is in it."""

    def __init__(self, store, data):
        self.bom = data.startswith(codecs.BOM_UTF8)
        self.text = data[len(codecs.BOM_UTF8) if self.bom else 0 :].decode("utf-8")
        root = store.document.getroot()
        self.tag = "{%s}trans-unit" % etree.QName(root).namespace
        self.units = list(root.iter(self.tag))
        self.spans = [m.span() for m in _TRANS_UNIT.finditer(self.text)]
        self.canonical = [_canonical(unit) for unit in self.units]
        self.outside = _outside_units(root, self.tag)

    def usable(self):
        if len(self.spans) != len(self.units):
            return False
        for unit, (start, end) in zip(self.units, self.spans, strict=True):
            head = re.match(r"<trans-unit\b[^>]*", self.text[start:end]).group()
            if f'id="{unit.get("id")}"' not in head and f"id='{unit.get('id')}'" not in head:
                return False
        return True

    def unchanged_outside(self, root):
        return (
            list(root.iter(self.tag)) == self.units
            and _outside_units(root, self.tag) == self.outside
        )

    def text_with(self, document):
        """The original text with each changed trans-unit's text replaced
        by lxml's, or None if lxml's can't be found."""
        written = None
        pieces = []
        last = 0
        for index, (start, end) in enumerate(self.spans):
            unit = self.units[index]
            if _canonical(unit) == self.canonical[index]:
                continue
            if written is None:
                written = etree.tostring(document, encoding="unicode")
                written_spans = [m.span() for m in _TRANS_UNIT.finditer(written)]
                if len(written_spans) != len(self.spans):
                    return None
            new = _minimal(self.text[start:end], written[slice(*written_spans[index])])
            pieces += [self.text[last:start], new]
            last = end
        pieces.append(self.text[last:])
        return "".join(pieces)


def _is_utf8(encoding):
    try:
        return codecs.lookup(encoding or "utf-8").name == "utf-8"
    except LookupError:
        return False


def parse(self, xml, *args, **kwargs):
    data = xml
    if hasattr(xml, "read"):
        xml.seek(0)
        data = xml.read()
        xml.seek(0)
    _parse(self, xml, *args, **kwargs)
    self._cat_original = None
    if store_flavour(self) is not None and isinstance(data, bytes):
        _remember(self, data)


def _remember(store, data):
    """Keep C{data}, the file's text, to save the trans-units that change
    into; or only whether it starts with a byte order mark, if they can't
    be found in it."""
    store._cat_original = None
    store._cat_bom = data.startswith(codecs.BOM_UTF8)
    if not _is_utf8(store.encoding):
        return
    try:
        original = _Original(store, data)
    except UnicodeDecodeError:
        return
    if original.usable():
        store._cat_original = original


def serialize(self, out):
    if store_flavour(self) is None:
        _serialize(self, out)
        return
    original = getattr(self, "_cat_original", None)
    bom = codecs.BOM_UTF8 if getattr(self, "_cat_bom", False) else b""
    text = None
    if original is not None and original.unchanged_outside(self.document.getroot()):
        text = original.text_with(self.document)
    if text is None:
        written = io.BytesIO()
        _serialize(self, written)
        data = bom + written.getvalue()
    else:
        data = bom + text.encode("utf-8")
    out.write(data)
    if original is None or text != original.text:
        # The next save compares with what this one wrote.
        _remember(self, data)


xliff.xlifffile.parse = parse
xliff.xlifffile.serialize = serialize
