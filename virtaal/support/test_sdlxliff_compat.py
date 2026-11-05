#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from translate.storage import xliff
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
    unit = _unit('Translated')

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


def test_several_segments_keep_translate_toolkits_state():
    unit = _store(segs=_seg('Translated') + '<sdl:seg id="2" conf="Translated"/>').units[0]

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
