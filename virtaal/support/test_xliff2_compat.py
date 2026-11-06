#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from io import BytesIO

from translate.storage import factory, xliff, xliff2
from translate.storage.workflow import StateEnum

from virtaal.support import xliff2_compat

XLIFF2 = b'''<?xml version="1.0" encoding="UTF-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0" srcLang="en" trgLang="af">
  <file id="f1">
    <unit id="1"><notes><note category="developer">A menu item</note></notes>
      <segment><source>Open</source></segment></unit>
    <unit id="2"><segment state="initial"><source>Close</source><target>Maak toe</target></segment></unit>
    <unit id="3"><segment state="translated"><source>Save</source><target>Stoor</target></segment></unit>
    <unit id="4"><segment state="final"><source>Quit</source><target>Verlaat</target></segment></unit>
  </file>
</xliff>
'''

XLIFF1 = b'''<?xml version="1.0" encoding="UTF-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2">
  <file original="a" datatype="plaintext" source-language="en" target-language="af">
    <body><trans-unit id="1"><source>Open</source></trans-unit></body>
  </file>
</xliff>
'''


def _store(tmp_path, data=XLIFF2, name='doc.xlf'):
    path = tmp_path / name
    path.write_bytes(data)
    return factory.getobject(str(path))


def test_the_factory_opens_an_xliff2_file_by_its_path_or_bytes(tmp_path):
    store = _store(tmp_path)
    upload = BytesIO(XLIFF2)
    upload.name = 'doc.xliff'

    assert isinstance(store, xliff2.Xliff2File)
    assert isinstance(factory.getobject(upload), xliff2.Xliff2File)
    assert [u.source for u in store.units] == ['Open', 'Close', 'Save', 'Quit']


def test_the_factory_still_opens_xliff1_as_xliff1(tmp_path):
    store = _store(tmp_path, XLIFF1)

    assert isinstance(store, xliff.xlifffile)
    assert not isinstance(store, xliff2.Xliff2File)


def test_is_xliff2_reads_the_version_or_the_namespace():
    assert xliff2_compat.is_xliff2(b'<xliff version="2.1">')
    assert xliff2_compat.is_xliff2(b'<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0">')
    assert not xliff2_compat.is_xliff2(XLIFF1)
    assert not xliff2_compat.is_xliff2(b'<other version="2.0"/>')


def test_an_its_version_on_xliff1_is_not_xliff2():
    # Okapi and other ITS 2.0 tools mark XLIFF 1.2 this way.
    head = (
        b'<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2"'
        b' xmlns:its="http://www.w3.org/2005/11/its" its:version="2.0">'
    )
    assert not xliff2_compat.is_xliff2(head)


def test_segment_states_read_as_virtaals_states(tmp_path):
    states = [u.get_state_n() for u in _store(tmp_path).units]

    assert states == [
        StateEnum.EMPTY,
        StateEnum.NEEDS_WORK,
        StateEnum.UNREVIEWED,
        StateEnum.FINAL,
    ]
    for state in states:
        assert any(low <= state < high for low, high in xliff2.Xliff2Unit.STATE.values())


def test_setting_a_state_writes_the_segments_state(tmp_path):
    store = _store(tmp_path)
    opened, fuzzy, translated, final = store.units

    opened.target = 'Oop'
    opened.set_state_n(StateEnum.UNREVIEWED)
    fuzzy.set_state_n(StateEnum.FINAL)
    translated.set_state_n(StateEnum.NEEDS_WORK)
    final.set_state_n(StateEnum.FINAL)

    reread = xliff2.Xliff2File.parsestring(bytes(store)).units
    assert [u.xmlelement.get('state') for u in reread] == [
        'translated',
        'reviewed',
        'initial',
        'final',
    ]


def test_notes_are_read_from_and_added_to_the_unit(tmp_path):
    store = _store(tmp_path)
    unit = store.units[0]

    unit.addnote('Check the menu', 'translator')
    store.units[1].addnote('Second')

    assert unit.getnotes('developer') == 'A menu item'
    assert unit.getnotes() == 'A menu item\nCheck the menu'
    reread = xliff2.Xliff2File.parsestring(bytes(store)).units
    assert reread[1].getnotes() == 'Second'
    unit_element = reread[1].xmlelement.getparent()
    assert unit_element[0].tag == reread[1].namespaced('notes')


def test_removenotes_by_origin_keeps_the_others(tmp_path):
    unit = _store(tmp_path).units[0]
    unit.addnote('Check the menu', 'translator')

    unit.removenotes('translator')
    assert unit.getnotes() == 'A menu item'
    unit.removenotes()
    assert unit.getnotes() == ''
    assert unit.xmlelement.getparent().find(unit.namespaced('notes')) is None


def test_storemodel_opens_and_saves_an_xliff2_file(tmp_path):
    from virtaal.models.storemodel import StoreModel

    class _Controller:
        def compare_stats(self, old, new):
            pass

    path = tmp_path / 'doc.xlf'
    path.write_bytes(XLIFF2)
    model = StoreModel(str(path), _Controller())
    model.get_units()[0].target = 'Oop'

    model.save_file()

    saved = StoreModel(str(path), _Controller())
    assert len(saved) == 4
    assert saved.get_units()[0].target == 'Oop'
    assert b'version="2.0"' in path.read_bytes()
