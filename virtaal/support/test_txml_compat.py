#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import glob
import io
import os

import pytest
from lxml import etree
from translate.storage import factory
from translate.storage.placeables import StringElem
from translate.storage.placeables.xliff import UnknownXML
from translate.storage.workflow import StateEnum

from virtaal.models import fileset
from virtaal.support import txml_compat

OKAPI = os.path.join('devsupport', 'testfiles', 'wordfast', 'okapi')
SAMPLES = sorted(glob.glob(os.path.join(OKAPI, '*.txml')))
TEST01 = os.path.join(OKAPI, 'Test01.docx.txml')
TEST02 = os.path.join(OKAPI, 'Test02.html.txml')

# Our own (CC0): states, a comment, a commented-out segment, CRLF.
OWN = (
    b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
    b'<txml locale="EN-US" version="1.0" targetlocale="DE" createdby="WF">\r\n'
    b'<translatable blockId="1">'
    b'<!--<segment segmentId="0"><source>Gone</source></segment>-->'
    b'<segment segmentId="1"><source>Open</source>'
    b'<comment creationid="Ann">Menu item</comment></segment>'
    b'<segment segmentId="2" unconfirmed="true" gtmt="false"><source>Close</source>'
    b'<target>Schliessen</target></segment>'
    b'<segment segmentId="3" gtmt="true"><source>Save</source>'
    b'<target score="85">Speichern</target></segment>'
    b'<segment segmentId="4" modified="true" gtmt="true"><source>Quit</source>'
    b'<target>Beenden</target></segment>'
    b'</translatable>\r\n'
    b'</txml>\r\n'
)


def _own():
    return txml_compat.TxmlFile.parsestring(OWN)


def _changed_lines(before, after):
    old, new = before.splitlines(), after.splitlines()
    assert len(old) == len(new)
    return [(a, b) for a, b in zip(old, new, strict=True) if a != b]


@pytest.mark.parametrize(
    ('name', 'count'),
    [('Test01.docx.txml', 10), ('Test02.html.txml', 10), ('Test03.mif.txml', 235)],
)
def test_the_okapi_samples_open_with_a_unit_per_segment(name, count):
    store = factory.getobject(os.path.join(OKAPI, name))

    assert isinstance(store, txml_compat.TxmlFile)
    assert len(store.units) == count
    assert (store.getsourcelanguage(), store.gettargetlanguage()) == ('en', 'fr')


@pytest.mark.parametrize('path', SAMPLES, ids=os.path.basename)
def test_a_sample_saves_byte_for_byte_after_showing_every_unit(path):
    with open(path, 'rb') as f:
        data = f.read()
    store = factory.getobject(path)
    for unit in store.units:
        assert unit.rich_source
        unit.rich_target = unit.rich_target
        unit.target = unit.target
        unit.set_state_n(unit.get_state_n())

    assert bytes(store) == data


def test_the_factory_opens_a_file_object_and_lists_the_format():
    with open(TEST02, 'rb') as f:
        upload = io.BytesIO(f.read())
    upload.name = 'upload.TXML'

    assert isinstance(factory.getobject(upload), txml_compat.TxmlFile)
    assert ('Wordfast TXML Translation File', ['txml'], ['application/x-wordfast-txml']) in (
        factory.supported
    )
    assert '.txml' in fileset.SCAN_EXTENSIONS


def test_states_from_the_target_unconfirmed_gtmt_and_modified():
    store = _own()

    assert [u.get_state_n() for u in store.units] == [
        StateEnum.EMPTY,
        StateEnum.NEEDS_REVIEW,
        StateEnum.NEEDS_REVIEW,
        StateEnum.UNREVIEWED,
    ]
    assert [u.getid() for u in store.units] == ['1:1', '1:2', '1:3', '1:4']
    sample = factory.getobject(TEST01).units
    # An unconfirmed edit, an edit, and a machine translation.
    assert [u.get_state_n() for u in sample[1:4]] == [80, 100, 80]


def test_marking_translated_or_for_review_writes_wordfasts_attributes():
    store = _own()
    _empty, unconfirmed, machine, _edited = store.units

    unconfirmed.set_state_n(StateEnum.UNREVIEWED)
    machine.set_state_n(StateEnum.UNREVIEWED)
    saved = bytes(store)
    assert b'<segment segmentId="2" gtmt="false">' in saved
    assert b'<segment segmentId="3" modified="true" gtmt="true">' in saved
    machine.set_state_n(StateEnum.NEEDS_REVIEW)
    assert b'<segment segmentId="3" modified="true" unconfirmed="true" gtmt="true">' in bytes(store)


def test_an_edit_changes_only_its_segment_and_marks_it_modified():
    store = _own()

    store.units[0].target = 'Oeffnen'

    changed = _changed_lines(OWN, bytes(store))
    assert len(changed) == 1
    old, new = changed[0]
    assert (
        old.replace(
            b'<segment segmentId="1"><source>Open</source>',
            b'<segment segmentId="1" modified="true"><source>Open</source><target>Oeffnen</target>',
        )
        == new
    )
    assert bytes(store).endswith(b'</translatable>\r\n</txml>\r\n')
    assert txml_compat.TxmlFile.parsestring(bytes(store)).units[0].target == 'Oeffnen'


def test_a_new_target_goes_after_the_whitespace_after_the_source():
    store = factory.getobject(TEST02)
    first = next(u for u in store.units if u.getid() == '3:1')

    first.target = 'Première phrase.'

    assert (
        b'<source>First sentence.</source><ws> </ws><target>Premi\xc3\xa8re phrase.</target>'
        in (bytes(store))
    )


def test_inline_codes_are_placeables_written_back_as_ut():
    store = factory.getobject(TEST02)
    unit = store.units[1]
    codes = [e for e in unit.rich_source[0].flatten() if isinstance(e, UnknownXML)]

    assert [(c.xml_node.tag, c.xml_node.get('id'), str(c)) for c in codes[:2]] == [
        ('ph', '1', '<b>'),
        ('ph', '2', '</b>'),
    ]
    unit.rich_target = [unit.rich_source[0].copy()]
    assert b'<target score="0">Text in <ut type="unknown" x="1">&lt;b&gt;</ut>bold' in bytes(store)
    target = etree.fromstring(bytes(store)).findall('.//segment')[1].find('target')
    assert [ut.get('id') for ut in target] == [None] * 4


def test_clearing_a_target_removes_it():
    store = _own()

    store.units[1].rich_target = [StringElem('')]

    assert b'<source>Close</source></segment>' in bytes(store)
    assert store.units[1].get_state_n() == StateEnum.EMPTY


def test_comments_are_notes_and_commented_out_segments_are_left_alone():
    store = _own()

    assert store.units[0].getnotes() == 'Menu item'
    assert store.units[0].comments() == [('Ann', 'Menu item')]
    assert store.units[0].getnotes('programmer') == ''
    assert 'Gone' not in [u.source for u in store.units]
    assert store.units[2].is_machine_translation() and store.units[2].match_score() == 85
    assert store.units[3].match_score() is None


def test_languages_are_read_and_written_as_wordfast_writes_them():
    store = _own()

    assert (store.getsourcelanguage(), store.gettargetlanguage()) == ('en_US', 'de')
    store.settargetlanguage('pt_BR')
    assert b'targetlocale="PT-BR"' in bytes(store)


def test_a_file_that_is_not_txml_is_refused():
    with pytest.raises(ValueError):
        txml_compat.TxmlFile.parsestring(b'<xliff version="1.2"/>')
