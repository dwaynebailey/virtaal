#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import glob
import io
import os
import shutil

import pytest
from translate.storage import factory, xliff
from translate.storage.placeables import StringElem
from translate.storage.workflow import StateEnum

from virtaal.support import catxliff_compat, phrase_compat

PHRASE = os.path.join('devsupport', 'testfiles', 'phrase')
SAMPLES = sorted(glob.glob(os.path.join(PHRASE, '*', '*.mxliff')))
# A Phrase TMS export: {1}{2} tags, every segment confirmed.
XLFQL = os.path.join(PHRASE, 'xlfql', 'sample.mxliff')
# Hand-written: confirmed 0 and 1, a lock, scores and origins.
PATOIS = os.path.join(PHRASE, 'patois', 'sample.mxliff')
M = phrase_compat.M


def _store(segment, level=1):
    data = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2"'
        f' xmlns:m="http://www.memsource.com/mxlf/2.0" version="1.2" m:version="2.15" m:level="{level}">'
        '<file original="a.docx" source-language="en" target-language="de" datatype="x-undefined">'
        '<body><group id="0">' + segment + '</group></body></file></xliff>'
    )
    return xliff.xlifffile.parsestring(data.encode('utf-8'))


def _segment(confirmed='0', target='Hallo', attributes=''):
    target = '' if target is None else f'<target>{target}</target>'
    return (
        f'<trans-unit id="s1" xml:space="preserve" m:confirmed="{confirmed}"'
        f' m:locked="false"{attributes}><source>Hello</source>{target}</trans-unit>'
    )


def _unit(confirmed='0', target='Hallo', attributes='', level=1):
    return _store(_segment(confirmed, target, attributes), level).units[0]


def _saved(store):
    out = io.BytesIO()
    store.serialize(out)
    return out.getvalue()


def test_translate_toolkit_still_ignores_phrases_confirmation(monkeypatch):
    # Once this fails translate-toolkit reads m:confirmed, and the state
    # part of catxliff_compat can go.
    unit = _unit('1')
    monkeypatch.setattr(xliff.xliffunit, 'get_state_n', catxliff_compat._get_state_n)
    monkeypatch.setattr(xliff.xliffunit, 'isapproved', catxliff_compat._isapproved)

    assert unit.get_state_n() == StateEnum.NEEDS_WORK


@pytest.mark.parametrize(
    'confirmed, target, level, state_n',
    [
        ('0', None, 1, StateEnum.EMPTY),
        ('0', 'Hallo', 1, StateEnum.NEEDS_REVIEW),
        ('1', 'Hallo', 1, StateEnum.UNREVIEWED),
        # Confirmed at translation, exported for review: still Translated.
        ('1', 'Hallo', 2, StateEnum.UNREVIEWED),
        ('2', 'Hallo', 2, StateEnum.UNREVIEWED),
        ('1', None, 1, StateEnum.EMPTY),
    ],
)
def test_the_confirmation_is_read_as_a_state(confirmed, target, level, state_n):
    assert _unit(confirmed, target, level=level).get_state_n() == state_n


def test_the_real_and_hand_written_samples_states():
    assert [u.get_state_n() for u in factory.getobject(XLFQL).units] == [StateEnum.UNREVIEWED] * 3
    units = factory.getobject(PATOIS).units
    assert [u.get_state_n() for u in units] == [
        StateEnum.EMPTY,
        StateEnum.NEEDS_REVIEW,
        StateEnum.UNREVIEWED,
        StateEnum.NEEDS_REVIEW,
    ]
    assert [catxliff_compat.is_locked(u) for u in units] == [False, True, False, False]


@pytest.mark.parametrize('level', [1, 3])
def test_confirming_writes_the_files_level(level):
    unit = _unit('0', level=level)

    unit.set_state_n(StateEnum.UNREVIEWED)

    assert unit.xmlelement.get(M + 'confirmed') == str(level)
    unit.set_state_n(StateEnum.NEEDS_REVIEW)
    assert unit.xmlelement.get(M + 'confirmed') == '0'


def test_a_phrase_segment_has_three_steps():
    unit = _unit()

    assert sorted(unit.STATE) == [StateEnum.EMPTY, StateEnum.NEEDS_REVIEW, StateEnum.UNREVIEWED]
    unit.set_state_n(StateEnum.FINAL)
    assert unit.xmlelement.get(M + 'confirmed') == '1'
    unit.markfuzzy()
    assert unit.xmlelement.get(M + 'confirmed') == '0'


def test_a_state_change_writes_only_the_confirmation():
    store = _store(_segment('0'))
    unit = store.units[0]
    before = dict(unit.xmlelement.attrib), dict(unit.get_target_dom().attrib)

    unit.marktranslated()
    unit.target = 'Neu'

    assert dict(unit.xmlelement.attrib) == {**before[0], M + 'confirmed': '1'}
    assert dict(unit.get_target_dom().attrib) == before[1]
    saved = _saved(store).decode()
    assert 'approved' not in saved and 'state=' not in saved


def test_a_confirmed_segment_stays_confirmed_at_its_level():
    unit = _unit('2', level=2)

    unit.target = 'Neu'

    assert unit.xmlelement.get(M + 'confirmed') == '2'


def test_a_locked_segment_keeps_its_target_and_confirmation():
    unit = _store(_segment('0').replace('m:locked="false"', 'm:locked="true"')).units[0]

    unit.target = 'Neu'
    unit.rich_target = [StringElem('Neu')]
    unit.set_state_n(StateEnum.UNREVIEWED)

    assert catxliff_compat.is_locked(unit)
    assert (unit.target, unit.xmlelement.get(M + 'confirmed')) == ('Hallo', '0')


@pytest.mark.parametrize(
    'attributes, expected',
    [
        (' m:score="1.01" m:trans-origin="tm"', ('tm', '101')),
        (' m:score="0.62" m:trans-origin="mt"', ('mt', '62')),
        (' m:score="0" m:trans-origin="null"', (None, None)),
        (' m:score="98.5"', (None, '98.5')),
        ('', (None, None)),
    ],
)
def test_the_origin_and_score(attributes, expected):
    assert phrase_compat.origin(_unit('1', 'Hallo', attributes)) == expected


@pytest.mark.parametrize('sample', SAMPLES, ids=lambda p: p.split(os.sep)[-2])
def test_a_sample_saves_byte_for_byte(sample):
    with open(sample, 'rb') as f:
        data = f.read()

    store = factory.getobject(sample)
    for unit in store.units:
        unit.rich_target = unit.rich_target

    assert _saved(store) == data


def test_an_edit_changes_only_that_target_and_its_confirmation():
    with open(PATOIS, 'rb') as f:
        data = f.read()
    store = factory.getobject(PATOIS)

    store.units[3].target = 'Neu'
    store.units[3].marktranslated()
    saved = _saved(store)

    changed = [(a, b) for a, b in zip(data.splitlines(), saved.splitlines(), strict=True) if a != b]
    assert changed == [
        (
            b'<trans-unit id="seg3" xml:space="preserve" m:para-id="0" m:confirmed="0"'
            b' m:locked="false" m:score="0.62" m:trans-origin="mt">',
            b'<trans-unit id="seg3" xml:space="preserve" m:para-id="0" m:confirmed="1"'
            b' m:locked="false" m:score="0.62" m:trans-origin="mt">',
        ),
        (b'<target>Entwurf.</target>', b'<target>Neu</target>'),
    ]


def test_the_metadata_and_tags_stay_as_they_are():
    with open(XLFQL, 'rb') as f:
        data = f.read()
    store = factory.getobject(XLFQL)

    store.units[0].target = '{1}{2}This is a test.'
    saved = _saved(store)

    assert saved.replace(b'{1}{2}This is a test.', b'{1}{2}That is a test.') == data


def test_a_phrase_file_saved_as_xlf_gets_the_same(tmp_path):
    path = tmp_path / 'sample.xlf'
    shutil.copy(PATOIS, path)

    units = factory.getobject(str(path)).units

    assert units[2].get_state_n() == StateEnum.UNREVIEWED
    assert catxliff_compat.is_locked(units[1])
