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
