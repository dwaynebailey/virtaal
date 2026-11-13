#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Work copies of a platform's files: the work folder, the side record,
the unit map and the platform's states on a PO file."""

import hashlib
import json
import os
from types import SimpleNamespace

import pytest
from translate.storage import po
from translate.storage.workflow import StateEnum

from virtaal.common import pan_app
from virtaal.support import catxliff_compat
from virtaal.support.platforms import Download, RemoteUnit, states, workcopy

PO = b'''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Open"
msgstr "Maak oop"

msgctxt "menu"
msgid "Open"
msgstr "Open"

msgid "Save"
msgstr "Stoor"

msgid "Save"
msgstr "Bewaar"

#, fuzzy
msgid "Close"
msgstr "Maak toe"

msgid "Help"
msgstr ""
'''


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))


def _remote(id, position, context, source, target='', state=StateEnum.UNREVIEWED, code=20):
    return RemoteUnit(id, position, context, [source], [target], state, code, None, code == 100)


def _store(data=PO):
    return po.pofile.parsestring(data)


def _units(store):
    return [u for u in store.units if not u.isheader()]


def _entries(remote):
    return workcopy.record_units(remote)


PLATFORM = SimpleNamespace(id='weblate', name='Weblate')
SERVER = 'https://weblate.example.org:8443/api/'


# The work folder #


def test_the_work_folder_is_under_the_state_dir_by_platform_host_and_slugs(tmp_path):
    folder = workcopy.work_folder('weblate', SERVER, 'hello', 'guides/manual', 'pt_BR')

    expected = ['platforms', 'weblate', 'weblate.example.org_8443', 'hello', 'guides', 'manual']
    assert folder == os.path.join(str(tmp_path / 'state'), *expected, 'pt_BR')


def test_a_platform_without_components_leaves_that_level_out(tmp_path):
    folder = workcopy.work_folder('dummy', 'dummy://one/', 'alpha', None, 'af')

    assert folder == os.path.join(
        str(tmp_path / 'state'), 'platforms', 'dummy', 'one', 'alpha', 'af'
    )


@pytest.mark.parametrize(
    'slug, part',
    [
        ('..', '_'),
        ('', '_'),
        ('a:b', 'a_b'),
        (' trailing. ', 'trailing'),
        ('x\\y', 'x_y'),
        ('con', '_con'),
        ('NUL.po', '_NUL.po'),
        ('com1.txt', '_com1.txt'),
        ('console', 'console'),
    ],
)
def test_a_slug_is_made_a_safe_file_name(slug, part):
    assert workcopy.safe_part(slug) == part


@pytest.mark.parametrize(
    'path, parts',
    [
        ('po/af.po', ['po', 'af.po']),
        ('res/values-af/strings.xml', ['res', 'values-af', 'strings.xml']),
        ('../../etc/passwd', ['etc', 'passwd']),
        ('po/./../af.po', ['po', 'af.po']),
        ('/etc/af.po', ['etc', 'af.po']),
        ('C:\\Windows\\af.po', ['C_', 'Windows', 'af.po']),
        ('//server/share/af.po', ['server', 'share', 'af.po']),
        ('po/aux.po', ['po', '_aux.po']),
        ('', ['_']),
    ],
)
def test_a_platforms_path_stays_inside_the_work_folder(path, parts):
    assert workcopy.safe_path(path) == parts


# Matching #


def test_po_units_match_by_context_then_source_then_position():
    local = _units(_store())
    remote = [
        _remote(11, 1, '', 'Open', 'Maak oop'),
        _remote(12, 2, 'menu', 'Open', 'Open'),
        _remote(13, 3, '', 'Save', 'Stoor'),
        _remote(14, 4, '', 'Save', 'Bewaar'),
        _remote(15, 5, '', 'Close', 'Maak toe', StateEnum.NEEDS_WORK, 10),
        _remote(16, 6, '', 'Help', '', StateEnum.EMPTY, 0),
    ]
    # The server's order needn't be the file's.
    remote.reverse()

    found = workcopy.match(local, _entries(remote))

    ids = {remote[r].id: str(local[i].target) for r, i in found.items()}
    assert ids == {
        11: 'Maak oop',
        12: 'Open',
        13: 'Stoor',
        14: 'Bewaar',
        15: 'Maak toe',
        16: '',
    }


def test_a_unit_the_file_lacks_matches_nothing():
    local = _units(_store())
    remote = [_remote(21, 1, '', 'Print', 'Druk'), _remote(22, 2, 'menu', 'Quit', 'Verlaat')]

    assert workcopy.match(local, _entries(remote)) == {}


def test_a_key_matches_though_its_source_changed():
    keyed = [SimpleNamespace(getid=lambda k=k: k, source=s) for k, s in (('a', 'A'), ('b', 'B'))]
    remote = [_remote(31, 1, 'b', 'B'), _remote(32, 2, 'a', 'A changed')]

    found = workcopy.match(keyed, _entries(remote), keyed=True)

    assert found == {0: 1, 1: 0}


# States on a PO file #


@pytest.mark.parametrize(
    'state, fuzzy',
    [
        (StateEnum.FINAL, False),
        (StateEnum.UNREVIEWED, False),
        (StateEnum.NEEDS_REVIEW, True),
        (StateEnum.REJECTED, True),
        (StateEnum.NEEDS_WORK, True),
    ],
)
def test_po_shows_the_platforms_state_and_stores_the_nearest(state, fuzzy):
    store = _store()
    unit = _units(store)[0]
    states.adopt(unit)

    unit.set_state_n(state)

    assert unit.get_state_n() == state
    assert unit.get_state_id() == state
    assert unit.isfuzzy() == fuzzy
    again = _units(_store(bytes(store)))[0]
    assert again.isfuzzy() == fuzzy


def test_po_offers_all_six_states_once_adopted():
    unit = _units(_store())[0]
    assert sorted(k for k in unit.STATE if k >= 0) == [0, 30, 100]

    states.adopt(unit, StateEnum.FINAL)

    assert sorted(k for k in unit.STATE if k >= 0) == list(states.PLATFORM_STATES)
    assert isinstance(unit, po.pounit)
    assert unit.get_state_n() == StateEnum.FINAL


def test_a_state_the_file_contradicts_gives_way_to_the_file():
    # Approved on the server, made fuzzy elsewhere since.
    unit = _units(_store())[4]
    assert unit.isfuzzy()

    states.adopt(unit, StateEnum.FINAL)

    assert unit.get_state_n() == StateEnum.NEEDS_WORK


def test_a_read_only_unit_is_locked():
    unit = _units(_store())[0]
    assert not catxliff_compat.is_locked(unit)

    states.lock(unit)

    assert catxliff_compat.is_locked(unit)


# The side record #


def _download(data=PO, template=None):
    remote = [
        _remote(11, 1, '', 'Open', 'Maak oop', StateEnum.FINAL, 30),
        _remote(12, 2, 'menu', 'Open', 'Open', None, 100),
        _remote(15, 5, '', 'Close', 'Maak toe', StateEnum.REJECTED, 11),
    ]
    return Download('af.po', data, template, remote, {'project': 'Hello', 'component': 'Program'})


def _create(**kwargs):
    return workcopy.create(PLATFORM, SERVER, 'hello', 'program', 'af', _download(**kwargs))


def test_a_download_is_written_with_its_side_record(tmp_path):
    copy = _create()

    folder = workcopy.work_folder('weblate', SERVER, 'hello', 'program', 'af')
    assert copy.path == os.path.join(folder, 'af.po')
    with open(copy.path, 'rb') as f:
        assert f.read() == PO
    with open(os.path.join(folder, '.platform.json'), 'rb') as f:
        record = json.loads(f.read().decode('utf-8'))
    assert record['server'] == SERVER
    assert (record['project'], record['component'], record['language']) == (
        'hello',
        'program',
        'af',
    )
    assert record['downloaded_at'].endswith('Z')
    assert len(record['sha256']) == 64
    first = record['units'][0]
    assert first['id'] == 11 and first['state'] == 30 and first['virtaal_state'] == 120
    assert first['target_hash'] == workcopy.text_hash(['Maak oop'])
    assert record['units'][1]['read_only'] is True


def test_the_file_and_template_keep_their_paths_as_the_platform_has_them(tmp_path):
    from virtaal.support import baseline

    download = _download(template=('res/values/strings.xml', b'<resources/>'))
    copy = workcopy.create(
        PLATFORM,
        SERVER,
        'hello',
        'android',
        'af',
        download._replace(filename='res/values-af/strings.xml'),
    )

    folder = workcopy.work_folder('weblate', SERVER, 'hello', 'android', 'af')
    assert copy.folder == folder
    assert copy.path == os.path.join(folder, 'res', 'values-af', 'strings.xml')
    template = os.path.join(folder, 'res', 'values', 'strings.xml')
    assert copy.template_path() == template
    assert baseline.remembered_template(copy.path) == template
    assert copy.record['file'] == 'res/values-af/strings.xml'
    assert copy.record['template'] == 'res/values/strings.xml'
    assert os.path.isfile(os.path.join(folder, '.platform.json'))


def test_a_nested_work_file_finds_its_record_and_its_template_none():
    download = _download(template=('locales/en.json', b'{}'))
    copy = workcopy.create(
        PLATFORM,
        SERVER,
        'hello',
        'website',
        'af',
        download._replace(filename='locales/af.json'),
    )

    again = workcopy.for_file(copy.path)

    assert again.path == copy.path and again.folder == copy.folder
    assert workcopy.for_file(copy.template_path()) is None
    found = workcopy.existing('weblate', SERVER, 'hello', 'website', 'af')
    assert found.path == copy.path


def test_a_template_at_the_files_own_path_goes_aside():
    copy = workcopy.create(
        PLATFORM, SERVER, 'hello', 'odd', 'af', _download(template=('AF.po', b'x'))
    )

    assert copy.template_path() == os.path.join(copy.folder, '_template', 'AF.po')


def test_a_path_out_of_the_work_folder_is_kept_in_it():
    copy = workcopy.create(
        PLATFORM,
        SERVER,
        'hello',
        'program',
        'af',
        _download()._replace(filename='../../../outside.po'),
    )

    assert copy.path == os.path.join(copy.folder, 'outside.po')


def test_the_work_file_finds_its_record_again():
    copy = _create()

    again = workcopy.for_file(copy.path)

    assert again.record == copy.record
    assert workcopy.for_file(copy.path + '.bak') is None


def test_a_file_elsewhere_has_no_work_copy(tmp_path):
    other = tmp_path / 'elsewhere' / 'af.po'
    other.parent.mkdir()
    other.write_bytes(PO)
    (other.parent / '.platform.json').write_bytes(json.dumps(_create().record).encode('utf-8'))

    assert workcopy.for_file(str(other)) is None


class _Store:
    pair = None

    def __init__(self, store):
        self.store = store

    def get_units(self):
        return _units(self.store)


def test_attaching_shows_states_and_locks():
    copy = _create()
    store = po.pofile.parsefile(copy.path)

    copy.attach(_Store(store))

    units = _units(store)
    assert units[0].get_state_n() == StateEnum.FINAL
    assert catxliff_compat.is_locked(units[1])
    # Rejected, the file has it fuzzy.
    assert units[4].get_state_n() == StateEnum.REJECTED
    assert units[2].get_state_n() == 100 and not states.is_adopted(units[2])


def test_saving_notes_the_units_changed_since_download():
    copy = _create()
    store = po.pofile.parsefile(copy.path)
    copy.attach(_Store(store))
    units = _units(store)
    units[4].set_state_n(StateEnum.NEEDS_REVIEW)
    store.save()

    copy.saved()

    record = workcopy.for_file(copy.path).record
    assert [u['id'] for u in record['units'] if u['changed']] == [15]
    assert record['units'][2]['local_state'] == StateEnum.NEEDS_REVIEW
    with open(copy.path, 'rb') as f:
        assert record['saved_sha256'] == hashlib.sha256(f.read()).hexdigest()


def test_a_new_text_is_a_change():
    copy = _create()
    store = po.pofile.parsefile(copy.path)
    copy.attach(_Store(store))
    _units(store)[0].target = 'Open dit'
    store.save()

    copy.saved()

    assert [u['id'] for u in copy.changed_units()] == [11]


def test_work_not_submitted_is_kept():
    copy = _create()
    assert not workcopy.existing('weblate', SERVER, 'hello', 'program', 'af').has_local_changes()

    with open(copy.path, 'ab') as f:
        f.write(b'\nmsgid "New"\nmsgstr "Nuut"\n')

    assert workcopy.existing('weblate', SERVER, 'hello', 'program', 'af').has_local_changes()
    assert workcopy.existing('weblate', SERVER, 'hello', 'other', 'af') is None


XLIFF2 = b'''<?xml version="1.0" encoding="UTF-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0" srcLang="en" trgLang="af">
 <file id="f1">
  <unit id="b2"><segment><source>Save</source><target>Bewaar</target></segment></unit>
  <unit id="a1"><segment><source>Save</source><target>Stoor</target></segment></unit>
 </file>
</xliff>
'''


def test_a_file_built_from_the_units_matches_them_by_id():
    from translate.storage import xliff2

    remote = [
        _remote('a1', 1, 'a1', 'Save', 'Stoor'),
        _remote('b2', 2, 'b2', 'Save', 'Bewaar'),
    ]
    options = {'origin': workcopy.BUILT, 'platform_format': 'DOCX', 'placeables': 'ruby'}
    download = Download('af.xlf', XLIFF2, None, remote, {'project': 'Hello'}, options)
    copy = workcopy.create(PLATFORM, SERVER, 'hello', 'program', 'af', download)
    store = xliff2.Xliff2File.parsefile(copy.path)
    model = SimpleNamespace(get_units=lambda: store.units)

    copy.attach(model)

    # Both "Save": by their ids, not in file order.
    assert {r: str(unit.target) for r, unit in copy.matched.items()} == {0: 'Stoor', 1: 'Bewaar'}
    assert model.placeable_format == 'ruby'
    record = workcopy.for_file(copy.path).record
    assert (record['origin'], record['platform_format']) == ('units', 'DOCX')


def test_a_downloaded_file_has_no_origin_nor_placeables():
    copy = _create()
    store = po.pofile.parsefile(copy.path)
    model = _Store(store)

    copy.attach(model)

    assert copy.record['origin'] is None and copy.record['placeables'] is None
    assert not hasattr(model, 'placeable_format')
