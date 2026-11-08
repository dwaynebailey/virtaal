#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""memoQ and Phrase files from devsupport/testfiles opened, edited
through the target TextBox, saved and reopened: the file changes only
in the edited trans-unit, and the tools' states, locks and notes show."""

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
from virtaal.support import catxliff_compat, formatplaceables
from virtaal.views.widgets import textbox as textbox_module

TESTFILES = os.path.join('devsupport', 'testfiles')
SAMPLES = sorted(
    glob.glob(os.path.join(TESTFILES, 'memoq', '*', '*.mqxliff'))
    + glob.glob(os.path.join(TESTFILES, 'phrase', '*', '*.mxliff'))
)
STATUSES = os.path.join(
    TESTFILES, 'memoq', 'mqxliff-viewer', 'translation_status_sample.xlsx_jpn.mqxliff'
)
MEMOQ_LOCK = os.path.join(TESTFILES, 'memoq', 'patois', 'sample.mqxliff')
TRACKED = os.path.join(TESTFILES, 'memoq', 'disfr', 'ChangeTracking_memoQ_1.mqxliff')
PHRASE = os.path.join(TESTFILES, 'phrase', 'patois', 'sample.mxliff')
PHRASE_TAGS = os.path.join(TESTFILES, 'phrase', 'xlfql', 'sample.mxliff')


def _changed_lines(before, after):
    old, new = before.splitlines(), after.splitlines()
    assert len(old) == len(new)
    return [(a, b) for a, b in zip(old, new, strict=True) if a != b]


class TestCatXliffRoundTrip(TestScaffolding):
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

    def _open_copy(self, original, tmp_path, name=None):
        path = tmp_path / (name or os.path.basename(original))
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

    def _type(self, view, text):
        editor = _Editor(self, view.targets[0])
        editor.buffer.select_range(editor.buffer.get_end_iter(), editor.buffer.get_start_iter())
        editor.type(text)
        return editor

    def _notes(self, view):
        return view._widgets['notes']['translator'].get_text()

    @pytest.mark.parametrize('original', SAMPLES, ids=lambda p: p.split(os.sep)[-2:][0])
    def test_a_sample_saves_unchanged_after_showing_every_unit(self, original, tmp_path):
        path, before = self._open_copy(original, tmp_path)

        for index in range(len(self.store_controller.store.get_units())):
            unit, _view = self._load(index)
            unit._modified = True
        self.store_controller.save_file()

        assert path.read_bytes() == before

    @pytest.mark.parametrize('original', SAMPLES, ids=lambda p: p.split(os.sep)[-2:][0])
    def test_an_edit_changes_only_its_trans_unit(self, original, tmp_path):
        path, before = self._open_copy(original, tmp_path)
        units = self.store_controller.store.get_units()
        index = next(i for i, u in enumerate(units) if not catxliff_compat.is_locked(u))
        _unit, view = self._load(index)

        self._type(view, 'Neu')
        self.store_controller.save_file()

        after = path.read_bytes()
        assert after[:3] == before[:3]
        changed = _changed_lines(before, after)
        assert 1 <= len(changed) <= 2
        assert any(b'Neu' in new for _old, new in changed)
        reopened = factory.getobject(str(path)).units
        assert [u.target for u in reopened].count('Neu') == 1

    def test_the_memoq_states_and_a_state_change(self, tmp_path):
        path, before = self._open_copy(STATUSES, tmp_path)
        extended = self.store_controller.store.stats['extended']
        # Rows 7-12 are translate="no", so not shown.
        assert {state: len(indices) for state, indices in extended.items()} == {
            StateEnum.EMPTY: 1,
            StateEnum.NEEDS_REVIEW: 2,
            StateEnum.UNREVIEWED: 1,
            StateEnum.FINAL: 2,
        }
        self._load(1)

        self.unit_controller.set_current_state(StateEnum.FINAL, from_user=True)
        self.store_controller.save_file()

        changed = _changed_lines(before, path.read_bytes())
        assert len(changed) == 1
        old, new = changed[0]
        assert old.replace(b'"PartiallyEdited"', b'"Reviewer1Confirmed"') == new

    def test_typing_into_a_pre_translated_row_leaves_it_to_review(self, tmp_path):
        path, _before = self._open_copy(STATUSES, tmp_path)
        _unit, view = self._load(2)

        self._type(view, 'Neu')
        self.store_controller.save_file()

        reopened = factory.getobject(str(path)).units[2]
        assert (reopened.target, reopened.get_state_n()) == ('Neu', StateEnum.NEEDS_REVIEW)
        assert reopened.xmlelement.get(catxliff_compat.memoq_compat.MQ + 'status') == (
            'PartiallyEdited'
        )

    def test_a_locked_memoq_row_is_read_only(self, tmp_path):
        path, before = self._open_copy(MEMOQ_LOCK, tmp_path)
        unit, view = self._load(2)
        target = view.targets[0]

        assert not target.get_editable()
        assert not view._widgets['state'].get_sensitive()
        assert "Locked: this translation can't be changed." in self._notes(view)
        view.set_target_n(0, 'Neu')
        unit.target = 'Neu'
        unit.set_state_n(StateEnum.FINAL)
        unit._modified = True
        self.store_controller.save_file()

        assert path.read_bytes() == before

    def test_memoq_match_rate_comments_and_last_user_are_notes(self, tmp_path):
        self._open_copy(STATUSES, tmp_path)

        _unit, view = self._load(2)

        # Its comment is its note too, shown above the source.
        assert self._notes(view) == 'Match: 101%\nLast changed by Ikue'

    def test_an_edit_in_a_tracked_row_is_tracked_and_undone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(catxliff_compat, 'tracking_user', lambda: 'Me')
        monkeypatch.setattr(catxliff_compat, 'tracking_time', lambda: '2026-11-09T10:00:00Z')
        path, before = self._open_copy(TRACKED, tmp_path)
        unit, view = self._load(1)
        assert view.targets[0].get_text() == '#1.'
        assert 'your edits are tracked' in self._notes(view)
        self.undo_controller.model.clear()

        editor = _Editor(self, view.targets[0])
        editor.buffer.place_cursor(editor.buffer.get_end_iter())
        editor.type('!')
        self.store_controller.save_file()

        ((old, new),) = _changed_lines(before, path.read_bytes())
        assert new == old.replace(
            b'#1.</target>',
            b'#1.<mrk mtype="x-mq-tc" mq:tctype="ins" mq:tcuser="Me"'
            b' mq:tctime="2026-11-09T10:00:00Z">!</mrk></target>',
        )
        editor.undo()
        unit._modified = True
        self.store_controller.save_file()
        assert path.read_bytes() == before

    def test_a_memoq_file_saved_as_xlf_opens_with_its_states(self, tmp_path):
        self._open_copy(STATUSES, tmp_path, 'statuses.xlf')

        unit, view = self._load(3)

        assert unit.get_state_n() == StateEnum.UNREVIEWED
        assert view._widgets['state'].get_sensitive()

    def test_the_phrase_states_lock_and_origin(self, tmp_path):
        path, before = self._open_copy(PHRASE, tmp_path)
        assert [u.get_state_n() for u in self.store_controller.store.get_units()] == [
            StateEnum.EMPTY,
            StateEnum.NEEDS_REVIEW,
            StateEnum.UNREVIEWED,
            StateEnum.NEEDS_REVIEW,
        ]
        _unit, view = self._load(1)
        assert not view.targets[0].get_editable()
        assert "Locked: this translation can't be changed." in self._notes(view)
        _unit, view = self._load(3)
        assert self._notes(view) == 'Origin: mt, 62% match'

        self.unit_controller.set_current_state(StateEnum.UNREVIEWED, from_user=True)
        self.store_controller.save_file()

        ((old, new),) = _changed_lines(before, path.read_bytes())
        assert old.replace(b'm:confirmed="0"', b'm:confirmed="1"') == new

    def test_phrase_tags_are_placeables_and_checked(self, tmp_path):
        self._open_copy(PHRASE_TAGS, tmp_path)
        unit, view = self._load(0)

        assert formatplaceables.format_of(self.store_controller.get_store(), unit) == 'mxliff'
        parsers = self.main_controller.placeables_controller.get_parsers_for_textbox(
            view.targets[0]
        )
        assert formatplaceables.PhraseTagPlaceable.parse in parsers
        self._type(view, 'That is a test.')
        failures = self.main_controller.checks_controller.check_unit(unit)
        assert failures['phrasetags'] == 'Missing Phrase tags: {1} {2}'
