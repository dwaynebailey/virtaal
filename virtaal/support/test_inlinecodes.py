#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from lxml import etree
from translate.storage import po, tmx, xliff

from virtaal.support import inlinecodes

XLIFF = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
<file original="a" source-language="en" target-language="af" datatype="plaintext"><body>
<trans-unit id="1" approved="yes"><source>%s</source><target>%s</target></trans-unit>
</body></file></xliff>"""

TMX = """<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4"><header srclang="en" datatype="plaintext" segtype="sentence"
adminlang="en" o-tmf="x" creationtool="x" creationtoolversion="1"/><body>
<tu><tuv xml:lang="en"><seg>%s</seg></tuv><tuv xml:lang="af"><seg>%s</seg></tuv></tu>
</body></tmx>"""

ALL_CODES = (
    'Click <g id="1">here <x id="2"/> now</g> or <bx id="3"/>x<ex id="4"/> '
    '<ph id="5">&lt;br/&gt;</ph> <bpt id="6">&lt;b&gt;</bpt>bold<ept id="6">&lt;/b&gt;</ept> '
    '<it id="7" pos="open">&lt;i&gt;</it> &amp; 5'
)


def _xliff_unit(source, target=''):
    return xliff.xlifffile.parsestring(XLIFF % (source, target)).units[0]


def _rich_source(source):
    return _xliff_unit(source).rich_source[0]


def test_an_xliff_units_codes_are_kept_as_xliff_markup():
    unit = _xliff_unit(ALL_CODES, 'Klik <g id="1">hier</g>')

    assert inlinecodes.unit_markup(unit) == (ALL_CODES, 'Klik <g id="1">hier</g>')


def test_a_target_without_codes_is_kept_as_its_text():
    unit = _xliff_unit('Click <g id="1">here</g> &amp; go', 'Klik hier &amp; gaan')

    assert inlinecodes.unit_markup(unit) == (
        'Click <g id="1">here</g> &amp; go',
        'Klik hier &amp; gaan',
    )


@pytest.mark.parametrize(
    'unit',
    [
        _xliff_unit('Click here', 'Klik hier'),
        po.pounit('Click <b>here</b>'),
    ],
)
def test_a_unit_with_no_codes_has_no_markup(unit):
    assert inlinecodes.unit_markup(unit) == (None, None)


def test_a_tmx_segments_codes_are_written_as_xliff_markup():
    store = tmx.tmxfile.parsestring(
        (
            TMX
            % (
                'Press <bpt i="1" x="1">&lt;b&gt;</bpt>OK<ept i="1">&lt;/b&gt;</ept> '
                '<ph x="2">&lt;br/&gt;</ph> <it pos="end">&lt;/i&gt;</it> <hi>now</hi>',
                'Druk OK',
            )
        ).encode('utf-8')
    )
    seg = store.units[0].getlanguageNodes()[0].find('seg')

    assert inlinecodes.from_tmx_seg(seg) == (
        'Press <bpt id="1">&lt;b&gt;</bpt>OK<ept id="1">&lt;/b&gt;</ept> '
        '<ph id="2">&lt;br/&gt;</ph> <it id="tmx1" pos="close">&lt;/i&gt;</it> now'
    )


def test_a_tmx_units_source_and_target_keep_their_codes():
    store = tmx.tmxfile.parsestring(
        (TMX % ('Press <ph x="1">&lt;br/&gt;</ph> now', 'Druk nou')).encode('utf-8')
    )

    assert inlinecodes.unit_markup(store.units[0]) == (
        'Press <ph id="1">&lt;br/&gt;</ph> now',
        'Druk nou',
    )


def test_a_tmx_segment_with_no_codes_has_no_markup():
    seg = etree.fromstring('<seg>Press <hi>OK</hi></seg>')

    assert inlinecodes.from_tmx_seg(seg) is None


@pytest.mark.parametrize(
    'markup, text, expected',
    [
        ('Click <g id="1">here</g>', 'Click here', True),
        ('Click <g id="1">here  </g> now', 'Click here now', True),
        ('Click <g id="1">here</g>', 'Click there', False),
        ('Click <b>here</b>', 'Click here', False),
        ('Click <x id="1">here</x>', 'Click here', False),
        ('Click <g id="1">here', 'Click here', False),
        ('<!DOCTYPE x [<!ENTITY a "b">]>&a;', 'b', False),
        (None, 'Click here', False),
    ],
)
def test_only_markup_of_kept_codes_holding_the_text_is_accepted(markup, text, expected):
    assert (inlinecodes.checked(markup, text) is not None) is expected


def _rebuilt(source_markup, target_markup, unit_source):
    rich = inlinecodes.rebuild(source_markup, target_markup, _rich_source(unit_source))
    return None if rich is None else inlinecodes.from_elem(rich, force=True)


def test_a_match_takes_the_units_own_codes():
    assert (
        _rebuilt(
            'Click <g id="1">here</g> to open<x id="2"/> the file',
            'Klik <g id="1">hier</g> en maak<x id="2"/> die lêer oop',
            'Click <g id="a">here</g> to open<x id="b"/> the files',
        )
        == 'Klik <g id="a">hier</g> en maak<x id="b"/> die lêer oop'
    )


def test_every_kind_of_code_is_rebuilt_with_the_units_own():
    unit_source = ALL_CODES.replace('id="', 'id="u')

    assert _rebuilt(
        ALL_CODES,
        'Klik <g id="1">hier <x id="2"/> nou</g> of <bx id="3"/>x<ex id="4"/> '
        '<ph id="5">&lt;br/&gt;</ph> <bpt id="6">&lt;b&gt;</bpt>vet<ept id="6">&lt;/b&gt;</ept> '
        '<it id="7" pos="open">&lt;i&gt;</it>',
        unit_source,
    ) == (
        'Klik <g id="u1">hier <x id="u2"/> nou</g> of <bx id="u3"/>x<ex id="u4"/> '
        '<ph id="u5">&lt;br/&gt;</ph> <bpt id="u6">&lt;b&gt;</bpt>vet<ept id="u6">&lt;/b&gt;</ept> '
        '<it id="u7" pos="open">&lt;i&gt;</it>'
    )


def test_a_units_native_code_replaces_the_matchs():
    assert (
        _rebuilt(
            'Press <ph id="1">&lt;br/&gt;</ph> now',
            'Druk <ph id="1">&lt;br/&gt;</ph> nou',
            'Press <ph id="9">&lt;br /&gt;</ph> now',
        )
        == 'Druk <ph id="9">&lt;br /&gt;</ph> nou'
    )


def test_a_target_may_reorder_the_codes():
    assert (
        _rebuilt(
            '<g id="1">Red</g> and <g id="2">blue</g>',
            '<g id="2">Blou</g> en <g id="1">rooi</g>',
            '<g id="a">Red</g> and <g id="b">blue</g>',
        )
        == '<g id="b">Blou</g> en <g id="a">rooi</g>'
    )


def test_a_tmx_pair_maps_onto_the_units_g():
    # A TMX bpt/ept pair stands for an XLIFF g: open then close.
    assert (
        _rebuilt(
            'Press <bpt id="1">&lt;b&gt;</bpt>OK<ept id="1">&lt;/b&gt;</ept>',
            'Druk <bpt id="1">&lt;b&gt;</bpt>OK<ept id="1">&lt;/b&gt;</ept>',
            'Press <g id="7">OK</g>',
        )
        == 'Druk <g id="7">OK</g>'
    )


def test_literal_tags_in_the_text_are_left_as_text():
    assert (
        _rebuilt(
            'Type &lt;b&gt; <x id="1"/>',
            'Tik &lt;b&gt; <x id="1"/>',
            'Type &lt;b&gt; <x id="2"/>',
        )
        == 'Tik &lt;b&gt; <x id="2"/>'
    )


def test_the_units_other_placeables_are_rebuilt_too():
    assert (
        _rebuilt(
            'Open %s <x id="1"/>',
            'Maak %s oop <x id="1"/>',
            'Open %d <x id="2"/>',
        )
        == 'Maak %d oop <x id="2"/>'
    )


@pytest.mark.parametrize(
    'source_markup, target_markup, unit_source',
    [
        # Fewer codes in the unit.
        ('<g id="1">A</g> <x id="2"/>', '<g id="1">B</g> <x id="2"/>', '<g id="1">A</g>'),
        # Another kind of code.
        ('A <x id="1"/>', 'B <x id="1"/>', 'A <g id="1">x</g>'),
        # A target code the source doesn't have.
        ('A <x id="1"/>', 'B <x id="1"/><x id="3"/>', 'A <x id="1"/>'),
        # Pairs that would cross over.
        (
            '<g id="1"><g id="2">A</g></g>',
            '<g id="1"><g id="2">B</g></g>',
            '<g id="1">A</g><g id="2">C</g>',
        ),
        # A plain unit.
        ('A <x id="1"/>', 'B <x id="1"/>', 'A'),
        # Markup that can't be read.
        ('A <x id="1">', 'B', 'A <x id="1"/>'),
    ],
)
def test_codes_that_dont_map_give_no_rich_target(source_markup, target_markup, unit_source):
    assert _rebuilt(source_markup, target_markup, unit_source) is None


def test_a_dropped_code_is_left_out():
    assert (
        _rebuilt(
            'Click <g id="1">here</g> now<x id="2"/>',
            'Klik <g id="1">hier</g> nou',
            'Click <g id="5">here</g> now<x id="6"/>',
        )
        == 'Klik <g id="5">hier</g> nou'
    )


def test_markup_is_written_to_tmx_with_tmx_codes():
    seg = etree.Element('seg')
    numbers = {}

    assert inlinecodes.write_tmx(seg, ALL_CODES, numbers)

    assert etree.tostring(seg, encoding='unicode') == (
        '<seg>Click <bpt i="1"/>here <ph x="2"/> now<ept i="1"/> or '
        '<it pos="begin" x="3"/>x<it pos="end" x="4"/> <ph x="5">&lt;br/&gt;</ph> '
        '<bpt i="2" x="6">&lt;b&gt;</bpt>bold<ept i="2">&lt;/b&gt;</ept> '
        '<it pos="begin" x="7">&lt;i&gt;</it> &amp; 5</seg>'
    )


def test_tmx_codes_read_back_as_the_same_kinds():
    seg = etree.Element('seg')
    inlinecodes.write_tmx(seg, 'A <g id="1">b</g> <x id="2"/>', {})

    markup = inlinecodes.from_tmx_seg(seg)

    assert markup == 'A <bpt id="1"></bpt>b<ept id="1"></ept> <ph id="2"></ph>'
    assert _rebuilt(
        markup,
        'B <bpt id="1"></bpt>c<ept id="1"></ept> <ph id="2"></ph>',
        'A <g id="5">b</g> <x id="6"/>',
    ) == ('B <g id="5">c</g> <x id="6"/>')
