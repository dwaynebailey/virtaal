#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import unicodedata

from translate.misc.multistring import multistring
from translate.storage import po

from virtaal.support.pogrep_compat import GrepFilter


def _unit(source, target):
    u = po.pofile().addsourceunit(source)
    u.target = target
    return u


def test_getmatches_does_not_raise():
    # Real crash (Python 3.14+): upstream GrepFilter.getmatches() ORs
    # in re.LOCALE, which is invalid with a str pattern.
    units = [_unit("Don't know", "Don't know")]
    f = GrepFilter(
        searchstring="Don't know",
        searchparts=('source', 'target'),
        ignorecase=True,
        useregexp=False,
        max_matches=200000,
    )

    matches, indexes = f.getmatches(units)

    assert indexes == [0]
    assert [m.start for m in matches] == [0, 0]  # matched in both source and target


def test_getmatches_maps_offsets_through_nfd_decomposed_accents():
    # A search term with no accents at all must still land on the right
    # offsets when *other* text in the same string is NFD-decomposed
    # (e.g. a Java/Oracle export), since the underlying normalize()/
    # real_index() machinery matches against the NFC-composed form.
    nfd_e_acute = unicodedata.normalize('NFD', "é")
    units = [_unit("x", nfd_e_acute + " Don't know and also Don't know " + nfd_e_acute)]
    f = GrepFilter(
        searchstring="Don't know",
        searchparts=('target',),
        ignorecase=True,
        useregexp=False,
        max_matches=200000,
    )

    matches, _ = f.getmatches(units)

    raw = units[0].target
    assert [raw[m.start : m.end] for m in matches] == ["Don't know", "Don't know"]


def test_getmatches_accepts_a_leading_inline_flag():
    # Upstream wraps the pattern in a group: "((?i)f)" is an error since
    # Python 3.11.
    units = [_unit("Open the file", "Maak oop")]
    f = GrepFilter(
        searchstring="(?i)F",
        searchparts=('source', 'target'),
        ignorecase=False,
        useregexp=True,
        max_matches=200000,
    )

    matches, indexes = f.getmatches(units)

    assert indexes == [0]
    assert [units[0].source[m.start : m.end] for m in matches] == ["f"]


def test_getmatches_stops_at_max_matches_instead_of_raising():
    # #4395: upstream raised ValueError("Too many matches found").
    units = [_unit("x", "aa"), _unit("x", "aa"), _unit("x", "aa")]
    f = GrepFilter(
        searchstring="a", searchparts=('target',), ignorecase=False, useregexp=False, max_matches=3
    )

    matches, indexes = f.getmatches(units)

    assert len(matches) == 3
    assert indexes == [0, 1]


def test_getmatches_with_an_empty_search_finds_nothing():
    f = GrepFilter(searchstring='', searchparts=('target',), max_matches=10)

    assert f.getmatches([_unit("x", "x")]) == ([], [])


def test_getmatches_searches_each_part_in_order():
    unit = _unit("a", "a")
    unit.addnote("a")
    unit.addlocation("a")
    f = GrepFilter(
        searchstring="a",
        searchparts=('source', 'target', 'notes', 'locations'),
        ignorecase=False,
        useregexp=False,
        max_matches=10,
    )

    matches, indexes = f.getmatches([_unit("x", "y"), unit])

    assert indexes == [1]
    assert [m.part for m in matches] == ['target', 'source', 'notes', 'locations']


def test_getmatches_searches_notes_one_character_at_a_time():
    # As upstream: getnotes() is one string, searched as a list of characters.
    unit = _unit("x", "y")
    unit.addnote("note")
    f = GrepFilter(
        searchstring="o",
        searchparts=('notes',),
        ignorecase=False,
        useregexp=False,
        max_matches=10,
    )

    assert [m.part_n for m in f.getmatches([unit])[0]] == [1]
    f.searchstring = "note"
    assert f.getmatches([unit]) == ([], [])


def test_getmatches_searches_every_plural_form():
    unit = po.pofile().addsourceunit(multistring(["file", "files"]))
    unit.target = multistring(["lêer", "lêers"])
    f = GrepFilter(
        searchstring="lêer",
        searchparts=('target',),
        ignorecase=False,
        useregexp=False,
        max_matches=10,
    )

    matches, _ = f.getmatches([unit])

    assert [(m.part_n, m.start) for m in matches] == [(0, 0), (1, 0)]


# "ne" with its acute accent precomposed, decomposed, and on a letter
# with no precomposed form (#4513).
_ACCENTED = ["n\u00e9", "ne\u0301", "q\u0301"]


def _search(texts, searchstring, useregexp=False):
    units = [_unit(str(n), text) for n, text in enumerate(texts)]
    f = GrepFilter(searchstring=searchstring, searchparts=('target',), useregexp=useregexp)
    return f.getmatches(units)


def test_a_combining_mark_alone_matches_where_it_composes_with_its_letter():
    _matches, indexes = _search(_ACCENTED, "\u0301")
    _matches, regex_indexes = _search(_ACCENTED, "[\u0300-\u036f]", useregexp=True)

    assert indexes == regex_indexes == [0, 1, 2]


def test_a_mark_match_covers_the_character_it_is_composed_into():
    matches, _indexes = _search(_ACCENTED, "\u0301")

    assert [(m.start, m.end) for m in matches] == [(1, 2), (2, 3), (1, 2)]


def test_a_letter_alone_still_misses_its_accented_form():
    _matches, indexes = _search(_ACCENTED + ["ne"], "ne")

    assert indexes == [3]


def test_replacing_a_composed_mark_keeps_its_letter():
    from virtaal.support.pogrep_compat import replacement

    matches, _indexes = _search(_ACCENTED, "\u0301")

    replaced = [
        m.unit.target[: m.start] + replacement(m, "\u0300") + m.unit.target[m.end :]
        for m in matches
    ]
    assert replaced == ["n\u00e8", "ne\u0300", "q\u0300"]


def test_a_mark_reordered_by_nfd_takes_its_whole_cluster():
    from virtaal.support.pogrep_compat import replacement

    # NFD puts the dot below (U+0323) before the acute of the e.
    matches, _indexes = _search(["\u00e9\u0323"], "\u0323")

    (match,) = matches
    assert (match.start, match.end) == (0, 2)
    assert replacement(match, "") == "\u00e9"
