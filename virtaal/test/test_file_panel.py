#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The files panel beside several files opened as one list."""

import time

import pytest
from gi.repository import Gdk, GLib, Gtk, Pango
from test_scaffolding import TestScaffolding
from translate.filters.checks import StandardChecker

from virtaal.controllers.filepanelcontroller import FilePanelController
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.models.fileset import FileSet
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
            'Unknown language (1 file)',
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
        (self.dir / 'notes' / 'extra.po').unlink()
        view = self._open_folder()

        assert not view.language_box.get_visible()

    def test_files_of_unknown_language_have_their_own_entry(self):
        from virtaal.models.fileset import UNKNOWN_LANGUAGE

        (self.dir / 'app' / 'de.po').unlink()
        _po(self.dir / 'notes' / 'more.po', [('Seven', '')], language=None)
        view = self._open_folder()
        assert [row[1] for row in view.language_combo.get_model()] == [
            'Afrikaans (3 files)',
            'Unknown language (2 files)',
        ]
        assert view.language_box.get_visible()

        self.panel.language_chosen(UNKNOWN_LANGUAGE)

        assert self.store_controller.stream_language == UNKNOWN_LANGUAGE
        assert set(_rows(self.panel.view)) == {'more.po (?)', 'extra.po (?)'}
        assert view._languages[view.language_combo.get_active()] == UNKNOWN_LANGUAGE
        # They open in the current target language.
        assert self.store_controller.store.get_target_language() is None
        assert self.lang_controller.target_lang.code == 'af'

        self.panel.language_chosen('af')

        assert len(_rows(self.panel.view)) == 9

    def test_a_language_other_than_the_target_is_announced(self, monkeypatch):
        notices = []
        monkeypatch.setattr(self.main_controller.view, 'show_info_notice', notices.append)
        self.lang_controller._target_lang = LanguageModel('fr')

        self._open_folder()

        assert self.store_controller.stream_language == 'af'
        assert len(notices) == 1 and 'Afrikaans' in notices[0]

    def test_a_regional_folder_of_the_target_language_is_shown_chosen(self, monkeypatch):
        notices = []
        monkeypatch.setattr(self.main_controller.view, 'show_info_notice', notices.append)
        for name in ('af', 'de'):
            (self.dir / 'app' / ('%s.po' % name)).unlink()
        (self.dir / 'help' / 'af' / 'guide.po').unlink()
        (self.dir / 'help' / 'af' / 'faq.po').unlink()
        _po(self.dir / 'fr-CA' / 'a.po', [('One', '')], language=None)
        _po(self.dir / 'pl-PL' / 'a.po', [('One', '')], language=None)
        self.lang_controller._target_lang = LanguageModel('fr')

        view = self._open_folder()

        assert self.store_controller.stream_language == 'fr_CA'
        assert view._languages[view.language_combo.get_active()] == 'fr_CA'
        assert notices == []

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

    def test_the_panel_sits_beside_the_units(self):
        view = self._open_folder()
        units = self.main_controller.view.gui.get_object('scrwnd_storeview')

        # With the navigation ribbon beside them.
        assert units.get_parent() is view.paned.get_child2()
        assert self.main_controller.view.nav_ribbon.get_parent() is units.get_parent()
        assert isinstance(view.paned, Gtk.Paned)

    def test_a_file_with_no_known_template_is_greyed_and_pairs_when_activated(self, monkeypatch):
        en = self.dir / 'app' / 'base.properties'
        en.write_text('hello=Hello\nbye=Goodbye\n', encoding='utf-8')
        lone = self.dir / 'app' / 'lone.properties'
        lone.write_text('hello=Hallo\n', encoding='utf-8')
        view = self._open_folder()

        row = _rows(view)['app/lone.properties']
        assert row[filepanelview.COL_SENSITIVE] is False
        assert row[filepanelview.COL_SEGMENTS] == []
        paths = [segment.entry.path for segment in self.store_controller.store.segments]
        assert str(lone) not in paths
        asked = []
        monkeypatch.setattr(
            self.main_controller.view,
            'ask_template',
            lambda target, guess=None: asked.append(target) or str(en),
        )

        monkeypatch.setattr(self.panel, 'GO_TO_SECONDS', 0.2)
        it = view.model.get_iter(row.path)
        view.treeview.row_activated(view.model.get_path(it), view.treeview.get_column(0))

        assert asked == [str(lone)]
        store = self.store_controller.store
        segment = next(s for s in store.segments if s.entry.path == str(lone))
        assert segment.entry.template.template == str(en)
        until = time.monotonic() + 2
        while self.store_controller.cursor.index != segment.offset and time.monotonic() < until:
            Gtk.main_iteration_do(False)
        assert self.store_controller.cursor.index == segment.offset
        assert 'app/lone.properties' not in [
            name for name, r in _rows(view).items() if not r[filepanelview.COL_SENSITIVE]
        ]

    def _unpaired_folder(self, monkeypatch):
        en = self.dir / 'app' / 'base.properties'
        en.write_text('hello=Hello\n', encoding='utf-8')
        lone = self.dir / 'app' / 'lone.properties'
        lone.write_text('hello=Hallo\n', encoding='utf-8')
        view = self._open_folder()
        monkeypatch.setattr(
            self.main_controller.view, 'ask_template', lambda target, guess=None: str(en)
        )
        monkeypatch.setattr(self.panel, 'GO_TO_SECONDS', 0.2)
        return view, str(lone)

    def _edit_first_unit(self):
        sc = self.store_controller
        sc.cursor.force_index(1)
        self.unit_controller.load_unit(sc.store[1])
        self.unit_controller.set_unit_target(0, ['Twee'])
        self.unit_controller.finish_current_unit()

    def test_the_menu_of_an_unpaired_file_chooses_its_source_language_file(self, monkeypatch):
        view, lone = self._unpaired_folder(monkeypatch)

        item = view.pair_menu(lone).get_children()[0]
        assert item.get_label() == '_Choose Source-Language File…'
        item.activate()

        paths = [segment.entry.path for segment in self.store_controller.store.segments]
        assert lone in paths

    def test_pairing_adds_the_file_in_place_and_keeps_unsaved_edits(self, monkeypatch):
        view, lone = self._unpaired_folder(monkeypatch)
        self._edit_first_unit()
        sc = self.store_controller
        store = sc.store
        edited = store[1]
        checker = StandardChecker()
        store.update_checks(checker)
        asked = []
        monkeypatch.setattr(
            self.main_controller, '_show_save_confirm_dialog', lambda: asked.append(1) or 'save'
        )

        view.pair_menu(lone).get_children()[0].activate()

        # Nothing asked, saved or read again: the same store and units.
        assert asked == []
        assert sc.store is store and store[1] is edited
        assert edited.target == 'Twee' and sc.is_modified()
        with open(self.dir / 'app' / 'af.po', encoding='utf-8') as f:
            assert 'msgstr "Twee"' not in f.read()
        paths = [segment.entry.path for segment in store.segments]
        assert lone in paths
        segment = store.segments[paths.index(lone)]
        later = store.segments[paths.index(lone) + 1]
        assert later.offset == segment.offset + segment.count
        assert len(store) == sum(s.count for s in store.segments)
        assert len(store.stats['total']) == len(store)
        # The checks moved with the files after it, and take it in.
        moved = {key: sorted(indices) for key, indices in store.checks.items() if indices}
        store.update_checks(checker)
        assert moved == {key: sorted(i) for key, i in store.checks.items() if i}
        rows = _rows(view)
        # Its path names no language: it takes the one shown, for good.
        assert rows['app/lone.properties'][filepanelview.COL_SENSITIVE] is True
        assert segment.entry.language == 'af'
        again = FileSet.from_paths([str(self.dir)])
        assert next(e for e in again if e.path == lone).language == 'af'
        template = rows['app/lone.properties/base.properties']
        assert template[filepanelview.COL_SENSITIVE] is False
        # The template is no longer a file waiting to be paired.
        assert 'app/base.properties' not in rows
        until = time.monotonic() + 2
        while sc.cursor.index != segment.offset and time.monotonic() < until:
            Gtk.main_iteration_do(False)
        assert sc.cursor.index == segment.offset

    def test_a_folder_of_only_unpaired_files_opens_empty_and_pairs(self, monkeypatch, tmp_path):
        folder = tmp_path / 'only'
        folder.mkdir()
        lone = folder / 'lone.properties'
        lone.write_text('hello=Hallo\n', encoding='utf-8')
        en = tmp_path / 'base.properties'
        en.write_text('hello=Hello\nbye=Goodbye\n', encoding='utf-8')
        sc = self.store_controller
        errors = []
        monkeypatch.setattr(self.main_controller, 'show_error', errors.append)

        assert self.main_controller.open_files([str(folder)]), errors

        assert len(sc.store) == 0 and sc.is_stream()
        view = self.panel.view
        assert view.shown
        row = _rows(view)['lone.properties']
        assert row[filepanelview.COL_SENSITIVE] is False and row[filepanelview.COL_PATH]
        # Nothing to move to, check, count or save, but the folder's name.
        sc.cursor.move(1)
        sc.cursor.move(-1)
        assert sc.store.get_stats_totals() == {}
        sc.store.update_checks(StandardChecker())
        modes = self.main_controller.mode_controller
        for name in list(modes.modes):
            modes.select_mode_by_name(name)
        modes.select_mode_by_name('Search')
        modes.modes['Search'].ent_search.set_text('Hallo')
        modes.select_mode_by_name(modes.default_mode_name)
        modes.refresh_mode()
        assert not sc.is_modified()
        self.main_controller.save_file()
        self.main_controller.view.refresh_title()
        assert 'only' in self.main_controller.view.main_window.get_title()
        monkeypatch.setattr(
            self.main_controller.view, 'ask_template', lambda target, guess=None: str(en)
        )
        monkeypatch.setattr(self.panel, 'GO_TO_SECONDS', 0.2)

        view.pair_menu(str(lone)).get_children()[0].activate()

        # Still a folder, with the files panel, though it holds one file.
        assert sc.is_stream() and self.panel.view.shown
        assert [segment.entry.path for segment in sc.store.segments] == [str(lone)]
        assert len(sc.store) == 2 and sc.cursor.index == 0

    def test_a_folder_with_nothing_to_translate_still_says_so(self, tmp_path, monkeypatch):
        folder = tmp_path / 'nothing'
        folder.mkdir()
        (folder / 'README').write_text('hi', encoding='utf-8')
        errors = []
        monkeypatch.setattr(self.main_controller, 'show_error', errors.append)

        assert not self.main_controller.open_files([str(folder)])
        assert 'nothing to translate' in errors[0]

    def test_a_paired_file_whose_path_names_a_language_keeps_it(self):
        lone = self.dir / 'help' / 'de' / 'extra.properties'
        lone.parent.mkdir()
        lone.write_text('hello=Hallo\n', encoding='utf-8')
        self._open_folder()

        assert self.panel._assigned_language(str(lone)) is None

    def test_a_folder_in_one_language_assigns_none(self, monkeypatch, tmp_path):
        folder = tmp_path / 'one'
        _po(folder / 'af.po', [('One', 'Een')])
        lone = folder / 'lone.properties'
        lone.write_text('hello=Hallo\n', encoding='utf-8')
        assert self.main_controller.open_files([str(folder)])

        assert self.panel._assigned_language(str(lone)) is None
