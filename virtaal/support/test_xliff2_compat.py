#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from io import BytesIO

import pytest
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


@pytest.mark.parametrize(
    ('key', 'readable'),
    [
        ('users.welcome_title', True),
        ('checkout_button_label', True),
        ('greeting', True),
        ('en.greeting', True),
        ('menu.items.[0]', True),
        ('IDS_FILE_OPEN', True),
        ('заголовок', True),
        ('5d41402abc4b2a76b9719d911017c592', False),
        ('2fd4e1c67a2d28fced849ee1bb76e7391b93eb12', False),
        ('5D41402ABC4B2A76B9719D911017C592:menu', True),
        ('5d41402abc4b2a76b9719d911017c592:3', False),
        ('123e4567-e89b-12d3-a456-426614174000', False),
        ('42', False),
        ('1.2.3', False),
        ('[0]', False),
        ('p1', False),
        ('seg_12', False),
        ('tu-3', False),
        ('', False),
        (None, False),
        ('  ', False),
        ('Save', False),
        ('save  file', False),
        ('Close', True),
    ],
)
def test_readable_key(key, readable):
    assert xliff2_compat.readable_key(key, ['Save file', 'Save']) is readable


NAMED = b'''<?xml version="1.0" encoding="UTF-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0"
 xmlns:pgs="urn:oasis:names:tc:xliff:pgs:1.0" version="2.2" srcLang="en" trgLang="af">
  <file id="f1">
    <unit id="1" name="checkout_button_label"><segment><source>Pay</source></segment></unit>
    <unit id="2" name="p2"><segment><source>Save</source></segment></unit>
    <unit id="3" name="Open"><segment><source>Open</source></segment></unit>
    <unit id="4"><segment><source>Quit</source></segment></unit>
    <unit id="5" name="files.count" pgs:switch="plural:count">
      <segment pgs:case="one"><source>%d file</source></segment>
      <segment pgs:case="other"><source>%d files</source></segment></unit>
  </file>
</xliff>
'''


def test_a_readable_name_is_the_units_location(tmp_path):
    from virtaal.models.storemodel import StoreModel

    path = tmp_path / 'named.xlf'
    path.write_bytes(NAMED)
    model = StoreModel(str(path), object())

    assert [u.getlocations() for u in model.get_units()] == [
        ['checkout_button_label'],
        [],
        [],
        [],
        ['files.count'],
    ]


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


ODD_LAYOUT = (
    b"\xef\xbb\xbf<?xml version='1.0' encoding='UTF-8'?>\r\n"
    b"<xliff xmlns='urn:oasis:names:tc:xliff:document:2.0' version='2.0' srcLang='en' trgLang='af'>\r\n"
    b"<file id='f1'><unit id='1'><segment><source>Open</source></segment></unit>\r\n"
    b"      <unit id='2' ><segment state='initial'><source>Close &amp; quit</source>"
    b"<target>Maak toe</target></segment></unit>\r\n"
    b"</file></xliff>"
)


def test_an_unchanged_file_saves_byte_for_byte():
    store = xliff2.Xliff2File.parsestring(ODD_LAYOUT)
    for unit in store.units:
        unit.target = unit.target

    assert bytes(store) == ODD_LAYOUT


def test_an_edit_changes_only_its_unit():
    store = xliff2.Xliff2File.parsestring(ODD_LAYOUT)

    store.units[0].target = 'Oop'
    store.units[0].set_state_n(StateEnum.UNREVIEWED)

    saved = bytes(store)
    assert saved == ODD_LAYOUT.replace(
        b"<segment><source>Open</source></segment>",
        b"<segment state=\"translated\"><source>Open</source><target>Oop</target></segment>",
    )
    assert bytes(xliff2.Xliff2File.parsestring(saved)) == saved


def test_a_change_outside_the_units_saves_through_the_toolkit():
    store = xliff2.Xliff2File.parsestring(ODD_LAYOUT)

    store.settargetlanguage('nl')

    saved = bytes(store)
    assert saved.startswith(b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\n')
    assert b'trgLang="nl"' in saved
    assert [u.target for u in xliff2.Xliff2File.parsestring(saved).units] == [None, 'Maak toe']


SPACED = (
    b'<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0" srcLang="en" trgLang="af">\n'
    b'  <file id="f1">\n'
    b'    <unit id="1">\n'
    b'      <segment state="translated" subState="my:done">\n'
    b'        <source>Open   <ph id="1"/>  now</source>\n'
    b'        <target>Maak   <ph id="1"/>  oop</target>\n'
    b'      </segment>\n'
    b'    </unit>\n'
    b'    <unit id="2">\n'
    b'      <segment>\n'
    b'        <source>Close</source>\n'
    b'      </segment>\n'
    b'    </unit>\n'
    b'  </file>\n'
    b'</xliff>\n'
)


def test_reading_and_setting_the_text_it_has_leaves_the_file_as_it_was():
    store = xliff2.Xliff2File.parsestring(SPACED)
    for unit in store.units:
        assert unit.rich_source
        unit.rich_target = unit.rich_target

    assert [str(u.rich_target[0]) for u in store.units] == ['Maak  oop', '']
    assert bytes(store) == SPACED


def test_a_new_target_goes_on_its_own_line():
    store = xliff2.Xliff2File.parsestring(SPACED)

    store.units[1].target = 'Maak toe'

    assert bytes(store) == SPACED.replace(
        b'<source>Close</source>\n',
        b'<source>Close</source>\n        <target>Maak toe</target>\n',
    )


def test_a_removed_target_takes_its_line_and_leaves_the_segment_initial():
    store = xliff2.Xliff2File.parsestring(SPACED)

    store.units[0].target = None

    assert bytes(store) == SPACED.replace(
        b'        <target>Maak   <ph id="1"/>  oop</target>\n', b''
    ).replace(b'state="translated" subState="my:done"', b'state="initial"')


def test_a_new_state_drops_the_old_substate():
    store = xliff2.Xliff2File.parsestring(SPACED)
    segment = store.units[0].xmlelement

    store.units[0].set_state_n(StateEnum.UNREVIEWED)
    assert segment.get('subState') == 'my:done'
    store.units[0].set_state_n(StateEnum.FINAL)
    assert (segment.get('state'), segment.get('subState')) == ('reviewed', None)
