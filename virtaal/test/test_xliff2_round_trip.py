#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""An XLIFF 2.2 file with plural and gender variants opened, shown, edited
through a target TextBox, saved and reopened."""

import os
import shutil

import pytest
from gi.repository import GLib
from test_scaffolding import TestScaffolding
from test_textbox_editing import _Editor
from translate.storage import factory

from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.views.widgets import textbox as textbox_module

PGS = os.path.join('devsupport', 'testfiles', 'xliff2', 'pgs.xlf')


class TestXliff2RoundTrip(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _main_loop(self, monkeypatch):
        self.idle_calls = []
        monkeypatch.setattr(
            GLib,
            'idle_add',
            lambda func, *args, **kwargs: self.idle_calls.append(lambda: func(*args)),
        )
        monkeypatch.setattr(pan_app, 'ui_language', 'en')
        undo = self.undo_controller
        monkeypatch.setattr(undo, '_select_unit', lambda unit: undo._flush_pending_refresh())
        monkeypatch.setattr(undo, '_correct_state_after_undo_redo', lambda unit=None: None)
        self.warnings = []
        monkeypatch.setattr(
            textbox_module.logging, 'warning', lambda msg, *args: self.warnings.append(msg % args)
        )
        self.store_controller.close_file()
        yield
        self.store_controller.close_file()

    def _open_copy(self, tmp_path):
        path = tmp_path / 'pgs.xlf'
        shutil.copy(PGS, path)
        self.store_controller.open_file(str(path))
        return path, path.read_bytes()

    def _load(self, index):
        unit = self.store_controller.store.get_units()[index]
        for textbox in self.unit_controller.view.targets:
            textbox._suggestion = None
        view = self.unit_controller.load_unit(unit)
        view._layout_update_targets()
        return unit, view

    @staticmethod
    def _labels(view):
        return [label.get_text() for label in view._form_label_widgets if label.get_visible()]

    def test_saves_unchanged_after_showing_every_unit(self, tmp_path):
        path, before = self._open_copy(tmp_path)

        for index in range(len(self.store_controller.store.get_units())):
            unit, _view = self._load(index)
            unit._modified = True
        self.store_controller.save_file()

        assert path.read_bytes() == before

    def test_a_case_is_a_labelled_box_and_an_edit_changes_only_its_segment(self, tmp_path):
        path, before = self._open_copy(tmp_path)
        _unit, view = self._load(1)

        assert self._labels(view) == [
            'one: 1, 21, 31',
            'few: 2, 3, 4',
            'many: 0, 5, 6',
            'other: 0.5, 1.5, 2.5',
        ]
        assert [s.get_parent().get_visible() for s in view.sources[:5]] == [True] * 4 + [False]
        editor = _Editor(self, view.targets[3])
        editor.type('Neu')
        self.store_controller.save_file()

        after = path.read_bytes()
        changed = [line for line in after.splitlines() if line not in before.splitlines()]
        assert changed == [
            b'      <segment id="deleted_other" pgs:case="other" xml:space="preserve">',
            b'        <target>Neu</target>',
        ]
        reopened = factory.getobject(str(path)).units[1]
        assert [str(form) for form in reopened.target.strings][3] == 'Neu'

    def test_gender_cases_and_a_unit_per_case_beyond_six(self, tmp_path):
        self._open_copy(tmp_path)

        _unit, view = self._load(3)
        assert self._labels(view) == ['feminine', 'masculine', 'other']
        _unit, view = self._load(5)
        assert self._labels(view) == []
        assert view._widgets['context_info'].get_text() == 'gender:host plural:guests = feminine 0'
