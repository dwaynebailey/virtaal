#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Characters the editor marks because they look like nothing, or like
an ordinary space: what to draw for each, and where."""

import re
import unicodedata
from typing import NamedTuple

from translate.storage.placeables import StringElem, base


class Marker(NamedTuple):
    label: str
    """What is drawn for the character."""
    zero_width: bool = False
    """The character has no width of its own, so room is made for the label."""
    name: str = ''
    """The character's name, if Unicode gives it none."""


MARKERS = {
    '\t': Marker('→', name='CHARACTER TABULATION'),
    '\u00a0': Marker('°'),
    '\u202f': Marker('°'),
    '\u2007': Marker('°'),
    '\u00ad': Marker('SHY', True),
    '\u200b': Marker('ZWSP', True),
    '\u200c': Marker('ZWNJ', True),
    '\u200d': Marker('ZWJ', True),
    '\u2060': Marker('WJ', True),
    '\ufeff': Marker('BOM', True),
    '\u200e': Marker('LRM', True),
    '\u200f': Marker('RLM', True),
    '\u061c': Marker('ALM', True),
    '\u202a': Marker('LRE', True),
    '\u202b': Marker('RLE', True),
    '\u202c': Marker('PDF', True),
    '\u202d': Marker('LRO', True),
    '\u202e': Marker('RLO', True),
    '\u2066': Marker('LRI', True),
    '\u2067': Marker('RLI', True),
    '\u2068': Marker('FSI', True),
    '\u2069': Marker('PDI', True),
    '\u2028': Marker('LS', True),
    '\u2029': Marker('PS', True),
}
"""The characters marked wherever they are."""

SPACE = Marker('·')
"""An ordinary space: marked when it is doubled or starts or ends a
line, or when every space is shown."""

_LINE_BREAKS = '\n\r\u2028\u2029'
_MARKED_RE = re.compile('[ %s]' % ''.join(MARKERS))
_PLACEABLE_RE = re.compile('[%s]' % ''.join(c for c in MARKERS if c != '\t'))


def describe(char):
    """The code point and name of char, such as "U+200B ZERO WIDTH SPACE"."""
    marker = MARKERS.get(char, SPACE)
    return 'U+%04X %s' % (ord(char), unicodedata.name(char, marker.name))


def _unusual_space(text, index):
    """Whether the space at text[index] is doubled or starts or ends a line."""
    before = text[index - 1] if index else '\n'
    after = text[index + 1] if index + 1 < len(text) else '\n'
    return any(c == ' ' or c in _LINE_BREAKS for c in (before, after))


def find_markers(text, start=0, end=None, all_spaces=False):
    """(index, marker) for each character of text[start:end] to mark.
    The characters either side of the range decide whether a space at
    its edge is doubled or starts or ends a line."""
    for match in _MARKED_RE.finditer(text, start, len(text) if end is None else end):
        index = match.start()
        char = text[index]
        if char != ' ':
            yield index, MARKERS[char]
        elif all_spaces or _unusual_space(text, index):
            yield index, SPACE


def zero_width_indices(text):
    """(index, marker) for each character of text that needs room made
    for its label."""
    for match in _PLACEABLE_RE.finditer(text):
        marker = MARKERS[match.group()]
        if marker.zero_width:
            yield match.start(), marker


class InvisiblePlaceable(base.Ph):
    """One of L{MARKERS} other than a tab, which can't be typed on most
    keyboards and so is inserted from the source."""

    iseditable = False
    istranslatable = False

    @classmethod
    def parse(cls, pstr):
        found = []
        last = 0
        for match in _PLACEABLE_RE.finditer(pstr):
            if match.start() != last:
                found.append(StringElem(pstr[last : match.start()]))
            found.append(cls([match.group()]))
            last = match.end()
        if found and last != len(pstr):
            found.append(StringElem(pstr[last:]))
        return found or None
