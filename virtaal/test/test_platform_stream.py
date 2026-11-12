#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Opening several translations from a translation server at once
(#3337), against the fake Weblate: a component's languages, or one
language in every component of a project, open together with the
Files Panel; Submit, Update and sending as you go then work file by
file, with one question and one list of conflicts."""

import os

import pytest
from gi.repository import Gtk
from test_scaffolding import TestScaffolding
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import TOKEN, demo_many, run_until
from virtaal.common import pan_app
from virtaal.controllers.filepanelcontroller import FilePanelController
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.support.platforms import Registry, submit, workcopy
from virtaal.support.platforms.browsedialog import COL_NAME, RESPONSE_OPEN, BrowseDialog
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.submitdialog import ConflictDialog, SubmitDialogs
from virtaal.support.platforms.updatedialog import UpdateDialogs
from virtaal.views import filepanelview

AF_COMPONENTS = [
    'Hello/Program (Afrikaans)',
    'Hello/Website (Afrikaans)',
    'Hello/Installer (Afrikaans)',
]


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


class Streaming:
    """The fake Weblate with several languages and components, a server
    list holding it and a registry with Weblate on."""

    @pytest.fixture(autouse=True)
    def _work(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pan_app, 'ui_language', 'en')
        monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        monkeypatch.setattr(
            self.main_controller.view,
            'ask_template',
            lambda *args: pytest.fail('A work copy never asks for its template'),
        )
        self.errors = []
        monkeypatch.setattr(self.main_controller, 'show_error', self.errors.append)
        self.told = []
        for dialogs in (SubmitDialogs, UpdateDialogs):
            monkeypatch.setattr(dialogs, '_tell', lambda _self, text, *a: self.told.append(text))
        self.failed = []
        monkeypatch.setattr(
            BrowseDialog, '_tell_failed', lambda _self, text: self.failed.append(text)
        )
        self.confirmed = []

        def confirm(dialogs, count):
            self.confirmed.append((count, [c.label() for c in dialogs.sending]))
            return True

        monkeypatch.setattr(SubmitDialogs, 'confirm', confirm)
        self.fake = demo_many().start()
        self.platform = WeblatePlatform(str(tmp_path / 'weblate.ini'))
        self.servers = ServerList([self.platform], str(tmp_path / 'platforms.ini'))
        self.servers.put(self.platform, self.fake.url, {'token': TOKEN})
        self.registry = Registry()
        self.registry._reminded = True
        monkeypatch.setattr(self.registry, 'servers', lambda: self.servers)
        self.registry.add(self.platform, self.main_controller)
        self.store_controller.close_file()
        yield
        self.store_controller.close_file()
        self.registry.remove(self.platform)
        self.fake.stop()
        assert self.fake.schema_problems == []

    @property
    def sc(self):
        return self.main_controller.store_controller

    def _browser(self):
        dialog = BrowseDialog(self.servers)
        _idle(dialog)
        hello = _expand(dialog, _find(dialog.filtered, 'Hello'))
        self.hello_path = dialog.filtered.get_path(hello)
        return dialog

    def _choose(self, dialog, component, languages):
        model = dialog.filtered
        hello = model.get_iter(self.hello_path)
        parent = _expand(dialog, _find(model, component, hello))
        selection = dialog.treeview.get_selection()
        selection.unselect_all()
        for language in languages:
            selection.select_iter(_find(model, language, parent))
        return parent

    def _wait(self, dialog):
        run_until(lambda: dialog.opened is not None or not dialog._downloading, timeout=30)

    def _open_languages(self, languages=('Afrikaans', 'German', 'Zulu')):
        dialog = self._browser()
        self._choose(dialog, 'Program', languages)
        dialog.dialog.response(RESPONSE_OPEN)
        self._wait(dialog)
        dialog.dialog.destroy()
        return dialog

    def _open_components(self):
        dialog = self._browser()
        self._choose(dialog, 'Program', ['Afrikaans'])
        dialog.open_in_all_components(dialog.chosen()[0])
        self._wait(dialog)
        dialog.dialog.destroy()
        return dialog

    def _opened(self, dialog):
        assert dialog.opened_all, (dialog.status.get_text(), self.failed)
        assert self.registry._open_paths(dialog.opened_all), self.errors
        return dialog.opened_all

    # Units of the stream, read or not #

    def _index(self, component, source):
        for segment in self.sc.store.segments:
            if os.sep + component + os.sep not in segment.path:
                continue
            for local, unit in enumerate(segment.units()):
                if str(unit.source) == source:
                    return segment.offset + local
        raise AssertionError('%s not in %s' % (source, component))

    def _unit(self, component, source):
        return self.sc.store.get_unit(self._index(component, source))

    def _edit(self, component, source, text, state=None):
        index = self._index(component, source)
        self.sc.cursor.force_index(index)
        unit = self.sc.store.get_unit(index)
        self.unit_controller.load_unit(unit)
        self.unit_controller.set_unit_target(0, [text])
        if state is not None:
            self.unit_controller.set_current_state(state, from_user=True)
        self.unit_controller.finish_current_unit()

    def _server_unit(self, component, source, code='af'):
        units = self.fake.projects['hello']['components'][component]['units'][code]
        return next(u for u in units if u['source'] == [source])

    def _status(self):
        bar = self.main_controller.view.status_bar
        return bar.get_message_area().get_children()[0].get_text()

    def _patched(self):
        return sorted(unit_id for unit_id, _data in self.fake.patched)


class TestOpenMany(Streaming, TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    def test_a_components_languages_open_together(self):
        paths = self._opened(self._open_languages())

        assert len(paths) == 3 and self.sc.is_stream()
        assert sorted(self.sc.all_files.languages()) == ['af', 'de', 'zu']
        assert sorted(c.label() for c in self.sc.work_copies.values()) == [
            'Hello/Program (Afrikaans)',
            'Hello/Program (German)',
            'Hello/Program (Zulu)',
        ]
        # The Files Panel's language picks the one shown.
        assert self.main_controller.open_stream_language('de')
        assert self.sc.work_copy.record['language'] == 'de'
        assert str(self._unit('program', 'Open').target) == 'Öffnen'
        assert self.main_controller.view.main_window.get_title().startswith(
            'Hello/Program (Weblate)'
        )

    def test_a_languages_menu_opens_it_in_every_component(self):
        dialog = self._browser()
        # Not for several languages chosen.
        self._choose(dialog, 'Program', ['Afrikaans', 'German'])
        assert not dialog._popup(None)
        self._choose(dialog, 'Program', ['Afrikaans'])

        assert dialog._popup(None)

        [item] = dialog._menu.get_children()
        assert item.get_label() == 'Open in All _Components'
        dialog._menu.popdown()
        item.activate()
        self._wait(dialog)
        assert len(dialog.opened_all) == 3
        dialog.dialog.destroy()

    def test_a_language_opens_in_every_component(self):
        paths = self._opened(self._open_components())

        assert self.sc.is_stream() and len(self.sc.fileset) == 3
        assert [workcopy.for_file(p).label() for p in paths] == AF_COMPONENTS
        # The glossary isn't one of them; the website's template pairs.
        assert str(self._unit('website', 'Welcome').target) == 'Welkom'
        assert self._unit('program', 'Open').get_state_n() == StateEnum.FINAL
        assert self.errors == []

    def test_downloads_are_counted_with_cancel(self, monkeypatch):
        from virtaal.support.platforms.submitdialog import ProgressDialog

        seen = []
        update = ProgressDialog.update

        def counted(progress, number, total):
            update(progress, number, total)
            seen.append((progress.label.get_text(), progress.cancel_button.get_visible()))

        monkeypatch.setattr(ProgressDialog, 'update', counted)
        dialog = self._browser()
        self._choose(dialog, 'Program', ['Afrikaans', 'German', 'Zulu'])

        dialog.open_selected()
        self._wait(dialog)

        assert seen == [
            ('Downloading 1 of 3…', True),
            ('Downloading 2 of 3…', True),
            ('Downloading 3 of 3…', True),
        ]
        assert dialog.progress is None and len(dialog.opened_all) == 3
        dialog.dialog.destroy()

    def test_cancel_opens_nothing(self):
        dialog = self._browser()
        self._choose(dialog, 'Program', ['Afrikaans', 'German', 'Zulu'])
        dialog.open_selected()
        assert dialog.progress.label.get_text() == 'Downloading 1 of 3…'

        dialog.progress.dialog.response(Gtk.ResponseType.CANCEL)
        self._wait(dialog)

        assert dialog.opened is None and dialog.opened_all == []
        assert dialog.progress is None and dialog.open_button.get_sensitive()
        assert self.failed == []
        dialog.dialog.destroy()

    def test_a_partial_failure_opens_the_rest_and_lists_what_failed(self):
        self.fake.fail('/api/translations/hello/installer/af/file/', 500)

        paths = self._opened(self._open_components())

        assert [workcopy.for_file(p).label() for p in paths] == AF_COMPONENTS[:2]
        assert self.failed == [
            '1 translation could not be downloaded:\n'
            '  Hello/Installer (Afrikaans)\n'
            '    The server replied with error 500.'
        ]
        assert len(self.sc.fileset) == 2

    def test_a_language_the_server_doesnt_have_is_left_out_quietly(self):
        self.fake.fail('/api/translations/hello/program/de/file/', 404)
        self.fake.fail('/api/translations/hello/program/zu/file/', 500)

        paths = self._opened(self._open_languages())

        assert [workcopy.for_file(p).label() for p in paths] == ['Hello/Program (Afrikaans)']
        assert self.failed == [
            '1 translation could not be downloaded:\n'
            '  Hello/Program (Zulu)\n'
            '    The server replied with error 500.'
        ]

    def test_when_the_server_has_none_of_them_the_browser_says_so(self):
        for code in ('af', 'de'):
            self.fake.fail('/api/translations/hello/installer/%s/file/' % code, 404)
        dialog = self._browser()
        self._choose(dialog, 'Installer', ['Afrikaans', 'German'])

        dialog.open_selected()
        self._wait(dialog)

        assert dialog.opened is None and dialog.opened_all == [] and self.failed == []
        assert dialog.status.get_text() == 'The server replied with error 404.'
        dialog.dialog.destroy()

    def test_when_every_download_fails_the_browser_says_why(self):
        for code in ('af', 'de'):
            self.fake.fail('/api/translations/hello/installer/%s/file/' % code, 500)
        dialog = self._browser()
        self._choose(dialog, 'Installer', ['Afrikaans', 'German'])

        dialog.open_selected()
        self._wait(dialog)

        assert dialog.opened is None
        assert dialog.status.get_text() == 'The server replied with error 500.'
        dialog.dialog.destroy()

    def test_unsubmitted_work_is_bold_per_language_and_opens_as_it_is(self):
        self._opened(self._open_languages(('Afrikaans', 'German')))
        assert self.main_controller.open_stream_language('de')
        self._edit('program', 'Close', 'Schließen')
        assert self.main_controller.save_file()
        self.sc.close_file()
        downloads = len(self.fake.requests_to('/api/translations/hello/program/de/file/'))

        dialog = self._browser()
        parent = self._choose(dialog, 'Program', ['Afrikaans', 'German'])
        model = dialog.filtered
        weights = {model[it][COL_NAME]: model[it][6] for it in _rows(model, parent)}
        assert weights['German'] > weights['Afrikaans'] == weights['Zulu']
        assert dialog.status.get_text() == '1 change to Hello/Program is not yet submitted.'
        dialog.dialog.response(RESPONSE_OPEN)
        self._wait(dialog)
        dialog.dialog.destroy()

        assert len(self.fake.requests_to('/api/translations/hello/program/de/file/')) == downloads
        self._opened(dialog)
        assert self.registry.notice_text() == '1 change to Hello/Program is not yet submitted.'


class TestStreamNames(Streaming, TestScaffolding):
    """A work copy's header in the unit list and its name in the Files
    Panel: its project and component, with its language in the unit
    list when the files opened are in several."""

    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)
        self.panel = FilePanelController(self.main_controller)

    def _headers(self):
        store = self.sc.store
        return [store.file_start(offset).display_name for offset, _entry in store.file_offsets()]

    def _panel_names(self):
        model = self.panel.view.model
        names = []

        def walk(it, prefix):
            while it is not None:
                names.append(prefix + model[it][filepanelview.COL_NAME])
                walk(model.iter_children(it), names[-1] + '/')
                it = model.iter_next(it)

        walk(model.get_iter_first(), '')
        return names

    def test_one_language_names_each_file_by_its_component(self):
        self._opened(self._open_components())

        assert self._headers() == ['Hello/Program', 'Hello/Website', 'Hello/Installer']
        # The project holding them all adds nothing; the website's
        # template is under it.
        assert self._panel_names() == ['Program', 'Website', 'Website/en.json', 'Installer']
        assert self.sc.modified_stream_files() == []
        self._edit('website', 'Welcome', 'Welkom hier')
        assert self.sc.modified_stream_files() == ['Hello/Website']

    def test_several_languages_add_the_language(self):
        self._opened(self._open_languages(('Afrikaans', 'German')))

        assert self._headers() == ['Hello/Program (Afrikaans)']
        # The panel's language list says which.
        assert self._panel_names() == ['Program']
        assert self.main_controller.open_stream_language('de')
        assert self._headers() == ['Hello/Program (German)']
        assert self._panel_names() == ['Program']


class TestStreamSubmitUpdate(Streaming, TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    def test_submit_sends_every_files_changes_with_one_question(self):
        self._opened(self._open_components())
        self._edit('program', 'Help', 'Hulp')
        self._edit('installer', 'Cancel', 'Kanselleer')
        assert self.registry.submit_item.get_sensitive()

        result = self.registry.submit()

        assert self.confirmed == [(2, ['Hello/Program (Afrikaans)', 'Hello/Installer (Afrikaans)'])]
        assert (result.total, result.sent, result.stop) == (2, 2, None)
        assert self._server_unit('program', 'Help')['target'] == ['Hulp']
        assert self._server_unit('installer', 'Cancel')['target'] == ['Kanselleer']
        assert self._status() == 'Submitted 2 units.'
        assert all(c.unsubmitted() == 0 for c in self.sc.work_copies.values())
        assert not self.registry.submit_item.get_sensitive()

    def test_conflicts_of_every_file_come_in_one_dialog(self, monkeypatch):
        self._opened(self._open_components())
        self._edit('program', 'Help', 'Hulp')
        self._edit('installer', 'Cancel', 'Kanselleer')
        self._edit('installer', 'Next', 'Volgende een')
        self.fake.edit(self._server_unit('installer', 'Cancel')['id'], 'Kanselleer dit')
        shown = []

        def run(dialog):
            shown.append([(c.where, c.source, c.mine, c.theirs) for c in dialog.conflicts])
            dialog.choose(dialog.conflicts[0].index, submit.TAKE_THEIRS)
            return dialog.decisions()

        monkeypatch.setattr(ConflictDialog, 'run', run)

        result = self.registry.submit()

        assert shown == [
            [('Hello/Installer (Afrikaans)', 'Cancel', ['Kanselleer'], ['Kanselleer dit'])]
        ]
        assert (result.total, result.sent, result.taken) == (3, 2, 1)
        assert str(self._unit('installer', 'Cancel').target) == 'Kanselleer dit'
        assert self._server_unit('installer', 'Next')['target'] == ['Volgende een']
        assert self._server_unit('program', 'Help')['target'] == ['Hulp']
        # Theirs is saved in the file too.
        assert not self.sc.is_modified()
        installer = next(c for c in self.sc.work_copies.values() if 'installer' in c.path)
        with open(installer.path, 'rb') as f:
            assert b'Kanselleer dit' in f.read()

    def test_a_locked_file_is_left_out_and_said(self):
        self._opened(self._open_components())
        self._edit('program', 'Help', 'Hulp')
        self._edit('installer', 'Cancel', 'Kanselleer')
        self.fake.lock('hello', 'installer')

        result = self.registry.submit()

        assert result.sent == 1 and result.stop == submit.LOCKED
        assert self._server_unit('program', 'Help')['target'] == ['Hulp']
        assert self.told == [
            'Submitted 1 of 2 changed units.\n'
            'Hello/Installer (Afrikaans)\n'
            '  Submitted 0 of 1 changed unit.\n'
            '  The translation is locked on the server.\n'
            'Submit again to send the rest.'
        ]

    def test_submit_reaches_a_language_not_shown(self):
        self._opened(self._open_languages(('Afrikaans', 'German')))
        assert self.main_controller.open_stream_language('de')
        self._edit('program', 'Close', 'Schließen')
        assert self.main_controller.save_file()
        assert self.main_controller.open_stream_language('af')
        assert self.sc.work_copy.record['language'] == 'af'
        self._edit('program', 'Help', 'Hulp')

        result = self.registry.submit()

        assert result.sent == 2
        assert self._server_unit('program', 'Close', 'de')['target'] == ['Schließen']
        assert self._server_unit('program', 'Help')['target'] == ['Hulp']

    def test_update_brings_in_every_files_changes(self):
        self._opened(self._open_components())
        self.fake.edit(self._server_unit('program', 'Help')['id'], 'Hulp')
        self.fake.edit(self._server_unit('installer', 'Cancel')['id'], 'Kanselleer')

        result = self.registry.update()

        assert result.taken == 2 and result.stop is None
        assert str(self._unit('program', 'Help').target) == 'Hulp'
        assert str(self._unit('installer', 'Cancel').target) == 'Kanselleer'
        assert not self.sc.is_modified()
        assert self._status() == 'Updated 2 units from the server.'
        assert self.fake.patched == []

    def test_update_asks_about_every_files_conflicts_at_once(self, monkeypatch):
        self._opened(self._open_components())
        self._edit('program', 'Help', 'Hulp')
        self._edit('installer', 'Cancel', 'Kanselleer')
        self.fake.edit(self._server_unit('program', 'Help')['id'], 'Help my')
        self.fake.edit(self._server_unit('installer', 'Cancel')['id'], 'Kanselleer dit')
        shown = []

        def run(dialog):
            shown.append([c.where for c in dialog.conflicts])
            for conflict in dialog.conflicts:
                dialog.choose(conflict.index, submit.TAKE_THEIRS)
            return dialog.decisions()

        monkeypatch.setattr(ConflictDialog, 'run', run)

        result = self.registry.update()

        assert shown == [['Hello/Program (Afrikaans)', 'Hello/Installer (Afrikaans)']]
        assert result.taken == 2
        assert str(self._unit('program', 'Help').target) == 'Help my'
        assert str(self._unit('installer', 'Cancel').target) == 'Kanselleer dit'

    def test_update_downloads_a_file_whose_strings_changed_again(self, monkeypatch):
        self._opened(self._open_components())
        self._edit('installer', 'Cancel', 'Kanselleer')
        self.fake.add_unit('hello', 'installer', 'af', 'Finish')
        self.fake.edit(self._server_unit('program', 'Help')['id'], 'Hulp')
        asked = []

        def ask(dialogs, plans):
            asked.append([(copy.label(), len(plan.kept)) for copy, plan in plans])
            return True

        monkeypatch.setattr(UpdateDialogs, 'ask_downloads', ask)

        result = self.registry.update()

        assert asked == [[('Hello/Installer (Afrikaans)', 1)]]
        assert result.stop is None and self.sc.is_stream()
        assert str(self._unit('installer', 'Cancel').target) == 'Kanselleer'
        assert self._index('installer', 'Finish') is not None
        assert str(self._unit('program', 'Help').target) == 'Hulp'
        installer = next(c for c in self.sc.work_copies.values() if 'installer' in c.path)
        assert installer.unsubmitted() == 1

    def test_finished_units_go_as_each_file_is_saved(self):
        self.servers.servers[0].send_finished = True
        self._opened(self._open_components())
        self._edit('program', 'Help', 'Hulp', StateEnum.UNREVIEWED)
        self._edit('installer', 'Cancel', 'Kanselleer', StateEnum.UNREVIEWED)
        self._edit('installer', 'Next', 'Volgende een', StateEnum.NEEDS_WORK)

        assert self.main_controller.save_file()
        run_until(
            lambda: not self.registry.trickler.running and not self.registry.trickler._next,
            timeout=30,
        )

        assert self._patched() == sorted(
            [
                self._server_unit('program', 'Help')['id'],
                self._server_unit('installer', 'Cancel')['id'],
            ]
        )
        # The draft waits for Submit.
        waiting = {c.label(): c.unsubmitted() for c in self.sc.work_copies.values()}
        assert waiting == dict(zip(AF_COMPONENTS, [0, 0, 1], strict=True))
        assert not any(c.record.get('queue') for c in self.sc.work_copies.values())
