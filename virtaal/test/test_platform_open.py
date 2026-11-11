#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Opening a translation from a translation server (#3337): the fake
Weblate's PO and JSON files downloaded, opened with Weblate's states
and locks, saved locally only and opened again from the work file."""

import os

import pytest
from test_scaffolding import TestScaffolding
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.support import catxliff_compat
from virtaal.support.platforms import workcopy
from virtaal.support.platforms.browsedialog import COL_NAME, RESPONSE_OPEN, BrowseDialog
from virtaal.support.platforms.servers import ServerList


def _rows(model, parent=None):
    it = model.iter_children(parent)
    while it is not None:
        yield it
        it = model.iter_next(it)


def _find(model, name, parent=None):
    return next(it for it in _rows(model, parent) if model[it][COL_NAME] == name)


def _idle(dialog):
    run_until(lambda: not dialog.spinner.get_visible())


def _expand(dialog, it):
    path = dialog.filtered.get_path(it)
    dialog.treeview.expand_row(path, False)
    _idle(dialog)
    return dialog.filtered.get_iter(path)


class TestPlatformOpen(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _work(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pan_app, 'ui_language', 'en')
        monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        self.errors = []
        monkeypatch.setattr(self.main_controller, 'show_error', self.errors.append)
        monkeypatch.setattr(
            self.main_controller.view,
            'ask_template',
            lambda *args: pytest.fail('A work copy never asks for its template'),
        )
        self.fake = demo().start()
        platform = WeblatePlatform(str(tmp_path / 'weblate.ini'))
        self.servers = ServerList([platform], str(tmp_path / 'platforms.ini'))
        self.servers.put(platform, self.fake.url, {'token': TOKEN})
        self.store_controller.close_file()
        yield
        self.store_controller.close_file()
        self.fake.stop()
        assert self.fake.schema_problems == []

    def _dialog(self, component='Program', language='Afrikaans'):
        dialog = BrowseDialog(self.servers)
        _idle(dialog)
        model = dialog.filtered
        hello = _expand(dialog, _find(model, 'Hello'))
        parent = _expand(dialog, _find(model, component, hello))
        dialog.treeview.get_selection().select_iter(_find(model, language, parent))
        return dialog

    def _download(self, component='Program'):
        dialog = self._dialog(component)
        assert dialog.open_button.get_sensitive()
        # As the button does: the dialog stays open for the download.
        dialog.dialog.response(RESPONSE_OPEN)
        run_until(lambda: dialog.opened is not None or dialog.status.get_text())
        dialog.dialog.destroy()
        return dialog

    def _open(self, component='Program'):
        dialog = self._download(component)
        assert dialog.opened, dialog.status.get_text()
        assert self.main_controller.open_file(dialog.opened), self.errors
        return dialog.opened

    def _unit(self, source):
        store = self.store_controller.store
        return next(u for u in store.get_units() if str(u.source) == source)

    def _states(self):
        return {str(u.source): u.get_state_n() for u in self.store_controller.store.get_units()}

    def _edit(self, source, text):
        sc = self.store_controller
        unit = self._unit(source)
        sc.cursor.force_index(sc.store.index_of(unit))
        self.unit_controller.load_unit(unit)
        self.unit_controller.set_unit_target(0, [text])
        self.unit_controller.finish_current_unit()

    def test_open_is_offered_for_a_language_only(self):
        dialog = BrowseDialog(self.servers)
        _idle(dialog)
        assert not dialog.open_button.get_sensitive()
        dialog.treeview.get_selection().select_iter(_find(dialog.filtered, 'Hello'))
        assert not dialog.open_button.get_sensitive()
        dialog.dialog.destroy()

    def test_a_po_file_opens_with_weblates_states(self):
        path = self._open()

        sc = self.store_controller
        assert sc.work_copy is not None and sc.work_copy.path == path
        assert path.startswith(os.path.join(pan_app.get_state_dir(), 'platforms', 'weblate'))
        assert self._states() == {
            'Open': StateEnum.FINAL,
            'Save': StateEnum.UNREVIEWED,
            'Close': StateEnum.NEEDS_REVIEW,
            'Quit': StateEnum.REJECTED,
            'Print': StateEnum.NEEDS_WORK,
            'File': StateEnum.UNREVIEWED,
            'Weblate': StateEnum.UNREVIEWED,
            'Help': StateEnum.EMPTY,
        }
        assert self.errors == []

    def test_the_title_names_the_project_component_and_platform(self):
        self._open()

        assert self.main_controller.get_store_title() == 'Hello/Program (Weblate)'
        assert 'Hello/Program (Weblate)' in self.main_controller.view.main_window.get_title()

    def test_a_read_only_unit_opens_locked(self):
        self._open()

        unit = self._unit('Weblate')
        assert catxliff_compat.is_locked(unit)
        view = self.unit_controller.load_unit(unit)
        assert view.locked
        assert not catxliff_compat.is_locked(self._unit('Open'))

    def test_saving_writes_the_local_copy_only_and_notes_the_changes(self):
        path = self._open()
        asked = len(self.fake.requests)

        self._edit('Help', 'Hulp')
        assert self.main_controller.save_file()

        assert len(self.fake.requests) == asked
        with open(path, 'rb') as f:
            assert b'Hulp' in f.read()
        help_unit = self.fake.projects['hello']['components']['program']['units']['af'][7]
        changed = workcopy.for_file(path).changed_units()
        assert [u['id'] for u in changed] == [help_unit['id']]

    def test_reopening_the_work_file_restores_its_context_offline(self):
        path = self._open()
        self._unit('Save').set_state_n(StateEnum.FINAL)
        self._unit('Open').set_state_n(StateEnum.REJECTED)
        self.store_controller.set_modified(True)
        assert self.main_controller.save_file()
        self.store_controller.close_file()
        self.fake.stop()

        assert self.main_controller.open_file(path)

        sc = self.store_controller
        assert sc.work_copy is not None
        states = self._states()
        assert states['Save'] == StateEnum.FINAL
        assert states['Open'] == StateEnum.REJECTED
        assert states['Close'] == StateEnum.NEEDS_REVIEW
        assert catxliff_compat.is_locked(self._unit('Weblate'))
        assert self.main_controller.get_store_title() == 'Hello/Program (Weblate)'

    def test_the_work_file_is_what_recent_files_records(self, monkeypatch):
        from virtaal.views import recent

        added = []
        monkeypatch.setattr(recent.rm, 'add_item', added.append)

        path = self._open()

        assert any(url.endswith(path) for url in added)

    def test_local_work_is_not_downloaded_over(self):
        path = self._open()
        self._edit('Help', 'Hulp')
        self.main_controller.save_file()
        files = len(self.fake.requests_to('/api/translations/hello/program/af/file/'))

        dialog = self._download()

        assert dialog.opened == path
        assert len(self.fake.requests_to('/api/translations/hello/program/af/file/')) == files

    def test_a_monolingual_file_opens_with_its_template(self):
        path = self._open('Website')

        store = self.store_controller.store
        assert path.endswith(os.path.join('website', 'af', 'locales', 'af.json'))
        assert store.pair is not None
        keyed = {u.getid(): u for u in store.get_units()}
        assert sorted(keyed) == ['open', 'save', 'welcome']
        assert keyed['open'].get_state_n() == StateEnum.FINAL
        assert str(keyed['welcome'].target) == 'Welkom'
        assert self.main_controller.get_store_title() == 'Hello/Website (Weblate)'

    def test_an_android_file_keeps_its_values_folder(self):
        from virtaal.models.fileset import path_language

        res = 'app/src/main/res/'
        self.fake.add_component(
            'hello',
            'android',
            'Android',
            file_format='aresource',
            filemask=res + 'values-*/strings.xml',
            template=res + 'values/strings.xml',
        )
        self.fake.add_translation(
            'hello', 'android', 'af', 'Afrikaans', filename=res + 'values-af/strings.xml'
        )
        self.fake.fill_android(
            'hello', 'android', 'af', {'open': 'Open', 'save': 'Save'}, {'open': 'Maak oop'}
        )

        path = self._open('Android')

        folder = self.store_controller.work_copy.folder
        parts = ('app', 'src', 'main', 'res')
        assert path == os.path.join(folder, *parts, 'values-af', 'strings.xml')
        assert self.store_controller.work_copy.template_path() == os.path.join(
            folder, *parts, 'values', 'strings.xml'
        )
        assert path_language(path, folder) == 'af'
        store = self.store_controller.store
        assert store.pair is not None
        keyed = {u.getid(): u for u in store.get_units()}
        assert sorted(keyed) == ['open', 'save']
        assert str(keyed['open'].target) == 'Maak oop'
        assert keyed['open'].get_state_n() == StateEnum.UNREVIEWED
        assert keyed['save'].get_state_n() == StateEnum.EMPTY

    def test_a_monolingual_files_states_survive_saving_and_reopening(self):
        path = self._open('Website')
        self._edit('Save', 'Stoor')
        assert self.main_controller.save_file()
        self.store_controller.close_file()

        assert self.main_controller.open_file(path)

        keyed = {u.getid(): u for u in self.store_controller.store.get_units()}
        assert keyed['open'].get_state_n() == StateEnum.FINAL
        # Typed in before the state timer, it's marked for review.
        assert keyed['save'].get_state_n() == StateEnum.NEEDS_REVIEW
        assert keyed['welcome'].get_state_n() == StateEnum.UNREVIEWED
        changed = workcopy.for_file(path).changed_units()
        assert [u['context'] for u in changed] == ['save']

    @pytest.mark.parametrize(
        'status, message',
        [
            (500, 'The server replied with error 500.'),
            (401, 'The server refused the token.'),
        ],
    )
    def test_a_download_error_is_shown_in_the_dialog(self, status, message):
        self.fake.fail('/api/translations/hello/program/af/file/', status)

        dialog = self._download()

        assert dialog.opened is None
        assert dialog.status.get_text() == message
        assert dialog.open_button.get_sensitive()

    def test_a_format_virtaal_cant_open_gives_the_usual_message(self):
        self.fake.add_component('hello', 'data', 'Data', file_format='binary')
        self.fake.add_translation('hello', 'data', 'af', 'Afrikaans', filename='data/af.bin')
        self.fake.set_file('hello', 'data', 'af', b'\x00\x01 not a translation', [])

        dialog = self._download('Data')

        assert dialog.opened and os.path.isfile(dialog.opened)
        assert not self.main_controller.open_file(dialog.opened)
        assert len(self.errors) == 1 and 'Could not open file.' in self.errors[0]
        # The work folder stays.
        assert os.path.isfile(dialog.opened)
