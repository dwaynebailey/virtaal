#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Check All Languages… and Find in All Languages… over a folder."""

import time

import pytest
from gi.repository import GLib
from test_scaffolding import TestScaffolding

from virtaal.controllers.filepanelcontroller import FilePanelController
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.controllers.setscancontroller import stream_index
from virtaal.models.langmodel import LanguageModel
from virtaal.views import setscanview


def _po(path, units, language):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ''.join('msgid "%s"\nmsgstr "%s"\n\n' % unit for unit in units)
    path.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Language: %s\\n"\n\n%s' % (language, body),
        encoding='utf-8',
    )
    return str(path)


def _wait(condition, seconds=30):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    assert condition()


def _rows(view):
    return [tuple(row) for row in view.liststore]


class TestSetScan(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)
        self.panel = FilePanelController(self.main_controller)
        self.scans = self.panel.scan_controller

    @pytest.fixture(autouse=True)
    def _files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        self.dir = tmp_path
        units = [('Open %s', 'Maak %s oop'), ('Close', 'Sluit'), ('Save %s', 'Stoor %s')]
        _po(tmp_path / 'af' / 'app.po', units, 'af')
        _po(tmp_path / 'af' / 'help.po', [('Help', 'Hulp')], 'af')
        de = [('Close', 'Schließen'), ('Open %s', 'Öffnen %d'), ('Save %s', 'Speichern %s')]
        _po(tmp_path / 'de' / 'app.po', de, 'de')
        self.de_help = _po(tmp_path / 'de' / 'help.po', [('Help', 'Hilfe')], 'de')
        self.lang_controller._target_lang = LanguageModel('af')
        self.store_controller.close_file()
        # The checks chosen are remembered from one window to the next.
        self.scans._chosen_checks = None
        yield
        self.scans.stop()
        self.store_controller.set_modified(False)
        self.store_controller.close_file()

    def _open_folder(self):
        self.main_controller.open_files([str(self.dir)])

    def _check_all(self):
        self.scans.show('check')
        view = self.scans.views['check']
        _wait(lambda: self.scans.scan is None)
        return view

    def test_the_menu_items_need_several_languages(self):
        items = self.scans.menu_items.values()
        assert not any(item.get_sensitive() for item in items)

        self._open_folder()
        assert all(item.get_sensitive() for item in items)

        (self.dir / 'de' / 'app.po').unlink()
        (self.dir / 'de' / 'help.po').unlink()
        self._open_folder()
        assert not any(item.get_sensitive() for item in items)
        self.scans.show('check')
        assert self.scans.views == {}

    def test_check_all_languages_lists_failures_in_every_language(self):
        self._open_folder()

        view = self._check_all()

        rows = _rows(view)
        assert [
            (r[setscanview.COL_CHECK], r[setscanview.COL_LANGUAGE], r[setscanview.COL_FILE])
            + (r[setscanview.COL_UNIT], r[setscanview.COL_TARGET])
            for r in rows
        ] == [('printf()', 'German', 'de/app.po', 2, 'Öffnen %d')]
        assert view.lbl_summary.get_text() == '1 problem'
        assert not view.progress.get_visible()
        # The af files are open: the scan read the de files from disk.
        assert self.store_controller.stream_language == 'af'

    def test_the_checks_chosen_are_the_ones_run(self):
        self._open_folder()
        view = self._check_all()
        assert 'pythonbraceformat' in view.chosen_checks()
        view.check_buttons['printf'].set_active(False)

        view.btn_start.clicked()
        _wait(lambda: self.scans.scan is None)

        assert [r[setscanview.COL_CHECK] for r in _rows(view)] == []
        view.check_buttons['printf'].set_active(True)
        view.btn_start.clicked()
        _wait(lambda: self.scans.scan is None)
        assert [r[setscanview.COL_CHECK] for r in _rows(view)] == ['printf()']

    def test_the_checks_chosen_are_kept_for_the_next_window(self):
        self._open_folder()
        view = self._check_all()
        view.check_buttons['printf'].set_active(False)
        view.btn_start.clicked()
        _wait(lambda: self.scans.scan is None)
        self.store_controller.close_file()

        self._open_folder()
        view = self._check_all()

        assert 'printf' not in view.chosen_checks()
        assert 'xmltags' in view.chosen_checks()

    def test_nothing_chosen_can_t_be_started(self):
        self._open_folder()
        view = self._check_all()

        for button in view.check_buttons.values():
            button.set_active(False)

        assert not view.btn_start.get_sensitive()

    def test_activating_a_result_opens_its_language_at_the_unit(self):
        self._open_folder()
        view = self._check_all()

        row = view.liststore[0]
        view.treeview.row_activated(row.path, view.treeview.get_column(0))

        sc = self.store_controller
        assert sc.stream_language == 'de'
        assert self.lang_controller.target_lang.code == 'de'
        # Once the units view has settled on the first unit.
        _wait(lambda: self.scans.jump is None)
        assert sc.cursor.index == 1
        unit = sc.cursor.deref()
        assert (unit.source, unit.target) == ('Open %s', 'Öffnen %d')
        # The window stays, for the next result.
        assert self.scans.views['check'] is view

    def test_a_result_in_the_open_language_moves_the_cursor_only(self):
        self._open_folder()
        self.panel.language_chosen('de')
        self.scans.show('search')
        view = self.scans.views['search']
        view.ent_search.set_text('Hilfe')
        view.btn_start.clicked()
        _wait(lambda: self.scans.scan is None)

        row = view.liststore[0]
        self.scans.open_result(
            row[setscanview.COL_PATH],
            row[setscanview.COL_CODE],
            row[setscanview.COL_INDEX],
            row[setscanview.COL_POSITION],
        )

        assert self.store_controller.stream_language == 'de'
        assert self.store_controller.cursor.deref().target == 'Hilfe'

    def test_find_in_all_languages_searches_source_and_translation(self):
        self._open_folder()
        self.scans.show('search')
        view = self.scans.views['search']
        assert not view.btn_start.get_sensitive()

        view.ent_search.set_text('open')
        view.btn_start.clicked()
        _wait(lambda: self.scans.scan is None)

        assert sorted(
            (r[setscanview.COL_LANGUAGE], r[setscanview.COL_TARGET]) for r in _rows(view)
        ) == [('Afrikaans', 'Maak %s oop'), ('German', 'Öffnen %d')]
        assert view.lbl_summary.get_text() == '2 units found'

    def test_a_second_result_chosen_before_the_first_is_reached_wins(self):
        self._open_folder()
        view = self._check_all()
        sc = self.store_controller
        self.scans.open_result(*[view.liststore[0][c] for c in (7, 8, 9, 10)])
        first = self.scans.jump

        # Still German: no language to open this time.
        self.scans.open_result(view.liststore[0][7], 'de', 1, 0)
        assert self.scans.jump is None
        assert sc.cursor.index == 0
        assert first is not None
        _wait(lambda: not sc.view._treeview._waiting_for_row_change)
        for _ in range(20):
            GLib.MainContext.default().iteration(False)

        assert sc.cursor.index == 0

    def test_a_cancelled_save_prompt_stays_in_the_language(self, monkeypatch):
        self._open_folder()
        view = self._check_all()
        self.store_controller.set_modified(True)
        monkeypatch.setattr(
            self.main_controller.view, 'show_save_confirm_dialog', lambda *a: 'cancel'
        )

        self.scans.open_result(*[view.liststore[0][c] for c in (7, 8, 9, 10)])

        assert self.store_controller.stream_language == 'af'

    def test_stop_ends_the_scan_and_says_how_far_it_got(self, monkeypatch):
        from virtaal.support import setscan

        monkeypatch.setattr(setscan, 'POLL_MS', 10000)
        self._open_folder()
        self.scans.show('check')
        view = self.scans.views['check']
        assert self.scans.scan is not None
        assert view.btn_stop.get_sensitive()

        view.dialog.response(setscanview.Gtk.ResponseType.CANCEL)

        assert self.scans.scan is None
        assert not view.btn_stop.get_sensitive()
        assert view.progress.get_visible()

    def test_closing_the_files_closes_the_windows(self):
        self._open_folder()
        self._check_all()

        self.store_controller.close_file()

        assert self.scans.views == {}

    def test_too_many_results_are_cut_short(self, monkeypatch):
        from virtaal.support import setscan

        monkeypatch.setattr(setscan, 'MAX_RESULTS', 1)
        self._open_folder()
        self.scans.show('search')
        view = self.scans.views['search']
        view.ent_search.set_text('e')
        view.btn_start.clicked()
        _wait(lambda: self.scans.scan is None)

        assert len(_rows(view)) == 1
        assert 'Only the first 1 are listed.' in view.lbl_summary.get_text()

    def test_the_files_panel_has_both_in_its_language_row(self):
        self._open_folder()
        menu = self.panel.view.all_languages_button.get_popup()
        labels = [item.get_label() for item in menu.get_children()]
        assert labels == ['Find in All _Languages…', 'Check All Lan_guages…']

        menu.get_children()[0].activate()

        assert 'search' in self.scans.views


class _Segment:
    def __init__(self, path, offset, valid):
        self.path = path
        self.offset = offset
        self.valid = valid
        self.count = len(valid)


def test_stream_index_finds_the_unit_by_its_index_in_the_file(tmp_path):
    a, b = str(tmp_path / 'a.po'), str(tmp_path / 'b.po')
    segments = [_Segment(a, 0, [1, 2, 3]), _Segment(b, 3, [1, 3, 4])]

    assert stream_index(segments, b, 3, 1) == 4
    # Not one the stream has: by position, within the file.
    assert stream_index(segments, b, 9, 7) == 5
    assert stream_index(segments, str(tmp_path / 'c.po'), 1, 0) is None
