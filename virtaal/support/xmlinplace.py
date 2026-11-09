#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Saving an XML store as it was read, apart from the units that changed.

A changed unit's text (an XLIFF 1.2 <trans-unit>, an XLIFF 2 <unit>) is
written by lxml in place of the original, keeping the original's
spelling of the tokens that didn't change; the rest of the file, the
byte order mark and line ends included, stays byte for byte. A file
whose units can't be found in its text, or whose other content changed,
is written by translate-toolkit."""

import codecs
import difflib
import io
import re

from lxml import etree

# A tag ends at the first ">" outside its attribute values.
_TOKEN = re.compile(
    r"""<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>|<(?:[^>"']|"[^"]*"|'[^']*')*>|[^<]+""", re.DOTALL
)
_ATTRIBUTE = re.compile(r"""([^\s=/<>]+)\s*=\s*("[^"]*"|'[^']*')""")
_REFERENCE = re.compile(r"&(?:#x([0-9A-Fa-f]+)|#([0-9]+)|(lt|gt|amp|quot|apos));")
_ENTITIES = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def _unit_pattern(name):
    """A unit's text: an empty one, or up to its end tag. Units don't
    nest; the count is checked against the parsed file."""
    start = r"<%s(?=[\s/>])" % re.escape(name)
    return re.compile(r"%s[^>]*?/>|%s.*?</%s\s*>" % (start, start, re.escape(name)), re.DOTALL)


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
    # In any order: lxml writes namespace declarations first.
    attributes = tuple(sorted((k, _unescape(v[1:-1])) for k, v in _ATTRIBUTE.findall(token)))
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


def minimal(old, new):
    """C{new}, lxml's text of a changed unit, keeping C{old}'s text
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
    """What isn't inside a unit: each other node's tag, attributes, text
    and tail, and each unit's tail."""
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


class Original:
    """The text a file was read from, and where each unit (the elements
    named C{name} in the root's namespace) is in it."""

    def __init__(self, store, data, name):
        self.bom = data.startswith(codecs.BOM_UTF8)
        self.text = data[len(codecs.BOM_UTF8) if self.bom else 0 :].decode("utf-8")
        root = store.document.getroot()
        self.tag = "{%s}%s" % (etree.QName(root).namespace, name)
        self.pattern = _unit_pattern(name)
        self.units = list(root.iter(self.tag))
        self.spans = [m.span() for m in self.pattern.finditer(self.text)]
        self.canonical = [_canonical(unit) for unit in self.units]
        self.outside = _outside_units(root, self.tag)

    def usable(self):
        if len(self.spans) != len(self.units):
            return False
        for unit, (start, end) in zip(self.units, self.spans, strict=True):
            head = re.match(r"<[^>]*", self.text[start:end]).group()
            if f'id="{unit.get("id")}"' not in head and f"id='{unit.get('id')}'" not in head:
                return False
        return True

    def unchanged_outside(self, root):
        return (
            list(root.iter(self.tag)) == self.units
            and _outside_units(root, self.tag) == self.outside
        )

    def text_with(self, document):
        """The original text with each changed unit's text replaced by
        lxml's, or None if lxml's can't be found."""
        written = None
        pieces = []
        last = 0
        for index, (start, end) in enumerate(self.spans):
            unit = self.units[index]
            if _canonical(unit) == self.canonical[index]:
                continue
            if written is None:
                written = etree.tostring(document, encoding="unicode")
                written_spans = [m.span() for m in self.pattern.finditer(written)]
                if len(written_spans) != len(self.spans):
                    return None
            new = minimal(self.text[start:end], written[slice(*written_spans[index])])
            pieces += [self.text[last:start], new]
            last = end
        pieces.append(self.text[last:])
        return "".join(pieces)


def _is_utf8(encoding):
    try:
        return codecs.lookup(encoding or "utf-8").name == "utf-8"
    except LookupError:
        return False


def read_data(xml):
    """The bytes a store's parse() is given in C{xml}, or None."""
    data = xml
    if hasattr(xml, "read"):
        xml.seek(0)
        data = xml.read()
        xml.seek(0)
    return data if isinstance(data, bytes) else None


def remember(store, data, name):
    """Keep C{data}, the file's text, to save the units that change
    into; or only whether it starts with a byte order mark, if they can't
    be found in it."""
    store._inplace_original = None
    store._inplace_bom = data.startswith(codecs.BOM_UTF8)
    store._inplace_name = name
    if not _is_utf8(store.encoding):
        return
    try:
        original = Original(store, data, name)
    except UnicodeDecodeError:
        return
    if original.usable():
        store._inplace_original = original


def serialize(store, out, fallback):
    """Write C{store} to C{out} in place of what it was read from, or by
    C{fallback} (translate-toolkit's serialize) if that can't be done."""
    original = getattr(store, "_inplace_original", None)
    bom = codecs.BOM_UTF8 if getattr(store, "_inplace_bom", False) else b""
    text = None
    if original is not None and original.unchanged_outside(store.document.getroot()):
        text = original.text_with(store.document)
    if text is None:
        written = io.BytesIO()
        fallback(store, written)
        data = bom + written.getvalue()
    else:
        data = bom + text.encode("utf-8")
    out.write(data)
    name = getattr(store, "_inplace_name", None)
    if name and (original is None or text != original.text):
        # The next save compares with what this one wrote.
        remember(store, data, name)
