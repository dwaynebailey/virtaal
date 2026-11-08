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
from lxml import etree
from translate.storage import factory, xliff
from translate.storage.placeables import StringElem
from translate.storage.workflow import StateEnum

from virtaal.models import fileset
from virtaal.support import catxliff_compat, memoq_compat

MEMOQ = os.path.join('devsupport', 'testfiles', 'memoq')
SAMPLES = sorted(glob.glob(os.path.join(MEMOQ, '*', '*.mqxliff')))
# One row per status, then the same locked (translate="no").
STATUSES = os.path.join(MEMOQ, 'mqxliff-viewer', 'translation_status_sample.xlsx_jpn.mqxliff')
# Hand-written: a lock without translate="no", MachineTranslated.
PATOIS = os.path.join(MEMOQ, 'patois', 'sample.mqxliff')
MQ = memoq_compat.MQ
XLIFF = '{urn:oasis:names:tc:xliff:document:1.2}'


def _store(rows, extra_ns=''):
    data = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2"'
        f' xmlns:mq="MQXliff"{extra_ns}><file original="a" source-language="en"'
        ' target-language="de" datatype="plaintext"><body>' + rows + '</body></file></xliff>'
    )
    return xliff.xlifffile.parsestring(data.encode('utf-8'))


def _row(status='NotStarted', target='Hallo', attributes=''):
    target = (
        '<target xml:space="preserve"/>'
        if target is None
        else (f'<target xml:space="preserve">{target}</target>')
    )
    return (
        f'<trans-unit id="1" mq:status="{status}"{attributes}>'
        f'<source xml:space="preserve">Hello</source>{target}</trans-unit>'
    )


def _unit(status='NotStarted', target='Hallo', attributes=''):
    return _store(_row(status, target, attributes)).units[0]


def _saved(store):
    out = io.BytesIO()
    store.serialize(out)
    return out.getvalue()


def test_translate_toolkit_still_ignores_the_memoq_status(monkeypatch):
    # Once this fails translate-toolkit reads mq:status, and the state
    # part of catxliff_compat can go.
    unit = _unit('ManuallyConfirmed')
    monkeypatch.setattr(xliff.xliffunit, 'get_state_n', catxliff_compat._get_state_n)
    monkeypatch.setattr(xliff.xliffunit, 'isapproved', catxliff_compat._isapproved)

    assert unit.get_state_n() == StateEnum.NEEDS_WORK


def test_the_extensions_open_as_xliff():
    for extension in ('mqxliff', 'mxliff'):
        assert factory.getclass(f'a.{extension}') is xliff.Xliff1File
        assert any(extension in extensions for _name, extensions, _m in factory.supported_files())
    assert {'.mqxliff', '.mxliff'} <= set(fileset.SCAN_EXTENSIONS)


def test_a_folder_scan_finds_memoq_and_phrase_files(tmp_path):
    for name in ('a.mqxliff', 'b.mxliff', 'c.txt'):
        (tmp_path / name).write_bytes(b'')

    found = fileset.scan_directory(str(tmp_path))

    assert sorted(os.path.basename(path) for path in found) == ['a.mqxliff', 'b.mxliff']


@pytest.mark.parametrize(
    'status, target, state_n',
    [
        ('NotStarted', None, StateEnum.EMPTY),
        ('NotStarted', 'Hallo', StateEnum.NEEDS_WORK),
        ('PreTranslated', 'Hallo', StateEnum.NEEDS_REVIEW),
        ('MachineTranslated', 'Hallo', StateEnum.NEEDS_REVIEW),
        ('AssembledFromFragments', 'Hallo', StateEnum.NEEDS_REVIEW),
        ('PartiallyEdited', 'Hallo', StateEnum.NEEDS_REVIEW),
        ('Edited', 'Hallo', StateEnum.NEEDS_REVIEW),
        ('Rejected', 'Hallo', StateEnum.REJECTED),
        ('ManuallyConfirmed', 'Hallo', StateEnum.UNREVIEWED),
        ('Reviewer1Confirmed', 'Hallo', StateEnum.FINAL),
        ('Proofread', 'Hallo', StateEnum.FINAL + 1),
        ('SomethingNew', 'Hallo', StateEnum.NEEDS_WORK),
    ],
)
def test_a_memoq_status_is_read_as_a_state(status, target, state_n):
    assert _unit(status, target).get_state_n() == state_n


def test_the_real_samples_states():
    units = factory.getobject(STATUSES).units

    assert [u.get_state_n() for u in units] == [
        StateEnum.EMPTY,
        StateEnum.NEEDS_REVIEW,
        StateEnum.NEEDS_REVIEW,
        StateEnum.UNREVIEWED,
        StateEnum.FINAL,
        StateEnum.FINAL + 1,
        StateEnum.EMPTY,
        StateEnum.NEEDS_REVIEW,
        StateEnum.NEEDS_REVIEW,
        StateEnum.UNREVIEWED,
        StateEnum.FINAL,
        StateEnum.FINAL + 1,
    ]
    assert [catxliff_compat.is_locked(u) for u in units] == [False] * 6 + [True] * 6


@pytest.mark.parametrize(
    'state_n, status',
    [
        (StateEnum.NEEDS_WORK, 'NotStarted'),
        (StateEnum.REJECTED, 'Rejected'),
        (StateEnum.NEEDS_REVIEW, 'PartiallyEdited'),
        (StateEnum.UNREVIEWED, 'ManuallyConfirmed'),
        (StateEnum.FINAL, 'Reviewer1Confirmed'),
        (StateEnum.FINAL + 1, 'Proofread'),
        # A number inside a step writes that step's status.
        (StateEnum.NEEDS_REVIEW + 2, 'PartiallyEdited'),
    ],
)
def test_a_state_is_written_as_its_memoq_status(state_n, status):
    unit = _unit('PreTranslated' if status != 'PartiallyEdited' else 'Rejected')

    unit.set_state_n(state_n)

    assert unit.xmlelement.get(MQ + 'status') == status


def test_a_state_change_writes_only_the_status():
    store = _store(_row('PreTranslated'))
    unit = store.units[0]
    before = dict(unit.xmlelement.attrib), dict(unit.get_target_dom().attrib)

    unit.set_state_n(StateEnum.UNREVIEWED)
    unit.markfuzzy(False)
    unit.marktranslated()

    assert dict(unit.xmlelement.attrib) == {**before[0], MQ + 'status': 'ManuallyConfirmed'}
    assert dict(unit.get_target_dom().attrib) == before[1]
    assert 'approved' not in _saved(store).decode() and 'state=' not in _saved(store).decode()


def test_the_current_state_leaves_a_status_in_the_same_step():
    unit = _unit('MachineTranslated')

    unit.set_state_n(StateEnum.NEEDS_REVIEW)

    assert unit.xmlelement.get(MQ + 'status') == 'MachineTranslated'


def test_typing_a_translation_confirms_it():
    unit = _unit('NotStarted', None)

    unit.target = 'Hallo'

    assert unit.xmlelement.get(MQ + 'status') == 'ManuallyConfirmed'
    assert unit.xmlelement.get('approved') is None
    assert unit.xmlelement.get('{http://www.w3.org/XML/1998/namespace}space') is None


def test_editing_an_inserted_translation_makes_it_edited():
    inserted, confirmed = _unit('PreTranslated'), _unit('Proofread')

    inserted.rich_target = [StringElem('Neu')]
    confirmed.rich_target = [StringElem('Neu')]

    assert inserted.xmlelement.get(MQ + 'status') == 'PartiallyEdited'
    assert confirmed.xmlelement.get(MQ + 'status') == 'Proofread'


@pytest.mark.parametrize(
    'status, fuzzy, translated',
    [
        ('PreTranslated', True, False),
        ('Rejected', True, False),
        ('ManuallyConfirmed', False, True),
        ('Proofread', False, True),
    ],
)
def test_translated_and_fuzzy_follow_the_status(status, fuzzy, translated):
    unit = _unit(status)

    assert (unit.isfuzzy(), unit.istranslated()) == (fuzzy, translated)


def test_marking_fuzzy_needs_review():
    unit = _unit('ManuallyConfirmed')

    unit.markfuzzy()

    assert unit.xmlelement.get(MQ + 'status') == 'PartiallyEdited'
    assert unit.get_state_n() == StateEnum.NEEDS_REVIEW


def test_a_memoq_unit_has_memoqs_steps_and_others_keep_theirs():
    plain = xliff.xlifffile.parsestring(
        b'<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2"><file original="a"'
        b' source-language="en" datatype="plaintext"><body><trans-unit id="1"><source>a</source>'
        b'</trans-unit></body></file></xliff>'
    ).units[0]

    assert StateEnum.REJECTED in _unit().STATE
    assert StateEnum.REJECTED not in plain.STATE
    assert catxliff_compat.flavour(plain) is None


@pytest.mark.parametrize('lock', [' mq:locked="locked"', ' mq:locked="locked" translate="no"'])
def test_a_locked_row_keeps_its_target_and_status(lock):
    unit = _unit('PartiallyEdited', 'Hallo', lock)

    unit.target = 'Neu'
    unit.rich_target = [StringElem('Neu')]
    unit.set_state_n(StateEnum.FINAL)
    unit.markfuzzy(False)

    assert catxliff_compat.is_locked(unit)
    assert (unit.target, unit.xmlelement.get(MQ + 'status')) == ('Hallo', 'PartiallyEdited')


def test_a_lock_without_translate_no_is_read_only():
    units = factory.getobject(PATOIS).units

    assert [(u.istranslatable(), catxliff_compat.is_locked(u)) for u in units][2] == (True, True)


def test_the_match_rate_comments_and_last_user():
    units = factory.getobject(STATUSES).units
    unit = _unit(
        'PreTranslated',
        'Hallo',
        ' mq:percent="101" mq:lastchanginguser="Ikue"',
    )
    comments = etree.fromstring(
        '<mq:comments xmlns:mq="MQXliff"><mq:comment creatoruser="Rev">Check  this</mq:comment>'
        '<mq:comment creatoruser="Rev" deleted="true">Gone</mq:comment></mq:comments>'
    )
    unit.xmlelement.append(comments)

    assert memoq_compat.match_rate(unit) == '101'
    assert memoq_compat.last_user(unit) == 'Ikue'
    assert memoq_compat.comments(unit) == [('Rev', 'Check this')]
    # The sample's comments are its notes too.
    assert memoq_compat.comments(units[0]) == []
    assert memoq_compat.match_rate(units[0]) is None


def test_the_version_history_is_not_the_unit():
    store = factory.getobject(STATUSES)
    unit = store.units[1]

    unit.target = 'Neu'

    historical = unit.xmlelement.find(f'{MQ}minorversions/{MQ}historical-unit')
    assert historical.find(XLIFF + 'target').text is None
    assert historical.find(XLIFF + 'source').text == 'This line is edited.'
    assert historical.get(MQ + 'status') == 'NotStarted'


@pytest.mark.parametrize('sample', SAMPLES, ids=os.path.basename)
def test_a_sample_saves_byte_for_byte(sample):
    with open(sample, 'rb') as f:
        data = f.read()

    store = factory.getobject(sample)
    for unit in store.units:
        # What showing a unit does.
        unit.rich_target = unit.rich_target

    assert _saved(store) == data


@pytest.mark.parametrize('sample', SAMPLES, ids=os.path.basename)
def test_an_edit_changes_only_that_target_and_status(sample, tmp_path):
    with open(sample, 'rb') as f:
        data = f.read()
    store = factory.getobject(sample)
    unit = [u for u in store.units if u.istranslatable() and not catxliff_compat.is_locked(u)][-1]
    status = unit.xmlelement.get(MQ + 'status')

    unit.target = 'Neu & <so>'
    saved = _saved(store)

    old_lines, new_lines = data.splitlines(), saved.splitlines()
    assert len(old_lines) == len(new_lines)
    changed = [(a, b) for a, b in zip(old_lines, new_lines, strict=True) if a != b]
    # The target's line and the trans-unit's, unless they are one.
    assert 1 <= len(changed) <= 2
    assert any(b'Neu &amp; &lt;so&gt;</target>' in new for _old, new in changed)
    for old, new in changed:
        old = old.replace(f'mq:status="{status}"'.encode(), b'mq:status="ManuallyConfirmed"')
        assert old == new or b'Neu &amp; &lt;so&gt;</target>' in new
    assert saved[:3] == data[:3]
    reopened = xliff.xlifffile.parsestring(saved)
    assert [u.target for u in reopened.units].count('Neu & <so>') == 1


def test_a_memoq_file_saved_as_xlf_gets_the_same(tmp_path):
    path = tmp_path / 'statuses.xlf'
    shutil.copy(STATUSES, path)

    units = factory.getobject(str(path)).units

    assert units[3].get_state_n() == StateEnum.UNREVIEWED
    assert catxliff_compat.is_locked(units[-1])


def test_a_later_save_compares_with_the_last_one():
    store = factory.getobject(STATUSES)
    store.units[1].target = 'Eins'
    first = _saved(store)

    store.units[2].target = 'Zwei'
    second = _saved(store)

    assert first.count(b'Eins') == second.count(b'Eins') == 1
    assert len(first.splitlines()) == len(second.splitlines())


def test_a_change_outside_the_units_saves_through_translate_toolkit():
    with open(STATUSES, 'rb') as f:
        data = f.read()
    store = factory.getobject(STATUSES)

    store.settargetlanguage('de')
    saved = _saved(store)

    assert saved.startswith(b'\xef\xbb\xbf<?xml')
    assert b'target-language="de"' in saved
    assert saved != data
    assert len(xliff.xlifffile.parsestring(saved).units) == 12


def test_a_crlf_file_keeps_its_line_ends():
    data = (
        '<?xml version="1.0" encoding="utf-8"?>\r\n<xliff version="1.2"'
        ' xmlns="urn:oasis:names:tc:xliff:document:1.2" xmlns:mq="MQXliff">\r\n<file'
        ' original="a" source-language="en" datatype="plaintext">\r\n<body>\r\n'
        + _row('NotStarted', None).replace('><', '>\r\n<')
        + '\r\n'
        + _row('NotStarted', None).replace('id="1"', 'id="2"').replace('><', '>\r\n<')
        + '\r\n</body>\r\n</file>\r\n</xliff>\r\n'
    ).encode('utf-8')
    store = xliff.xlifffile.parsestring(data)

    store.units[1].target = 'Hallo\nWelt'
    saved = _saved(store)

    assert saved.count(b'\r\n') == data.count(b'\r\n') + 1
    assert b'\n' not in saved.replace(b'\r\n', b'')
