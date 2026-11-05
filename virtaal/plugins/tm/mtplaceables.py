#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Placeables kept intact through machine translation (#1423).

Before a query each placeable of the source is marked up the way the
service leaves alone, and the reply's markup is turned back into the
source's own placeables. A placeable the service lost is added at the
end and one it repeated is dropped: the request is never sent again, as
the services bill per character."""

import html
import re
from typing import NamedTuple

from virtaal.support import abstraction


class Protected(NamedTuple):
    text: str
    """What to send."""
    placeables: tuple
    """The source's placeables, as L{abstraction.Placeable}s."""
    markup: bool
    """Whether text is markup: escaped, with its placeables marked."""


class Restored(NamedTuple):
    text: str
    """The translation with every placeable once."""
    returned: str
    """The translation as it came back: with the placeables where the
    service put them, and nothing added or dropped."""


def _pairs(placeables):
    """The (open, close) indexes of the tag pairs that nest properly."""
    pairs = [(i, p.pair) for i, p in enumerate(placeables) if p.pair is not None and p.pair > i]
    return {
        (i, j)
        for i, j in pairs
        if not any(i < k < j < m or k < i < m < j for k, m in pairs if (k, m) != (i, j))
    }


def _escape(text):
    return html.escape(text, quote=False)


class _Style:
    always = False
    """Whether to send markup even when the text has no placeables: for a
    service that reads all text as HTML."""
    by_content = False
    """Whether a placeable comes back as its own text, so that text found
    loose in the reply is that placeable."""

    def protect(self, text):
        found = tuple(abstraction.placeables(text))
        if not found and not self.always:
            return Protected(text, (), False)
        out = []
        offset = 0
        for i, placeable in enumerate(found):
            out.append(_escape(text[offset : placeable.start]))
            out.append(self._mark(i, found))
            offset = placeable.end
        out.append(_escape(text[offset:]))
        return Protected(self._wrap(''.join(out)), found, True)

    def restore(self, reply, protected):
        if not protected.markup:
            return Restored(reply, reply)
        pieces = self._pieces(self._unwrap(reply), protected.placeables)
        return _rule_f(pieces, protected.placeables, self.by_content)

    def _wrap(self, text):
        return text

    def _unwrap(self, text):
        return text


def _text(text):
    return (None, html.unescape(text))


class XmlIds(_Style):
    """Each placeable an empty <x id="n"/>, and a tag pair <g id="n">…</g>
    around what it holds, for a service that keeps XML tags."""

    _TAG = re.compile(r'<(/?)\s*([A-Za-z]+)((?:\s+[^>]*?)?)\s*(/?)>')
    _ID = re.compile(r'\bid\s*=\s*["\'](\d+)["\']')

    def _mark(self, i, placeables):
        placeable = placeables[i]
        if placeable.pair is not None:
            pair = (i, placeable.pair) if placeable.pair > i else (placeable.pair, i)
            if pair in _pairs(placeables):
                return '<g id="%d">' % i if pair[0] == i else '</g>'
        return '<x id="%d"/>' % i

    def _pieces(self, reply, placeables):
        pieces = []
        opened = []
        offset = 0
        for tag in self._TAG.finditer(reply):
            pieces.append(_text(reply[offset : tag.start()]))
            offset = tag.end()
            closing, name, attributes, empty = tag.groups()
            name = name.lower()
            found = self._ID.search(attributes)
            index = int(found.group(1)) if found else None
            if index is not None and index >= len(placeables):
                index = None
            if name == 'g' and closing:
                if opened:
                    index = opened.pop()
                    if index is not None:
                        pieces.append((placeables[index].pair, None))
            elif name == 'g':
                if index is not None and placeables[index].pair is None:
                    index = None
                opened.append(index)
                if index is not None:
                    pieces.append((index, None))
            elif name == 'x' and not closing and index is not None:
                pieces.append((index, None))
            # Any other tag the service made up goes.
        pieces.append(_text(reply[offset:]))
        return pieces


class Spans(_Style):
    """Each placeable inside an element the service doesn't translate,
    for a service that reads text as HTML."""

    by_content = True

    def __init__(self, start, end, wrap=None, always=False):
        self.start = start
        self.end = end
        self.wrap = wrap
        self.always = always
        name = re.escape(re.match(r'<([\w-]+)', start).group(1))
        self._element = re.compile(r'<%s\b[^>]*>(.*?)</%s\s*>' % (name, name), re.DOTALL)

    def _mark(self, i, placeables):
        return self.start + _escape(placeables[i].raw) + self.end

    def _wrap(self, text):
        if self.wrap is None:
            return text
        return self.wrap[0] + text + self.wrap[1]

    def _unwrap(self, text):
        if self.wrap is not None:
            stripped = text.strip()
            if stripped.startswith(self.wrap[0]) and stripped.endswith(self.wrap[1]):
                return stripped[len(self.wrap[0]) : len(stripped) - len(self.wrap[1])]
        return text

    def _pieces(self, reply, placeables):
        by_text = {}
        for i, placeable in enumerate(placeables):
            by_text.setdefault(placeable.raw, []).append(i)
        seen = {}
        pieces = []
        offset = 0
        for element in self._element.finditer(reply):
            pieces.extend(_loose(reply[offset : element.start()]))
            offset = element.end()
            content = html.unescape(element.group(1))
            indexes = by_text.get(content) or by_text.get(content.strip())
            if not indexes:
                pieces.append((None, content))
                continue
            raw = placeables[indexes[0]].raw
            k = seen.get(raw, 0)
            seen[raw] = k + 1
            pieces.append((indexes[min(k, len(indexes) - 1)], None))
        pieces.extend(_loose(reply[offset:]))
        return pieces


def _loose(text):
    """Text between our elements, without any tags the service made up."""
    return [_text(part) for part in re.split(r'<[^>]*>', text) if part]


def _rule_f(pieces, placeables, by_content):
    """Restored from (placeable index or None, text) pieces: each
    placeable where it first came back, the missing ones at the end."""
    returned = []
    out = []
    seen = set()
    for index, text in pieces:
        if index is None:
            returned.append(text)
            out.append(text)
            continue
        raw = placeables[index].raw
        returned.append(raw)
        if index not in seen:
            seen.add(index)
            out.append(raw)
    loose = ''.join(text for index, text in pieces if index is None)
    missing = []
    for i, placeable in enumerate(placeables):
        if i in seen:
            continue
        if by_content and placeable.raw in loose:
            # It came back without its markup.
            continue
        missing.append(placeable.raw)
    text = ''.join(out)
    if missing:
        # Not a closing tag: it ends what came before.
        if text and not text[-1].isspace() and not missing[0].startswith('</'):
            text += ' '
        text += ''.join(missing)
    return Restored(text, ''.join(returned))


def suggestion(source, restored, tmsource):
    """The match for a restored translation of source. One whose
    placeables had to be added or dropped shows what came back."""
    match = {'source': source, 'target': restored.text, 'tmsource': tmsource}
    if restored.returned != restored.text:
        match['tm_target'] = restored.returned
    return match
