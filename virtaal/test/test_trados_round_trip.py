#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Trados files from devsupport/testfiles/trados opened, edited through
the target TextBox, saved and reopened: the edit lands where Trados reads
it, and every other element, attribute and text survives."""

import glob
import os
import shutil
import zipfile

import pytest
from gi.repository import GLib
from lxml import etree
from test_scaffolding import TestScaffolding
from test_textbox_editing import _Editor
from translate.storage import factory
from translate.storage.workflow import StateEnum

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.support import sdlxliff_compat, xliff_compat
from virtaal.views.widgets import textbox as textbox_module

TRADOS = os.path.join('devsupport', 'testfiles', 'trados')
XLIFF = '{urn:oasis:names:tc:xliff:document:1.2}'
SDL = '{http://sdl.com/FileTypes/SdlXliff/1.0}'
# Text inside these is content; whitespace elsewhere is indentation,
# which translate-toolkit rewrites on save.
CONTENT = {
    XLIFF + name for name in ('source', 'seg-source', 'target', 'note', 'mrk', 'g', 'seg')
} | {'seg', 'note', 'prop'}

SDLXLIFF_FILES = sorted(glob.glob(os.path.join(TRADOS, '*', '*.sdlxliff')))
PACKAGES = sorted(glob.glob(os.path.join(TRADOS, '*', '*.sdl?px')))
WORKBENCH_TMX = sorted(glob.glob(os.path.join(TRADOS, '*', '*English - German.tmx')))
# Every Trados status, twice, by sdl:seg id.
STATUSES = glob.glob(os.path.join(TRADOS, 'matecat', '*.sdlxliff'))[0]
# Segments 6 and 7 are locked.
COMMENTS = os.path.join(TRADOS, 'okapi', 'comments.sdlxliff')


def _in_content(element):
    return any(e.tag in CONTENT for e in element.iterancestors()) or element.tag in CONTENT


def _infoset(element, collapse=False):
    """Every element under C{element} as (tag, attributes, text, tail),
    whitespace-only text kept only inside content. C{collapse}: compare
    whitespace as xml:space="default" does."""

    def text(value, keep):
        if value is None or (not keep and not value.strip()):
            return None
        if collapse:
            return " ".join(value.split()) or None
        return value

    return [
        (
            e.tag,
            dict(e.attrib),
            text(e.text, _in_content(e) and e.tag in CONTENT),
            text(e.tail, e.getparent() is not None and _in_content(e.getparent())),
        )
        for e in element.iter()
        if e.tag is not etree.Comment
    ]


def _units(root):
    return [e for e in root.iter(XLIFF + 'trans-unit', 'tu')]


def _without(root, edited_index):
    """C{root}'s infoset outside unit C{edited_index}."""
    root = etree.fromstring(etree.tostring(root))
    edited = _units(root)[edited_index]
    edited.getparent().replace(edited, etree.Element('edited'))
    return _infoset(root)


def _segs(root):
    """Each <sdl:seg>'s attributes by its id."""
    return {seg.get('id'): dict(seg.attrib) for seg in root.iter(SDL + 'seg')}


def _package_files(tmp_path):
    files = []
    for package in PACKAGES:
        folder = tmp_path / os.path.basename(package)
        with zipfile.ZipFile(package) as archive:
            archive.extractall(folder)
        files += sorted(glob.glob(str(folder / '**' / '*.sdlxliff'), recursive=True))
    return files


class TestTradosRoundTrip(TestScaffolding):
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

    def _retype(self, path, pick):
        """Open C{path}, replace the target of the first unit C{pick}
        accepts with "Neu" through the editor, save. Returns the edited
        unit's index in the file, or None."""
        self.store_controller.open_file(str(path))
        units = self.store_controller.store.get_units()
        unit = next((u for u in units if pick(u)), None)
        if unit is None:
            self.store_controller.save_file()
            return None
        for textbox in self.unit_controller.view.targets:
            textbox._suggestion = None
        view = self.unit_controller.load_unit(unit)
        view._layout_update_targets()
        editor = _Editor(self, view.targets[0])
        editor.buffer.select_range(editor.buffer.get_end_iter(), editor.buffer.get_start_iter())
        editor.type('Neu')
        unit._modified = True
        self.store_controller.save_file()
        return _units(unit.xmlelement.getroottree().getroot()).index(unit.xmlelement)

    def _round_trip_sdlxliff(self, original, tmp_path):
        tmp_path.mkdir(exist_ok=True)
        path = tmp_path / os.path.basename(original)
        shutil.copy(original, path)
        before = etree.parse(str(path)).getroot()

        index = self._retype(path, lambda u: xliff_compat.segment_marker(u) is not None)

        after = etree.parse(str(path)).getroot()
        assert len(after.findall('.//' + SDL + '*')) == len(before.findall('.//' + SDL + '*'))
        if index is None:
            assert _infoset(after) == _infoset(before)
            return
        assert _without(after, index) == _without(before, index)
        edited = _units(after)[index]
        target = edited.find(XLIFF + 'target')
        markers = [m for m in target.iter(XLIFF + 'mrk') if m.get('mtype') == 'seg']
        assert len(markers) == 1 and markers[0].text == 'Neu' and len(markers[0]) == 0
        for name in ('source', 'seg-source', SDL + 'seg-defs'):
            tag = name if name.startswith('{') else XLIFF + name
            old = _units(before)[index].find(tag)
            assert (old is None) == (edited.find(tag) is None)
            if old is not None:
                # The edit makes the unit xml:space="preserve"; reading
                # it had already collapsed its whitespace.
                assert _infoset(edited.find(tag), collapse=True) == _infoset(old, collapse=True)
        reopened = factory.getobject(str(path)).units
        assert [str(u.rich_target[0]) for u in reopened].count('Neu') == 1

    @pytest.mark.parametrize('original', SDLXLIFF_FILES, ids=os.path.basename)
    def test_an_sdlxliff_sample(self, original, tmp_path):
        self._round_trip_sdlxliff(original, tmp_path)

    def test_the_sdlxliff_files_in_the_okapi_packages(self, tmp_path):
        files = _package_files(tmp_path / 'packages')

        assert len(files) == 8
        for number, original in enumerate(files):
            self._round_trip_sdlxliff(original, tmp_path / str(number))

    @pytest.mark.parametrize('original', WORKBENCH_TMX, ids=os.path.basename)
    def test_a_workbench_tmx_sample(self, original, tmp_path):
        # srclang EN-GB, tuvs EN-US and DE-DE (#4626).
        path = tmp_path / os.path.basename(original)
        shutil.copy(original, path)
        before = etree.parse(str(path)).getroot()
        count = len(before.findall('.//tu'))

        self.store_controller.open_file(str(path))
        assert len(self.store_controller.store.get_units()) == count
        self.store_controller.close_file()
        index = self._retype(path, lambda u: True)

        after = etree.parse(str(path)).getroot()
        assert _without(after, index) == _without(before, index)
        old_tuvs, new_tuvs = (_units(root)[index].findall('tuv') for root in (before, after))
        assert _infoset(new_tuvs[0]) == _infoset(old_tuvs[0])
        assert new_tuvs[1].attrib == old_tuvs[1].attrib
        assert etree.tostring(new_tuvs[1].find('seg'), with_tail=False) == b'<seg>Neu</seg>'
        reopened = factory.getobject(str(path)).units[index]
        assert (reopened.source, reopened.target) == (
            factory.getobject(original).units[index].source,
            'Neu',
        )

    def _open_copy(self, original, tmp_path):
        path = tmp_path / os.path.basename(original)
        shutil.copy(original, path)
        self.store_controller.open_file(str(path))
        return path, etree.parse(str(path)).getroot()

    def _load(self, *seg_ids):
        unit = next(
            u
            for u in self.store_controller.store.get_units()
            if [s.get('id') for s in sdlxliff_compat.segment_defs(u)] == list(seg_ids)
        )
        for textbox in self.unit_controller.view.targets:
            textbox._suggestion = None
        view = self.unit_controller.load_unit(unit)
        view._layout_update_targets()
        return unit, view

    def test_a_state_change_writes_only_that_segments_status(self, tmp_path):
        path, before = self._open_copy(STATUSES, tmp_path)
        self._load('2')

        self.unit_controller.set_current_state(StateEnum.FINAL, from_user=True)
        self.store_controller.save_file()

        after = etree.parse(str(path)).getroot()
        old, new = _segs(before), _segs(after)
        assert new.pop('2') == {**old.pop('2'), 'conf': 'ApprovedTranslation'}
        assert new == old
        reopened = factory.getobject(str(path)).units
        assert [u.get_state_n() for u in reopened if u.istranslatable()][:7] == [
            StateEnum.NEEDS_WORK,
            StateEnum.FINAL,
            StateEnum.UNREVIEWED,
            StateEnum.REJECTED,
            StateEnum.FINAL,
            StateEnum.REJECTED + 1,
            StateEnum.FINAL + 1,
        ]

    def test_editing_keeps_a_status_between_steps(self, tmp_path):
        path, before = self._open_copy(STATUSES, tmp_path)
        _unit, view = self._load('6')
        editor = _Editor(self, view.targets[0])
        editor.buffer.select_range(editor.buffer.get_end_iter(), editor.buffer.get_start_iter())

        editor.type('Neu')
        self.store_controller.save_file()

        assert _segs(etree.parse(str(path)).getroot()) == _segs(before)
        reopened = [
            u for u in factory.getobject(str(path)).units if sdlxliff_compat.segment_defs(u)
        ]
        assert str(reopened[5].rich_target[0]) == 'Neu'

    def test_undoing_a_state_change_keeps_the_status(self, tmp_path):
        path, before = self._open_copy(STATUSES, tmp_path)
        unit, _view = self._load('7')
        self.undo_controller.model.clear()
        self.unit_controller.set_current_state(StateEnum.REJECTED, from_user=True)

        self.undo_controller._on_undo_activated()
        assert unit._current_state == StateEnum.FINAL + 1
        self.store_controller.save_file()

        assert _segs(etree.parse(str(path)).getroot()) == _segs(before)
        self.undo_controller._on_redo_activated()
        self.store_controller.save_file()
        assert _segs(etree.parse(str(path)).getroot())['7']['conf'] == 'RejectedTranslation'

    def test_the_state_lists_have_every_trados_status(self, tmp_path):
        self._open_copy(STATUSES, tmp_path)

        extended = self.store_controller.store.stats['extended']

        assert {state: len(indices) for state, indices in extended.items()} == {
            StateEnum.EMPTY: 2,
            StateEnum.NEEDS_WORK: 1,
            StateEnum.REJECTED: 4,
            StateEnum.NEEDS_REVIEW: 2,
            StateEnum.UNREVIEWED: 2,
            StateEnum.FINAL: 4,
        }

    def test_a_locked_segment_is_read_only(self, tmp_path):
        path, before = self._open_copy(COMMENTS, tmp_path)
        unit, view = self._load('6', '7')
        target = view.targets[0]

        assert not target.get_editable()
        assert not view._widgets['state'].get_sensitive()
        assert not view.mnu_transfer.get_sensitive()
        assert "Locked: this translation can't be changed." in (
            view._widgets['notes']['translator'].get_text()
        )
        # What typing does: GtkTextView inserts only where it is editable.
        assert not target.buffer.insert_interactive_at_cursor('Neu', -1, target.get_editable())
        view.set_target_n(0, 'Neu')
        view.copy_original(target)
        view.advance_workflow_state(1)
        unit.target = 'Neu'
        unit.set_state_n(StateEnum.FINAL)
        unit._modified = True
        self.store_controller.save_file()

        # Showing a unit collapses its source's whitespace, as
        # xml:space="default" allows.
        after = etree.parse(str(path)).getroot()
        assert _infoset(after, collapse=True) == _infoset(before, collapse=True)
        _unit, view = self._load('8', '9', '10')
        assert str(unit.rich_target[0]) == str(factory.getobject(COMMENTS).units[0].rich_target[0])
        assert view.targets[0].get_editable()
        assert view._widgets['state'].get_sensitive()
        assert view.mnu_transfer.get_sensitive()
