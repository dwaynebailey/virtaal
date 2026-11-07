#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import cairo
import pytest
from gi.repository import Gdk, Gtk
from test_scaffolding import TestScaffolding
from translate.storage.placeables import StringElem, parse

from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.support.invisiblechars import MARKERS, InvisiblePlaceable
from virtaal.views.placeablesguiinfo import InvisibleGUI
from virtaal.views.widgets.textbox import TextBox


class TestTextBoxMarkers(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    def setup_method(self, method):
        self.windows = []

    def teardown_method(self, method):
        for window in self.windows:
            window.destroy()

    def _shown(self, text, width=200, height=100):
        textbox = TextBox(self.main_controller, role='target')
        textbox.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        textbox.set_text(text)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_size_request(width, height)
        scrolled.add(textbox)
        window = Gtk.OffscreenWindow()
        window.add(scrolled)
        window.show_all()
        window.check_resize()
        self.windows.append(window)
        return textbox

    def _drawn(self, textbox, monkeypatch, width=200, height=100):
        drawn = []
        monkeypatch.setattr(
            textbox,
            '_draw_marker',
            lambda cr, layout, marker, itr, fg: drawn.append((itr.get_offset(), marker.label)),
        )

        # Only the markers: on Windows CI, drawing text (the lines or a
        # pilcrow) into an image surface leaves the context out of memory.
        monkeypatch.setattr(textbox, '_draw_pilcrow', lambda *args: None)
        textbox.do_draw_layer(
            Gtk.TextViewLayer.ABOVE_TEXT,
            cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)),
        )
        return drawn

    def _width_of(self, textbox, offset):
        return textbox.marker_area(textbox.buffer.get_iter_at_offset(offset))[2]

    def test_a_zero_width_character_is_given_room_for_its_label(self):
        textbox = self._shown('a\u200bb')

        assert self._width_of(textbox, 1) > 10

    def test_a_zero_width_character_ending_a_line_gets_as_much_room(self):
        textbox = self._shown('a\ufeffb\na\ufeff')

        assert abs(self._width_of(textbox, 1) - self._width_of(textbox, 5)) <= 2

    def test_room_follows_a_zero_width_character_as_its_line_changes(self):
        textbox = self._shown('a\u200bb')
        middle = self._width_of(textbox, 1)
        end = textbox.buffer.get_end_iter()
        textbox.buffer.delete(textbox.buffer.get_iter_at_offset(2), end)
        for _ in range(50):
            Gtk.main_iteration_do(False)

        assert abs(self._width_of(textbox, 1) - middle) <= 2

    def test_room_is_made_for_a_typed_zero_width_character(self):
        textbox = self._shown('ab')
        textbox.buffer.insert(textbox.buffer.get_iter_at_offset(1), '\u200d')
        for _ in range(50):
            Gtk.main_iteration_do(False)

        assert self._width_of(textbox, 1) > 10
        assert self._width_of(textbox, 2) < 20

    def test_markers_are_drawn_on_invisible_characters_and_unusual_spaces(self, monkeypatch):
        textbox = self._shown('a\u00a0b\u200bc d  e\u200f ')
        monkeypatch.setitem(pan_app.settings.general, 'showspaces', '')

        assert self._drawn(textbox, monkeypatch) == [
            (1, '°'),
            (3, 'ZWSP'),
            (7, '·'),
            (8, '·'),
            (10, 'RLM'),
            (11, '·'),
        ]

    def test_every_space_is_drawn_when_all_spaces_are_shown(self, monkeypatch):
        textbox = self._shown('a b c')
        monkeypatch.setitem(pan_app.settings.general, 'showspaces', '1')

        assert self._drawn(textbox, monkeypatch) == [(1, '·'), (3, '·')]

    def test_many_invisible_characters_make_no_tag_each(self):
        textbox = self._shown('a\u200bb\u00a0\n')
        tags = textbox.buffer.get_tag_table().get_size()

        textbox.set_text('a\u200bb\u00a0\n' * 1000)

        assert textbox.buffer.get_tag_table().get_size() == tags
        assert textbox.get_children() == []

    def test_only_the_markers_of_the_lines_on_screen_are_drawn(self, monkeypatch):
        textbox = self._shown('a\u00a0b\u200bc\n' * 5000)

        drawn = self._drawn(textbox, monkeypatch)

        assert 0 < len(drawn) < 40
        assert drawn[:2] == [(1, '°'), (3, 'ZWSP')]

    def test_only_the_spaces_on_screen_of_a_long_line_are_drawn(self, monkeypatch):
        textbox = self._shown('word ' * 5000)
        monkeypatch.setitem(pan_app.settings.general, 'showspaces', '1')

        drawn = self._drawn(textbox, monkeypatch)

        assert 0 < len(drawn) < 100
        assert drawn[0] == (4, '·')

    def test_an_invisible_character_is_a_placeable_that_can_be_inserted(self):
        textbox = self._shown('a\u00a0b')

        [elem] = [e for e in textbox.selectable_elems() if isinstance(e, InvisiblePlaceable)]

        assert str(elem) == '\u00a0'
        assert isinstance(elem.gui_info, InvisibleGUI)

    @pytest.mark.parametrize(
        ('char', 'name'),
        [('\u200b', 'U+200B ZERO WIDTH SPACE'), ('\u200d', 'U+200D ZERO WIDTH JOINER')],
    )
    def test_the_tooltip_names_the_character_under_the_pointer(self, char, name):
        textbox = self._shown('a%sb' % char)
        left, top, width, _height = textbox.marker_area(textbox.buffer.get_iter_at_offset(1))
        x, y = textbox.buffer_to_window_coords(
            Gtk.TextWindowType.WIDGET, left + width // 2, top + 2
        )
        tooltip = []

        class FakeTooltip:
            def set_text(self, text):
                tooltip.append(text)

        assert textbox._on_query_tooltip(textbox, x, y, False, FakeTooltip())
        assert tooltip == [name]
        x, y = textbox.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, 1, top + 2)
        assert not textbox._on_query_tooltip(textbox, x, y, False, FakeTooltip())

    def test_show_all_spaces_in_the_view_menu_sets_the_preference(self, monkeypatch):
        monkeypatch.setitem(pan_app.settings.general, 'showspaces', '')
        item = self.unit_controller.view.mnu_show_spaces

        item.set_active(True)
        shown = pan_app.settings.general['showspaces']
        item.set_active(False)

        assert shown == '1'
        assert pan_app.settings.general['showspaces'] == ''

    def test_a_separator_ending_a_line_is_boxed_past_the_lines_end(self, monkeypatch):
        textbox = self._shown('ab\u2029cd')
        boxes = []

        class Context:
            def set_line_width(self, width):
                pass

            def rectangle(self, *rect):
                boxes.append(rect)

            def stroke(self):
                pass

        monkeypatch.setattr(Gdk, 'cairo_set_source_rgba', lambda cr, fg: None)
        monkeypatch.setattr(Gtk, 'render_layout', lambda *args: None)
        itr = textbox.buffer.get_iter_at_offset(2)
        marker = MARKERS['\u2029']
        layout = textbox._marker_layout(marker)
        textbox._draw_marker(Context(), layout, marker, itr, Gdk.RGBA())

        [(x, _y, width, _height)] = boxes
        line_end = textbox.get_iter_location(itr).x
        assert x >= line_end
        assert width >= layout.get_pixel_size()[0]

    # A long text's invisible characters past its start are parsed later.

    def _invisibles(self, textbox):
        return [e for e in textbox.elem.depth_first() if isinstance(e, InvisiblePlaceable)]

    def _leaves(self, elem):
        return [(type(e).__name__, str(e)) for e in elem.flatten()]

    def _settle(self):
        for _ in range(500):
            Gtk.main_iteration_do(False)

    LONG = 'a\u00a0b\u200bc\n' * 2000

    def test_a_short_texts_invisible_characters_are_parsed_at_once(self):
        textbox = self._shown('a\u00a0b\u200bc\n' * 10)

        assert len(self._invisibles(textbox)) == 20
        assert not textbox._pending_leaves and textbox._pending_id is None

    def test_a_long_texts_invisible_characters_after_its_start_are_parsed_when_idle(self):
        textbox = self._shown(self.LONG)
        at_once = len(self._invisibles(textbox))
        full = parse(StringElem(self.LONG), textbox.placeables_controller.parsers)

        assert 0 < at_once < 4000
        assert textbox.has_selectable_elems()
        self._settle()
        assert len(self._invisibles(textbox)) == 4000
        assert self._leaves(textbox.elem) == self._leaves(full)
        assert textbox.get_text() == self.LONG

    def test_every_placeable_is_parsed_when_they_are_all_needed(self):
        textbox = self._shown(self.LONG)

        elems = textbox.selectable_elems()

        assert sum(isinstance(e, InvisiblePlaceable) for e in elems) == 4000
        assert textbox._pending_id is None

    def test_the_first_placeable_is_selected_even_past_the_start(self):
        text = 'word ' * 2000 + 'x\u200by ' + 'word ' * 2000
        textbox = self._shown(text)

        textbox.select_first_elem()

        assert str(textbox.selected_elem) == '\u200b'
        assert textbox.selected_elem_index == 0
        assert textbox.has_selectable_elems()

    def test_the_next_placeable_follows_a_selection_made_before_the_rest_was_parsed(self):
        text = '\u200b' + 'word ' * 2000 + 'x\u200by\u00a0z'
        textbox = self._shown(text)
        textbox.select_first_elem()
        self._settle()

        textbox.move_elem_selection(1)

        assert str(textbox.selected_elem) == '\u200b'
        assert textbox.selected_elem_index == 1
        assert textbox.elem.elem_offset(textbox.selected_elem) == text.index('x') + 1
