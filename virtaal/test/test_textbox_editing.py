#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Placeable editing through a target TextBox's real keystroke paths -
GTK's key bindings and input-method commit - with undo and redo through
the real UndoController."""

import json
import os
import random
from types import SimpleNamespace

import pytest
from gi.repository import Gdk, GLib, Gtk
from test_scaffolding import TestScaffolding
from translate.storage import factory
from translate.storage.placeables import base, general

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.modes.searchmode import SearchMode
from virtaal.plugins.tm.tmcontroller import TMController
from virtaal.support import inlinecodes
from virtaal.support.pogrep_compat import GrepFilter
from virtaal.views.widgets import textbox as textbox_module

PLACEABLES_PO = "devsupport/testfiles/placeables.po"
PLACEABLES_XLIFF = "devsupport/testfiles/placeables.xliff"
EDITS_AT_EVERY_POSITION = "devsupport/testfiles/textbox-edits-at-every-position.json"

_ONE_UNIT_PO = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\nmsgid "Coffee"\nmsgstr "%s"\n'
BEGUN_G_XLIFF = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
<file original="test" source-language="en" target-language="af" datatype="plaintext">
<body><trans-unit id="1">
<source>Click <g id="1">here</g></source>
<target>Klik .</target>
</trans-unit></body>
</file>
</xliff>
"""

# Targets begun with some of their codes.
HELD_CODES_XLIFF = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
<file original="test" source-language="en" target-language="af" datatype="plaintext">
<body><trans-unit id="1">
<source>Click <g id="1">here</g> to <x id="2"/>continue</source>
<target>Klik <g id="1">hier</g> </target>
</trans-unit><trans-unit id="2">
<source>Some <bx id="3"/>bold<ex id="3"/> text<x id="4"/>.</source>
<target>Party <bx id="3"/>vet<ex id="3"/> </target>
</trans-unit><trans-unit id="3">
<source>Press<x id="5"/> <g id="6">Enter</g></source>
<target>Druk<x id="5"/> </target>
</trans-unit></body>
</file>
</xliff>
"""

# Text GTK lays out as clusters of several code points.
CLUSTER_TEXTS = [
    'ক্ষমা',  # Bengali conjunct
    'हिंदी',  # Devanagari
    'بَيْت',  # Arabic harakat
    'กี่',  # Thai tone mark
    'שָׁלוֹם',  # Hebrew niqqud
    'Việt',  # NFC
    'Việt',  # NFD
    '한국',  # NFD 한국
]
NESTED_G_XLIFF = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.1" xmlns="urn:oasis:names:tc:xliff:document:1.1">
<file original="test" source-language="en" target-language="af" datatype="plaintext">
<body><trans-unit id="1">
<source>A <g id="1">x <x id="2"/> y</g> b</source>
<target>A <g id="1">x <x id="2"/> y</g> b</target>
</trans-unit></body>
</file>
</xliff>
"""


def pilcrow_offsets(textbox):
    """The buffer offsets of the newlines textbox shows a pilcrow for."""
    buffer, tag = textbox.buffer, textbox._pilcrow_tag
    if tag is None:
        return []
    return [
        offset
        for offset in range(buffer.get_char_count())
        if buffer.get_iter_at_offset(offset).has_tag(tag)
    ]


class _Editor:
    """Edits a target the way a translator does, checking after every step
    that the buffer still shows the target's placeable tree."""

    def __init__(self, test, textbox):
        self.test = test
        self.textbox = textbox
        self.buffer = textbox.buffer

    def run_idle(self):
        while self.test.idle_calls:
            self.test.idle_calls.pop(0)()

    def check(self):
        textbox, buffer = self.textbox, self.buffer
        assert self.test.warnings == []
        assert textbox.get_text() == str(textbox.elem)
        assert textbox.elem.gui_info.length() == buffer.get_char_count()
        # Every newline placeable shows a pilcrow.
        newlines = [
            textbox.elem.gui_info.index(e)
            for e in textbox.elem.depth_first()
            if isinstance(e, general.NewlinePlaceable)
        ]
        assert pilcrow_offsets(textbox) == sorted(newlines)

    def type(self, text):
        for char in text:
            self.commit(char)

    def commit(self, text):
        # What GtkTextView's input-method commit does - one keystroke, or
        # a whole syllable from an input method that commits clusters.
        self.buffer.begin_user_action()
        self.buffer.delete_selection(True, True)
        self.buffer.insert_interactive_at_cursor(text, -1, True)
        self.buffer.end_user_action()
        self.run_idle()
        self.check()

    def backspace(self, times=1):
        for _ in range(times):
            self.textbox.emit('backspace')
            self.run_idle()
            self.check()

    def delete(self, times=1):
        for _ in range(times):
            self.textbox.emit('delete-from-cursor', Gtk.DeleteType.CHARS, 1)
            self.run_idle()
            self.check()

    def arrow(self, count, shift=False):
        self.textbox.emit('move-cursor', Gtk.MovementStep.LOGICAL_POSITIONS, count, shift)
        self.run_idle()
        self.check()

    def place(self, offset):
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(offset))

    def select(self, start, end):
        self.buffer.select_range(
            self.buffer.get_iter_at_offset(end), self.buffer.get_iter_at_offset(start)
        )

    def undo(self):
        self.test.undo_controller._on_undo_activated()
        self.run_idle()
        self.check()

    def redo(self):
        self.test.undo_controller._on_redo_activated()
        self.run_idle()
        self.check()

    @property
    def cursor(self):
        return self.buffer.props.cursor_position

    @property
    def text(self):
        return str(self.textbox.elem)


class TestTextBoxEditing(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _main_loop(self, monkeypatch):
        # Pumping the real main loop hangs in this suite - run idle
        # callbacks from the editor instead.
        self.idle_calls = []
        monkeypatch.setattr(
            GLib, 'idle_add', lambda func, *args: self.idle_calls.append(lambda: func(*args))
        )
        undo = self.undo_controller
        # Every edit stays on one unit: no store cursor to navigate with.
        monkeypatch.setattr(undo, '_select_unit', lambda unit: undo._flush_pending_refresh())
        monkeypatch.setattr(undo, '_correct_state_after_undo_redo', lambda unit=None: None)
        self.warnings = []
        monkeypatch.setattr(
            textbox_module.logging, 'warning', lambda msg, *args: self.warnings.append(msg % args)
        )

    def _editor(self, path, index):
        # A fresh store each time: load_unit() ignores the unit already loaded.
        unit = factory.getobject(path).getunits()[index]
        for textbox in self.unit_controller.view.targets:
            textbox._suggestion = None
        view = self.unit_controller.load_unit(unit)
        view._layout_update_targets()
        self.undo_controller.model.clear()
        editor = _Editor(self, view.targets[0])
        editor.check()
        return editor

    def _placeables(self, editor):
        return [
            e
            for e in editor.textbox.elem.depth_first()
            if isinstance(e, general.PythonFormattingPlaceable)
        ]

    def _g(self, editor):
        return [e for e in editor.textbox.elem.depth_first() if isinstance(e, base.G)]

    # XLIFF unit 0, "Click <g>here</g> to continue.": "<1|" at 6, "here"
    # at 7-10, ">" at 11.

    def test_deleting_all_of_a_gs_text_keeps_the_g(self):
        # #1302, #3325
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.place(11)

        editor.backspace(4)

        assert editor.text == 'Click  to continue.'
        assert len(self._g(editor)) == 1

    def test_typing_into_an_emptied_g_puts_the_text_inside_it(self):
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.place(11)
        editor.backspace(4)

        editor.type('XY')

        assert str(self._g(editor)[0]) == 'XY'

    def test_undo_puts_deleted_text_back_inside_the_g(self):
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.place(11)
        editor.backspace(4)
        editor.type('XY')

        for _ in range(6):
            editor.undo()

        assert str(self._g(editor)[0]) == 'here'
        assert editor.text == 'Click here to continue.'

    def test_typing_over_a_gs_selected_text_keeps_the_g(self):
        # #3325
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.select(7, 11)

        editor.type('X')

        assert str(self._g(editor)[0]) == 'X'
        assert editor.text == 'Click X to continue.'

    def test_typing_inside_a_g_inserts(self):
        # #2087
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.place(8)

        editor.type('Z')

        assert str(self._g(editor)[0]) == 'hZere'

    def test_undo_restores_text_deleted_inside_a_g_after_deleting_the_g(self):
        # #1303
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.place(9)
        editor.backspace()
        editor.place(6)
        editor.delete()
        assert editor.text == 'Click  to continue.'

        editor.undo()
        editor.undo()

        assert editor.text == 'Click here to continue.'
        assert str(self._g(editor)[0]) == 'here'

    def test_deleting_text_after_a_g_keeps_the_cursor_after_it(self):
        # #1304
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.place(editor.buffer.get_char_count())

        editor.backspace(len(' to continue.'))

        assert editor.cursor == 12

    def test_typing_at_the_inside_end_of_a_tag_puts_the_text_inside_it(self):
        # "An <it>opening</it> isolated tag.": the end widget is at 11.
        editor = self._editor(PLACEABLES_XLIFF, 4)
        editor.place(11)

        editor.type('s')

        assert editor.text == 'An openings isolated tag.'
        assert str(editor.textbox.elem.sub[1]) == 'openings'

    # NESTED_G_XLIFF, "A <g>x <x/> y</g> b": "<1|" at 2, "x " at 3-4, the
    # <x/> at 5, " y" at 6-7, "|1>" at 8.

    def _nested_g_editor(self, tmp_path):
        path = tmp_path / 'nested.xlf'
        path.write_text(NESTED_G_XLIFF)
        return self._editor(str(path), 0)

    def test_typing_at_the_inside_start_of_a_g_holding_a_placeable(self, tmp_path):
        editor = self._nested_g_editor(tmp_path)
        editor.place(3)

        editor.type('Z')

        assert editor.text == 'A Zx  y b'
        assert str(self._g(editor)[0]) == 'Zx  y'

    def test_typing_at_the_inside_end_of_a_g_holding_a_placeable(self, tmp_path):
        editor = self._nested_g_editor(tmp_path)
        editor.place(8)

        editor.type('Z')

        assert editor.text == 'A x  yZ b'
        assert str(self._g(editor)[0]) == 'x  yZ'

    def test_typing_inside_a_locked_g_changes_nothing(self, tmp_path):
        editor = self._nested_g_editor(tmp_path)
        self._g(editor)[0].iseditable = False
        editor.place(3)

        editor.type('Z')

        assert editor.text == 'A x  y b'

    # #2014: inserting a TM suggestion keeps the rest of the target.

    def _tm_controller(self, monkeypatch):
        monkeypatch.setattr(self.unit_controller.view, '_focused_target_n', 0)
        controller = TMController.__new__(TMController)
        controller.main_controller = self.main_controller
        return controller

    def _insert(self, monkeypatch, editor, text):
        self._tm_controller(monkeypatch).insert_match({'target': text})
        editor.run_idle()
        editor.check()

    def test_insert_match_goes_in_at_the_cursor_and_keeps_the_g(self, monkeypatch):
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.place(12)

        self._insert(monkeypatch, editor, ' daar')

        assert editor.text == 'Click here daar to continue.'
        assert str(self._g(editor)[0]) == 'here'
        assert editor.cursor == 17

    def test_insert_match_replaces_only_the_selection(self, monkeypatch):
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.select(0, 6)

        self._insert(monkeypatch, editor, 'Klik ')

        assert editor.text == 'Klik here to continue.'
        assert str(self._g(editor)[0]) == 'here'

    def test_insert_match_marks_the_unit_modified_and_undoes_in_one_step(self, monkeypatch):
        editor = self._editor(PLACEABLES_XLIFF, 0)
        unit = self.unit_controller.current_unit
        editor.select(0, 6)

        self._insert(monkeypatch, editor, 'Klik ')

        assert self.unit_controller.view._modified
        assert unit._modified
        assert str(unit.rich_target[0]) == 'Klik here to continue.'

        editor.undo()

        assert editor.text == 'Click here to continue.'
        assert str(self._g(editor)[0]) == 'here'

    def test_select_match_still_replaces_the_whole_target(self, monkeypatch):
        editor = self._editor(PLACEABLES_XLIFF, 0)

        self._tm_controller(monkeypatch).select_match({'target': 'Klik hier vir meer.'})
        editor.run_idle()

        assert editor.text == 'Klik hier vir meer.'
        assert self._g(editor) == []

    # #4708: a local TM match keeps its inline codes, which become the unit's own.

    def _select_tagged(self, monkeypatch, editor, source_markup, target_markup):
        self._tm_controller(monkeypatch).select_match(
            {
                'target': 'Klik hier en gaan voort.',
                'source_markup': source_markup,
                'target_markup': target_markup,
            }
        )
        editor.run_idle()
        editor.check()

    def test_select_match_gives_a_tagged_match_the_units_codes(self, monkeypatch):
        editor = self._editor(PLACEABLES_XLIFF, 0)
        unit = self.unit_controller.current_unit

        self._select_tagged(
            monkeypatch,
            editor,
            'Click <g id="7">here</g> to go on.',
            'Klik <g id="7">hier</g> en gaan voort.',
        )

        assert editor.text == 'Klik hier en gaan voort.'
        assert [(g.id, str(g)) for g in self._g(editor)] == [('1', 'hier')]
        assert (
            inlinecodes.from_elem(unit.rich_target[0]) == 'Klik <g id="1">hier</g> en gaan voort.'
        )

        editor.undo()

        assert editor.text == 'Click here to continue.'
        assert not self.undo_controller.model.can_undo()

    def test_select_match_with_codes_that_dont_map_takes_the_text(self, monkeypatch):
        editor = self._editor(PLACEABLES_XLIFF, 0)

        self._select_tagged(
            monkeypatch,
            editor,
            'Click <x id="7"/>here to go on.',
            'Klik <x id="7"/>hier en gaan voort.',
        )

        assert editor.text == 'Klik hier en gaan voort.'
        assert self._g(editor) == []

    def test_select_match_with_codes_into_a_plain_unit_takes_the_text(self, monkeypatch):
        editor = self._editor(PLACEABLES_PO, 0)

        self._select_tagged(
            monkeypatch,
            editor,
            'Click <g id="7">here</g> to go on.',
            'Klik <g id="7">hier</g> en gaan voort.',
        )

        assert editor.text == 'Klik hier en gaan voort.'

    # The TM menu's Insert gives a tagged match the unit's codes too.

    def _insert_tagged(self, monkeypatch, editor, target_markup):
        self._tm_controller(monkeypatch).insert_match(
            {
                'target': 'hier',
                'source_markup': 'Click <g id="7">here</g> to go on.',
                'target_markup': target_markup,
            }
        )
        editor.run_idle()
        editor.check()

    def test_insert_match_gives_a_tagged_match_the_units_codes(self, monkeypatch, tmp_path):
        path = tmp_path / 'begun.xlf'
        path.write_text(BEGUN_G_XLIFF, encoding='utf-8')
        editor = self._editor(str(path), 0)
        unit = self.unit_controller.current_unit
        editor.place(5)

        self._insert_tagged(monkeypatch, editor, '<g id="7">hier</g>')

        assert editor.text == 'Klik hier.'
        assert [(g.id, str(g)) for g in self._g(editor)] == [('1', 'hier')]
        assert inlinecodes.from_elem(unit.rich_target[0]) == 'Klik <g id="1">hier</g>.'
        assert unit._modified

        editor.undo()

        assert editor.text == 'Klik .'
        assert not self.undo_controller.model.can_undo()

    def _insert_held(self, monkeypatch, tmp_path, index, target_markup):
        path = tmp_path / 'held.xlf'
        path.write_text(HELD_CODES_XLIFF, encoding='utf-8')
        editor = self._editor(str(path), index)
        unit = self.unit_controller.current_unit
        editor.place(editor.buffer.get_char_count())
        self._tm_controller(monkeypatch).insert_match(
            {
                'target': '',
                'source_markup': inlinecodes.unit_markup(unit)[0],
                'target_markup': target_markup,
            }
        )
        editor.run_idle()
        editor.check()
        return editor, unit

    def test_insert_match_takes_a_code_the_target_holds_as_its_text(self, monkeypatch, tmp_path):
        editor, unit = self._insert_held(
            monkeypatch, tmp_path, 0, 'Klik <g id="1">hier</g> en <x id="2"/>gaan voort'
        )

        assert editor.text == 'Klik hier Klik hier en gaan voort'
        assert [str(g) for g in self._g(editor)] == ['hier']
        assert inlinecodes.from_elem(unit.rich_target[0]) == (
            'Klik <g id="1">hier</g> Klik hier en <x id="2"/>gaan voort'
        )

        editor.undo()

        assert inlinecodes.from_elem(editor.textbox.elem) == 'Klik <g id="1">hier</g> '
        assert not self.undo_controller.model.can_undo()

    def test_insert_match_takes_both_halves_of_a_held_pair_as_text(self, monkeypatch, tmp_path):
        editor, unit = self._insert_held(
            monkeypatch,
            tmp_path,
            1,
            'Sommige <bx id="3"/>vet<ex id="3"/> teks<x id="4"/>.',
        )

        assert inlinecodes.from_elem(unit.rich_target[0]) == (
            'Party <bx id="3"/>vet<ex id="3"/> Sommige vet teks<x id="4"/>.'
        )

    def test_insert_match_takes_a_held_standalone_code_as_nothing(self, monkeypatch, tmp_path):
        editor, unit = self._insert_held(
            monkeypatch, tmp_path, 2, 'Druk<x id="5"/> <g id="6">Enter</g>'
        )

        assert inlinecodes.from_elem(unit.rich_target[0]) == (
            'Druk<x id="5"/> Druk <g id="6">Enter</g>'
        )

    # A match whose codes don't map goes in as its text, without the
    # native code a TMX or XLIFF code holds; in a PO file that is text.

    TMX_IT_MATCH = {
        'target': '<b>Klik hier en gaan voort.',
        'source_markup': '<it id="tmx1" pos="open">&lt;b&gt;</it>Click here to continue.',
        'target_markup': '<it id="tmx1" pos="open">&lt;b&gt;</it>Klik hier en gaan voort.',
    }
    XLIFF_BPT_MATCH = {
        'target': 'Klik <b>hier</b> en gaan voort.',
        'source_markup': 'Click <bpt id="1">&lt;b&gt;</bpt>here<ept id="1">&lt;/b&gt;</ept>'
        '<x id="2"/> to continue.',
        'target_markup': 'Klik <bpt id="1">&lt;b&gt;</bpt>hier<ept id="1">&lt;/b&gt;</ept>'
        '<x id="2"/> en gaan voort.',
    }

    @pytest.mark.parametrize('match', [TMX_IT_MATCH, XLIFF_BPT_MATCH])
    def test_select_match_with_codes_that_dont_map_leaves_out_their_code(self, monkeypatch, match):
        editor = self._editor(PLACEABLES_XLIFF, 0)
        unit = self.unit_controller.current_unit

        self._tm_controller(monkeypatch).select_match(match)
        editor.run_idle()
        editor.check()

        assert editor.text == 'Klik hier en gaan voort.'
        assert unit.target == 'Klik hier en gaan voort.'

    @pytest.mark.parametrize('match', [TMX_IT_MATCH, XLIFF_BPT_MATCH])
    def test_insert_match_with_codes_that_dont_map_leaves_out_their_code(self, monkeypatch, match):
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.place(0)

        self._tm_controller(monkeypatch).insert_match(match)
        editor.run_idle()
        editor.check()

        assert editor.text == 'Klik hier en gaan voort.Click here to continue.'
        assert [str(g) for g in self._g(editor)] == ['here']

    def test_select_match_into_a_po_unit_keeps_the_code_text(self, monkeypatch):
        editor = self._editor(PLACEABLES_PO, 0)

        self._tm_controller(monkeypatch).select_match(self.TMX_IT_MATCH)
        editor.run_idle()

        assert editor.text == '<b>Klik hier en gaan voort.'

    def test_insert_match_into_a_po_unit_keeps_the_code_text(self, monkeypatch):
        editor = self._editor(PLACEABLES_PO, 0)
        editor.select(0, editor.buffer.get_char_count())

        self._tm_controller(monkeypatch).insert_match(self.XLIFF_BPT_MATCH)
        editor.run_idle()

        assert editor.text == 'Klik <b>hier</b> en gaan voort.'

    def test_deleting_an_x_can_be_undone(self):
        # "A line break<x/>after it.": X has a widget and no text.
        editor = self._editor(PLACEABLES_XLIFF, 1)
        editor.place(12)

        editor.delete()
        assert editor.buffer.get_char_count() == len('A line breakafter it.')

        editor.undo()
        assert editor.buffer.get_char_count() == len('A line breakafter it.') + 1

    def test_a_selection_ending_inside_a_tags_widget_keeps_the_tag(self):
        # "A placeholder: <ph>CODE</ph> here.": GTK can't delete only one
        # of the tag's widgets.
        editor = self._editor(PLACEABLES_XLIFF, 3)

        editor.select(1, 17)
        editor.delete()

        assert editor.text == 'AODE here.'

    # PO placeables are parsed from the text, so editing inside one edits
    # its text.

    def test_typing_inside_a_placeable_edits_its_text(self):
        editor = self._editor(PLACEABLES_PO, 6)  # "%(count)s files renamed"
        editor.place(5)

        editor.type('X')

        assert editor.text == '%(couXnt)s files renamed'

    def test_deleting_inside_a_placeable_edits_its_text(self):
        editor = self._editor(PLACEABLES_PO, 2)  # an e-mail address
        editor.place(len('Contact us at translate'))

        editor.backspace(3)

        assert editor.text == 'Contact us at transl-devel@lists.sourceforge.net.'

    def test_breaking_a_placeable_makes_it_plain_text_until_it_is_whole_again(self):
        editor = self._editor(PLACEABLES_PO, 6)  # "%(count)s files renamed"
        editor.place(len('%(count)'))

        editor.backspace()
        assert not self._placeables(editor)

        editor.type(')')
        assert [str(e) for e in self._placeables(editor)] == ['%(count)s']

    def test_undo_restores_text_deleted_inside_a_placeable(self):
        editor = self._editor(PLACEABLES_PO, 6)
        editor.place(5)
        editor.backspace()
        editor.delete()

        editor.undo()
        editor.undo()

        assert editor.text == '%(count)s files renamed'

    def test_redo_puts_the_cursor_where_the_deletion_was(self):
        editor = self._editor(PLACEABLES_XLIFF, 0)
        editor.place(10)
        editor.backspace()

        editor.undo()
        editor.redo()

        assert editor.cursor == 9

    # PO unit 0, "First line.\nSecond line.": the line break is at 11,
    # drawn as a pilcrow, and the second line starts at 12 (#3830).

    def test_right_arrow_steps_over_a_newline(self):
        editor = self._editor(PLACEABLES_PO, 0)
        editor.place(11)

        editor.arrow(1)

        assert editor.cursor == 12

    def test_left_arrow_stops_before_a_newline(self):
        editor = self._editor(PLACEABLES_PO, 0)
        editor.place(12)

        editor.arrow(-1)

        assert editor.cursor == 11

    def test_shift_right_selects_the_whole_newline(self):
        editor = self._editor(PLACEABLES_PO, 0)
        editor.place(11)

        editor.arrow(1, shift=True)

        assert [i.get_offset() for i in editor.buffer.get_selection_bounds()] == [11, 12]

    def test_typing_at_the_line_end_goes_before_the_newline(self):
        editor = self._editor(PLACEABLES_PO, 0)
        editor.place(11)

        editor.type('X')

        assert editor.text == 'First line.X\nSecond line.'
        assert editor.cursor == len('First line.X')
        assert pilcrow_offsets(editor.textbox) == [12]

    def test_delete_at_the_line_end_removes_the_newline(self):
        editor = self._editor(PLACEABLES_PO, 0)
        editor.place(11)

        editor.delete()

        assert editor.text == 'First line.Second line.'
        assert pilcrow_offsets(editor.textbox) == []

    def test_backspace_after_a_newline_removes_it(self):
        editor = self._editor(PLACEABLES_PO, 0)
        editor.place(12)

        editor.backspace()

        assert editor.text == 'First line.Second line.'

    @pytest.mark.parametrize(
        'typed, left',
        [
            ('কি', 'ক'),  # Bengali consonant + vowel sign
            ('ক্ষি', 'ক্ষ'),  # Bengali conjunct + vowel sign
            ('हिंदी', 'हिंद'),  # Devanagari
            ('بَ', 'ب'),  # Arabic letter + fatha (#986)
        ],
    )
    def test_backspace_after_a_combining_mark_deletes_only_the_mark(self, typed, left):
        editor = self._editor(PLACEABLES_PO, 0)
        editor.place(editor.buffer.get_char_count())
        editor.type(typed)

        editor.backspace()

        assert editor.text == 'First line.\nSecond line.' + left

        editor.undo()

        assert editor.text == 'First line.\nSecond line.' + typed

        editor.redo()

        assert editor.text == 'First line.\nSecond line.' + left

    # Pango deletes a Latin, Greek or Cyrillic letter together with its
    # marks.

    @pytest.mark.parametrize(
        'typed, left',
        [
            ('é', ''),  # NFD é
            ('Việ', 'Vi'),  # NFD ệ
            ('ά', ''),  # NFD ά
            ('й', ''),  # NFD й
        ],
    )
    def test_backspace_after_a_latin_combining_mark_deletes_the_whole_letter(self, typed, left):
        editor = self._editor(PLACEABLES_PO, 0)
        original = editor.text
        editor.place(editor.buffer.get_char_count())
        editor.type(typed)

        editor.backspace()

        assert editor.text == original + left

        editor.undo()

        assert editor.text == original + typed

        editor.redo()

        assert editor.text == original + left

    @pytest.mark.parametrize(
        'cluster',
        [
            'कि',  # Devanagari consonant + vowel sign
            'กี่',  # Thai consonant + vowel + tone mark
            'שָׁ',  # Hebrew shin + qamats + shin dot
            'é',  # NFD é
            '한',  # NFD 한
        ],
    )
    def test_delete_before_a_cluster_removes_all_of_it(self, cluster):
        editor = self._editor(PLACEABLES_PO, 0)
        original = editor.text
        start = editor.buffer.get_char_count()
        editor.place(start)
        editor.type(cluster + 'x')
        editor.place(start)

        editor.delete()

        assert editor.text == original + 'x'

        editor.undo()

        assert editor.text == original + cluster + 'x'

        editor.redo()

        assert editor.text == original + 'x'

    @pytest.mark.parametrize('text', ['ক্ষি', 'Việt', 'שָׁלוֹם'])
    def test_typing_over_selected_marks_replaces_all_of_them(self, text):
        editor = self._editor(PLACEABLES_PO, 0)
        original = editor.text
        start = editor.buffer.get_char_count()
        editor.place(start)
        editor.type(text)
        editor.select(start, start + len(text))

        editor.type('X')

        assert editor.text == original + 'X'

        editor.undo()

        assert editor.text == original + text

        editor.redo()

        assert editor.text == original + 'X'

    @pytest.mark.parametrize('text', CLUSTER_TEXTS)
    def test_an_input_method_commit_undoes_and_redoes_in_one_step(self, text):
        editor = self._editor(PLACEABLES_PO, 0)
        original = editor.text
        editor.place(editor.buffer.get_char_count())

        editor.commit(text)

        assert editor.text == original + text

        editor.undo()

        assert editor.text == original

        editor.redo()

        assert editor.text == original + text

    @pytest.mark.parametrize('text', CLUSTER_TEXTS)
    def test_undoing_and_redoing_typed_marks_keeps_every_code_point(self, text):
        editor = self._editor(PLACEABLES_PO, 0)
        original = editor.text
        editor.place(editor.buffer.get_char_count())
        editor.type(text)

        for _ in text:
            editor.undo()

        assert editor.text == original

        for _ in text:
            editor.redo()

        assert editor.text == original + text

    def test_editing_keeps_each_parts_own_normalisation_form(self, tmp_path):
        path = tmp_path / 'nfd.po'
        path.write_text(_ONE_UNIT_PO % 'Café %s', encoding='utf-8')
        editor = self._editor(str(path), 0)
        editor.place(editor.buffer.get_char_count())

        editor.type(' café')

        assert editor.text == 'Café %s café'
        assert self.unit_controller.current_unit.target == 'Café %s café'

    @pytest.mark.parametrize(
        'target, search, found',
        [
            ('%s café {0} é', 'é', 'é'),  # NFD target, NFC search
            ('%s café {0} é', 'é', 'é'),  # NFC target, NFD search
        ],
    )
    def test_search_highlights_the_targets_own_code_points(self, tmp_path, target, search, found):
        path = tmp_path / 'search.po'
        path.write_text(_ONE_UNIT_PO % target, encoding='utf-8')
        editor = self._editor(str(path), 0)
        unit = self.unit_controller.current_unit
        mode = SearchMode.__new__(SearchMode)
        mode.unitview = SimpleNamespace(unit=unit, sources=[], targets=[editor.textbox])
        mode.matches, _ = GrepFilter(
            searchstring=search,
            searchparts=('target',),
            ignorecase=False,
            useregexp=False,
            max_matches=10,
        ).getmatches([unit])

        mode._highlight_textbox_matches(editor.textbox, select_match=False)

        tag = editor.buffer.get_tag_table().lookup('search_highlight')
        highlighted = ''.join(
            char
            for i, char in enumerate(editor.textbox.get_text())
            if editor.buffer.get_iter_at_offset(i).has_tag(tag)
        )
        assert highlighted == found * 2

    # Random edits: whatever a translator does, the buffer keeps showing
    # the tree, undoing everything gets the original back and redoing
    # everything gets the last text back. One branch per kind of edit.

    @pytest.mark.parametrize('seed', range(40))
    def test_random_edits(self, seed):  # noqa: C901, PLR0912
        rnd = random.Random(seed)
        path = rnd.choice([PLACEABLES_PO, PLACEABLES_XLIFF])
        editor = self._editor(path, rnd.randrange(len(factory.getobject(path).getunits())))
        original = editor.text
        for _ in range(30):
            length = editor.buffer.get_char_count()
            action = rnd.choice(
                [
                    'type',
                    'type',
                    'placeable',
                    'newline',
                    'backspace',
                    'delete',
                    'place',
                    'arrow',
                    'select-delete',
                    'select-type',
                    'undo',
                    'redo',
                ]
            )
            if action == 'type':
                editor.type(rnd.choice('ab '))
            elif action == 'placeable':
                editor.type(rnd.choice(['%s', '%d', '{0}', '<b>', '</b>', '&amp;']))
            elif action == 'newline':
                editor.type('\n')
            elif action == 'backspace':
                editor.backspace()
            elif action == 'delete':
                editor.delete()
            elif action == 'place':
                editor.place(rnd.randint(0, length))
            elif action == 'arrow':
                editor.arrow(rnd.choice([-1, 1]), shift=rnd.random() < 0.3)
            elif action.startswith('select'):
                editor.select(*sorted((rnd.randint(0, length), rnd.randint(0, length))))
                editor.type('z') if action == 'select-type' else editor.delete()
            elif action == 'undo' and self.undo_controller.model.can_undo():
                editor.undo()
            elif action == 'redo' and self.undo_controller.model.can_redo():
                editor.redo()

        while self.undo_controller.model.can_redo():
            editor.redo()
        final = editor.text
        while self.undo_controller.model.can_undo():
            editor.undo()
        assert editor.text == original
        while self.undo_controller.model.can_redo():
            editor.redo()
        assert editor.text == final

    # Characterisation: what one Backspace or Delete does at every cursor
    # position, and the tags placeable selection leaves, for every unit
    # in the fixtures - with the <g>s locked (not editable) and fragile
    # too. Recorded in EDITS_AT_EVERY_POSITION.

    def _characterisation_cases(self, tmp_path):
        nested = tmp_path / 'nested.xlf'
        nested.write_text(NESTED_G_XLIFF)
        cases = [
            (path, index, None)
            for path in (PLACEABLES_PO, PLACEABLES_XLIFF)
            for index in range(len(factory.getobject(path).getunits()))
        ]
        cases += [(str(nested), 0, lock) for lock in (None, 'locked', 'fragile')]
        cases += [(PLACEABLES_XLIFF, 0, lock) for lock in ('locked', 'fragile')]
        return [('%s:%d:%s' % (os.path.basename(p), i, lock), p, i, lock) for p, i, lock in cases]

    def _locked_editor(self, path, index, lock):
        editor = self._editor(path, index)
        for g in self._g(editor) if lock else ():
            g.iseditable = False
            g.isfragile = lock == 'fragile'
        return editor

    def _deletions(self, path, index, lock):
        rows = []
        for pos in range(self._locked_editor(path, index, lock).buffer.get_char_count() + 1):
            row = []
            for key in ('backspace', 'delete'):
                editor = self._locked_editor(path, index, lock)
                editor.place(pos)
                getattr(editor, key)()
                row.append([editor.text, editor.buffer.get_char_count(), editor.cursor])
            rows.append(row)
        return rows

    def _selections(self, path, index, lock):
        editor = self._locked_editor(path, index, lock)
        textbox = editor.textbox
        steps = [['loaded', _placeable_tags(textbox), editor.cursor, textbox.selected_elem_index]]
        count = len(textbox.selectable_elems())
        for offset in [*range(count + 1), -1, 0, -1, None]:
            textbox.select_elem(offset=offset)
            steps.append(
                [offset, _placeable_tags(textbox), editor.cursor, textbox.selected_elem_index]
            )
        return steps

    def test_backspace_and_delete_at_every_position(self, tmp_path):
        with open(EDITS_AT_EVERY_POSITION, encoding='utf-8') as f:
            expected = json.load(f)['deletions']
        for name, path, index, lock in self._characterisation_cases(tmp_path):
            assert self._deletions(path, index, lock) == expected[name], name

    def test_selecting_each_placeable_in_turn(self, tmp_path):
        with open(EDITS_AT_EVERY_POSITION, encoding='utf-8') as f:
            expected = json.load(f)['selections']
        for name, path, index, lock in self._characterisation_cases(tmp_path):
            assert self._selections(path, index, lock) == expected[name], name


def _placeable_tags(textbox):
    """The ranges of each of textbox's placeable tags, sorted, with
    whether the tag shows the selected placeable's background."""
    selected_bg = Gdk.RGBA()
    selected_bg.parse(textbox_module.current_theme['selected_placeable_bg'])
    buffer = textbox.buffer
    tags = []
    for tag in textbox._placeable_tags:
        ranges = []
        it = buffer.get_start_iter()
        while it.starts_tag(tag) or it.forward_to_tag_toggle(tag):
            if not it.starts_tag(tag):
                continue
            start = it.get_offset()
            it.forward_to_tag_toggle(tag)
            ranges.append([start, it.get_offset()])
        props = tag.props
        tags.append([ranges, props.background_set and props.background_rgba.equal(selected_bg)])
    return sorted(tags)
