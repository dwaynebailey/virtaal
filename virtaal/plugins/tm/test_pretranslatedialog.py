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


def _dialog(store, matches=(), config=None):
    tm = TMController.__new__(TMController)
    tm.abstract_placeables = True
    tm.min_quality = 75
    tm.config = config if config is not None else {}
    tm.plugin_controller = SimpleNamespace(plugins={'tm': _Model(list(matches))})
    tm.file_units = lambda: store.units
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
    dialog.use_mt.set_active(True)

    dialog.dialog.response(RESPONSE_RUN)
    idle()

    assert pretranslate.load_options(config) == pretranslate.Options(90, True, False)
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
