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

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.support import xliff_compat
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
