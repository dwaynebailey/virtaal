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
"""

import re

from translate.tools.pogrep import GrepFilter as _UpstreamGrepFilter
from translate.tools.pogrep import find_matches


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
                matches.extend(find_matches(unit, part, strings(unit), self.re_search))
        return matches


def _plural_strings(text, unit):
    return text.strings if unit.hasplural() else [text]


# (part, GrepFilter flag, the unit's strings for it), searched in this order.
_PARTS = (
    ('target', 'search_target', lambda unit: _plural_strings(unit.target, unit)),
    ('source', 'search_source', lambda unit: _plural_strings(unit.source, unit)),
    ('notes', 'search_notes', lambda unit: unit.getnotes()),
    ('locations', 'search_locations', lambda unit: unit.getlocations()),
)
