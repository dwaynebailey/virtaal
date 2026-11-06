#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The files panel beside several files opened as one list."""

import sys

import pytest
from gi.repository import Gdk, GLib, Gtk, Pango
from test_scaffolding import TestScaffolding

from virtaal.controllers.filepanelcontroller import FilePanelController
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.models.langmodel import LanguageModel
from virtaal.views import filepanelview


def _po(path, units, language='af'):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = '"Language: %s\\n"\n' % language if language else ''
    body = ''.join('msgid "%s"\nmsgstr "%s"\n\n' % unit for unit in units)
    path.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n%s\n%s'
        % (header, body),
        encoding='utf-8',
    )
    return str(path)


def _rows(view):
    found = {}

    def walk(it, prefix):
        while it is not None:
            row = view.model[it]
            name = prefix + row[filepanelview.COL_NAME]
            found[name] = row
            walk(view.model.iter_children(it), name + '/')
            it = view.model.iter_next(it)

    walk(view.model.get_iter_first(), '')
    return found


class TestFilePanel(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)
        self.panel = FilePanelController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        self.dir = tmp_path
        _po(tmp_path / 'app' / 'af.po', [('One', 'Een'), ('Two', '')])
        _po(tmp_path / 'help' / 'af' / 'guide.po', [('Three', ''), ('Four', 'Vier')])
        _po(tmp_path / 'help' / 'af' / 'faq.po', [('Five', '')])
        _po(tmp_path / 'app' / 'de.po', [('One', 'Eins')], language='de')
        _po(tmp_path / 'notes' / 'extra.po', [('Six', '')], language=None)
        # Not set_language_pair(): with no file open, that fails.
        self.lang_controller._target_lang = LanguageModel('af')
        self.store_controller.close_file()
        yield
        self.store_controller.set_modified(False)
        self.store_controller.close_file()

    def _open_folder(self):
        self.main_controller.open_files([str(self.dir)])
        return self.panel.view

    def test_a_folder_opens_with_the_panel_shown(self):
        view = self._open_folder()

        assert view.shown
        assert self.panel.menu_item.get_sensitive()
        assert self.panel.menu_item.get_active()
        assert set(_rows(view)) == {
            'app',
            'app/af.po',
            'help',
            'help/af',
            'help/af/faq.po',
            'help/af/guide.po',
            'notes',
            'notes/extra.po (?)',
        }

    def test_several_files_open_with_the_panel_available_but_hidden(self):
        self.main_controller.open_files(
            [str(self.dir / 'app' / 'af.po'), str(self.dir / 'help' / 'af' / 'faq.po')]
        )

        assert not self.panel.view.shown
        assert self.panel.menu_item.get_sensitive()
        assert not self.panel.menu_item.get_active()

        self.panel.menu_item.activate()

        assert self.panel.view.shown

    def test_one_file_has_no_panel(self):
        self._open_folder()

        self.main_controller.open_file(str(self.dir / 'help' / 'af' / 'faq.po'))

        assert not self.panel.view.shown
        assert not self.panel.menu_item.get_sensitive()

    def test_progress_is_shown_per_file_and_folder(self):
        rows = _rows(self._open_folder())

        assert rows['app/af.po'][filepanelview.COL_PERCENT] == 50
        assert rows['help'][filepanelview.COL_PERCENT] == 33
        assert rows['notes'][filepanelview.COL_PERCENT] == 0

    def test_selecting_files_shows_only_their_units(self):
        view = self._open_folder()
        rows = _rows(view)
        selection = view.treeview.get_selection()

        selection.select_path(rows['app/af.po'].path)
        selection.select_path(rows['help/af/faq.po'].path)

        cursor = self.store_controller.cursor
        sources = [self.store_controller.store.peek_unit(i).source for i in cursor.indices]
        assert sources == ['One', 'Two', 'Five']
        assert self.store_controller.view._treeview.get_model().row_count() == 3

    def test_selecting_a_folder_shows_the_files_in_it(self):
        view = self._open_folder()

        view.treeview.get_selection().select_path(_rows(view)['help'].path)

        store = self.store_controller.store
        cursor = self.store_controller.cursor
        assert [store.peek_unit(i).source for i in cursor.indices] == ['Five', 'Three', 'Four']
        assert store.peek_unit(cursor.index).source == 'Five'

    def test_escape_clears_the_selection(self):
        view = self._open_folder()
        view.treeview.get_selection().select_path(_rows(view)['help'].path)
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.key.keyval = Gdk.KEY_Escape

        view.treeview.emit('key-press-event', event)

        assert self.store_controller.cursor.scope is None
        assert len(self.store_controller.cursor.indices) == 6

    def test_the_cursor_s_file_and_folders_are_marked(self):
        view = self._open_folder()
        cursor = self.store_controller.cursor

        cursor.index = 4

        rows = _rows(view)
        bold = sorted(
            name for name, row in rows.items() if row[filepanelview.COL_WEIGHT] == Pango.Weight.BOLD
        )
        assert bold == ['help', 'help/af', 'help/af/guide.po']
        assert view.treeview.get_selection().count_selected_rows() == 0

    def test_choosing_a_language_opens_its_files(self):
        view = self._open_folder()
        assert [row[1] for row in view.language_combo.get_model()] == [
            'Afrikaans (3 files)',
            'German (1 file)',
        ]
        assert view.language_box.get_visible()

        self.panel.language_chosen('de')

        assert self.store_controller.stream_language == 'de'
        assert set(_rows(self.panel.view)) == {'app', 'app/de.po', 'notes', 'notes/extra.po (?)'}
        assert self.lang_controller.target_lang.code == 'de'
        assert self.panel.view.shown

    def test_a_cancelled_save_prompt_keeps_the_language(self, monkeypatch):
        view = self._open_folder()
        self.store_controller.set_modified(True)
        monkeypatch.setattr(
            self.main_controller.view, 'show_save_confirm_dialog', lambda *a: 'cancel'
        )

        self.panel.language_chosen('de')

        assert self.store_controller.stream_language == 'af'
        assert view.language_combo.get_active() == 0

    def test_a_folder_in_one_language_has_no_language_list(self):
        (self.dir / 'app' / 'de.po').unlink()
        view = self._open_folder()

        assert not view.language_box.get_visible()

    def test_a_language_other_than_the_target_is_announced(self, monkeypatch):
        notices = []
        monkeypatch.setattr(self.main_controller.view, 'show_info_notice', notices.append)
        self.lang_controller._target_lang = LanguageModel('fr')

        self._open_folder()

        assert self.store_controller.stream_language == 'af'
        assert len(notices) == 1 and 'Afrikaans' in notices[0]

    def test_accessible_names(self):
        view = self._open_folder()

        assert view.treeview.get_accessible().get_name() == 'Files'
        assert view.language_combo.get_accessible().get_name() == 'Language'

    def test_a_folder_given_to_open_file_opens_as_a_folder(self):
        self.main_controller.open_file(str(self.dir))

        assert self.store_controller.is_stream()
        assert self.store_controller.opened_folder() == str(self.dir)

    def test_progress_follows_edits(self, monkeypatch):
        view = self._open_folder()
        monkeypatch.setattr(FilePanelController, 'PROGRESS_DELAY_MS', 0)
        sc = self.store_controller
        unit = sc.store[1]
        unit.target = 'Twee'
        unit.markfuzzy(False)

        sc.update_unit_stats(unit)
        context = GLib.MainContext.default()
        while self.panel._progress_source is not None:
            context.iteration(True)

        assert _rows(view)['app/af.po'][filepanelview.COL_PERCENT] == 100

    @pytest.mark.skipif(
        sys.platform == 'win32', reason='test classes share one process and its builder cache'
    )
    def test_the_panel_sits_beside_the_units(self):
        view = self._open_folder()
        units = self.main_controller.view.gui.get_object('scrwnd_storeview')

        # With the navigation ribbon beside them.
        assert units.get_parent() is view.paned.get_child2()
        assert self.main_controller.view.nav_ribbon.get_parent() is units.get_parent()
        assert isinstance(view.paned, Gtk.Paned)
