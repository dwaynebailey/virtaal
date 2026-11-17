#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Zuza Software Foundation
#
# This file is part of Virtaal.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.
"""Helpers for picking the language codes to send to a remote service.

Kept free of GTK imports so that it can be unit tested on its own."""

from __future__ import absolute_import, print_function, unicode_literals

# Languages where the region also implies the script or a strongly divergent
# written standard, so dropping it would give suggestions in the wrong one.
NO_REGION_FALLBACK = set(['zh'])


def normalize_code(code):
    """Normalise a code for comparison: 'pt-BR' and 'pt_br' -> 'pt_br'."""
    return code.replace('-', '_').lower()


def fallback_codes(code):
    """Return the codes to try for C{code}, most specific first.

    The region is dropped, but a modifier such as '@latin' is kept because it
    names the script::

        'af_ZA'       -> ['af_ZA', 'af']
        'pt-BR'       -> ['pt_BR', 'pt']
        'sr_RS@latin' -> ['sr_RS@latin', 'sr@latin']
        'zh_TW'       -> ['zh_TW']
    """
    if not code:
        return []
    code = code.replace('-', '_')
    base, sep, modifier = code.partition('@')
    lang = base.split('_', 1)[0]
    candidates = [code]
    if lang != base and lang.lower() not in NO_REGION_FALLBACK:
        candidates.append(lang + sep + modifier)
    return candidates


def _filter_supported(candidates, supported):
    """Keep the candidates found in C{supported}, using the server's spelling."""
    by_normal = dict((normalize_code(s), s) for s in supported)
    result = []
    for candidate in candidates:
        match = by_normal.get(normalize_code(candidate))
        if match is not None and match not in result:
            result.append(match)
    return result


def candidate_pairs(source, target, supported=None):
    """Return (source, target) code pairs to try, in order of preference.

    Falling back on the source language (usually en_US -> en) loses little, so
    all source fallbacks are tried before giving up the target region.

    @param supported: None, or a tuple (source_codes, target_codes) of the
        languages the server says it has. Pairs outside it are dropped.
    """
    sources = fallback_codes(source)
    targets = fallback_codes(target)
    if supported is not None:
        sources = _filter_supported(sources, supported[0])
        targets = _filter_supported(targets, supported[1])
    return [(s, t) for t in targets for s in sources]
