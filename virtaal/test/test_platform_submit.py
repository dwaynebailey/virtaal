#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""File > Submit to Server… (#3337) on a file opened from the fake
Weblate: the menu item, Submit saving first while Save never sends,
the confirm, conflicts, and what the title, status bar and messages
say."""

import pytest
from gi.repository import Gtk
from test_scaffolding import TestScaffolding

from devsupport.testing.fakeweblate import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.support.platforms import (
    Budget,
    Registry,
    serversdialog,
    submit,
    submitdialog,
    workcopy,
)
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.submitdialog import (
    ConflictDialog,
    ProgressDialog,
    SubmitDialogs,
    budget_warning,
    result_message,
)


class TestPlatformSubmit(TestScaffolding):
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
        monkeypatch.setattr(
            self.main_controller,
            'show_error',
            lambda msg, parent=None, detail='': self.errors.append((msg + '\n\n' + detail).strip()),
        )
        self.told = []
        monkeypatch.setattr(SubmitDialogs, '_tell', lambda _self, text, *a: self.told.append(text))
        self.asked = []
        self.answer = True

        def confirm(_self, count):
            self.asked.append(count)
            return self.answer

        monkeypatch.setattr(SubmitDialogs, 'confirm', confirm)
        self.fake = demo().start()
        self.platform = WeblatePlatform(str(tmp_path / 'weblate.ini'))
        self.servers = ServerList([self.platform], str(tmp_path / 'platforms.ini'))
        self.servers.put(self.platform, self.fake.url, {'token': TOKEN})
        self.registry = Registry()
        monkeypatch.setattr(self.registry, 'servers', lambda: self.servers)
        self.registry.add(self.platform, self.main_controller)
        self.store_controller.close_file()
        yield
        self.store_controller.close_file()
        self.registry.remove(self.platform)
        self.fake.stop()
        assert self.fake.schema_problems == []

    def _open(self):
        session = self.platform.session(self.fake.url, {'token': TOKEN})
        found = []
        session.download('hello', 'program', 'af', found.append, found.append)
        run_until(lambda: found)
        copy = workcopy.create(
            self.platform, self.servers.servers[0].url, 'hello', 'program', 'af', found[0]
        )
        assert self.main_controller.open_file(copy.path), self.errors
        return copy.path

    def _unit(self, source):
        store = self.store_controller.store
        return next(u for u in store.get_units() if str(u.source) == source)

    def _edit(self, source, text):
        sc = self.store_controller
        unit = self._unit(source)
        sc.cursor.force_index(sc.store.index_of(unit))
        self.unit_controller.load_unit(unit)
        self.unit_controller.set_unit_target(0, [text])
        self.unit_controller.finish_current_unit()

    def _server_unit(self, source):
        return next(u for u in self.fake.units.values() if u['source'] == [source])

    def _patches(self):
        return self.fake.requests_to('/api/units/')

    def _title(self):
        return self.main_controller.view.main_window.get_title()

    def _status(self):
        bar = self.main_controller.view.status_bar
        return bar.get_message_area().get_children()[0].get_text()

    def test_submit_is_offered_for_a_work_copy_with_changes(self):
        item = self.registry.submit_item
        assert not item.get_sensitive()
        self._open()
        assert not item.get_sensitive()

        self._edit('Help', 'Hulp')
        assert item.get_sensitive()
        assert self.main_controller.save_file()
        assert item.get_sensitive()

        self.registry.submit()
        assert not item.get_sensitive()

    def test_save_never_sends(self):
        self._open()
        self._edit('Help', 'Hulp')

        assert self.main_controller.save_file()

        assert self._patches() == []
        assert self.fake.requests_to('/api/components/') == []

    def test_submit_saves_then_sends_and_says_submitted(self):
        path = self._open()
        self._edit('Help', 'Hulp')
        assert self.store_controller.is_modified()

        result = self.registry.submit()

        assert not self.store_controller.is_modified()
        with open(path, 'rb') as f:
            assert b'Hulp' in f.read()
        assert self.asked == [1]
        assert result.sent == 1 and self.told == []
        assert self._server_unit('Help')['target'] == ['Hulp']
        assert self._title().startswith('Hello/Program (Weblate, submitted)')
        assert self._status() == 'Submitted 1 unit.'
        assert workcopy.for_file(path).submitted()

        self._edit('Help', 'Hulp!')
        assert 'submitted' not in self._title()

    def test_saying_no_sends_nothing(self):
        self._open()
        self._edit('Help', 'Hulp')
        self.answer = False

        assert self.registry.submit() is None

        assert self._patches() == []
        assert not self.store_controller.is_modified()

    def test_nothing_to_submit_is_said(self):
        self._open()
        self.store_controller.set_modified(True)

        assert self.registry.submit() is None

        assert self.told == ['There are no changes to submit.']
        assert self.asked == []

    def test_a_locked_translation_is_said(self):
        self._open()
        self._edit('Help', 'Hulp')
        self.fake.lock('hello', 'program')

        result = self.registry.submit()

        assert result.stop == submit.LOCKED
        assert self.told == [
            'Submitted 0 of 1 changed unit.\n'
            'The translation is locked on the server.\n'
            'Submit again to send the rest.'
        ]
        assert 'submitted' not in self._title()

    def test_taking_theirs_shows_and_saves_their_text(self, monkeypatch):
        path = self._open()
        self._edit('Save', 'Bewaar')
        self.fake.edit(self._server_unit('Save')['id'], 'Stoor dit')
        # The editor is on the unit whose text the server's replaces.
        save = self._unit('Save')
        self.store_controller.cursor.force_index(self.store_controller.store.index_of(save))
        self.unit_controller.load_unit(save)
        seen = []

        def run(dialog):
            (conflict,) = dialog.conflicts
            seen.append((conflict.source, conflict.mine, conflict.theirs))
            dialog.choose(conflict.index, submit.TAKE_THEIRS)
            return dialog.decisions()

        monkeypatch.setattr(ConflictDialog, 'run', run)

        result = self.registry.submit()

        assert seen == [('Save', ['Bewaar'], ['Stoor dit'])]
        assert result.taken == 1 and self._patches() == []
        assert str(self._unit('Save').target) == 'Stoor dit'
        assert self.unit_controller.current_unit is save
        shown = self.unit_controller.view.targets[0].get_buffer()
        assert shown.get_text(shown.get_start_iter(), shown.get_end_iter(), True) == 'Stoor dit'
        assert not self.store_controller.is_modified()
        with open(path, 'rb') as f:
            assert b'Stoor dit' in f.read()
        assert workcopy.for_file(path).submitted()
        assert self._title().startswith('Hello/Program (Weblate, submitted)')

    def test_cancelling_the_conflicts_stops_submit(self, monkeypatch):
        self._open()
        self._edit('Save', 'Bewaar')
        self._edit('Help', 'Hulp')
        self.fake.edit(self._server_unit('Save')['id'], 'Stoor dit')
        monkeypatch.setattr(ConflictDialog, 'run', lambda dialog: None)

        result = self.registry.submit()

        assert result.stop == submit.CANCELLED and self._patches() == []
        assert self.told[0].endswith('Submit again to send the rest.')


def _conflicts():
    return [
        submit.Conflict(3, 'Save', ['Bewaar'], ['Stoor dit']),
        submit.Conflict(7, 'Files', ['Lêer', 'Lêers'], ['Leêr', 'Leêrs']),
    ]


def test_the_conflict_dialog_skips_until_told(monkeypatch):
    monkeypatch.setattr(submitdialog, 'run_dialog', lambda dialog: Gtk.ResponseType.OK)
    dialog = ConflictDialog(_conflicts())
    assert dialog.decisions() == {3: submit.SKIP, 7: submit.SKIP}

    dialog.choose(3, submit.KEEP_MINE)
    dialog.choose(7, submit.TAKE_THEIRS)

    assert dialog.run() == {3: submit.KEEP_MINE, 7: submit.TAKE_THEIRS}


def test_cancelling_the_conflict_dialog_decides_nothing(monkeypatch):
    monkeypatch.setattr(submitdialog, 'run_dialog', lambda dialog: Gtk.ResponseType.CANCEL)

    assert ConflictDialog(_conflicts()).run() is None


def test_the_progress_counts_and_cancels():
    cancelled = []
    progress = ProgressDialog(None, lambda: cancelled.append(True))

    progress.update(12, 340)
    assert progress.label.get_text() == 'Submitting 12 of 340…'
    progress.dialog.response(Gtk.ResponseType.CANCEL)

    assert cancelled == [True]
    assert not progress.cancel_button.get_sensitive()
    progress.destroy()


class _Copy:
    def __init__(self):
        from types import SimpleNamespace

        self.matched = {2: SimpleNamespace(source='Save'), 5: SimpleNamespace(source='Help')}


@pytest.fixture
def _english(monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')


def _result(**fields):
    result = submit.Result()
    result.total = 4
    for name, value in fields.items():
        setattr(result, name, value)
    return result


@pytest.mark.usefixtures('_english')
def test_a_full_submit_needs_no_message():
    assert (
        result_message(_result(sent=4), _Copy(), WeblatePlatform.__new__(WeblatePlatform)) is None
    )


@pytest.mark.usefixtures('_english')
def test_a_submit_cut_short_says_how_far_it_got_and_what_to_do(monkeypatch):
    monkeypatch.setattr(submitdialog, 'clock_time', lambda seconds: '14:05')
    platform = WeblatePlatform.__new__(WeblatePlatform)

    budget = result_message(_result(sent=2, stop=submit.BUDGET, reset_in=900), _Copy(), platform)
    cancelled = result_message(_result(sent=1, stop=submit.CANCELLED), _Copy(), platform)
    refused = result_message(
        _result(sent=2, skipped=1, failures={2: 'You may not approve.'}), _Copy(), platform
    )

    assert budget == (
        'Submitted 2 of 4 changed units.\n'
        "The server’s request limit is used up until 14:05.\n"
        'Submit again to send the rest.'
    )
    assert cancelled == 'Submitted 1 of 4 changed units.\nSubmit again to send the rest.'
    assert refused == (
        'Submitted 2 of 4 changed units.\n'
        '1 unit was refused by the server:\n'
        '  Save: You may not approve.\n'
        '1 unit changed on the server was left as it is.\n'
        'Submit again to send the rest.'
    )


@pytest.mark.usefixtures('_english')
def test_a_budget_too_small_for_the_changes_is_warned_of_only_when_known(monkeypatch):
    monkeypatch.setattr(serversdialog, 'clock_time', lambda seconds: '08:40')

    assert budget_warning(None, 200) is None
    assert budget_warning(Budget(4980, 1200, 5000), 200) is None
    # A server that says what is left but not its limit.
    assert budget_warning(Budget(10, 1200), 200) is None
    assert budget_warning(Budget(200, 1200, 5000), 200) == (
        '200 of 5,000 requests left until 08:40. Submit will pause or stop when they run out.'
    )


@pytest.mark.usefixtures('_english')
def test_the_confirm_shows_the_budget_warning(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(serversdialog, 'clock_time', lambda seconds: '08:40')
    shown = []

    def answer(dialog):
        shown.append(dialog.props.secondary_text)
        return Gtk.ResponseType.CANCEL

    monkeypatch.setattr(submitdialog, 'run_dialog', answer)
    dialogs = SubmitDialogs.__new__(SubmitDialogs)
    dialogs.parent = None
    dialogs.copy = SimpleNamespace(
        location=lambda: 'Hello/Program', platform_name=lambda: 'Weblate'
    )
    dialogs.session = SimpleNamespace(budget=lambda: Budget(30, 600, 5000))

    assert not dialogs.confirm(12)
    dialogs.session = SimpleNamespace(budget=lambda: None)
    dialogs.confirm(12)

    assert shown == [
        'Hello/Program\n\n30 of 5,000 requests left until 08:40. '
        'Submit will pause or stop when they run out.',
        'Hello/Program',
    ]
