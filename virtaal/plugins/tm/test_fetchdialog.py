#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Fetch Suggestions dialog (#1679), with services that answer only
when a test says so."""

from types import SimpleNamespace

import pytest
from gi.repository import Gtk

from virtaal.plugins.tm.fetchdialog import (
    COL_AVAILABLE,
    COL_CHARS,
    COL_NAME,
    COL_STATUS,
    COL_UNITS,
    COL_USE,
    RESPONSE_FETCH,
    RESPONSE_STOP,
    FetchSuggestionsDialog,
)
from virtaal.plugins.tm.fetched import FetchedSuggestions
from virtaal.plugins.tm.tmcontroller import TMController


def _unit(source, translated=False):
    return SimpleNamespace(source=source, istranslated=lambda: translated)


class _Request:
    def __init__(self, batch):
        self.batch = batch
        self.handlers = {}

    def connect(self, signal, handler):
        self.handlers[signal] = handler

    def answer(self):
        self.handlers['http-success'](self, b'')

    def fail(self):
        self.handlers['http-server-error'](self, 500)


class _Service:
    """A backend with the bulk interface, each batch two units."""

    online_service = 'Somewhere'

    def __init__(self, name, bulk_max=None, billed=False, ready=True, online=True):
        self.internal_name = self.display_name = name
        self.BULK_MAX = bulk_max
        self.billed = billed
        self.ready = ready
        self.sent = []
        if not online:
            self.online_service = None

    def bulk_ready(self):
        return self.ready

    def bulk_batches(self, sources):
        return [sources[i : i + 2] for i in range(0, len(sources), 2)]

    def bulk_characters(self, sources):
        return sum(len(s) for s in sources)

    def bulk_send(self, batch):
        self.sent.append(_Request(batch))
        return self.sent[-1]

    def bulk_parse(self, batch, reply):
        return {source: [{'source': source, 'target': source.upper()}] for source in batch}

    def bulk_wait(self, request, status):
        return None

    # The allowance: None for no limit; 'ask' to answer when the test says.
    allowance = None

    def bulk_allowance(self, on_done, on_error):
        if self.allowance == 'ask':
            self.asked_allowance = (on_done, on_error)
        else:
            on_done(self.allowance)


UNITS = [_unit('Open'), _unit('Close'), _unit('Save'), _unit('Done', translated=True)]


@pytest.fixture
def made(tmp_path):
    made = []
    yield made
    for dialog, fetched in made:
        if dialog.dialog.get_window() is not None:
            dialog.dialog.destroy()
        fetched.close()


def _dialog(made, tmp_path, *models, units=UNITS, stream=False, checks=frozenset(), checked=None):
    """The dialog for units; checks None for checks not worked out yet,
    which then come out as checked."""
    store = SimpleNamespace(
        get_units=lambda: units,
        checks=None if checks is None else dict(checks),
        get_filename=lambda: str(tmp_path / 'file.po'),
    )

    def update_store_checks(checker):
        store.checks = dict(checked or {})
        return store.checks

    fetched = FetchedSuggestions(path=lambda: str(tmp_path / 'tm.db'))
    fetched.load(units, 'en', 'af')
    controller = TMController.__new__(TMController)
    controller.fetched = fetched
    controller.fetch_words = 1000
    controller.storecursor = SimpleNamespace(index=1)
    controller.main_controller = SimpleNamespace(
        store_controller=SimpleNamespace(
            get_store=lambda: store,
            is_stream=lambda: stream,
            update_store_checks=update_store_checks,
        ),
        checks_controller=SimpleNamespace(get_checker=lambda: 'checker'),
    )
    dialog = FetchSuggestionsDialog(controller, models)
    dialog.show()
    made.append((dialog, fetched))
    return dialog


def _rows(dialog, *cols):
    return [tuple(row[col] for col in cols) for row in dialog.liststore]


def test_each_service_shows_how_much_it_would_be_asked_for(made, tmp_path):
    dialog = _dialog(
        made,
        tmp_path,
        _Service('amagama', bulk_max=2),
        _Service('google', billed=True),
        _Service('apertium', bulk_max=0),
        _Service('deepl', billed=True, ready=False),
        _Service('This file', bulk_max=0, online=False),
    )

    assert _rows(dialog, COL_NAME, COL_AVAILABLE, COL_USE, COL_UNITS, COL_CHARS, COL_STATUS) == [
        ('amagama', True, True, 'Next hour (about 2 units)', '', ''),
        ('apertium', False, False, '', '', 'Unavailable'),
        ('deepl', False, False, '', '', 'Unavailable'),
        # Billed: the user ticks it.
        ('google', True, False, 'Whole file', '13', ''),
    ]
    assert dialog.billed_note.get_visible()
    assert not dialog.progress.get_visible()
    assert not dialog.stop_button.get_visible()


def test_without_a_billed_service_the_note_is_hidden(made, tmp_path):
    dialog = _dialog(made, tmp_path, _Service('amagama'))

    assert not dialog.billed_note.get_visible()


def test_nothing_ticked_nothing_to_fetch(made, tmp_path):
    dialog = _dialog(made, tmp_path, _Service('google', billed=True))

    assert not dialog.fetch_button.get_sensitive()
    dialog._on_toggled(None, '0')
    assert dialog.fetch_button.get_sensitive()


def test_fetching_asks_every_ticked_service_and_shows_progress(made, tmp_path):
    amagama, google = _Service('amagama'), _Service('google', billed=True)
    dialog = _dialog(made, tmp_path, amagama, google)
    dialog._on_toggled(None, '1')

    dialog.dialog.response(RESPONSE_FETCH)

    # From the current unit, Close, on.
    assert [r.batch for r in amagama.sent] == [['Close', 'Save']]
    assert [r.batch for r in google.sent] == [['Close', 'Save']]
    assert dialog.stop_button.get_visible() and not dialog.fetch_button.get_sensitive()
    amagama.sent[0].answer()
    assert _rows(dialog, COL_STATUS) == [('2 of 3',), ('',)]
    assert dialog.progress.get_text() == '2 of 6'
    amagama.sent[1].answer()
    google.sent[0].fail()

    assert _rows(dialog, COL_STATUS) == [('Fetched',), ('Failed',)]
    assert dialog.controller.fetched.matches('amagama', 'Save') == [
        {'source': 'Save', 'target': 'SAVE'}
    ]
    dialog._all_done()
    assert _rows(dialog, COL_AVAILABLE, COL_UNITS, COL_STATUS) == [
        (False, 'Whole file', 'Fetched'),
        (True, 'Whole file', 'Failed'),
    ]
    assert _rows(dialog, COL_CHARS) == [('',), ('13',)]
    assert not dialog.stop_button.get_visible()


def test_stop_cancels_every_fetch(made, tmp_path):
    amagama = _Service('amagama')
    dialog = _dialog(made, tmp_path, amagama)
    dialog.dialog.response(RESPONSE_FETCH)

    dialog.dialog.response(RESPONSE_STOP)
    amagama.sent[0].answer()

    assert _rows(dialog, COL_STATUS) == [('Stopped',)]
    assert len(amagama.sent) == 1
    assert dialog.dialog.get_window() is not None


def test_closing_stops_the_fetch(made, tmp_path):
    amagama = _Service('amagama')
    dialog = _dialog(made, tmp_path, amagama)
    dialog.dialog.response(RESPONSE_FETCH)

    dialog.dialog.response(Gtk.ResponseType.CLOSE)
    amagama.sent[0].answer()

    assert len(amagama.sent) == 1
    assert dialog.controller.fetched.matches('amagama', 'Open') is None


def test_a_service_with_everything_fetched_has_nothing_to_fetch(made, tmp_path):
    fetched = FetchedSuggestions(path=lambda: str(tmp_path / 'tm.db'))
    fetched.load(UNITS, 'en', 'af')
    for service in ('amagama', 'google'):
        fetched.keep(service, {s: [] for s in ('Open', 'Close', 'Save')})
    fetched.close()

    dialog = _dialog(made, tmp_path, _Service('amagama'), _Service('google', billed=True))

    assert _rows(dialog, COL_AVAILABLE, COL_USE, COL_CHARS, COL_STATUS) == [
        (False, False, '', 'Nothing to fetch'),
        (False, False, '', 'Nothing to fetch'),
    ]


def test_several_files_open_together_are_fetched_for_as_one(made, tmp_path):
    # Two files, one after the other; "Done" is translated but fails a check.
    first = [_unit('Open'), _unit('Done', translated=True)]
    second = [_unit('Close'), _unit('Quit', translated=True)]
    amagama = _Service('amagama')

    dialog = _dialog(
        made, tmp_path, amagama, units=first + second, stream=True, checks={'check-endpunc': [1]}
    )

    assert _rows(dialog, COL_UNITS) == [('All files',)]
    dialog.dialog.response(RESPONSE_FETCH)
    # From the current unit, the first file's second, on through the second file.
    assert [r.batch for r in amagama.sent] == [['Done', 'Close']]


# A service's character allowance (DeepL's free one)


def test_whats_left_of_the_allowance_shows_and_caps_the_fetch(made, tmp_path):
    deepl = _Service('deepl', billed=True)
    deepl.allowance = 'ask'
    dialog = _dialog(made, tmp_path, deepl)
    assert _rows(dialog, COL_AVAILABLE, COL_STATUS) == [(False, 'Checking the allowance…')]

    # Close, Save and Open are 5, 4 and 4 characters.
    deepl.asked_allowance[0](10)

    assert _rows(dialog, COL_AVAILABLE, COL_USE, COL_UNITS, COL_CHARS, COL_STATUS) == [
        (True, False, 'Next hour (about 2 units)', '9 of 10 left', 'Cut to the allowance left')
    ]
    dialog._on_toggled(None, '0')
    dialog.dialog.response(RESPONSE_FETCH)
    assert [r.batch for r in deepl.sent] == [['Close', 'Save']]


def test_with_enough_left_nothing_is_cut(made, tmp_path):
    deepl = _Service('deepl', billed=True)
    deepl.allowance = 500000

    dialog = _dialog(made, tmp_path, deepl)

    assert _rows(dialog, COL_UNITS, COL_CHARS, COL_STATUS) == [
        ('Whole file', '13 of 500,000 left', '')
    ]


def test_an_allowance_used_up_asks_nothing(made, tmp_path):
    deepl = _Service('deepl', billed=True)
    deepl.allowance = 0

    dialog = _dialog(made, tmp_path, deepl)

    assert _rows(dialog, COL_AVAILABLE, COL_CHARS, COL_STATUS) == [
        (False, '0 of 0 left', 'Cut to the allowance left')
    ]


def test_a_service_that_wont_say_whats_left_is_unavailable(made, tmp_path):
    deepl = _Service('deepl', billed=True)
    deepl.allowance = 'ask'
    dialog = _dialog(made, tmp_path, deepl)

    deepl.asked_allowance[1]()

    assert _rows(dialog, COL_AVAILABLE, COL_STATUS) == [(False, 'Unavailable')]


def test_the_allowance_is_asked_again_after_a_fetch(made, tmp_path):
    deepl = _Service('deepl', billed=True)
    deepl.allowance = 100
    dialog = _dialog(made, tmp_path, deepl)
    dialog._on_toggled(None, '0')
    dialog.dialog.response(RESPONSE_FETCH)
    deepl.allowance = 'ask'
    for _n in range(2):
        deepl.sent[-1].answer()

    dialog._all_done()

    assert _rows(dialog, COL_STATUS) == [('Checking the allowance…',)]
    deepl.asked_allowance[0](87)
    assert _rows(dialog, COL_CHARS, COL_STATUS) == [('0 of 87 left', 'Fetched')]


def test_a_service_that_keeps_asking_to_wait_shows_stopped(made, tmp_path, monkeypatch):
    from virtaal.plugins.tm import bulkfetch

    # Each wait ends at once.
    monkeypatch.setattr(bulkfetch.GLib, 'timeout_add', lambda ms, func: func() or 1)
    monkeypatch.setattr(bulkfetch.GLib, 'source_remove', lambda source_id: None)
    amagama = _Service('amagama')
    amagama.bulk_wait = lambda request, status: 1
    dialog = _dialog(made, tmp_path, amagama)
    dialog.dialog.response(RESPONSE_FETCH)

    for _n in range(bulkfetch.BulkFetch.MAX_WAITS + 1):
        amagama.sent[-1].handlers['http-client-error'](amagama.sent[-1], 429)

    assert _rows(dialog, COL_STATUS) == [('Stopped',)]


def test_fetch_can_be_pressed_once_the_allowance_arrives(made, tmp_path):
    amagama = _Service('amagama')
    amagama.allowance = 'ask'
    dialog = _dialog(made, tmp_path, amagama)
    assert not dialog.fetch_button.get_sensitive()

    amagama.asked_allowance[0](None)

    assert _rows(dialog, COL_USE) == [(True,)]
    assert dialog.fetch_button.get_sensitive()


# The quality checks, worked out in the background


@pytest.fixture
def child(monkeypatch):
    """check_in_background() that waits for the test to finish it."""
    from virtaal.support import statsworker

    started, stopped = [], []

    def check_in_background(paths, checker, done):
        started.append((paths, checker, done))
        return 'the child'

    monkeypatch.setattr(statsworker, 'check_in_background', check_in_background)
    monkeypatch.setattr(statsworker, 'stop_checking', stopped.append)
    return SimpleNamespace(started=started, stopped=stopped)


CHECKED_UNITS = [_unit('Open'), _unit('Close'), _unit('Done', translated=True)]


def test_the_dialog_opens_before_the_checks_are_worked_out(made, tmp_path, child):
    (tmp_path / 'file.po').write_text('')
    google = _Service('google', billed=True)

    dialog = _dialog(
        made, tmp_path, google, units=CHECKED_UNITS, checks=None, checked={'check-x': [2]}
    )

    assert [(paths, checker) for paths, checker, _done in child.started] == [
        ([str(tmp_path / 'file.po')], 'checker')
    ]
    # Close and Open, from what is known: 9 characters.
    assert _rows(dialog, COL_UNITS, COL_CHARS) == [('Whole file', '9')]
    dialog._on_toggled(None, '0')
    assert dialog.fetch_button.get_sensitive()

    child.started[0][2](True)

    # Done, failing a check, joins them.
    assert _rows(dialog, COL_UNITS, COL_CHARS) == [('Whole file', '13')]


def test_a_running_fetch_takes_in_the_units_failing_a_check(made, tmp_path, child):
    (tmp_path / 'file.po').write_text('')
    amagama = _Service('amagama')
    dialog = _dialog(
        made, tmp_path, amagama, units=CHECKED_UNITS, checks=None, checked={'check-x': [2]}
    )
    dialog.dialog.response(RESPONSE_FETCH)
    assert dialog.fetches[amagama].sources == ['Close', 'Open']

    child.started[0][2](True)
    amagama.sent[0].answer()

    assert [r.batch for r in amagama.sent] == [['Close', 'Open'], ['Done']]
    assert dialog.progress.get_text() == '2 of 3'
    assert _rows(dialog, COL_STATUS) == [('2 of 3',)]


def test_closing_the_dialog_stops_the_checks(made, tmp_path, child):
    (tmp_path / 'file.po').write_text('')
    amagama = _Service('amagama')
    dialog = _dialog(made, tmp_path, amagama, units=CHECKED_UNITS, checks=None)

    dialog.dialog.response(Gtk.ResponseType.CLOSE)
    child.started[0][2](True)

    assert child.stopped == ['the child']
    assert dialog.controller._checks is None
    assert dialog.controller.main_controller.store_controller.get_store().checks is None


def test_checks_already_known_are_not_worked_out_again(made, tmp_path, child):
    _dialog(made, tmp_path, _Service('amagama'), units=CHECKED_UNITS, checks={'check-x': [2]})

    assert child.started == []
