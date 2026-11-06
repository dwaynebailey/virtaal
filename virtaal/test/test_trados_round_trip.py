#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Trados files from devsupport/testfiles/trados opened, edited through
the target TextBox, saved and reopened: the edit lands where Trados reads
it, and every other element, attribute and text survives."""

import copy
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
from virtaal.support import sdlxliff_compat
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
# Segments "1 a" and "1 b" in one trans-unit, the marker ids encoded.
WEIRD_IDS = os.path.join(TRADOS, 'okapi', 'test-weird-ids.docx.sdlxliff')
# The same, with Trados comments on each segment.
TEST_COMMENTS = os.path.join(TRADOS, 'okapi', 'test-comments.docx.sdlxliff')
# One segment with origin, percent and a comment on its translation.
COMMENTED = b"""<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0"
 xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2" sdl:version="1.0">
<doc-info xmlns="http://sdl.com/FileTypes/SdlXliff/1.0"><cmt-defs>
<cmt-def id="c1"><Comments><Comment severity="Medium" user="Reviewer"
 date="2026-11-05T10:00:00+02:00" version="1.0">Check the term</Comment></Comments></cmt-def>
</cmt-defs></doc-info>
<file original="a.txt" datatype="x-sdlfilterframework2" source-language="en-US"
 target-language="de-DE"><body>
<trans-unit id="u1"><source>Open the file</source>
<seg-source><mrk mtype="seg" mid="1">Open the file</mrk></seg-source>
<target><mrk mtype="seg" mid="1"><mrk mtype="x-sdl-comment" sdl:cid="c1">Datei</mrk>
 \xc3\xb6ffnen</mrk></target>
<sdl:seg-defs><sdl:seg id="1" conf="Draft" origin="tm" origin-system="Main TM"
 percent="95"/></sdl:seg-defs>
</trans-unit></body></file></xliff>
"""


def _in_content(element):
    return any(e.tag in CONTENT for e in element.iterancestors()) or element.tag in CONTENT


def _infoset(element):
    """Every element under C{element} as (tag, attributes, text, tail),
    whitespace-only text kept only inside content."""

    def text(value, keep):
        if value is None or (not keep and not value.strip()):
            return None
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


def _marker(trans_unit, mid, name='target'):
    return next(
        m
        for m in trans_unit.find(XLIFF + name).iter(XLIFF + 'mrk')
        if m.get('mtype') == 'seg' and m.get('mid') == mid
    )


def _comment_ids(marker):
    return [
        m.get(SDL + 'cid') for m in marker.iterdescendants() if m.get('mtype') == 'x-sdl-comment'
    ]


def _edited(root, index, mid):
    """Trans-unit C{index}'s infoset without segment C{mid}'s translation
    and status."""
    trans_unit = copy.deepcopy(_units(root)[index])
    marker = _marker(trans_unit, mid)
    marker.text = None
    for child in list(marker):
        marker.remove(child)
    for seg in trans_unit.iter(SDL + 'seg'):
        if seg.get('id') == sdlxliff_compat._seg_id(mid):
            seg.attrib.pop('conf', None)
    return _infoset(trans_unit)


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
        unit's index in the file, or None, and the unit."""
        self.store_controller.open_file(str(path))
        units = self.store_controller.store.get_units()
        unit = next((u for u in units if pick(u)), None)
        if unit is None:
            self.store_controller.save_file()
            return None, None
        self._type(unit, 'Neu')
        self.store_controller.save_file()
        return _units(unit.xmlelement.getroottree().getroot()).index(unit.xmlelement), unit

    def _type(self, unit, text):
        """Replace C{unit}'s target with C{text} through the editor, and
        return the editor."""
        for textbox in self.unit_controller.view.targets:
            textbox._suggestion = None
        view = self.unit_controller.load_unit(unit)
        view._layout_update_targets()
        editor = _Editor(self, view.targets[0])
        editor.buffer.select_range(editor.buffer.get_end_iter(), editor.buffer.get_start_iter())
        editor.type(text)
        unit._modified = True
        return editor

    def _round_trip_sdlxliff(self, original, tmp_path):
        tmp_path.mkdir(exist_ok=True)
        path = tmp_path / os.path.basename(original)
        shutil.copy(original, path)
        before = etree.parse(str(path)).getroot()

        index, unit = self._retype(
            path,
            lambda u: (
                isinstance(u, sdlxliff_compat.SegmentUnit) and not sdlxliff_compat.is_locked(u)
            ),
        )

        after = etree.parse(str(path)).getroot()
        if index is None:
            assert _infoset(after) == _infoset(before)
            return
        assert _without(after, index) == _without(before, index)
        marker = _marker(_units(after)[index], unit.mid)
        assert marker.xpath('string()') == 'Neu'
        # Only the segment's comments stay, round the new text.
        before_marker = _marker(_units(before)[index], unit.mid)
        assert _comment_ids(marker) == _comment_ids(before_marker)
        assert all(m.get('mtype') == 'x-sdl-comment' for m in marker.iterdescendants())
        assert _edited(after, index, unit.mid) == _edited(before, index, unit.mid)
        reopened = factory.getobject(str(path)).units
        assert [u.target for u in reopened].count('Neu') == 1

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
        index, _unit = self._retype(path, lambda u: True)

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
        unit, view = self._load('6')
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

        after = etree.parse(str(path)).getroot()
        assert _infoset(after) == _infoset(before)
        _unit, view = self._load('8')
        assert str(unit.rich_target[0]) == str(factory.getobject(COMMENTS).units[0].rich_target[0])
        assert view.targets[0].get_editable()
        assert view._widgets['state'].get_sensitive()
        assert view.mnu_transfer.get_sensitive()
        # Showing an indented segment leaves it as it is.
        _unit._modified = True
        self.store_controller.save_file()
        assert _infoset(etree.parse(str(path)).getroot()) == _infoset(before)

    def test_origin_and_comments_show_with_the_translators_notes(self, tmp_path):
        self._open_copy(STATUSES, tmp_path)

        assert self._notes('3') == (
            'Origin: auto-propagated (Propagated from segment 2), 100% match'
        )
        assert self._notes('1') == 'Origin: interactive'
        self._open_copy(COMMENTS, tmp_path)
        assert self._notes('8') == (
            'Origin: source\n'
            'Comment from Tay, AmandaX: To add into the current promotion page\n'
            'Comment from System Administrator: To add into the current promotion page'
        )

    def test_a_state_change_keeps_the_origin_and_comments(self, tmp_path):
        path = tmp_path / 'commented.sdlxliff'
        path.write_bytes(COMMENTED)
        before = etree.parse(str(path)).getroot()
        self.store_controller.open_file(str(path))
        assert self._notes('1') == (
            'Origin: tm (Main TM), 95% match\nComment from Reviewer: Check the term'
        )

        self.unit_controller.set_current_state(StateEnum.UNREVIEWED, from_user=True)
        self.store_controller.save_file()

        after = etree.parse(str(path)).getroot()
        assert _segs(after) == {'1': {**_segs(before)['1'], 'conf': 'Translated'}}
        for root in (before, after):
            root.find('.//' + SDL + 'seg').attrib.pop('conf')
        assert _infoset(after) == _infoset(before)

    def _notes(self, *seg_ids):
        _unit, view = self._load(*seg_ids)
        return view._widgets['notes']['translator'].get_text()

    @pytest.mark.parametrize(
        'original,segments',
        [
            (COMMENTS, ['6', '7', '8', '9', '10', None, '11', '12']),
            (WEIRD_IDS, ['1 a', '1 b', '2', '3']),
            (TEST_COMMENTS, ['1 a', '1 b', '2', '3']),
        ],
        ids=['comments', 'weird-ids', 'test-comments'],
    )
    def test_each_trados_segment_is_a_unit(self, original, segments, tmp_path):
        _path, root = self._open_copy(original, tmp_path)
        units = self.store_controller.store.get_units()

        segs = [sdlxliff_compat.segment_def(u) for u in units]
        assert [None if seg is None else seg.get('id') for seg in segs] == segments
        markers = [
            (m, tu)
            for tu in _units(root)
            for m in tu.find(XLIFF + 'seg-source').iter(XLIFF + 'mrk')
            if m.get('mtype') == 'seg'
        ]
        assert [(u.xmlelement.get('id'), u.mid) for u in units] == [
            (tu.get('id'), m.get('mid')) for m, tu in markers
        ]
        assert len({u.getid() for u in units}) == len(units)

    def test_a_segments_source_and_target_are_its_markers(self, tmp_path):
        self._open_copy(WEIRD_IDS, tmp_path)
        units = self.store_controller.store.get_units()

        assert [(u.source, u.target) for u in units] == [
            ('This is one sentence, etc.', 'Ceci est une phrase, etc. '),
            ('and more text goes here.', 'Plus de texte ici.'),
            ('Test sentence.', ''),
            ('and more text goes here.', ''),
        ]
        assert [u.get_state_n() for u in units] == [
            StateEnum.NEEDS_REVIEW,
            StateEnum.UNREVIEWED,
            StateEnum.EMPTY,
            StateEnum.EMPTY,
        ]

    def test_the_stats_count_each_segment(self, tmp_path):
        self._open_copy(WEIRD_IDS, tmp_path)
        store = self.store_controller.store

        assert store.stats['extended'] == {
            StateEnum.NEEDS_REVIEW: [0],
            StateEnum.UNREVIEWED: [1],
            StateEnum.EMPTY: [2, 3],
        }
        assert {state: totals['units'] for state, totals in store.get_stats_totals().items()} == {
            'needs-review': 1,
            'unreviewed': 1,
            'empty': 2,
        }

    def test_editing_a_segment_changes_only_its_marker_and_status(self, tmp_path):
        path, before = self._open_copy(WEIRD_IDS, tmp_path)
        unit, _view = self._load('1 b')

        self._type(unit, 'Neu.')
        self.unit_controller.set_current_state(StateEnum.FINAL, from_user=True)
        self.store_controller.save_file()

        after = etree.parse(str(path)).getroot()
        assert _without(after, 0) == _without(before, 0)
        assert _edited(after, 0, '1_x0020_b') == _edited(before, 0, '1_x0020_b')
        assert _marker(_units(after)[0], '1_x0020_b').text == 'Neu.'
        assert _segs(after)['1 b']['conf'] == 'ApprovedTranslation'
        assert _segs(after)['1 a'] == _segs(before)['1 a']
        reopened = factory.getobject(str(path)).units
        assert [(u.target, u.get_state_n()) for u in reopened[:2]] == [
            ('Ceci est une phrase, etc. ', StateEnum.NEEDS_REVIEW),
            ('Neu.', StateEnum.FINAL),
        ]

    def test_typing_fills_an_empty_segment_marker(self, tmp_path):
        path, before = self._open_copy(WEIRD_IDS, tmp_path)
        unit, _view = self._load('2')

        self._type(unit, 'Ein Satz.')
        self.store_controller.save_file()

        after = etree.parse(str(path)).getroot()
        assert _without(after, 1) == _without(before, 1)
        assert _edited(after, 1, '2') == _edited(before, 1, '2')
        marker = _marker(_units(after)[1], '2')
        assert (marker.text, len(marker)) == ('Ein Satz.', 0)

    def test_undo_and_redo_of_a_segment_edit(self, tmp_path):
        path, before = self._open_copy(WEIRD_IDS, tmp_path)
        unit, _view = self._load('1 b')
        self.undo_controller.model.clear()
        editor = self._type(unit, 'Neu.')

        steps = 0
        while unit.target != 'Plus de texte ici.' and steps < 5:
            editor.undo()
            steps += 1
        assert unit.target == 'Plus de texte ici.'
        self.store_controller.save_file()
        assert _infoset(etree.parse(str(path)).getroot()) == _infoset(before)
        for _step in range(steps):
            editor.redo()
        assert unit.target == 'Neu.'
        self.store_controller.save_file()

        after = etree.parse(str(path)).getroot()
        assert _marker(_units(after)[0], '1_x0020_b').text == 'Neu.'
        assert _edited(after, 0, '1_x0020_b') == _edited(before, 0, '1_x0020_b')

    def test_a_locked_segment_does_not_lock_the_next(self, tmp_path):
        path = tmp_path / 'comments.sdlxliff'
        with open(COMMENTS, 'rb') as original:
            path.write_bytes(original.read().replace(b'id="7" locked="true"', b'id="7"', 1))
        before = etree.parse(str(path)).getroot()
        self.store_controller.open_file(str(path))
        _unit, view = self._load('6')
        assert not view.targets[0].get_editable()
        unit, view = self._load('7')
        assert view.targets[0].get_editable()

        self._type(unit, 'Neu')
        self.store_controller.save_file()

        after = etree.parse(str(path)).getroot()
        assert _marker(_units(after)[0], '7').text == 'Neu'
        assert _edited(after, 0, '7') == _edited(before, 0, '7')

    def test_each_segment_shows_its_own_comments(self, tmp_path):
        self._open_copy(TEST_COMMENTS, tmp_path)

        assert self._notes('1 a') == (
            'Origin: interactive\n'
            'Comment from ysavourel: Comment 2 (warning)\n'
            'Comment from ysavourel: This is comment 1 (info)\n'
            'Comment from ysavourel: Second comment entry'
        )
        assert self._notes('1 b') == (
            'Origin: mt\n'
            'Comment from ysavourel: Comment seg-level\n'
            'Comment from ysavourel: Yet another comment entry (error)\n'
            'Comment from ysavourel: Comment 3 (error)'
        )
        assert self._notes('2') == ''
