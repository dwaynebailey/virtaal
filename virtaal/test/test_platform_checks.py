#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Weblate's checks and flags in the editor (#3337), on files opened
from the fake Weblate: its flags steer Virtaal's checks, a check failing
on the server shows, and a platform that can't check changes nothing."""

import pytest
from test_scaffolding import TestScaffolding

from devsupport.testing.fakeweblate import TOKEN, demo_checks, run_until
from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.plugins.weblate.checks import WeblateCheck
from virtaal.support import extrachecks
from virtaal.support.platforms import Download, Registry, RemoteUnit, workcopy
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.test_platforms import DummyPlatform


class TestPlatformChecks(TestScaffolding):
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
        self.fake = demo_checks().start()
        self.platform = WeblatePlatform(str(tmp_path / 'weblate.ini'))
        self.dummy = DummyPlatform()
        self.servers = ServerList([self.platform, self.dummy], str(tmp_path / 'platforms.ini'))
        self.servers.put(self.platform, self.fake.url, {'token': TOKEN})
        self.servers.put(self.dummy, 'dummy://one', {'user': 'me', 'password': 'secret'})
        self.registry = Registry()
        monkeypatch.setattr(self.registry, 'servers', lambda: self.servers)
        self.registry.add(self.platform, self.main_controller)
        self.registry.add(self.dummy, self.main_controller)
        self.store_controller.close_file()
        yield
        self.store_controller.close_file()
        self.registry.remove(self.platform)
        self.registry.remove(self.dummy)
        self.fake.stop()
        assert self.fake.schema_problems == []
        assert WeblateCheck not in extrachecks.EXTRA_CHECKS

    def _open(self):
        session = self.platform.session(self.fake.url, {'token': TOKEN})
        found = []
        session.download('hello', 'program', 'af', found.append, found.append)
        run_until(lambda: found)
        copy = workcopy.create(
            self.platform, self.servers.servers[0].url, 'hello', 'program', 'af', found[0]
        )
        assert self.main_controller.open_file(copy.path), self.errors
        return copy

    def _unit(self, source):
        store = self.store_controller.store
        return next(u for u in store.get_units() if str(u.source) == source)

    def _show(self, source):
        sc = self.store_controller
        unit = self._unit(source)
        sc.cursor.force_index(sc.store.index_of(unit))
        self.unit_controller.load_unit(unit)
        return unit

    def _shown_checks(self):
        """(name, description) of each failure the checks button shows."""
        view = self.checks_controller.unitview
        return [(n.get_text(), d.get_text()) for n, d in view.check_rows]

    def _type(self, text):
        self.unit_controller.set_unit_target(0, text)
        return self.checks_controller.check_unit(self.unit_controller.current_unit)

    def test_a_check_failing_on_the_server_shows(self):
        self._open()

        self._show('Print')

        assert ('Weblate', 'Weblate reports failing checks: Unchanged') in self._shown_checks()
        assert self.errors == []

    def test_flags_steer_the_checks_as_you_type(self):
        self._open()

        self._show('Save')
        failures = self._type('Bewaar dit')
        assert failures['maxlength'] == 'Longer than 6 characters'
        assert ('Long', 'Longer than 6 characters') in self._shown_checks()

        self._show('File')
        assert 'endpunc' not in self._type('Lêer.')

    def test_the_stats_cache_has_the_servers_checks(self):
        self._open()

        store = self.store_controller.get_store()
        checks = store.update_checks(self.checks_controller.get_checker())

        print_index = self.store_controller.store.index_of(self._unit('Print'))
        assert print_index in checks['check-weblate']

    def test_a_platform_that_cant_check_changes_nothing(self, tmp_path):
        units = [RemoteUnit('a', 1, '', ['Alpha'], ['Alfa'], 120, 'final', None, False)]
        data = b'msgid "Alpha"\nmsgstr "Alfa"\n'
        download = Download('alpha.po', data, None, units, {'project': 'Alpha', 'component': None})
        copy = workcopy.create(self.dummy, 'dummy://one', 'alpha', None, 'af', download)
        plain = tmp_path / 'alpha.po'
        plain.write_bytes(data)
        assert self.main_controller.open_file(str(plain)), self.errors
        expected = self.checks_controller.get_checker().config.extra_checks

        assert self.main_controller.open_file(copy.path), self.errors

        checker = self.checks_controller.get_checker()
        assert checker.config.extra_checks == expected
        assert 'weblate' not in self.checks_controller.check_unit(self._show('Alpha'))

    def test_update_shows_what_the_server_says_now(self, monkeypatch):
        from virtaal.support.platforms.updatedialog import UpdateDialogs

        told = []
        monkeypatch.setattr(UpdateDialogs, '_tell', lambda _self, text, *a: told.append(text))
        self._open()
        self._show('Help')
        assert not any(name == 'Weblate' for name, _d in self._shown_checks())
        help_id = next(u['id'] for u in self.fake.units.values() if u['source'] == ['Help'])
        self.fake.fail_check(help_id, 'end_stop')

        self.registry.update()

        self._show('Open')
        self._show('Help')
        assert ('Weblate', 'Weblate reports a failing check') in self._shown_checks()

    def _confirm(self, monkeypatch):
        """Submit's question as (its text, its buttons), answered Cancel."""
        from gi.repository import Gtk

        from virtaal.support.platforms import submitdialog

        asked = []

        def answer(dialog):
            text = dialog.get_property('secondary-text')
            buttons = [b.get_label() for b in dialog.get_action_area().get_children()]
            asked.append((text, buttons))
            return Gtk.ResponseType.CANCEL

        monkeypatch.setattr(submitdialog, 'run_dialog', answer)
        assert self.registry.submit() is None
        return asked[0]

    def test_submit_warns_of_changes_failing_enforced_checks(self, monkeypatch):
        self._open()
        self._show('Save')
        self._type('Bewaar dit')
        self.unit_controller.finish_current_unit()

        text, buttons = self._confirm(monkeypatch)

        assert '1 change fails checks this project enforces.' in text
        assert buttons == ['_Cancel', 'Submit _Anyway']
        assert self.fake.patched == []

    def test_submit_asks_as_before_when_nothing_fails_them(self, monkeypatch):
        self._open()
        self._show('Save')
        self._type('Berg')
        self.unit_controller.finish_current_unit()

        text, buttons = self._confirm(monkeypatch)

        assert 'enforces' not in text and buttons == ['_Cancel', '_Submit']
