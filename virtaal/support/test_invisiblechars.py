#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from translate.storage.placeables import StringElem

from virtaal.support.invisiblechars import (
    MARKERS,
    SPACE,
    InvisiblePlaceable,
    describe,
    find_markers,
    zero_width_indices,
)


def marked(text, *args, **kwargs):
    return [(index, marker.label) for index, marker in find_markers(text, *args, **kwargs)]


@pytest.mark.parametrize(
    ('char', 'label'),
    [
        ('\u00a0', '°'),
        ('\u202f', '°'),
        ('\u200b', 'ZWSP'),
        ('\u200c', 'ZWNJ'),
        ('\u200d', 'ZWJ'),
        ('\ufeff', 'BOM'),
        ('\u2060', 'WJ'),
        ('\u200e', 'LRM'),
        ('\u202e', 'RLO'),
        ('\u2066', 'LRI'),
        ('\u00ad', 'SHY'),
        ('\t', '→'),
    ],
)
def test_an_invisible_character_is_marked_wherever_it_is(char, label):
    assert marked('a%sb' % char) == [(1, label)]


def test_a_single_space_between_words_is_marked_only_when_all_spaces_are():
    assert marked('a b') == []
    assert marked('a b', all_spaces=True) == [(1, SPACE.label)]


@pytest.mark.parametrize(
    ('text', 'indices'),
    [
        (' a', [0]),
        ('a ', [1]),
        ('a  b', [1, 2]),
        ('a\n b', [2]),
        ('a \nb', [1]),
        ('a b\u2028 c', [4]),
    ],
)
def test_a_space_that_is_doubled_or_starts_or_ends_a_line_is_marked(text, indices):
    assert [index for index, label in marked(text) if label == SPACE.label] == indices


def test_the_characters_beside_a_range_decide_its_edge_spaces():
    # "a b  c": the range is " b " with a character either side.
    assert marked('a b  c', 1, 4) == [(3, SPACE.label)]


def test_only_the_range_is_marked():
    assert marked('\u200bab\u200b', 1, 3) == []


def test_zero_width_characters_need_room_and_spaces_do_not():
    assert [i for i, _m in zero_width_indices('\u00a0\u200b\t\u200e ')] == [1, 3]


def test_every_label_is_short_and_every_character_is_named():
    for char, marker in MARKERS.items():
        assert 0 < len(marker.label) <= 4
        assert describe(char).startswith('U+%04X ' % ord(char))
        assert len(describe(char)) > len('U+0000 ')


def test_describe_names_the_character():
    assert describe('\u200b') == 'U+200B ZERO WIDTH SPACE'
    assert describe('\t') == 'U+0009 CHARACTER TABULATION'


def test_the_placeable_is_each_invisible_character_but_a_tab():
    found = InvisiblePlaceable.parse('a\u00a0b\u200b\tc')

    assert [(type(e).__name__, str(e)) for e in found] == [
        ('StringElem', 'a'),
        ('InvisiblePlaceable', '\u00a0'),
        ('StringElem', 'b'),
        ('InvisiblePlaceable', '\u200b'),
        ('StringElem', '\tc'),
    ]
    assert InvisiblePlaceable.parse('a\tb') is None
    assert isinstance(found[0], StringElem)
