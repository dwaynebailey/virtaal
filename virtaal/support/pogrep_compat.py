#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Compatibility shim around translate-toolkit's pogrep.GrepFilter.

GrepFilter.getmatches() always ORs re.LOCALE into its flags, even
though it only ever compiles str patterns - Python 3.14+ raises
"ValueError: cannot use LOCALE flag with a str pattern" instead of
silently accepting it, crashing every search/replace. It also wraps
the pattern in a group, which moves a leading inline flag such as
"(?i)" off the start - an error since Python 3.11. Upstream bugs;
drop this shim once translate-toolkit fixes them.

The shim also stops at max_matches instead of raising ValueError, which
the search never caught (#4395); keep that when the rest goes.

GrepFilter matches in NFC, where a combining mark that composes with its
letter is gone, so a search for the mark alone missed it (#4513). The
text is searched in NFD too, for matches starting with a mark; such a
match covers the whole composed character, with the rest of that
character kept in its prefix and suffix for replace().
"""

import bisect
import re
import unicodedata

from translate.tools.pogrep import GrepFilter as _UpstreamGrepFilter
from translate.tools.pogrep import GrepMatch, find_matches


class GrepFilter(_UpstreamGrepFilter):
    """GrepFilter with the re.LOCALE and inline-flag errors fixed."""

    def getmatches(self, units):
        if not self.searchstring:
            return [], []

        searchstring = self.searchstring
        # Same flags as upstream, minus re.LOCALE (invalid for str
        # patterns - see module docstring above).
        flags = re.MULTILINE | re.UNICODE

        if self.ignorecase:
            flags |= re.IGNORECASE
        if not self.useregexp:
            searchstring = re.escape(searchstring)
        self.re_search = re.compile(searchstring, flags)
        if not self.useregexp:
            # Unescaped, a decomposed pattern would change a regex's classes.
            nfd = unicodedata.normalize('NFD', self.searchstring)
            self.re_search_nfd = re.compile(re.escape(nfd), flags)
        else:
            self.re_search_nfd = self.re_search

        # Everything below here is upstream's getmatches(), except that
        # it stops at max_matches instead of raising.
        matches = []
        indexes = []

        for index, unit in enumerate(units):
            unit_matches = self._unit_matches(unit)
            if unit_matches:
                matches.extend(unit_matches)
                indexes.append(index)

            # Upstream raises ValueError here; stop at the limit instead,
            # keeping the matches found so far (#4395).
            if self.max_matches and len(matches) >= self.max_matches:
                del matches[self.max_matches :]
                break

        return matches, indexes

    def _unit_matches(self, unit):
        matches = []
        for part, searched, strings in _PARTS:
            if getattr(self, searched):
                texts = strings(unit)
                found = find_matches(unit, part, texts, self.re_search)
                found += _mark_matches(unit, part, texts, self.re_search_nfd, found)
                matches.extend(sorted(found, key=lambda m: (m.part_n, m.start, m.end)))
        return matches


def _is_mark(char):
    return unicodedata.category(char).startswith('M')


def _mark_spans(string, re_search):
    """(start, end, prefix, suffix) of each match in the NFD form of
    C{string} that starts with a combining mark, in C{string}'s offsets."""
    nfd = unicodedata.normalize('NFD', string)
    if nfd == unicodedata.normalize('NFC', string):
        return
    # bounds[i]: where the original string's character i starts in nfd.
    bounds = [0]
    for char in string:
        bounds.append(bounds[-1] + len(unicodedata.normalize('NFD', char)))
    for matchobj in re_search.finditer(nfd):
        start, end = matchobj.span()
        if start == end or not _is_mark(nfd[start]):
            continue
        # Not a letter without the marks that follow it.
        if end < len(nfd) and _is_mark(nfd[end]) and not _is_mark(nfd[end - 1]):
            continue
        first = bisect.bisect_right(bounds, start) - 1
        last = bisect.bisect_left(bounds, end)
        if unicodedata.normalize('NFD', string[first:last]) != nfd[bounds[first] : bounds[last]]:
            # Marks reordered across characters: take the whole cluster.
            while first > 0 and _is_mark(string[first]):
                first -= 1
            while last < len(string) and _is_mark(string[last]):
                last += 1
        yield first, last, nfd[bounds[first] : start], nfd[end : bounds[last]]


def _mark_matches(unit, part, strings, re_search, found):
    """Matches in the NFD form of C{strings} that start with a combining
    mark, other than those in C{found}."""
    seen = {(m.part_n, m.start, m.end) for m in found}
    matches = []
    for n, string in enumerate(strings):
        if not string:
            continue
        for first, last, prefix, suffix in _mark_spans(string, re_search):
            if (n, first, last) in seen:
                continue
            seen.add((n, first, last))
            match = GrepMatch(unit, part=part, part_n=n, start=first, end=last)
            match.prefix = prefix
            match.suffix = suffix
            matches.append(match)
    return matches


def replacement(match, replace_str):
    """What replaces C{match}'s text when it is replaced by C{replace_str}:
    a match on part of a composed character keeps the rest of it."""
    prefix = getattr(match, 'prefix', '')
    suffix = getattr(match, 'suffix', '')
    if not prefix and not suffix:
        return replace_str
    return unicodedata.normalize('NFC', prefix + replace_str + suffix)


def _plural_strings(text, unit):
    return text.strings if unit.hasplural() else [text]


# (part, GrepFilter flag, the unit's strings for it), searched in this order.
_PARTS = (
    ('target', 'search_target', lambda unit: _plural_strings(unit.target, unit)),
    ('source', 'search_source', lambda unit: _plural_strings(unit.source, unit)),
    ('notes', 'search_notes', lambda unit: unit.getnotes()),
    ('locations', 'search_locations', lambda unit: unit.getlocations()),
)
