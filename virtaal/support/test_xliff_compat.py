#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from lxml import etree
from translate.storage import xliff
from translate.storage.placeables import StringElem
from translate.storage.placeables import xliff as xliff_placeables

from virtaal.support import xliff_compat

HEADER = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2">'
    '<file original="a" datatype="plaintext" source-language="en" target-language="de">'
    '<body><trans-unit id="1"><source>Hello</source>'
)


def _unit(target):
    data = HEADER + target + '</trans-unit></body></file></xliff>'
    return xliff.xlifffile.parsestring(data.encode('utf-8')).units[0]


def _target_xml(unit):
    node = unit.get_target_dom()
    text = etree.tostring(node, encoding='unicode', with_tail=False)
    return text.replace(' xmlns="urn:oasis:names:tc:xliff:document:1.2"', '')


def test_translate_toolkit_still_reads_a_segment_marker_as_a_placeable():
    # Once this fails translate-toolkit treats the marker as structure,
    # and xliff_compat can go.
    unit = _unit('<target><mrk mtype="seg" mid="1"/></target>')

    rich = xliff_compat._get_rich_target(unit)

    assert isinstance(rich[0].sub[0], xliff_placeables.UnknownXML)


def test_the_rich_target_is_the_segments_text():
    unit = _unit('<target><mrk mtype="seg" mid="1">Hallo <g id="2">Welt</g></mrk></target>')

    assert str(unit.rich_target[0]) == 'Hallo Welt'
    assert not isinstance(unit.rich_target[0].sub[0], xliff_placeables.UnknownXML)


def test_a_rich_target_goes_inside_the_marker():
    unit = _unit('<target><mrk mtype="seg" mid="1"/></target>')

    unit.rich_target = [StringElem('Hallo')]

    assert _target_xml(unit) == '<target><mrk mtype="seg" mid="1">Hallo</mrk></target>'


def test_a_plain_target_goes_inside_the_marker_and_is_translated():
    unit = _unit('<target><x id="6"/> <mrk mtype="seg" mid="1">Alt</mrk></target>')

    unit.target = 'Neu'

    assert _target_xml(unit) == (
        '<target state="translated"><x id="6"/> <mrk mtype="seg" mid="1">Neu</mrk></target>'
    )


def test_several_segments_are_left_to_translate_toolkit():
    target = (
        '<target><mrk mtype="seg" mid="1">Eins.</mrk> <mrk mtype="seg" mid="2">Zwei.</mrk></target>'
    )
    unit = _unit(target)

    assert xliff_compat.segment_marker(unit) is None
    assert len(unit.rich_target[0].sub) == 3


def test_text_outside_the_marker_is_left_to_translate_toolkit():
    unit = _unit('<target>Vorher <mrk mtype="seg" mid="1">Alt</mrk></target>')

    assert xliff_compat.segment_marker(unit) is None


def test_other_markers_are_not_segments():
    unit = _unit('<target><mrk mtype="x-sdl-comment">Alt</mrk></target>')

    assert xliff_compat.segment_marker(unit) is None


def test_a_target_without_a_marker_is_unchanged():
    unit = _unit('<target>Alt <g id="1">fett</g></target>')

    unit.rich_target = [StringElem('Neu')]

    assert _target_xml(unit) == '<target>Neu</target>'


def test_translate_toolkit_still_reindents_a_seg_source():
    # Once this fails translate-toolkit keeps <seg-source> as it is, and
    # the XMLindent change in xliff_compat can go.
    assert 'seg-source' not in xliff_compat._ttk_xml_indent['leaves']


def test_saving_keeps_the_text_between_segments():
    seg_source = (
        '<seg-source><mrk mtype="seg" mid="1">One.</mrk> <mrk mtype="seg" mid="2">Two.</mrk>'
        '</seg-source>'
    )
    unit = _unit(seg_source + '<target/>')

    assert seg_source.encode() in bytes(unit._store)
