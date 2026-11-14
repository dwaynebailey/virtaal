#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from types import SimpleNamespace

import pytest
from gi.repository import GLib, GObject
from translate.storage import po

from virtaal.controllers.unitcontroller import UnitController
from virtaal.plugins.tm import pretranslate
from virtaal.plugins.tm.fetchdialog import COL_AVAILABLE, COL_CHARS, COL_NAME, COL_STATUS, COL_USE
from virtaal.plugins.tm.pretranslatedialog import RESPONSE_RUN, PretranslateDialog, qualities
from virtaal.plugins.tm.tmcontroller import TMController


class _StoreController(GObject.GObject):
    __gsignals__ = {
        'store-loaded': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'store-closed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }


class _Model:
    display_name = 'A TM'

    def __init__(self, matches):
        self.matches = matches

    def lookup(self, unit, on_done):
        on_done([dict(m) for m in self.matches if m['source'] == unit.source])


class _Undo:
    def __init__(self):
        self.changes = []

    def change_units(self, changes):
        self.changes.append(changes)
        for unit, _before, after in changes:
            from virtaal.support import unitsnapshot

            unitsnapshot.restore(unit, after)


@pytest.fixture
def idle(monkeypatch):
    calls = []

    def add(func, *args, **kwargs):
        calls.append(lambda: func(*args))
        return len(calls)

    monkeypatch.setattr(GLib, 'idle_add', add)
    monkeypatch.setattr(GLib, 'source_remove', lambda _id: None)

    def run():
        while calls:
            calls.pop(0)()

    return run


def _store(*entries):
    store = po.pofile()
    for source, target in entries:
        store.addsourceunit(source).target = target
    return store


def _unit(store, source):
    return next(unit for unit in store.units if unit.source == source)


class _Online(_Model):
    """A free online translation memory, whose file fetch waits for the
    test."""

    display_name = internal_name = online_service = 'Amagama'
    billed = machine_translation = False
    BULK_MAX = None

    def bulk_ready(self):
        return True


class _Fetch:
    def __init__(self, model, sources, on_progress, on_done, characters=None):
        self.model, self.total, self.fetched, self.finished = model, len(sources), 0, None
        self.sources, self.characters = sources, characters
        self._on_progress, self._on_done = on_progress, on_done

    def answer(self):
        self.model.fetched.update(
            {source: [{'source': source, 'target': source[::-1]}] for source in self.sources}
        )
        self.fetched = self.total
        self._on_progress(self)
        self.end('done')

    def end(self, finished):
        self.finished = finished
        self._on_done(self)

    def cancel(self):
        if self.finished is None:
            self.end('cancelled')


def _dialog(store, matches=(), config=None, online=None, mt=()):
    tm = TMController.__new__(TMController)
    tm.abstract_placeables = True
    tm.min_quality = 75
    tm.config = config if config is not None else {}
    plugins = {'tm': _Model(list(matches))}
    if online is not None:
        plugins['amagama'] = online
    plugins.update((model.internal_name, model) for model in mt)
    tm.plugin_controller = SimpleNamespace(plugins=plugins)
    tm.file_units = lambda: store.units
    tm.fetches = []
    tm.unfetched = lambda model, sources: [
        s for s in map(str, sources) if s not in getattr(model, 'fetched', ())
    ]
    tm.fetch_sources = lambda *args: tm.fetches.append(_Fetch(*args)) or tm.fetches[-1]
    unit_controller = UnitController.__new__(UnitController)
    tm.main_controller = SimpleNamespace(
        checks_controller=None,
        lang_controller=SimpleNamespace(get_target_nplurals=lambda: 2),
        store_controller=_StoreController(),
        unit_controller=unit_controller,
        undo_controller=_Undo(),
    )
    return PretranslateDialog(tm, None)


OPEN = {'source': 'Open', 'target': 'Maak oop', 'quality': 100}


def test_the_lowest_qualities_offered_start_at_the_suggestions_lowest():
    assert qualities(75) == [100, 95, 90, 85, 80, 75]
    assert qualities(90) == [100, 95, 90]


def test_it_fills_and_says_what_it_filled(idle):
    store = _store(('Open', ''), ('Close', ''), ('Quit', 'Hou op'))
    dialog = _dialog(store, [OPEN])
    dialog.show()
    assert not dialog.close_button.get_visible()

    dialog.dialog.response(RESPONSE_RUN)
    idle()

    (changes,) = dialog.main_controller.undo_controller.changes
    assert [unit.source for unit, _before, _after in changes] == ['Open']
    assert _unit(store, 'Open').target == 'Maak oop'
    assert _unit(store, 'Open').isfuzzy()
    assert dialog.intro.get_text() == 'Filled 1 unit.'
    labels = [child.get_text() for child in reversed(dialog.summary.get_children())]
    assert labels == ['From translation memory:', '1', 'Still to translate:', '1']
    assert dialog.summary.get_visible()
    assert all(child.get_visible() for child in dialog.summary.get_children())
    assert dialog.close_button.get_visible()
    assert not dialog.run_button.get_visible()
    dialog.dialog.destroy()


def test_it_remembers_the_options_chosen(idle):
    config = {}
    dialog = _dialog(_store(('Open', '')), config=config)
    dialog.quality.set_active_id('90')
    dialog.exact_translated.set_active(True)

    dialog.dialog.response(RESPONSE_RUN)
    idle()

    assert pretranslate.load_options(config) == pretranslate.Options(90, True)
    assert _dialog(_store(('Open', '')), config=config).quality.get_active_id() == '90'
    dialog.dialog.destroy()


def test_it_names_the_files_review_state():
    dialog = _dialog(_store(('Open', '')))
    texts = [w.get_text() for w in dialog.options_box.get_children() if hasattr(w, 'get_text')]

    assert 'The units filled are marked “Needs work”, for you to review.' in texts
    dialog.dialog.destroy()


def test_with_nothing_to_fill_it_says_so():
    dialog = _dialog(_store(('Open', 'Maak oop')))

    assert dialog.intro.get_text() == 'Every unit has a translation already.'
    assert not dialog.run_button.get_sensitive()
    dialog.dialog.destroy()


def test_cancelling_changes_nothing(idle):
    store = _store(('Open', ''))
    dialog = _dialog(store, [OPEN])
    dialog.dialog.response(RESPONSE_RUN)

    dialog.dialog.response(-6)
    idle()

    assert dialog.main_controller.undo_controller.changes == []
    assert _unit(store, 'Open').target == ''


def test_the_file_closing_stops_it(idle):
    store = _store(('Open', ''))
    dialog = _dialog(store, [OPEN])
    dialog.dialog.response(RESPONSE_RUN)

    dialog.main_controller.store_controller.emit('store-closed')
    idle()

    assert dialog.main_controller.undo_controller.changes == []


def test_a_unit_of_a_file_not_read_yet_is_read_from_the_stream():
    from virtaal.models.streammodel import PeekUnit

    store = _store(('Open', ''))
    dialog = _dialog(store)
    real = _unit(store, 'Open')
    dialog.main_controller.store_controller.get_store = lambda: SimpleNamespace(
        get_unit=lambda index: real
    )

    assert dialog._read(4, PeekUnit('1', 'Open', '', 0)) is real
    assert dialog._read(4, real) is real
    dialog.dialog.destroy()


ONLINE_NOTE = 'Free online translation memories, such as Amagama, are asked for these units first.'


def _texts(box):
    return [w.get_text() for w in box.get_children() if hasattr(w, 'get_text')]


def test_it_says_online_translation_memories_are_asked_only_with_one_on():
    with_online = _dialog(_store(('Open', '')), online=_Online([]))
    without = _dialog(_store(('Open', '')))

    assert ONLINE_NOTE in _texts(with_online.options_box)
    assert ONLINE_NOTE not in _texts(without.options_box)
    with_online.dialog.destroy()
    without.dialog.destroy()


def test_it_shows_the_fetch_then_fills(idle):
    store = _store(('Open', ''))
    dialog = _dialog(store, online=_Online([OPEN]))
    dialog.show()

    dialog.dialog.response(RESPONSE_RUN)
    idle()
    assert dialog.phase.get_visible()
    assert dialog.phase.get_text() == 'Fetching suggestions…'
    assert dialog.progress.get_text() == '0 of 1'
    dialog.tm_controller.fetches[0].end('done')
    idle()

    assert _unit(store, 'Open').target == 'Maak oop'
    assert not dialog.phase.get_visible()
    dialog.dialog.destroy()


def test_it_says_which_service_could_not_be_reached(idle):
    store = _store(('Open', ''))
    dialog = _dialog(store, [OPEN], online=_Online([]))
    dialog.show()

    dialog.dialog.response(RESPONSE_RUN)
    dialog.tm_controller.fetches[0].end('failed')
    idle()

    texts = _texts(dialog.dialog.get_content_area())
    assert "Amagama couldn't be reached; only the other sources were used." in texts
    assert dialog.intro.get_text() == 'Filled 1 unit.'
    dialog.dialog.destroy()


def test_cancelling_while_fetching_changes_nothing(idle):
    store = _store(('Open', ''))
    dialog = _dialog(store, [OPEN], online=_Online([OPEN]))
    dialog.dialog.response(RESPONSE_RUN)

    dialog.dialog.response(-6)
    idle()

    assert dialog.tm_controller.fetches[0].finished == 'cancelled'
    assert dialog.main_controller.undo_controller.changes == []
    assert _unit(store, 'Open').target == ''


# Machine translation services


class _MT:
    BULK_MAX = None
    machine_translation = True
    online_service = 'MT'

    def __init__(self, name, billed=False, allowance=None):
        self.internal_name = self.display_name = name
        self.billed = billed
        self.allowance = allowance
        self.fetched = {}

    def bulk_ready(self):
        return True

    def bulk_allowance(self, on_done, on_error):
        if self.allowance == 'refused':
            on_error()
        else:
            on_done(self.allowance)

    def bulk_characters(self, sources):
        return sum(len(s) for s in sources)

    def lookup(self, unit, on_done):
        on_done(self.fetched.get(unit.source, []))


def _rows(dialog):

    return {
        row[COL_NAME]: (row[COL_USE], row[COL_AVAILABLE], row[COL_CHARS], row[COL_STATUS])
        for row in dialog.liststore
    }


def _tick(dialog, name):
    path = next(str(row.path) for row in dialog.liststore if row[COL_NAME] == name)
    dialog._on_toggled(None, path)


def test_it_lists_the_machine_translation_services_with_paid_ones_unticked():
    store = _store(('Open', ''), ('Close', ''))
    config = {'pretranslate_mt_services': 'free,paid'}
    dialog = _dialog(store, config=config, mt=[_MT('free'), _MT('paid', billed=True)])

    assert _rows(dialog) == {
        'free': (True, True, '', ''),
        'paid': (False, True, '9', ''),
    }
    assert dialog.billed_note.get_visible()
    dialog.dialog.destroy()


def test_with_no_machine_translation_on_there_is_no_list():
    dialog = _dialog(_store(('Open', '')))

    assert len(dialog.liststore) == 0
    assert dialog.treeview.get_parent() is None
    dialog.dialog.destroy()


def test_a_free_services_tick_is_remembered_and_a_paid_ones_is_not(idle):
    config = {}
    models = [_MT('free'), _MT('paid', billed=True)]
    dialog = _dialog(_store(('Open', '')), config=config, mt=models)
    _tick(dialog, 'free')
    _tick(dialog, 'paid')

    dialog.dialog.response(RESPONSE_RUN)
    idle()

    assert config['pretranslate_mt_services'] == 'free'
    again = _dialog(_store(('Open', '')), config=config, mt=models)
    assert [name for name, row in _rows(again).items() if row[0]] == ['free']
    dialog.dialog.destroy()
    again.dialog.destroy()


def test_a_paid_service_is_sent_only_what_translation_memory_left_and_shown(idle):
    store = _store(('Open', ''), ('Close', ''), ('Quit', ''))
    paid = _MT('paid', billed=True)
    dialog = _dialog(store, [OPEN], mt=[paid])
    assert _rows(dialog)['paid'][2] == '13'
    _tick(dialog, 'paid')

    dialog.dialog.response(RESPONSE_RUN)
    idle()
    (fetch,) = dialog.tm_controller.fetches
    assert fetch.sources == ['Close', 'Quit']
    assert _rows(dialog)['paid'][2:] == ('9', '0 of 2')
    assert dialog.phase.get_text() == 'Fetching suggestions…'
    fetch.answer()
    idle()

    assert _rows(dialog)['paid'][2:] == ('9', 'Fetched')
    assert [_unit(store, s).target for s in ('Open', 'Close', 'Quit')] == [
        'Maak oop',
        'esolC',
        'tiuQ',
    ]
    labels = [child.get_text() for child in reversed(dialog.summary.get_children())]
    assert labels[:4] == ['From translation memory:', '1', 'From machine translation:', '2']
    dialog.dialog.destroy()


def test_a_service_is_cut_to_its_allowance(idle):
    store = _store(('Close', ''), ('Quit', ''), ('Save', ''))
    paid = _MT('paid', billed=True, allowance=9)
    dialog = _dialog(store, mt=[paid])
    assert _rows(dialog)['paid'][2:] == ('9 of 9 left', 'Cut to the allowance left')
    _tick(dialog, 'paid')

    dialog.dialog.response(RESPONSE_RUN)
    idle()

    (fetch,) = dialog.tm_controller.fetches
    assert fetch.sources == ['Close', 'Quit'] and fetch.characters == 9
    dialog.dialog.destroy()


def test_a_service_that_wont_say_its_allowance_cant_be_ticked():
    dialog = _dialog(_store(('Open', '')), mt=[_MT('paid', billed=True, allowance='refused')])

    assert _rows(dialog)['paid'] == (False, False, '', 'Unavailable')
    dialog.dialog.destroy()
