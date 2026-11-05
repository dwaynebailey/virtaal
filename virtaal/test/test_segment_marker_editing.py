#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""#4623: typing into an SDLXLIFF target through the real target TextBox
keeps the text inside Trados's <mrk mtype="seg"> segment marker."""

import pytest
from gi.repository import GLib
from lxml import etree
from test_scaffolding import TestScaffolding
from test_textbox_editing import _Editor
from translate.storage import factory

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.support import xliff_compat  # noqa: F401 - applies its patch on import
from virtaal.views.widgets import textbox as textbox_module

SDLXLIFF = """<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0" \
xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2" sdl:version="1.0">
<file original="a.docx" datatype="x-sdlfilterframework2" source-language="en-us" \
target-language="de-de"><body>
<trans-unit id="u1"><source>First sample</source><seg-source>\
<mrk mtype="seg" mid="1">First sample</mrk></seg-source>\
<target><mrk mtype="seg" mid="1"/></target>\
<sdl:seg-defs><sdl:seg id="1"/></sdl:seg-defs></trans-unit>
<trans-unit id="u2"><source>Second sample</source><seg-source>\
<mrk mtype="seg" mid="2">Second sample</mrk></seg-source>\
<target><mrk mtype="seg" mid="2">Zweite Probe</mrk></target>\
<sdl:seg-defs><sdl:seg id="2" conf="Translated"/></sdl:seg-defs></trans-unit>
<trans-unit id="u3"><source>Third sample</source><seg-source>\
<g id="5"><mrk mtype="seg" mid="3">Third sample</mrk></g></seg-source>\
<target><g id="5"><mrk mtype="seg" mid="3"/></g></target>\
<sdl:seg-defs><sdl:seg id="3"/></sdl:seg-defs></trans-unit>
</body></file></xliff>
"""
XLIFF_NS = "{urn:oasis:names:tc:xliff:document:1.2}"


def _saved_target(path, index):
    """The target element of trans-unit C{index} in the file on disk, as
    XML without its namespace declarations."""
    root = etree.parse(str(path)).getroot()
    target = list(root.iter(XLIFF_NS + "trans-unit"))[index].find(XLIFF_NS + "target")
    text = etree.tostring(target, encoding="unicode", with_tail=False)
    for prefix in (
        ' xmlns="urn:oasis:names:tc:xliff:document:1.2"',
        ' xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0"',
    ):
        text = text.replace(prefix, "")
    return text


class TestSegmentMarkerEditing(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _main_loop(self, monkeypatch):
        self.idle_calls = []
        monkeypatch.setattr(
            GLib, 'idle_add', lambda func, *args: self.idle_calls.append(lambda: func(*args))
        )
        undo = self.undo_controller
        monkeypatch.setattr(undo, '_select_unit', lambda unit: undo._flush_pending_refresh())
        monkeypatch.setattr(undo, '_correct_state_after_undo_redo', lambda unit=None: None)
        self.warnings = []
        monkeypatch.setattr(
            textbox_module.logging, 'warning', lambda msg, *args: self.warnings.append(msg % args)
        )

    def _edit(self, tmp_path, index, edit):
        path = tmp_path / 'sample.docx.sdlxliff'
        path.write_text(SDLXLIFF, encoding='utf-8')
        store = factory.getobject(str(path))
        unit = store.getunits()[index]
        for textbox in self.unit_controller.view.targets:
            textbox._suggestion = None
        view = self.unit_controller.load_unit(unit)
        view._layout_update_targets()
        self.undo_controller.model.clear()
        editor = _Editor(self, view.targets[0])
        editor.check()
        edit(editor)
        store.save()
        return path

    def test_typing_into_a_blank_target_goes_inside_the_marker(self, tmp_path):
        path = self._edit(tmp_path, 0, lambda editor: editor.type('Neu'))

        assert _saved_target(path, 0) == '<target><mrk mtype="seg" mid="1">Neu</mrk></target>'
        assert factory.getobject(str(path)).getunits()[0].target == 'Neu'

    def test_retyping_a_whole_translation_keeps_the_marker(self, tmp_path):
        def retype(editor):
            editor.select(0, len(editor.text))
            editor.type('Neu')

        path = self._edit(tmp_path, 1, retype)

        assert _saved_target(path, 1) == '<target><mrk mtype="seg" mid="2">Neu</mrk></target>'

    def test_typing_at_the_start_goes_inside_the_marker(self, tmp_path):
        def type_at_start(editor):
            editor.place(0)
            editor.type('Neu ')

        path = self._edit(tmp_path, 1, type_at_start)

        assert (
            _saved_target(path, 1)
            == '<target><mrk mtype="seg" mid="2">Neu Zweite Probe</mrk></target>'
        )

    def test_undo_empties_the_marker_again(self, tmp_path):
        def type_and_undo(editor):
            editor.commit('Neu')
            editor.undo()

        path = self._edit(tmp_path, 0, type_and_undo)

        assert _saved_target(path, 0) == '<target><mrk mtype="seg" mid="1"/></target>'

    def test_a_marker_inside_formatting_receives_the_text(self, tmp_path):
        path = self._edit(tmp_path, 2, lambda editor: editor.type('Neu'))

        assert (
            _saved_target(path, 2)
            == '<target><g id="5"><mrk mtype="seg" mid="3">Neu</mrk></g></target>'
        )

    def test_the_rest_of_the_file_is_unchanged(self, tmp_path):
        path = self._edit(tmp_path, 0, lambda editor: editor.type('Neu'))
        root = etree.parse(str(path)).getroot()

        units = list(root.iter(XLIFF_NS + "trans-unit"))
        assert (
            _saved_target(path, 1) == '<target><mrk mtype="seg" mid="2">Zweite Probe</mrk></target>'
        )
        assert len(root.findall('.//{http://sdl.com/FileTypes/SdlXliff/1.0}seg')) == 3
        assert [u.get('id') for u in units] == ['u1', 'u2', 'u3']
