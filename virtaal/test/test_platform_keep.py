#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Keeping a work copy's unsubmitted work (#3337) in Virtaal, against
the fake Weblate: the notices that keep it in view, crash recovery
re-attaching the platform, File > Update from Server…, and sending
finished units as you go."""

import pytest
from gi.repository import GLib, Gtk
from test_recovery_resume import UnsavedScaffolding
from test_scaffolding import TestScaffolding
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.support.platforms import Registry, states, submit, workcopy
from virtaal.support.platforms.browsedialog import BrowseDialog
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.submitdialog import ConflictDialog, SubmitDialogs
from virtaal.support.platforms.updatedialog import UpdateDialogs


class Weblating:
    """The fake Weblate, a server list holding it and a registry with
    Weblate on, for a test class with main_controller."""

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
        self.told = []
        for dialogs in (SubmitDialogs, UpdateDialogs):
            monkeypatch.setattr(dialogs, '_tell', lambda _self, text, *a: self.told.append(text))
        monkeypatch.setattr(SubmitDialogs, 'confirm', lambda _self, count: True)
        self.fake = demo().start()
        self.platform = WeblatePlatform(str(tmp_path / 'weblate.ini'))
        self.servers = ServerList([self.platform], str(tmp_path / 'platforms.ini'))
        self.servers.put(self.platform, self.fake.url, {'token': TOKEN})
        self.registry = Registry()
        # No start-up reminder unless a test asks for one.
        self.registry._reminded = True
        monkeypatch.setattr(self.registry, 'servers', lambda: self.servers)
        self.registry.add(self.platform, self.main_controller)
        self.main_controller.store_controller.close_file()
        yield
        self.main_controller.store_controller.close_file()
        self.registry.remove(self.platform)
        self.fake.stop()
        assert self.fake.schema_problems == []

    def _download(self):
        session = self.platform.session(self.fake.url, {'token': TOKEN})
        found = []
        session.download('hello', 'program', 'af', found.append, found.append)
        run_until(lambda: found)
        return workcopy.create(
            self.platform, self.servers.servers[0].url, 'hello', 'program', 'af', found[0]
        )

    def _open(self, path=None):
        path = path or self._download().path
        assert self.main_controller.open_file(path), self.errors
        return path

    def _unit(self, source):
        store = self.main_controller.store_controller.store
        return next(u for u in store.get_units() if str(u.source) == source)

    def _edit(self, source, text, state=None):
        sc = self.main_controller.store_controller
        uc = self.main_controller.unit_controller
        unit = self._unit(source)
        sc.cursor.force_index(sc.store.index_of(unit))
        uc.load_unit(unit)
        uc.set_unit_target(0, [text])
        if state is not None:
            # As chosen in the editor: else leaving a unit at once, before
            # the state timer, marks it needing review.
            uc.set_current_state(state, from_user=True)
        uc.finish_current_unit()

    def _server_unit(self, source):
        return next(u for u in self.fake.units.values() if u['source'] == [source])

    def _notice(self):
        """(its text, its buttons' labels), or None."""
        notice = self.registry._notice
        if notice is None:
            return None
        buttons = sorted(b.get_label() for b in notice.get_action_area().get_children())
        return self.registry.notice_text(), buttons

    def _status(self):
        bar = self.main_controller.view.status_bar
        return bar.get_message_area().get_children()[0].get_text()


OPEN_NOTICE = ['Su_bmit to Server…', 'Up_date from Server…']


class TestUnsubmittedWork(Weblating, TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    def test_a_fresh_download_says_nothing(self):
        self._open()

        assert self._notice() is None

    def test_reopening_after_a_restart_says_what_waits_with_submit_and_update(self):
        path = self._open()
        self._edit('Help', 'Hulp')
        self._edit('Print', 'Druk uit')
        assert self.main_controller.save_file()
        self.main_controller.store_controller.close_file()

        # As after a restart: the work copy from Recent Files.
        self._open(path)

        assert self._notice() == (
            '2 changes to Hello/Program are not yet submitted.',
            OPEN_NOTICE,
        )
        assert self.registry.submit_item.get_sensitive()

    def test_the_notice_follows_saves_and_goes_once_all_are_sent(self):
        path = self._open()
        self._edit('Help', 'Hulp')
        assert self.main_controller.save_file()
        self.main_controller.store_controller.close_file()
        self._open(path)
        self._edit('Print', 'Druk uit')
        assert self.main_controller.save_file()
        assert self._notice()[0] == '2 changes to Hello/Program are not yet submitted.'

        self.registry.submit()

        assert self._notice() is None

    def test_submit_on_the_notice_submits(self):
        path = self._open()
        self._edit('Help', 'Hulp')
        assert self.main_controller.save_file()
        self.main_controller.store_controller.close_file()
        self._open(path)

        self.registry._notice.response(0)

        assert self._server_unit('Help')['target'] == ['Hulp']

    def test_closing_with_work_not_submitted_says_so_once_with_open(self, monkeypatch):
        path = self._open()
        self._edit('Help', 'Hulp')
        assert self.main_controller.save_file()

        self.main_controller.close_file()

        text, buttons = self._notice()
        assert text == '1 change to Hello/Program is not yet submitted.'
        assert buttons == [GLib.dgettext('gtk30', '_Open')]
        opened = []
        monkeypatch.setattr(self.main_controller, 'open_file', opened.append)
        self.registry._notice.response(0)
        assert opened == [path] and self._notice() is None

    def test_quitting_asks_nothing_and_start_up_reminds_once(self):
        path = self._open()
        self._edit('Help', 'Hulp')
        assert self.main_controller.save_file()
        self.main_controller.store_controller.close_file()
        self.registry._hide_notice()

        # A new Virtaal with no file open.
        self.registry._remind_at_start()

        assert self._notice() == (
            '1 change to Hello/Program is not yet submitted.',
            [GLib.dgettext('gtk30', '_Open')],
        )
        assert workcopy.unsubmitted_copies({'weblate'})[0].path == path

    def test_reopening_from_the_browser_offers_update(self, monkeypatch):
        path = self._open()
        self._edit('Help', 'Hulp')
        assert self.main_controller.save_file()
        self.main_controller.store_controller.close_file()
        chosen = []

        def run(dialog):
            dialog.server = self.servers.servers[0]
            dialog.session = dialog.server.session()
            dialog.treeview.get_selection().unselect_all()
            monkeypatch.setattr(dialog, 'selected', lambda: ('hello', 'program', 'af'))
            dialog.open_selected()
            chosen.append(dialog.opened)
            return dialog.opened

        monkeypatch.setattr(BrowseDialog, 'run', run)
        downloads = len(self.fake.requests_to('/api/translations/hello/program/af/file/'))

        assert self.registry.browse()

        # The local copy, not a new download over it.
        assert chosen == [path]
        assert len(self.fake.requests_to('/api/translations/hello/program/af/file/')) == downloads
        assert self._notice() == ('1 change to Hello/Program is not yet submitted.', OPEN_NOTICE)


class TestUpdate(Weblating, TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    def test_update_is_offered_for_any_work_copy(self):
        assert not self.registry.update_item.get_sensitive()

        self._open()

        assert self.registry.update_item.get_sensitive()

    def test_update_saves_then_brings_in_theirs(self):
        path = self._open()
        self._edit('Help', 'Hulp')
        self.fake.edit(self._server_unit('Open')['id'], 'Open maak')

        result = self.registry.update()

        assert result.taken == 1 and result.stop is None
        assert str(self._unit('Open').target) == 'Open maak'
        assert str(self._unit('Help').target) == 'Hulp'
        assert not self.main_controller.store_controller.is_modified()
        with open(path, 'rb') as f:
            data = f.read()
        assert b'Open maak' in data and b'Hulp' in data
        assert self._status() == 'Updated 1 unit from the server.'
        assert self.fake.patched == []

    def test_nothing_new_is_said(self):
        self._open()

        self.registry.update()

        assert self._status() == 'There are no changes on the server.'

    def test_a_conflict_asks_with_submits_dialog(self, monkeypatch):
        self._open()
        self._edit('Save', 'Bewaar')
        self.fake.edit(self._server_unit('Save')['id'], 'Stoor dit')
        seen = []

        def run(dialog):
            (conflict,) = dialog.conflicts
            ok = dialog.dialog.get_widget_for_response(Gtk.ResponseType.OK)
            seen.append((ok.get_label(), conflict.mine, conflict.theirs))
            dialog.choose(conflict.index, submit.TAKE_THEIRS)
            return dialog.decisions()

        monkeypatch.setattr(ConflictDialog, 'run', run)

        result = self.registry.update()

        assert seen == [('_OK', ['Bewaar'], ['Stoor dit'])]
        assert result.taken == 1
        assert str(self._unit('Save').target) == 'Stoor dit'

    def test_a_changed_set_of_units_downloads_again_keeping_local_edits(self, monkeypatch):
        path = self._open()
        self._edit('Help', 'Hulp')
        self.fake.add_unit('hello', 'program', 'af', 'Undo')
        asked = []

        def ask(dialog, copy, plan):
            asked.append((len(plan.kept), len(plan.lost)))
            return True

        monkeypatch.setattr(UpdateDialogs, 'ask_download', ask)

        result = self.registry.update()

        assert asked == [(1, 0)] and result.downloaded is not None
        sc = self.main_controller.store_controller
        assert sc.work_copy is not None and sc.work_copy.path == path
        assert [str(u.source) for u in sc.store.get_units()][-1] == 'Undo'
        assert str(self._unit('Help').target) == 'Hulp'
        assert not sc.is_modified()
        assert workcopy.for_file(path).unsubmitted() == 1

    def test_the_download_question_names_what_would_be_lost(self, monkeypatch):
        self._open()
        self._edit('Help', 'Hulp')
        self.fake.remove_unit(self._server_unit('Help')['id'])
        shown = []

        def answer(dialog):
            shown.append((dialog.props.text, dialog.props.secondary_text))
            return Gtk.ResponseType.CANCEL

        from virtaal.support.platforms import updatedialog

        monkeypatch.setattr(updatedialog, 'run_dialog', answer)

        result = self.registry.update()

        assert result.stop == submit.CANCELLED
        assert shown == [
            (
                'The strings to translate changed on the server since you downloaded them. '
                'Download them again?',
                '1 change not yet submitted is to a string removed on the server, '
                'and will be lost:\n  Help',
            )
        ]
        assert str(self._unit('Help').target) == 'Hulp'


class TestSendAsYouGo(Weblating, TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    def _finish(self, source, text):
        self._edit(source, text, StateEnum.UNREVIEWED)

    def _patches(self):
        return [unit_id for unit_id, _data in self.fake.patched]

    def test_save_never_sends_with_the_option_off(self):
        self._open()
        self._edit('Help', 'Hulp')

        assert self.main_controller.save_file()

        assert self.registry.trickler.running is False
        assert self.fake.requests_to('/api/units/') == []
        assert self.fake.requests_to('/api/components/') == []

    def test_finished_units_go_as_they_are_saved(self):
        self.servers.servers[0].send_finished = True
        path = self._open()
        self._finish('Help', 'Hulp')
        self._edit('Print', 'Druk uit', StateEnum.NEEDS_WORK)

        assert self.main_controller.save_file()
        run_until(lambda: not self.registry.trickler.running, timeout=30)

        assert self._patches() == [self._server_unit('Help')['id']]
        assert self._status() == 'Submitted 1 unit.'
        # The draft waits for Submit.
        record = workcopy.for_file(path)
        assert record.unsubmitted() == 1 and record.record['queue'] == []

    def test_a_conflict_while_sending_is_left_for_submit_and_noted(self):
        self.servers.servers[0].send_finished = True
        self._open()
        self.fake.edit(self._server_unit('Help')['id'], 'Help my')
        self._finish('Help', 'Hulp')

        assert self.main_controller.save_file()
        run_until(lambda: not self.registry.trickler.running, timeout=30)

        assert self._patches() == []
        assert self._notice() == (
            '1 finished unit was also changed on the server, and waits for Submit.',
            ['Su_bmit to Server…'],
        )

    def test_reopening_sends_what_was_queued(self):
        self.servers.servers[0].send_finished = True
        path = self._open()
        self.fake.offline = True
        self._finish('Help', 'Hulp')
        assert self.main_controller.save_file()
        run_until(lambda: not self.registry.trickler.running, timeout=30)
        self.main_controller.store_controller.close_file()
        assert workcopy.for_file(path).record['queue'] == [self._server_unit('Help')['id']]
        self.fake.offline = False

        self._open(path)
        run_until(lambda: not self.registry.trickler.running, timeout=30)

        assert self._patches() == [self._server_unit('Help')['id']]
        assert workcopy.for_file(path).record['queue'] == []


class TestRecoveringAWorkCopy(Weblating, UnsavedScaffolding):
    def test_a_crash_resumes_with_the_platform_attached(self):
        path = self._download().path
        sc = self.store_controller
        sc.open_file(path)
        self._type(sc.store.index_of(self._unit('Help')), 'Hulp')
        self._edit_at(0)
        self._crash()

        self._resume()

        assert sc.work_copy is not None and sc.work_copy.path == path
        approved = self._unit('Open')
        assert states.is_adopted(approved) and approved.get_state_n() == 120
        [(kind, count, recover, _discard)] = [n for n in self.notices if n[0] == 'recover']
        assert count == 1
        recover()
        assert str(self._unit('Help').target) == 'Hulp'

        assert self.main_controller.save_file()
        assert workcopy.for_file(path).unsubmitted() == 1
