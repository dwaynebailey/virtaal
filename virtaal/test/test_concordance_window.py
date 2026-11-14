#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Edit > Search Translation Memory…: the window, its search of every
source that can search, and inserting from it."""

import os
import time

import pytest
from gi.repository import Gdk, GLib, Gtk
from test_scaffolding import TestScaffolding

from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.tm import concordance as concordance_window
from virtaal.plugins.tm.tmcontroller import TMController
from virtaal.support import tmdb
from virtaal.views import setscanview
from virtaal.views.widgets import shortcutswindow

MODELS = [
    '_dummytm',
    'amagama',
    'apertium',
    'bundle',
    'currentfile',
    'deepl',
    'google_translate',
    'localtm',
    'microsoft_translator',
    'moses',
    'remotetm',
]
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _wait(condition, seconds=5):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    assert condition()


class _Model:
    """A source of suggestions searching a real TM database."""

    display_name = 'Test TM'

    def __init__(self, db, answer=True, fail=False):
        self.db = db
        self.answer = answer
        self.fail = fail
        self.asked = []
        self.cancelled = 0

    def concordance(self, options, on_done, on_error):
        self.asked.append(options)
        if self.fail:
            on_error()
        elif self.answer:
            found = self.db.concordance(
                options['text'],
                'en',
                'af',
                side=options['side'],
                case=options['case'],
                whole_words=options['whole_words'],
            )
            on_done(found['matches'], found['total'], found['complete'])

    def cancel_concordance(self):
        self.cancelled += 1


def _po(path, units):
    body = ''.join('msgid "%s"\nmsgstr "%s"\n\n' % unit for unit in units)
    path.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Language: af\\n"\n\n' + body,
        encoding='utf-8',
    )
    return str(path)


class TestConcordanceWindow(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _environment(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pan_app, 'get_config_dir', lambda: str(tmp_path))
        undo = self.undo_controller
        monkeypatch.setattr(undo, '_select_unit', lambda unit: undo._flush_pending_refresh())
        monkeypatch.setattr(undo, '_correct_state_after_undo_redo', lambda unit=None: None)
        self.db_path = str(tmp_path / 'tm.db')
        self.db = tmdb.TMDB(self.db_path)
        units = [
            ('Open &File', 'Maak &lêer oop'),
            ('Delete %d files', 'Skrap %d lêers'),
            ('Profile', 'Profiel'),
        ]
        self.db.sync_store(
            '/files/app.po',
            [{'source': s, 'target': t, 'context': None, 'key': s} for s, t in units],
            'en',
            'af',
        )
        self.tm = TMController(self.main_controller, {'disabled_models': MODELS})
        self.model = _Model(self.db)
        self.window = self.tm.concordance
        monkeypatch.setattr(self.window, '_models', lambda: iter([('Test TM', self.model)]))
        self.store_controller.close_file()
        self.po = _po(
            tmp_path / 'open.po', [('Open the file', ''), ('Save the file', 'Stoor die lêer')]
        )
        self.store_controller.open_file(self.po)
        yield
        self.store_controller.set_modified(False)
        self.store_controller.close_file()
        self.tm.destroy()
        for connection, _cursor in tmdb.TMDB._tm_dbs.pop(self.db_path, {}).values():
            connection.close()

    def _load(self, index=0):
        unit = self.store_controller.store.get_units()[index]
        view = self.unit_controller.load_unit(unit)
        view._layout_update_targets()
        view.focused_target_n = 0
        return view

    def _focus(self, monkeypatch, textbox, start=None, end=None):
        if start is not None:
            buffer = textbox.get_buffer()
            buffer.select_range(buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end))
        window = self.main_controller.view.main_window
        monkeypatch.setattr(window, 'get_focus', lambda: textbox)

    def _settle(self):
        context = GLib.MainContext.default()
        while self.window._scan is not None:
            context.iteration(False)

    def _search(self, text, side=None):
        self.window._view().show()
        self.window.view.set_search(text, side)
        self.window.start()
        self._settle()
        return [tuple(row) for row in self.window.view.liststore]

    def test_a_selected_translation_is_searched_in_the_translations(self, monkeypatch):
        view = self._load()
        view.targets[0].set_text('Maak die lêer oop')
        self._focus(monkeypatch, view.targets[0], 9, 13)

        self.window.show()
        self._settle()

        assert self.model.asked[-1]['text'] == 'lêer'
        assert self.model.asked[-1]['side'] == 'target'
        assert self.window.view.ent_search.get_text() == 'lêer'
        assert [m['target'] for m in self.window.matches] == [
            'Maak &lêer oop',
            'Stoor die lêer',
            'Skrap %d lêers',
        ]
        assert self.window.view.dialog.get_visible()

    def test_a_selected_source_is_searched_in_the_sources(self, monkeypatch):
        view = self._load()
        self._focus(monkeypatch, view.sources[0], 0, 4)

        self.window.show()

        assert self.model.asked[-1]['text'] == 'Open'
        assert self.model.asked[-1]['side'] == 'source'
        assert not self.window.view.chk_target.get_active()

    def test_nothing_selected_opens_the_window_without_searching(self, monkeypatch):
        view = self._load()
        self._focus(monkeypatch, view.targets[0])

        self.window.show()

        assert self.model.asked == []
        assert self.window.view.dialog.get_visible()

    def test_the_rows_show_the_phrase_highlighted_and_where_it_is_from(self):
        rows = self._search('open file')

        ((source, target, found_in, tip),) = rows
        assert source == '<span %s>Open &amp;File</span>' % concordance_window.HIGHLIGHT
        assert target == 'Maak &amp;lêer oop'
        assert found_in == 'app.po'
        assert '/files/app.po' in tip
        assert self.window.view.lbl_summary.get_text() == '1 match'

    def test_the_preview_shows_the_selected_row_whole(self):
        self._search('files', 'source')

        self.window.view.treeview.get_selection().select_path(Gtk.TreePath(0))

        source, target = (
            view.get_buffer().get_text(*view.get_buffer().get_bounds(), False)
            for view in self.window.view.previews
        )
        assert (source, target) == ('Delete %d files', 'Skrap %d lêers')
        assert self.window.view.btn_insert.get_sensitive()
        assert not self.window.view.btn_go_to.get_sensitive()

    def test_insert_puts_the_translation_at_the_cursor_and_undo_takes_it_out(self):
        view = self._load()
        target = view.targets[0]
        target.set_text('Begin ')
        target.get_buffer().place_cursor(target.get_buffer().get_end_iter())
        self._search('open file')
        self.undo_controller.model.clear()

        self.window.insert(0)

        assert target.get_text() == 'Begin Maak &lêer oop'
        assert self.window.view.dialog.get_visible()
        # Undo waits for the main window to come forward again.
        _wait(lambda: not self.window.view.dialog.is_active())
        self.undo_controller._on_undo_activated()
        _wait(lambda: target.get_text() == 'Begin ')

    def test_insert_puts_the_part_selected_in_the_preview(self):
        view = self._load()
        target = view.targets[0]
        target.set_text('')
        self._search('open file')
        self.window.view.treeview.get_selection().select_path(Gtk.TreePath(0))
        buffer = self.window.view.previews[1].get_buffer()
        buffer.select_range(buffer.get_iter_at_offset(6), buffer.get_iter_at_offset(10))

        self.window.insert(0)

        assert target.get_text() == 'lêer'

    def test_the_files_open_are_searched_with_their_unsaved_changes(self):
        self._load(1)
        self.store_controller.store.get_units()[0].target = 'Maak die lêer oop'

        rows = self._search('the file', 'source')

        assert [(row[0], row[1], row[2]) for row in rows] == [
            (
                'Open <span %s>the file</span>' % concordance_window.HIGHLIGHT,
                'Maak die lêer oop',
                'open.po',
            ),
        ]
        assert [m['index'] for m in self.window.matches] == [0]

    def test_the_unit_being_edited_is_left_out(self):
        view = self._load(1)
        view.targets[0].set_text('Stoor die lêer')

        assert self._search('the file', 'source') == []

    def test_an_untranslated_unit_is_left_out(self):
        rows = self._search('open the', 'source')

        assert rows == []

    def test_go_to_unit_moves_to_a_match_of_the_files_open(self):
        self._search('save the', 'source')
        self.window.view.treeview.get_selection().select_path(Gtk.TreePath(0))
        assert self.window.view.btn_go_to.get_sensitive()

        self.window.go_to(0)

        assert self.store_controller.cursor.index == 1

    def test_closing_the_files_drops_their_matches(self):
        self._search('the file', 'source')

        self.store_controller.close_file()

        assert [tuple(row) for row in self.window.view.liststore] == []

    def test_the_files_open_are_searched_a_slice_at_a_time(self, monkeypatch):
        monkeypatch.setattr(concordance_window, 'OPEN_FILES_STEP', 1)
        self.window._view()
        self.window.view.set_search('the file', 'source')
        self.window.start()
        context = GLib.MainContext.default()
        context.iteration(False)

        assert 'Searching Open Files…' in self.window.view.lbl_summary.get_text()
        self._settle()
        assert self.window.view.lbl_summary.get_text() == '1 match'

    def test_copy_puts_the_translation_on_the_clipboard(self):
        self._search('profile')

        self.window.copy(0)

        assert Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() == 'Profiel'

    def test_edit_copy_copies_the_selected_rows_translation(self):
        # macOS's Cmd+C goes to the Edit menu, which sends it to the list.
        self._search('open file')
        self.window.view.treeview.get_selection().select_path(Gtk.TreePath(0))

        self.window.view.treeview.emit('copy-clipboard')

        assert Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() == 'Maak &lêer oop'

    def test_stop_cancels_a_search_on_its_way(self):
        self.model.answer = False
        self._search('open file')
        assert 'Searching Test TM…' in self.window.view.lbl_summary.get_text()

        self.window.stop()

        assert self.model.cancelled >= 1
        assert self.window.view.lbl_summary.get_text() == '0 matches'
        assert not self.window.view.btn_stop.get_sensitive()

    def test_a_source_that_fails_says_so(self):
        self.model.fail = True

        self._search('open file')

        assert self.window.view.lbl_summary.get_text() == '0 matches Test TM couldn’t be searched.'

    def test_a_phrase_too_short_can_t_be_searched(self):
        self.window._view()

        self.window.view.set_search('a ', None)

        assert not self.window.view.can_start()

    def test_ctrl_k_is_the_shortcut(self):
        known, key = Gtk.AccelMap.lookup_entry(concordance_window.ACCEL_PATH)

        assert known
        assert (key.accel_key, key.accel_mods) == (Gdk.KEY_k, Gdk.ModifierType.CONTROL_MASK)
        assert self.main_controller.view.gui.get_object('mnu_search_tm').get_sensitive()


@pytest.mark.parametrize(
    'path', ['share/virtaal/virtaal.accel', 'devsupport/mac-bundle/virtaal.accel']
)
def test_macos_has_cmd_k(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as accel:
        lines = accel.read().splitlines()

    assert '(gtk_accel_path "%s" "<Meta>k")' % concordance_window.ACCEL_PATH in lines


def test_the_shortcuts_window_lists_it():
    shortcuts = [key for _title, rows in shortcutswindow.SHORTCUT_GROUPS for key, _text in rows]

    assert '<Primary>k' in shortcuts


@pytest.mark.parametrize(
    ('count', 'listed', 'truncated', 'failed', 'searching', 'expected'),
    [
        (1, 1, False, [], [], '1 match'),
        (
            500,
            200,
            True,
            [],
            ['Local TM'],
            '500 matches Only the first 200 are listed. Searching Local TM…',
        ),
        (0, 0, False, ['Local TM'], [], '0 matches Local TM couldn’t be searched.'),
    ],
)
def test_concordance_summary(count, listed, truncated, failed, searching, expected):
    assert setscanview.concordance_summary(count, listed, truncated, failed, searching) == expected


def test_a_merged_row_says_how_many():
    assert concordance_window.found_in({'found_in': 'app.po', 'count': 3}) == 'app.po ×3'


def test_the_side_follows_the_parts_chosen():
    assert concordance_window.side_name(['source', 'target']) == 'both'
    assert concordance_window.side_name(['target']) == 'target'


def test_the_tooltip_lists_every_file_and_the_date():
    match = {'context': 'menu', 'origins': ['/a.po', '/b <c>.po'], 'time': 1699963200}

    tip = concordance_window.tooltip(match)

    assert tip == GLib.markup_escape_text('menu\nIn 2 files:\n/a.po\n/b <c>.po\n2023-11-14')
