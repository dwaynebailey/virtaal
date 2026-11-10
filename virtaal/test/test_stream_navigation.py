#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Moving through several files opened as one list of units."""

import pytest
from gi.repository import GLib
from test_scaffolding import TestScaffolding

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.models.streammodel import StreamModel
from virtaal.support import statsdb, statsworker


def _po(path, units):
    body = ''.join('msgid "%s"\nmsgstr "%s"\n\n' % unit for unit in units)
    path.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Language: af\\n"\n\n%s' % body,
        encoding='utf-8',
    )
    return str(path)


def _drain():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class TestStreamNavigation(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        # Read a file only once the cursor reaches it.
        monkeypatch.setattr(StreamModel, 'PREFETCH_UNITS', 0)
        self.monkeypatch = monkeypatch
        self.dir = tmp_path
        _po(tmp_path / 'a.po', [('One.', 'Een'), ('Two', 'Twee')])
        _po(tmp_path / 'b.po', [('Three', 'Drie'), ('Four', 'Vier')])
        _po(tmp_path / 'c.po', [('Five.', 'Vyf'), ('Six', '')])
        self.store_controller.close_file()
        yield
        self.store_controller.set_modified(False)
        self.mode_controller.select_default_mode()
        self.store_controller.close_file()

    def _item(self, name):
        return self.main_controller.view.gui.get_object(name)

    def _open(self, *paths):
        self.store_controller.open_files([str(path) for path in paths or [self.dir]])
        _drain()

    def _activate(self, name):
        self._item(name).activate()
        _drain()

    def test_next_and_previous_file_go_to_each_files_first_unit(self):
        self._open()
        cursor = self.store_controller.cursor

        self._activate('mnu_next_file')
        assert cursor.index == 2
        self._activate('mnu_next_file')
        assert cursor.index == 4
        assert self.store_controller.store.segments[2].loaded
        self._activate('mnu_next_file')
        assert cursor.index == 4

        cursor.index = 5
        _drain()
        self._activate('mnu_prev_file')
        assert cursor.index == 2
        self._activate('mnu_prev_file')
        self._activate('mnu_prev_file')
        assert cursor.index == 0

    def test_next_file_stays_within_the_files_chosen(self):
        self._open()
        cursor = self.store_controller.cursor
        cursor.set_scope([(0, 2), (4, 6)])
        _drain()

        self._activate('mnu_next_file')

        assert cursor.index == 4

    def test_first_and_last_unit_stay_in_the_current_file(self):
        self._open()
        cursor = self.store_controller.cursor
        cursor.index = 3
        _drain()

        self._activate('mnu_first')
        assert cursor.index == 2
        self._activate('mnu_last')
        assert cursor.index == 3

    def test_first_and_last_unit_of_all_files_are_the_ends_of_the_stream(self):
        self._open()
        cursor = self.store_controller.cursor
        cursor.index = 3
        _drain()

        self._activate('mnu_last_of_all')
        assert cursor.index == 5
        self._activate('mnu_first_of_all')
        assert cursor.index == 0

    @pytest.mark.parametrize(
        ('item', 'path', 'keyval'),
        [('mnu_first_of_all', 'FirstOfAll', 'Home'), ('mnu_last_of_all', 'LastOfAll', 'End')],
    )
    def test_alt_home_and_end_are_the_items_of_all_files(self, item, path, keyval):
        from gi.repository import Gdk, Gtk

        path = '<Virtaal>/Navigation/' + path
        _found, key = Gtk.AccelMap.lookup_entry(path)

        assert (key.accel_key, key.accel_mods) == (
            Gdk.keyval_from_name(keyval),
            Gdk.ModifierType.MOD1_MASK,
        )
        assert self._item(item).get_accel_path() == path

    def test_the_file_items_are_only_for_several_files(self):
        self._open(self.dir / 'a.po')
        assert not self._item('mnu_next_file').get_sensitive()
        assert not self._item('mnu_prev_file').get_sensitive()
        assert not self._item('mnu_first_of_all').get_sensitive()
        assert not self._item('mnu_last_of_all').get_sensitive()
        assert self._item('mnu_first').get_sensitive()

        self._open()
        assert self._item('mnu_next_file').get_sensitive()
        assert self._item('mnu_prev_file').get_sensitive()

        self.store_controller.close_file()
        assert not self._item('mnu_next_file').get_sensitive()

    def test_units_stop_at_the_ends_of_the_stream(self):
        self._open()
        cursor = self.store_controller.cursor
        cursor.index = 5
        _drain()

        cursor.move(1)
        assert cursor.index == 5
        cursor.index = 0
        cursor.move(-1)
        assert cursor.index == 0

    def _search(self, text, enter=True):
        mode = self.mode_controller.modes['Search']
        # Set by the search box's first style-set, which needs a window.
        mode.default_base = '#ffffff'
        select_match = mode.select_match

        def select_unit_only(match):
            try:
                select_match(match)
            except IndexError:
                # Its text boxes: the editor is only laid out in a window.
                pass

        self.monkeypatch.setattr(mode, 'select_match', select_unit_only)
        # Left from an earlier test, it would search on selecting the mode.
        mode.ent_search.set_text('')
        self.mode_controller.select_mode(mode)
        mode.ent_search.set_text(text)
        if enter:
            mode._on_entry_activate(mode.ent_search)
        else:
            mode._cancel_search_timeout()
            mode.update_search()
        _drain()
        return mode

    def test_search_finds_a_unit_in_a_file_not_read_yet(self):
        self._open()
        sc = self.store_controller
        assert not sc.store.segments[2].loaded

        mode = self._search('Six')

        assert sc.cursor.index == 5
        assert sc.store.segments[2].loaded
        unit = sc.store.segments[2].model.get_unit(1)
        assert sc.cursor.deref() is unit
        assert [match.unit for match in mode.matches] == [unit]

    def test_search_wraps_between_the_first_and_last_file(self):
        self._open()
        sc = self.store_controller
        mode = self._search('i', enter=False)
        # In Drie, Vier, Five and Six.
        assert sc.cursor.indices == [2, 3, 4, 5]
        assert sc.cursor.index == 2
        assert not sc.store.segments[2].loaded

        mode._move_match(-1)
        _drain()
        assert sc.cursor.index == 5

        mode._move_match(1)
        _drain()
        assert sc.cursor.index == 2

    def test_search_stays_within_the_files_chosen(self):
        self._open()
        sc = self.store_controller
        sc.cursor.set_scope([(0, 2), (4, 6)])
        _drain()

        mode = self._search('i')

        assert sc.cursor.indices == [4, 5]
        assert [match.unit.source for match in mode.matches] == ['Five.', 'Six']

    def test_quality_checks_leave_files_not_read_yet_to_a_child(self):
        asked = []
        self.monkeypatch.setattr(
            statsworker,
            'check_in_background',
            lambda filenames, checker, done: asked.append((filenames, done)) or True,
        )
        self._open()
        sc = self.store_controller
        mode = self.mode_controller.modes['QualityCheck']
        self.mode_controller.select_mode(mode)

        assert [len(paths) for paths, _done in asked] == [2]
        assert mode.btn_popup.get_label() == 'Checking files…'
        assert mode.stats['check-endpunc'] == [0]
        mode.filter_checks = ['check-endpunc']
        mode.update_indices()
        assert sc.cursor.indices == [0]

        checker = self.checks_controller.get_checker()
        for path in asked[0][0]:
            statsdb.StatsCache().filechecks(path, checker)
        asked[0][1](True)
        _drain()

        assert mode.stats['check-endpunc'] == [0, 4]
        assert sc.cursor.indices == [0, 4]
        assert mode.btn_popup.get_label() != 'Checking files…'
