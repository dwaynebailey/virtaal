#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""File > Update from Server… (#3337) in Virtaal, against the fake
Weblate."""

import pytest
from gi.repository import Gtk
from test_scaffolding import TestScaffolding

from devsupport.testing.fakeweblate import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.support.platforms import Registry, submit, workcopy
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

    def _status(self):
        bar = self.main_controller.view.status_bar
        return bar.get_message_area().get_children()[0].get_text()


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
            buttons = dialog.dialog.get_action_area().get_children()
            seen.append(([b.get_label() for b in buttons], conflict.mine, conflict.theirs))
            dialog.choose(conflict.index, submit.TAKE_THEIRS)
            return dialog.decisions()

        monkeypatch.setattr(ConflictDialog, 'run', run)

        result = self.registry.update()

        assert seen == [(['_Cancel', '_OK'], ['Bewaar'], ['Stoor dit'])]
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
        assert len(workcopy.for_file(path).changed_units()) == 1

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
