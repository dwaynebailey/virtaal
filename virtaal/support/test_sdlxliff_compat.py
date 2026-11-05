#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from lxml import etree
from translate.storage import pypo, xliff
from translate.storage.placeables import StringElem
from translate.storage.workflow import StateEnum

from virtaal.support import sdlxliff_compat

SDL = sdlxliff_compat.SDL
LEVELS = [
    (None, StateEnum.NEEDS_WORK),
    ('RejectedTranslation', StateEnum.REJECTED),
    ('RejectedSignOff', StateEnum.REJECTED + 1),
    ('Draft', StateEnum.NEEDS_REVIEW),
    ('Translated', StateEnum.UNREVIEWED),
    ('ApprovedTranslation', StateEnum.FINAL),
    ('ApprovedSignOff', StateEnum.FINAL + 1),
]


def _seg(conf=None, extra=''):
    conf = '' if conf is None else f' conf="{conf}"'
    return f'<sdl:seg id="1"{conf}{extra}/>'


def _store(target='Hallo', segs=None):
    segs = _seg() if segs is None else segs
    target = '<mrk mtype="seg" mid="1">%s</mrk>' % target
    data = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2"'
        ' xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0" version="1.2">'
        '<file original="a" datatype="x-sdlfilterframework2" source-language="en"'
        ' target-language="de"><body><trans-unit id="u1"><source>Hello</source>'
        '<seg-source><mrk mtype="seg" mid="1">Hello</mrk></seg-source>'
        f'<target>{target}</target><sdl:seg-defs>{segs}</sdl:seg-defs>'
        '</trans-unit></body></file></xliff>'
    )
    return xliff.xlifffile.parsestring(data.encode('utf-8'))


def _unit(conf=None, target='Hallo', extra=''):
    return _store(target, _seg(conf, extra)).units[0]


def _conf(unit):
    return sdlxliff_compat.segment_defs(unit)[0].get('conf')


def test_translate_toolkit_still_ignores_the_trados_status(monkeypatch):
    # Once this fails translate-toolkit reads conf, and the state part of
    # sdlxliff_compat can go.
    monkeypatch.setattr(xliff.xliffunit, 'isapproved', sdlxliff_compat._isapproved)
    segment = _unit('Translated')
    unit = xliff.xliffunit.createfromxmlElement(segment.xmlelement)
    unit.namespace = segment.namespace

    assert sdlxliff_compat._get_state_n(unit) == StateEnum.NEEDS_WORK
    assert not sdlxliff_compat._isapproved(unit)


@pytest.mark.parametrize('conf,state_n', LEVELS)
def test_a_trados_status_is_read_as_a_state(conf, state_n):
    assert _unit(conf).get_state_n() == state_n


def test_an_empty_unspecified_segment_is_untranslated():
    assert _unit(target='').get_state_n() == StateEnum.EMPTY


@pytest.mark.parametrize('conf,state_n', LEVELS[1:])
def test_a_state_is_written_as_its_trados_status(conf, state_n):
    unit = _unit()

    unit.set_state_n(state_n)

    assert _conf(unit) == conf
    assert unit.get_state_n() == state_n


@pytest.mark.parametrize('state_n', [StateEnum.EMPTY, StateEnum.NEEDS_WORK])
def test_untranslated_and_needs_work_are_unspecified(state_n):
    unit = _unit('Draft')

    unit.set_state_n(state_n)

    assert _conf(unit) is None


def test_a_number_inside_a_step_writes_that_steps_status():
    unit = _unit()

    unit.set_state_n(StateEnum.NEEDS_REVIEW + 2)

    assert _conf(unit) == 'Draft'


def test_a_state_change_writes_only_the_status():
    unit = _unit('Draft')

    unit.set_state_n(StateEnum.FINAL)

    assert unit.get_target_dom().get('state') is None
    assert unit.xmlelement.get('approved') is None


def test_setting_the_current_state_leaves_the_segment_alone():
    unit = _unit('RejectedSignOff')

    unit.set_state_n(StateEnum.REJECTED + 1)

    assert _conf(unit) == 'RejectedSignOff'


@pytest.mark.parametrize(
    'conf,fuzzy,translated',
    [
        (None, True, False),
        ('Draft', True, False),
        ('RejectedTranslation', True, False),
        ('Translated', False, True),
        ('ApprovedSignOff', False, True),
    ],
)
def test_translated_and_fuzzy_follow_the_status(conf, fuzzy, translated):
    unit = _unit(conf)

    assert (unit.isfuzzy(), unit.istranslated()) == (fuzzy, translated)


def test_marking_fuzzy_writes_the_status():
    unit = _unit('Translated')

    unit.markfuzzy(True)
    assert _conf(unit) is None
    unit.markfuzzy(False)
    assert _conf(unit) == 'Translated'
    assert unit.xmlelement.get('approved') is None


def test_a_trados_segment_has_the_rejected_step():
    assert StateEnum.REJECTED in _unit().STATE
    assert StateEnum.REJECTED not in xliff.xliffunit.STATE


def _whole(segs):
    """A trans-unit with two segments and a target Trados didn't split."""
    data = (
        '<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2"'
        ' xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0" version="1.2">'
        '<file original="a" datatype="x-sdlfilterframework2" source-language="en"'
        ' target-language="de"><body><trans-unit id="u1"><source>One. Two.</source>'
        '<seg-source><mrk mtype="seg" mid="1">One.</mrk> <mrk mtype="seg" mid="2">Two.</mrk>'
        f'</seg-source><target>Eins. Zwei.</target><sdl:seg-defs>{segs}</sdl:seg-defs>'
        '</trans-unit></body></file></xliff>'
    )
    return xliff.xlifffile.parsestring(data.encode('utf-8')).units[0]


def test_a_trans_unit_kept_whole_keeps_translate_toolkits_state():
    unit = _whole(_seg('Translated') + '<sdl:seg id="2" conf="Translated"/>')

    assert unit.STATE is xliff.xliffunit.STATE
    assert unit.get_state_n() == StateEnum.NEEDS_WORK
    unit.set_state_n(StateEnum.UNREVIEWED)
    assert unit.get_target_dom().get('state') == 'translated'
    assert [seg.get('conf') for seg in sdlxliff_compat.segment_defs(unit)] == ['Translated'] * 2


def test_plain_xliff_keeps_translate_toolkits_state():
    data = (
        b'<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2">'
        b'<file original="a" datatype="plaintext" source-language="en" target-language="de">'
        b'<body><trans-unit id="1"><source>Hello</source>'
        b'<target state="needs-review-translation">Hallo</target></trans-unit></body></file></xliff>'
    )
    unit = xliff.xlifffile.parsestring(data).units[0]

    assert unit.STATE is xliff.xliffunit.STATE
    assert unit.get_state_n() == StateEnum.NEEDS_REVIEW
    unit.set_state_n(StateEnum.FINAL)
    assert unit.get_target_dom().get('state') == 'signed-off'
    assert unit.xmlelement.get('approved') == 'yes'


@pytest.mark.parametrize(
    'whole,segs',
    [
        (False, _seg('Draft', ' locked="true"')),
        (True, _seg() + '<sdl:seg id="2" locked="true"/>'),
    ],
)
def test_a_locked_segment_keeps_its_target_and_status(whole, segs):
    unit = _whole(segs) if whole else _store(segs=segs).units[0]
    before = bytes(unit._store)

    unit.target = 'Neu'
    unit.rich_target = [StringElem('Neu')]
    unit.set_state_n(StateEnum.FINAL)
    unit.markfuzzy(False)

    assert sdlxliff_compat.is_locked(unit)
    assert bytes(unit._store) == before


def test_a_locked_segment_drops_a_rich_target_changed_in_place():
    # The editor changes the cached rich target, then sets it.
    unit = _unit(extra=' locked="true"')
    rich_target = unit.rich_target
    rich_target[0] = StringElem('Neu')

    unit.rich_target = rich_target

    assert str(unit.rich_target[0]) == 'Hallo'


def test_only_a_trados_lock_locks_a_unit():
    assert not sdlxliff_compat.is_locked(_unit(extra=' locked="false"'))
    assert not sdlxliff_compat.is_locked(pypo.pounit('Open'))


def test_a_segments_origin():
    seg = sdlxliff_compat.segment_def(
        _unit(extra=' origin="tm" origin-system="Main TM" percent="95"')
    )

    assert sdlxliff_compat.segment_origin(seg) == ('tm', 'Main TM', '95')
    assert sdlxliff_compat.segment_origin(sdlxliff_compat.segment_def(_unit())) == (None,) * 3


def _commented(target):
    data = (
        '<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2"'
        ' xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0" version="1.2">'
        '<doc-info xmlns="http://sdl.com/FileTypes/SdlXliff/1.0"><cmt-defs>'
        '<cmt-def id="a"><Comments><Comment user="Ann">One</Comment>'
        '<Comment>Two\n  lines</Comment></Comments></cmt-def>'
        '<cmt-def id="b"><Comments><Comment user="Bob">Three</Comment></Comments></cmt-def>'
        '</cmt-defs></doc-info>'
        '<file original="a" datatype="x-sdlfilterframework2" source-language="en"'
        ' target-language="de"><body><trans-unit id="u1">'
        '<source><mrk mtype="x-sdl-comment" sdl:cid="b">Hello</mrk></source>'
        f'<target>{target}</target></trans-unit></body></file></xliff>'
    )
    return xliff.xlifffile.parsestring(data.encode('utf-8')).units[0]


def test_comments_in_document_order_once_each():
    unit = _commented(
        '<mrk mtype="x-sdl-comment" sdl:cid="b">Hallo</mrk>'
        '<mrk mtype="x-sdl-comment" sdl:cid="a">Welt</mrk>'
        '<mrk mtype="x-sdl-comment" sdl:cid="gone">!</mrk>'
    )

    assert sdlxliff_compat.comments(unit) == [('Bob', 'Three'), ('Ann', 'One'), (None, 'Two lines')]
    assert sdlxliff_compat.comments(unit, [unit.get_target_dom()[1]]) == [
        ('Ann', 'One'),
        (None, 'Two lines'),
    ]


def test_no_comments():
    assert sdlxliff_compat.comments(_commented('Hallo'), []) == []
    assert sdlxliff_compat.comments(_unit()) == []
    assert sdlxliff_compat.comments(pypo.pounit('Open')) == []


def _split(seg_source, target, segs='', xml_ns=True):
    """A store with one trans-unit and the given <seg-source>, <target>
    and <sdl:seg> contents; C{target} None leaves the target out."""
    ns = ' xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0"' if xml_ns else ''
    target = '' if target is None else f'<target>{target}</target>'
    seg_defs = f'<sdl:seg-defs>{segs}</sdl:seg-defs>' if segs else ''
    data = (
        f'<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2"{ns} version="1.2">'
        '<file original="f" datatype="x-sdlfilterframework2" source-language="en"'
        ' target-language="de"><body><trans-unit id="u1"><source>All</source>'
        f'<seg-source>{seg_source}</seg-source>{target}{seg_defs}'
        '</trans-unit><trans-unit id="u2"><source>Last</source>'
        '<seg-source><mrk mtype="seg" mid="9">Last</mrk></seg-source>'
        '<target><mrk mtype="seg" mid="9"/></target></trans-unit></body></file></xliff>'
    )
    return xliff.xlifffile.parsestring(data.encode('utf-8'))


def _mrk(mid, text=''):
    return (
        f'<mrk mtype="seg" mid="{mid}">{text}</mrk>' if text else f'<mrk mtype="seg" mid="{mid}"/>'
    )


THREE = (
    _mrk(1, 'One.') + ' <x id="5"/>' + _mrk(2, 'Two <g id="6">bold</g>.') + ' ' + _mrk(3, 'Three.')
)
THREE_SEGS = '<sdl:seg id="1" conf="Translated"/><sdl:seg id="2" locked="true"/><sdl:seg id="3"/>'


def _target_xml(unit):
    node = unit.xmlelement.find(unit.namespaced('target'))
    text = etree.tostring(node, encoding='unicode', with_tail=False)
    for ns in (' xmlns="urn:oasis:names:tc:xliff:document:1.2"', ' ' + XMLNS_SDL):
        text = text.replace(ns, '')
    return text


XMLNS_SDL = 'xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0"'


def test_each_segment_is_a_unit():
    store = _split(THREE, _mrk(1, 'Eins.') + ' <x id="5"/>' + _mrk(2) + ' ' + _mrk(3, 'Drei.'))

    units = store.units
    assert [type(u) for u in units] == [sdlxliff_compat.SegmentUnit] * 4
    assert [u.getid() for u in units] == ['f\x04u1\x041', 'f\x04u1\x042', 'f\x04u1\x043', 'f\x04u2']
    assert [u.source for u in units] == ['One.', 'Two bold.', 'Three.', 'Last']
    assert [u.target for u in units] == ['Eins.', '', 'Drei.', '']
    assert [type(e).__name__ for e in units[1].rich_source[0].sub] == [
        'StringElem',
        'G',
        'StringElem',
    ]
    assert [u._store for u in units] == [store] * 4
    assert store.findid('f\x04u1\x043') is units[2]


def test_each_segment_has_its_own_state_lock_and_origin():
    segs = THREE_SEGS.replace('id="3"', 'id="3" origin="tm" percent="80"')
    units = _split(THREE, _mrk(1, 'Eins.') + _mrk(2, 'Zwei.') + _mrk(3), segs).units

    assert [u.get_state_n() for u in units[:3]] == [
        StateEnum.UNREVIEWED,
        StateEnum.NEEDS_WORK,
        StateEnum.EMPTY,
    ]
    assert [sdlxliff_compat.is_locked(u) for u in units[:3]] == [False, True, False]
    assert sdlxliff_compat.segment_origin(sdlxliff_compat.segment_def(units[2])) == (
        'tm',
        None,
        '80',
    )
    assert all(u.STATE is sdlxliff_compat.STATE for u in units)


def test_a_segment_id_is_its_mid_decoded():
    seg_source = _mrk('1_x0020_a', 'One.') + ' ' + _mrk('1_x0020_b', 'Two.')
    segs = '<sdl:seg id="1 a" conf="Draft"/><sdl:seg id="1 b" conf="Translated"/>'

    units = _split(seg_source, None, segs).units

    assert [sdlxliff_compat.segment_def(u).get('id') for u in units[:2]] == ['1 a', '1 b']
    assert [u.getid()[-10:] for u in units[:2]] == ['\x041_x0020_a', '\x041_x0020_b']


def test_editing_a_segment_changes_only_its_marker_and_status():
    target = _mrk(1, 'Eins.') + ' <x id="5"/>' + _mrk(2) + ' ' + _mrk(3, 'Drei.')
    unit = _split(THREE, target, THREE_SEGS).units[2]

    unit.rich_target = [StringElem('Neu.')]
    unit.set_state_n(StateEnum.FINAL)

    assert _target_xml(unit) == '<target>' + target.replace('Drei.', 'Neu.') + '</target>'
    assert [s.get('conf') for s in unit.xmlelement.iter(SDL + 'seg')] == [
        'Translated',
        None,
        'ApprovedTranslation',
    ]
    assert unit.xmlelement.get('{http://www.w3.org/XML/1998/namespace}space') is None


def test_a_locked_segment_does_not_lock_the_others():
    units = _split(THREE, _mrk(1) + _mrk(2, 'Alt.') + _mrk(3), THREE_SEGS).units

    units[1].target = 'Neu.'
    units[1].set_state_n(StateEnum.FINAL)
    units[0].target = 'Eins.'

    assert [u.target for u in units[:3]] == ['Eins.', 'Alt.', '']
    assert sdlxliff_compat.segment_def(units[1]).get('conf') is None


def test_a_missing_target_marker_is_added_in_segment_order():
    units = _split(THREE, ' <x id="5"/>' + _mrk(3, 'Drei.')).units

    units[1].target = 'Zwei.'
    units[0].rich_target = [StringElem('Eins.')]

    assert _target_xml(units[0]) == (
        '<target> <x id="5"/>'
        + _mrk(1, 'Eins.')
        + _mrk(2, 'Zwei.')
        + _mrk(3, 'Drei.')
        + '</target>'
    )


def test_a_missing_target_is_the_sources_layout_with_empty_segments():
    store = _split(THREE, None, THREE_SEGS)
    before = bytes(store)

    store.units[2].target = ''
    assert bytes(store) == before
    store.units[2].target = 'Drei.'

    assert _target_xml(store.units[0]) == (
        '<target>' + _mrk(1) + ' <x id="5"/>' + _mrk(2) + ' ' + _mrk(3, 'Drei.') + '</target>'
    )
    assert [u.target for u in store.units[:3]] == ['', '', 'Drei.']


def test_a_state_change_adds_a_missing_segment_definition():
    unit = _split(_mrk(1, 'One.') + _mrk(2, 'Two.'), _mrk(1) + _mrk(2, 'Zwei.')).units[1]

    unit.set_state_n(StateEnum.NEEDS_WORK)
    assert unit.xmlelement.find(SDL + 'seg-defs') is None
    unit.set_state_n(StateEnum.UNREVIEWED)

    seg_defs = unit.xmlelement.find(SDL + 'seg-defs')
    assert seg_defs.getprevious().tag == unit.namespaced('target')
    assert [dict(s.attrib) for s in seg_defs] == [{'id': '2', 'conf': 'Translated'}]
    assert unit.get_state_n() == StateEnum.UNREVIEWED


def test_each_segment_has_its_own_comments():
    store = _split(
        _mrk(1, 'Hello') + _mrk(2, '<mrk mtype="x-sdl-comment" sdl:cid="b">World</mrk>'),
        _mrk(1, '<mrk mtype="x-sdl-comment" sdl:cid="a">Hallo</mrk>') + _mrk(2, 'Welt'),
    )
    cmt_defs = etree.fromstring(
        f'<doc-info xmlns="{SDL[1:-1]}"><cmt-defs>'
        '<cmt-def id="a"><Comments><Comment user="Ann">One</Comment></Comments></cmt-def>'
        '<cmt-def id="b"><Comments><Comment user="Bob">Two</Comment></Comments></cmt-def>'
        '</cmt-defs></doc-info>'
    )
    store.document.getroot().insert(0, cmt_defs)

    assert sdlxliff_compat.comments(store.units[0]) == [('Ann', 'One')]
    assert sdlxliff_compat.comments(store.units[1]) == [('Bob', 'Two')]


def test_a_segment_is_its_markers_text_without_indentation():
    indented = '\n      <g id="1">%s</g>\n    '
    seg_source = '\n    ' + _mrk(1, indented % 'Hello') + '\n    ' + _mrk(2, 'a  b\n      c ')
    target = '\n    ' + _mrk(1, indented % 'Hallo') + '\n    ' + _mrk(2, 'x\ny ') + '\n  '
    store = _split(seg_source, target)
    before = bytes(store)

    units = store.units
    # A wrapped line is a space; a line break alone is text.
    assert [u.source for u in units[:2]] == ['Hello', 'a  b c ']
    assert [str(u.rich_target[0]) for u in units[:2]] == ['Hallo', 'x\ny ']
    assert [u.target for u in units[:2]] == ['Hallo', 'x\ny ']
    # What showing a unit does.
    units[0].rich_target = units[0].rich_target
    units[0].target = 'Hallo'
    assert bytes(store) == before


def test_segments_of_one_trans_unit_are_different_units():
    units = _split(_mrk(1, 'Same.') + _mrk(2, 'Same.'), None).units

    assert units[0] != units[1]
    assert units.index(units[1]) == 1
    assert units[0] == units[0].copy()
    assert units[1].copy().getid() == 'u1\x042'


@pytest.mark.parametrize(
    'seg_source,target,xml_ns',
    [
        # Not SDLXLIFF.
        (_mrk(1, 'One.') + _mrk(2, 'Two.'), None, False),
        # A target Trados didn't split.
        (_mrk(1, 'One.') + _mrk(2, 'Two.'), 'Eins. Zwei.', True),
        # Segments that can't be told apart.
        (_mrk(1, 'One.') + _mrk(1, 'Two.'), None, True),
        ('<mrk mtype="seg">One.</mrk>', None, True),
        # Text outside the only segment, as translate-toolkit wrote it.
        (_mrk(1, 'One.'), 'Eins.' + _mrk(1), True),
    ],
)
def test_a_trans_unit_stays_whole(seg_source, target, xml_ns):
    unit = _split(seg_source, target, xml_ns=xml_ns).units[0]

    assert type(unit) is xliff.xliffunit
