#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Okapi's Wordfast .txml samples opened, shown, edited through a target
TextBox, saved and reopened."""

import glob
import os
import shutil

import pytest
from gi.repository import GLib
from test_scaffolding import TestScaffolding
from test_textbox_editing import _Editor
from translate.storage import factory
from translate.storage.workflow import StateEnum

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.views.widgets import textbox as textbox_module

OKAPI = os.path.join('devsupport', 'testfiles', 'wordfast', 'okapi')
SAMPLES = sorted(glob.glob(os.path.join(OKAPI, '*.txml')))


def _changed_lines(before, after):
    old, new = before.splitlines(), after.splitlines()
    assert len(old) == len(new)
    return [(a, b) for a, b in zip(old, new, strict=True) if a != b]


class TestTxmlRoundTrip(TestScaffolding):
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

    def _open_copy(self, original, tmp_path):
        path = tmp_path / os.path.basename(original)
        shutil.copy(original, path)
        self.store_controller.open_file(str(path))
        return path, path.read_bytes()

    def _load(self, index):
        unit = self.store_controller.store.get_units()[index]
        for textbox in self.unit_controller.view.targets:
            textbox._suggestion = None
        view = self.unit_controller.load_unit(unit)
        view._layout_update_targets()
        return unit, view

    @pytest.mark.parametrize('original', SAMPLES, ids=os.path.basename)
    def test_a_sample_saves_unchanged_after_showing_every_unit(self, original, tmp_path):
        path, before = self._open_copy(original, tmp_path)

        for index in range(len(self.store_controller.store.get_units())):
            unit, _view = self._load(index)
            unit._modified = True
        self.store_controller.save_file()

        assert path.read_bytes() == before

    def test_typing_a_translation_changes_only_its_segment(self, tmp_path):
        path, before = self._open_copy(os.path.join(OKAPI, 'Test02.html.txml'), tmp_path)
        _unit, view = self._load(2)

        _Editor(self, view.targets[0]).type('Neu')
        self.store_controller.save_file()

        changed = _changed_lines(before, path.read_bytes())
        assert changed == [
            (
                b'<translatable blockId="3" datatype="html"><segment segmentId="1" gtmt="false">'
                b'<source>First sentence.</source><ws> </ws></segment>'
                b'<segment segmentId="2" gtmt="false"><source>Second sentence.</source></segment>'
                b'</translatable>',
                b'<translatable blockId="3" datatype="html"><segment segmentId="1" modified="true"'
                b' gtmt="false"><source>First sentence.</source><ws> </ws><target>Neu</target>'
                b'</segment><segment segmentId="2" gtmt="false"><source>Second sentence.</source>'
                b'</segment></translatable>',
            )
        ]
        reopened = factory.getobject(str(path)).units[2]
        assert (reopened.target, reopened.get_state_n()) == ('Neu', StateEnum.UNREVIEWED)

    def test_a_machine_translation_needs_review_and_says_so(self, tmp_path):
        self._open_copy(os.path.join(OKAPI, 'Test02.html.txml'), tmp_path)

        unit, view = self._load(1)

        assert unit.get_state_n() == StateEnum.NEEDS_REVIEW
        assert view._widgets['notes']['translator'].get_text() == 'Origin: mt'
        assert view.targets[0].text_filter is not None
