#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Finding a phrase in translations (a concordance search): where the
phrase is in a text, how the hits rank, and merging identical pairs.

A text matches in NFD with each character case folded, so a composed
and a decomposed letter match, and a hit never ends inside a letter's
marks. A text that doesn't hold the phrase as typed may still hold it
past its placeables and accelerator: "Open File" is in "Open &File" and
"%s files" in "%d files", through their abstracts (L{abstraction}).

Needs only translate-toolkit and the standard library, for the local TM
server process.
"""

import json
import re
import unicodedata
from typing import NamedTuple

from virtaal.support import abstraction

MIN_LENGTH = 2
"""The shortest phrase searched for."""

LIMIT = 200
"""The most matches listed from one source."""

SIDES = {'source': ('source',), 'target': ('target',), 'both': ('source', 'target')}

_ACCEL_CHARS = '&_~'
_MARKERS = re.compile('[-]')


class Hit(NamedTuple):
    spans: tuple
    """(start, end) of each hit in the text."""
    whole: bool
    """Whether a hit is a whole word or words."""
    exact: bool
    """Whether a hit has the phrase's own case."""


def cf(text):
    """text in NFKC, case folded: what the local TM's SQL compares
    phrases with non-ASCII letters on."""
    if text is None:
        return None
    return unicodedata.normalize('NFKC', unicodedata.normalize('NFKC', text).casefold())


def like_escape(text):
    """text for a LIKE pattern with ESCAPE '\\'."""
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def json_escape(text):
    """text as it is inside a JSON string."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


def strip_accelerators(text):
    for char in _ACCEL_CHARS:
        text = text.replace(char, '')
    return text


def has_markers(text):
    """Whether an abstract has placeables' markers."""
    return _MARKERS.search(text) is not None


def _is_mark(char):
    return unicodedata.category(char).startswith('M')


def _is_word(char):
    return char.isalnum() or char == '_' or _is_mark(char)


def _fold(text, case):
    """(text in NFD, case folded unless case, offset in text of each of
    its characters or None for the same offsets)."""
    if text.isascii():
        return (text if case else text.lower()), None
    pieces = []
    where = []
    for i, char in enumerate(text):
        piece = unicodedata.normalize('NFD', char)
        if not case:
            piece = piece.casefold()
        pieces.append(piece)
        where.extend([i] * len(piece))
    return ''.join(pieces), where


class Query:
    """A phrase to find, with the search's options."""

    def __init__(self, text, case=False, whole_words=False):
        self.text = unicodedata.normalize('NFC', text)
        self.case = case
        self.whole_words = whole_words
        try:
            self.abstract = abstraction.abstract(self.text)[0]
        except Exception:
            self.abstract = self.text
        self._needle = _fold(self.text, case)[0]
        self._abstract_needle = _fold(self.abstract, case)[0]
        self._quick = None if any(map(_is_mark, self.text)) else self._quick_form(self.text)
        self._loose = self._loose_pattern()

    def _quick_form(self, text):
        return text if self.case else text.casefold()

    def _loose_pattern(self):
        """A regular expression finding the abstract's text in a text
        with its accelerator markers taken out, a placeable anywhere."""
        parts = _MARKERS.split(strip_accelerators(self.abstract).replace('…', ''))
        pattern = '.*?'.join(re.escape(part) for part in parts)
        return re.compile(pattern, 0 if self.case else re.IGNORECASE)

    def is_ascii(self):
        """Whether the phrase and its abstract's text are ASCII."""
        return self.text.isascii() and _MARKERS.sub('', self.abstract).isascii()

    def loose_like(self):
        """The abstract as a LIKE pattern for a text with its accelerator
        markers taken out, a placeable or "…" anywhere."""
        text = strip_accelerators(self.abstract).replace('…', '')
        return '%' + '%'.join(like_escape(part) for part in _MARKERS.split(text)) + '%'

    def maybe(self, text):
        """Whether text may hold the phrase: false only if it can't."""
        if self._quick is None:
            return True
        normal = unicodedata.normalize('NFC', text)
        if self._quick in self._quick_form(normal):
            return True
        return self._loose.search(strip_accelerators(normal)) is not None

    def find(self, text):
        """The L{Hit} of the phrase in text, or None."""
        if not text:
            return None
        hit = self._find(self._needle, self.text, text, None)
        if hit is not None:
            return hit
        try:
            abstract, offsets = abstraction.abstract_offsets(text)
        except Exception:
            return None
        if abstract == text and self.abstract == self.text:
            return None
        return self._find(self._abstract_needle, self.abstract, abstract, offsets)

    def _find(self, needle, phrase, text, offsets):
        if not needle:
            return None
        folded, where = _fold(text, self.case)
        spans = []
        whole = exact = False
        start = folded.find(needle)
        while start >= 0:
            end = start + len(needle)
            bounded = _bounded(folded, start, end, needle)
            if _cut_letter(folded, end, needle) or (self.whole_words and not bounded):
                start = folded.find(needle, start + 1)
                continue
            first, last = _original(where, start, end)
            exact = exact or unicodedata.normalize('NFC', text[first:last]) == phrase
            whole = whole or bounded
            spans.append(_raw(offsets, first, last))
            start = folded.find(needle, end)
        if not spans:
            return None
        return Hit(tuple(spans), whole, exact)


def _cut_letter(folded, end, needle):
    """Whether a hit ending at end leaves out marks of its last letter."""
    return end < len(folded) and _is_mark(folded[end]) and not _is_mark(needle[-1])


def _bounded(folded, start, end, needle):
    """Whether the hit at start to end is whole words."""
    if _is_word(needle[0]) and start > 0 and _is_word(folded[start - 1]):
        return False
    return not (_is_word(needle[-1]) and end < len(folded) and _is_word(folded[end]))


def _original(where, start, end):
    if where is None:
        return start, end
    return where[start], where[end - 1] + 1


def _raw(offsets, first, last):
    """The (start, end) in the raw text of an abstract's first to last."""
    if offsets is None:
        return (first, last)
    return (offsets[first][0], offsets[last - 1][1])


def match_hits(query, sides, texts):
    """Each side's (shown text, form number, L{Hit}) for the first of its
    texts the phrase is in, sides being those searched and texts the
    texts of each side, the first form first; {} if none is."""
    found = {}
    for side in sides:
        for n, text in enumerate(texts.get(side) or ()):
            hit = query.find(text)
            if hit is not None:
                found[side] = (text, n, hit)
                break
    return found


def describe(match, found, texts=None):
    """match with what was found (L{match_hits}) in texts: each side's
    shown text and hit spans, and whether a hit is whole words and in its
    case. A side without a hit shows the form going with the other's."""
    hits = {}
    for side, (text, n, hit) in found.items():
        if n:
            match[side + '_shown'] = text
        hits[side] = [list(span) for span in hit.spans]
    for side, other in (('source', 'target'), ('target', 'source')):
        forms = (texts or {}).get(other) or ()
        if side in found and other not in found and found[side][1] and forms:
            form = forms[min(found[side][1], len(forms) - 1)]
            if form != match[other]:
                match[other + '_shown'] = form
    match['hits'] = hits
    match['whole'] = any(hit.whole for _text, _n, hit in found.values())
    match['exact'] = any(hit.exact for _text, _n, hit in found.values())
    return match


def shown(match, side):
    """The text of side shown for match: the form the phrase was found in."""
    return match.get(side + '_shown', match[side])


def merge(matches):
    """matches with each identical pair of source and translation once,
    counted, with every origin, the newest time and the best hit, and
    what any of them has that the first lacks."""
    merged = {}
    for match in matches:
        key = (shown(match, 'source'), shown(match, 'target'))
        kept = merged.get(key)
        if kept is None:
            merged[key] = dict(match, count=match.get('count', 1))
            continue
        for name, value in match.items():
            kept.setdefault(name, value)
        kept['count'] += match.get('count', 1)
        kept['origins'] = _union(kept.get('origins', ()), match.get('origins', ()))
        kept['tids'] = _union(kept.get('tids', ()), match.get('tids', ()))
        kept['time'] = max(kept.get('time') or 0, match.get('time') or 0)
        kept['whole'] = kept.get('whole') or match.get('whole')
        kept['exact'] = kept.get('exact') or match.get('exact')
        kept['first'] = kept.get('first') or match.get('first')
    return list(merged.values())


def _union(first, second):
    return list(dict.fromkeys([*first, *second]))


def rank_key(match):
    """What matches are listed by: those of a source listed first, whole
    words before part of a word, the phrase's case before another,
    shorter sources, more occurrences, then newer."""
    return (
        not match.get('first', False),
        not match.get('whole'),
        not match.get('exact'),
        len(shown(match, 'source')),
        -match.get('count', 1),
        -(match.get('time') or 0),
    )


def rank(matches):
    return sorted(matches, key=rank_key)
