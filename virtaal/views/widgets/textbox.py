#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import time
from collections import deque

from gi.repository import Gdk, GLib, Gtk, Pango
from gi.repository.GObject import SignalFlags
from translate.storage.placeables import StringElem
from translate.storage.placeables import parse as elem_parse

from virtaal.common import pan_app
from virtaal.support import inlinecodes, invisiblechars
from virtaal.support.translate_compat import forceunicode
from virtaal.views import placeablesguiinfo, theme
from virtaal.views.theme import current_theme

MARKER_SCALE = 0.7
"""The size of a zero-width character's label, against the text's."""

EAGER_LENGTH = 5000
"""Invisible characters past this many of a text's first characters are
parsed as placeables in idle steps, or when every placeable is needed."""

_PENDING_STEP = 0.01
"""Seconds an idle step parses invisible characters for."""


def colors_equal(a, b):
    """Compare two colors that could be Gdk.RGBA or str."""
    if isinstance(a, str):
        x = Gdk.RGBA()
        x.parse(a)
        a = x

    if isinstance(b, str):
        x = Gdk.RGBA()
        x.parse(b)
        b = x

    return a.equal(b)


def _tag_colors(tags, fg):
    """(foreground, background or None) of a pilcrow from those of
    tags, lowest priority first, that colour it, else fg."""
    if isinstance(fg, str):
        rgba = Gdk.RGBA()
        rgba.parse(fg)
        fg = rgba
    bg = None
    for tag in tags:
        if not getattr(tag, 'colours_pilcrow', False):
            continue
        if tag.props.foreground_set:
            fg = tag.props.foreground_rgba
        if tag.props.background_set:
            bg = tag.props.background_rgba
    return fg, bg


def unlock_partly_covered_leaves(elem, start, end):
    """Make each non-editable leaf that [start, end) only partly covers
    plain text, since the toolkit refuses to edit inside one. Every
    such leaf with text is parsed from the text (%s, &amp;, a newline),
    so a reparse recognises whatever is left of it. With start == end,
    this unlocks a leaf that offset falls strictly inside of.
    @returns: Whether any leaf was unlocked."""
    unlocked = False
    for leaf in elem.flatten():
        if leaf.iseditable:
            continue
        leaf_start = elem.elem_offset(leaf)
        leaf_end = leaf_start + len(leaf)
        if start < leaf_end and leaf_start < end and not (start <= leaf_start and leaf_end <= end):
            parent = elem.get_parent_elem(leaf)
            if parent is None:
                continue
            parent.sub = [StringElem(str(leaf)) if sub is leaf else sub for sub in parent.sub]
            unlocked = True
    return unlocked


def _unparsed_leaves(tree, end=None):
    """The plain leaves of tree holding invisible characters not yet
    parsed as placeables, those starting before end if given."""
    offset = 0
    for leaf in tree.flatten():
        if end is not None and offset >= end:
            return
        text = str(leaf)
        if type(leaf) is StringElem and leaf.istranslatable and invisiblechars.has_placeable(text):
            yield leaf
        offset += len(text)


_suggestion_provider = None
_suggestion_color = None


def _ensure_suggestion_css(screen):
    """Style a suggestion's selection as subtle text rather than a real
    selection. Screen-wide, since the selection node doesn't see
    providers added to the textview's own style context."""
    global _suggestion_provider, _suggestion_color
    color = current_theme['subtle_fg']
    if _suggestion_provider is None:
        _suggestion_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            screen, _suggestion_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    if color != _suggestion_color:
        _suggestion_provider.load_from_data(
            (
                'textview.suggestion text selection '
                '{ color: %s; background-color: transparent; }' % color
            ).encode()
        )
        _suggestion_color = color


def _tag_bound(bound, default, start_index, end_index):
    """A buffer offset for a create_tags() bound: relative to the
    element's start, from its end if negative, default if None."""
    if bound is None:
        bound = default
    if bound < 0:
        return bound + end_index + 1
    return bound + start_index


#################################
#  Placeable:  |<<|content|>>|  #
#  Cursor:     a  b       c  d  #
# ===============================#
#           Editable            #
# ===============================#
#   |  Backspace  |  Delete     #
# ---|-------------|-------------#
# a |  N/A        |  Placeable  #
# b |  Nothing    | @Delete "c" #
# c | @Delete "t" |  Nothing    #
# d |  Placeable  |  N/A        #
# ===============================#
#         Non-Editable          #
# ===============================#
# a |  N/A        |  Placeable  #
# b | *Nothing    | *Nothing    #
# c | *Nothing    | *Nothing    #
# d |  Placeable  |  N/A        #
#################################
# What a single-character Backspace or Delete deletes when the cursor is
# at a boundary position (a, b, c, d) of a placeable. Without widgets,
# positions b and c fall away, and an editable placeable's "Placeable"
# needs a widget on that side.
#
# @ Deleted like any other text ("normal").
# * "Placeable" instead if the placeable is fragile.
_BOUNDARY_ACTIONS = {
    # (position, key_is_delete): action for an editable placeable
    ('a', False): 'normal',
    ('a', True): 'placeable',
    ('b', False): 'nothing',
    ('b', True): 'normal',
    ('c', False): 'normal',
    ('c', True): 'nothing',
    ('d', False): 'placeable',
    ('d', True): 'normal',
}


class TextBox(Gtk.TextView):
    """
    A C{Gtk.TextView} extended to work with our nifty L{StringElem} parsed
    strings.
    """

    __gtype_name__ = 'TextBox'
    __gsignals__ = {
        'element-selected': (SignalFlags.RUN_FIRST, None, (object,)),
        'key-pressed': (SignalFlags.RUN_LAST, bool, (object, str)),
        'refreshed': (SignalFlags.RUN_FIRST, None, (object,)),
        'text-deleted': (SignalFlags.RUN_LAST, bool, (object, object, int, int, object)),
        'text-inserted': (SignalFlags.RUN_LAST, bool, (object, int, object)),
        'changed': (SignalFlags.RUN_LAST, None, ()),
    }

    SPECIAL_KEYS = {
        'alt-down': [(Gdk.KEY_Down, Gdk.ModifierType.MOD1_MASK)],
        'alt-left': [(Gdk.KEY_Left, Gdk.ModifierType.MOD1_MASK)],
        'alt-right': [(Gdk.KEY_Right, Gdk.ModifierType.MOD1_MASK)],
        'enter': [(Gdk.KEY_Return, 0), (Gdk.KEY_KP_Enter, 0)],
        'ctrl-enter': [
            (Gdk.KEY_Return, Gdk.ModifierType.CONTROL_MASK),
            (Gdk.KEY_KP_Enter, Gdk.ModifierType.CONTROL_MASK),
        ],
        'ctrl-shift-enter': [
            (Gdk.KEY_Return, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK),
            (Gdk.KEY_KP_Enter, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK),
        ],
        'shift-tab': [
            (Gdk.KEY_ISO_Left_Tab, Gdk.ModifierType.SHIFT_MASK),
            (Gdk.KEY_Tab, Gdk.ModifierType.SHIFT_MASK),
        ],
        'ctrl-tab': [
            (Gdk.KEY_ISO_Left_Tab, Gdk.ModifierType.CONTROL_MASK),
            (Gdk.KEY_Tab, Gdk.ModifierType.CONTROL_MASK),
        ],
        'ctrl-shift-tab': [
            (Gdk.KEY_ISO_Left_Tab, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK),
            (Gdk.KEY_Tab, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK),
        ],
    }
    """A table of name-keybinding mappings. The name (key) is passed as the
    second parameter to the 'key-pressed' event."""
    unselectables = [StringElem]
    """A list of classes that should not be selectable with Alt+Left or Alt+Right."""

    # INITIALIZERS #
    def __init__(self, main_controller, text=None, selector_textbox=None, role=None):
        """Constructor.
        @type  main_controller: L{virtaal.controllers.main_controller}
        @param main_controller: The main controller instance.
        @type  text: String
        @param text: The initial text to set in the new text box. Optional.
        @type  selector_textbox: C{TextBox}
        @param selector_textbox: The text box in which placeable selection
            (@see{select_elem}) should happen. Optional."""
        super().__init__()
        self.set_input_hints(self.get_input_hints() | Gtk.InputHints.NO_EMOJI)
        self.buffer = self.get_buffer()
        self.elem = None
        self.main_controller = main_controller
        self.placeables_controller = main_controller.placeables_controller
        self.refresh_cursor_pos = -1
        self._structural_change_pending = False
        self._render_pending = False
        self.elem_before_edit = None
        self.role = role
        # Removes characters the unit's file format can't store, or None.
        self.text_filter = None
        self.selector_textbox = selector_textbox or self
        self.selector_textboxes = [selector_textbox or self]
        self.selected_elem = None
        self.selected_elem_index = None
        self._suggestion = None
        self._completion_popup = None
        self._list_kind = None
        self._listed = None
        self._insert_list = None
        # The tag table is shared (search highlight, gtkspell): remove only ours.
        self._placeable_tags = []
        self._pilcrow_tag = None
        # Make room for a zero-width character's label, one per label.
        self._room_tags = {}
        # Leaves whose invisible characters are still to be parsed.
        self._pending_leaves = deque()
        self._pending_id = None
        self.undo_controller = main_controller.undo_controller

        self.__connect_default_handlers()

        if self.placeables_controller is None or self.undo_controller is None:
            # This should always happen, because the text boxes are created
            # when the unit controller is created, which happens before the
            # creation of the placeables- and undo controllers.
            self.__controller_connect_id = main_controller.connect(
                'controller-registered', self.__on_controller_register
            )
        if text:
            self.set_text(text)

    def __connect_default_handlers(self):
        self.connect('button-press-event', self._on_event_remove_suggestion)
        self.connect('focus-out-event', self._on_event_remove_suggestion)
        self.connect('key-press-event', self._on_key_pressed)
        self.connect('move-cursor', self._on_event_remove_suggestion)
        self.connect('unmap', self._on_event_remove_suggestion)
        self.set_has_tooltip(True)
        self.connect('query-tooltip', self._on_query_tooltip)
        self.buffer.connect('insert-text', self._on_insert_text)
        self.buffer.connect_after('insert-text', self._on_insert_text_after)
        self.buffer.connect('delete-range', self._on_delete_range)
        self.buffer.connect_after('delete-range', self._on_delete_range_after)
        self.buffer.connect('begin-user-action', self._on_begin_user_action)
        self.buffer.connect('end-user-action', self._on_end_user_action)

    def _get_suggestion(self):
        return self._suggestion

    def _set_suggestion(self, value):
        if value is None:
            self.hide_suggestion()
            self._suggestion = None
            self.get_style_context().remove_class('suggestion')
            self._close_list('suggestion', soon=True)
            return

        if not (
            isinstance(value, dict)
            and 'text' in value
            and value['text']
            and 'offset' in value
            and value['offset'] >= 0
        ):
            raise ValueError('invalid suggestion dictionary: %s' % (value))

        if self.suggestion_is_visible():
            self.hide_suggestion()
        self._suggestion = value
        self.show_suggestion()
        self._list_suggestions(value)

    suggestion = property(_get_suggestion, _set_suggestion)

    # THE LIST OF COMPLETIONS #
    # One popup list at the cursor serves both autocomplete's completions
    # (kind 'suggestion': the selected one is the grey text after the
    # cursor) and the translations of a term to insert (kind 'insert').

    def _list_suggestions(self, suggestion):
        """List suggestion's candidates when it has more than one."""
        candidates = suggestion.get('candidates') or ()
        if len(candidates) < 2:
            self._close_list('suggestion')
            return
        popup = self.completion_popup
        if self._list_kind == 'suggestion' and popup.is_showing() and self._listed is candidates:
            return  # Another of the same list selected.
        self._list_kind = 'suggestion'
        self._listed = candidates

        def show():
            current = self._suggestion
            if current is None or current.get('candidates') is not candidates:
                return False
            if not self.get_mapped():
                return False
            first = candidates[0]
            popup.show_at(
                self,
                first['offset'] - (len(first['word']) - len(first['text'])),
                [c['word'] for c in candidates],
                lambda _word: self._accept_visible_suggestion(),
                self._select_suggestion,
                current.get('index', 0),
            )
            return False

        # Deferred so the text layout is valid when positioning the popup.
        GLib.idle_add(show)

    def _select_suggestion(self, index):
        candidates = self._listed
        if self._suggestion is None or not candidates or index >= len(candidates):
            return
        self.suggestion = dict(candidates[index], candidates=candidates, index=index)

    def _accept_visible_suggestion(self):
        if not self.suggestion_is_visible():
            return False
        self.hide_suggestion()
        self._accept_suggestion(self.suggestion)
        self.suggestion = None
        self.emit("changed")
        return True

    def _close_list(self, kind, soon=False):
        popup = self._completion_popup
        if popup is None or self._list_kind != kind or not popup.is_showing():
            return
        if soon:
            popup.dismiss_soon()
        else:
            popup.dismiss()

    @property
    def completion_popup(self):
        if self._completion_popup is None:
            from virtaal.views.widgets.completionpopup import CompletionPopup

            self._completion_popup = CompletionPopup()
        return self._completion_popup

    # OVERRIDDEN METHODS #
    def get_stringelem(self):
        if self.elem is None:
            return None
        return elem_parse(self.elem, self.placeables_controller.get_parsers_for_textbox(self))

    def get_text(self, start_iter=None, end_iter=None):
        """Return the text rendered in this text box.
        Uses C{Gtk.TextBuffer.get_text()}."""
        if isinstance(start_iter, int):
            start_iter = self.buffer.get_iter_at_offset(start_iter)
        if isinstance(end_iter, int):
            end_iter = self.buffer.get_iter_at_offset(end_iter)
        if start_iter is None:
            start_iter = self.buffer.get_start_iter()
        if end_iter is None:
            end_iter = self.buffer.get_end_iter()
        return forceunicode(self.buffer.get_text(start_iter, end_iter, include_hidden_chars=True))

    def set_text(self, text, update=False):
        """Set the text rendered in this text box.
        Uses C{Gtk.TextBuffer.set_text()}.
        @type  text: str|str|L{StringElem}
        @param text: The text to render in this text box."""
        if not isinstance(text, StringElem):
            text = StringElem(text)

        if self.elem is None:
            self.elem = StringElem('')

        if text is not self.elem:
            # If text is self.elem, we are busy with a refresh and we should remember the selected element.
            self.selected_elem = None
            self.selected_elem_index = None

            if self.text_filter is not None:
                text = text.copy()
                text.apply_to_strings(self.text_filter)

            # We have to edit the existing .elem for the sake of the undo controller
            if self.placeables_controller:
                self.elem.sub = [self._parse_start(text)]
                self.elem.prune()
            else:
                self.elem.sub = [text]
            self.update_tree()
        elif update:
            self.update_tree()
        self.emit("changed")

    # METHODS #
    def add_default_gui_info(self, elem):
        """Add default GUI info to string elements in the tree that does not
        have any GUI info.

        Only leaf nodes are (currently) extended with a C{StringElemGUI}
        (or sub-class) instance. Other nodes has C{gui_info} set to C{None}.

        @type  elem: StringElem
        @param elem: The root of the string element tree to add default
            GUI info to.
        """
        if not isinstance(elem, StringElem):
            return

        if not hasattr(elem, 'gui_info') or not elem.gui_info:
            if not self.placeables_controller:
                return
            elem.gui_info = self.placeables_controller.get_gui_info(elem)(elem=elem, textbox=self)

        for sub in elem.sub:
            self.add_default_gui_info(sub)

    def apply_gui_info(self, elem, include_subtree=True, offset=None):
        if getattr(elem, 'gui_info', None):
            if offset is None:
                offset = self.elem.gui_info.index(elem)
            if offset >= 0:
                self._apply_tags(elem, offset, include_subtree)

        if include_subtree:
            for sub, index in elem.gui_info.iter_sub_with_index():
                if isinstance(sub, StringElem):
                    self.apply_gui_info(sub, offset=index + offset)

    def _apply_tags(self, elem, offset, include_subtree):
        """Apply elem's tags to the buffer, elem being at offset in it."""
        start_index = offset
        end_index = offset + elem.gui_info.length()
        for tag, tag_start, tag_end in elem.gui_info.create_tags():
            if tag is None:
                continue
            if (
                include_subtree
                and colors_equal(elem.gui_info.fg, placeablesguiinfo.StringElemGUI.fg)
                and colors_equal(elem.gui_info.bg, placeablesguiinfo.StringElemGUI.bg)
            ):
                continue
            tag_start = max(_tag_bound(tag_start, 0, start_index, end_index), start_index)
            tag_end = min(_tag_bound(tag_end, end_index, start_index, end_index), end_index)
            self.buffer.get_tag_table().add(tag)
            self._placeable_tags.append(tag)
            start_iter = self.buffer.get_iter_at_offset(tag_start)
            end_iter = self.buffer.get_iter_at_offset(tag_end)
            self.buffer.apply_tag(tag, start_iter, end_iter)

    def _remove_placeable_tags(self):
        tagtable = self.buffer.get_tag_table()
        for tag in self._placeable_tags:
            tagtable.remove(tag)
        self._placeable_tags = []

    def mark_pilcrow(self, start, end):
        """Show a pilcrow for the newline at buffer offsets [start, end)."""
        if self._pilcrow_tag is None:
            self._pilcrow_tag = self.buffer.create_tag(None)
        self.buffer.apply_tag(
            self._pilcrow_tag,
            self.buffer.get_iter_at_offset(start),
            self.buffer.get_iter_at_offset(end),
        )

    def _marker_layout(self, marker, fg=None):
        layout = self.create_pango_layout(marker.label)
        attrs = Pango.AttrList()
        if marker.zero_width:
            attrs.insert(Pango.attr_scale_new(MARKER_SCALE))
        if fg is not None:
            attrs.insert(
                Pango.attr_foreground_new(*(round(c * 65535) for c in (fg.red, fg.green, fg.blue)))
            )
        layout.set_attributes(attrs)
        return layout

    def make_room(self, start=0, end=None):
        """Make room for the label of each zero-width character in
        buffer offsets [start, end)."""
        buffer = self.buffer
        start = max(start, 0)
        start_iter = buffer.get_iter_at_offset(start)
        end_iter = buffer.get_end_iter() if end is None else buffer.get_iter_at_offset(end)
        for tag in self._room_tags.values():
            buffer.remove_tag(tag, start_iter, end_iter)
        text = buffer.get_text(start_iter, end_iter, include_hidden_chars=True)
        for index, marker in invisiblechars.zero_width_indices(text):
            itr = buffer.get_iter_at_offset(start + index)
            after = itr.copy()
            after.forward_char()
            # A line's first and last characters get half their spacing.
            edge = itr.starts_line() or after.ends_line()
            tag = self._room_tags.get((marker.label, edge))
            if tag is None:
                tag = self._room_tags[marker.label, edge] = buffer.create_tag(None)
            width = self._marker_layout(marker).get_size()[0] + 4 * Pango.SCALE
            tag.props.letter_spacing = width * 2 if edge else width
            buffer.apply_tag(tag, itr, after)

    def do_draw_layer(self, layer, cr):
        if layer != Gtk.TextViewLayer.ABOVE_TEXT:
            return
        if self._pilcrow_tag is not None:
            self._draw_pilcrows(cr)
        self._draw_markers(cr)

    def _draw_pilcrows(self, cr):
        """Draw a pilcrow at the end of each line being drawn that ends
        in a marked newline. cr is in buffer coordinates."""
        drawn, area = Gdk.cairo_get_clip_rectangle(cr)
        if not drawn:
            return
        itr, _top = self.get_line_at_y(area.y)
        layout = self.create_pango_layout('¶')
        size = layout.get_pixel_size()
        while True:
            end = itr.copy()
            if not end.ends_line():
                end.forward_to_line_end()
            if end.has_tag(self._pilcrow_tag):
                self._draw_pilcrow(cr, layout, itr, end, *size)
            if not itr.forward_line():
                return
            y, height = self.get_line_yrange(itr)
            # Lines not laid out yet have no height, and aren't drawn.
            if y > area.y + area.height or not height:
                return

    def _draw_pilcrow(self, cr, layout, line_start, end, width, height):
        rect, _weak = self.get_cursor_locations(end)
        x = rect.x
        start_x = self.get_cursor_locations(line_start)[0].x
        # Right to left, the line ends at its left.
        if start_x > x or (start_x == x and x > self.get_visible_rect().width // 2):
            x -= width
        y = rect.y + max(rect.height - height, 0)
        fg, bg = _tag_colors(end.get_tags(), placeablesguiinfo.NewlineGUI.fg)
        if bg is not None:
            Gdk.cairo_set_source_rgba(cr, bg)
            cr.rectangle(x, rect.y, width, rect.height)
            cr.fill()
        attrs = Pango.AttrList()
        attrs.insert(
            Pango.attr_foreground_new(*(round(c * 65535) for c in (fg.red, fg.green, fg.blue)))
        )
        layout.set_attributes(attrs)
        Gtk.render_layout(self.get_style_context(), cr, x, y, layout)

    def _drawn_range(self, cr):
        """The buffer offsets [start, end) of the display lines being
        drawn, or None."""
        drawn, area = Gdk.cairo_get_clip_rectangle(cr)
        if drawn:
            drawn, area = area.intersect(self.get_visible_rect())
        if not drawn:
            return None
        bottom = area.y + area.height
        start = self.get_iter_at_location(area.x, area.y)[1]
        self.backward_display_line_start(start)
        end = self.get_iter_at_location(area.x + area.width, bottom)[1]
        self.forward_display_line_end(end)
        end = end.get_offset() + 1
        # Lines not laid out yet have no height, and aren't drawn.
        line = start.copy()
        while line.forward_line() and line.get_offset() < end:
            y, height = self.get_line_yrange(line)
            if y > bottom or not height:
                end = line.get_offset()
        return start.get_offset(), end

    def _draw_markers(self, cr):
        """Draw a marker on each invisible character (see
        L{invisiblechars}) on the lines being drawn. cr is in buffer
        coordinates."""
        drawn = self._drawn_range(cr)
        if drawn is None:
            return
        start, end = drawn
        # A character either side decides a space at the range's edges.
        context = 1 if start else 0
        text = self.get_text(start - context, end + 1)
        all_spaces = bool(pan_app.settings.general.get('showspaces'))
        default_fg = _tag_colors((), placeablesguiinfo.InvisibleGUI.fg)[0]
        layouts = {}
        for index, marker in invisiblechars.find_markers(
            text, context, min(end - start + context, len(text)), all_spaces
        ):
            itr = self.buffer.get_iter_at_offset(start - context + index)
            fg = _tag_colors(itr.get_tags(), default_fg)[0]
            key = (marker, fg.to_string())
            layout = layouts.get(key)
            if layout is None:
                layout = layouts[key] = self._marker_layout(marker, fg)
            self._draw_marker(cr, layout, marker, itr, fg)

    def marker_area(self, itr):
        """(x, y, width, height) of the character at itr, in buffer
        coordinates. A joiner has no width of its own in its letter's
        cluster, but the room made for it follows it."""
        rect = self.get_iter_location(itr)
        x, width = rect.x, rect.width
        if not width and not itr.ends_line():
            after = itr.copy()
            after.forward_char()
            after = self.get_iter_location(after)
            if after.y == rect.y:
                width = after.x - x
        # Right to left, a width can be negative.
        return min(x, x + width), rect.y, abs(width), rect.height

    def _draw_marker(self, cr, layout, marker, itr, fg):
        x, y, width, height = self.marker_area(itr)
        label_width, label_height = layout.get_pixel_size()
        rtl = self.get_direction() == Gtk.TextDirection.RTL
        if marker.zero_width and width <= 3:
            # No room made (a line break): boxed past the line's end.
            width = label_width + 4
            x -= width if rtl else 0
        if width:
            label_x = x + (width - label_width) / 2
        elif rtl:
            label_x = x - label_width
        else:
            label_x = x
        if marker.zero_width:
            Gdk.cairo_set_source_rgba(cr, fg)
            cr.set_line_width(1)
            cr.rectangle(x + 1.5, y + 1.5, width - 3, height - 3)
            cr.stroke()
        Gtk.render_layout(
            self.get_style_context(), cr, label_x, y + (height - label_height) / 2, layout
        )

    def _on_query_tooltip(self, widget, x, y, keyboard_mode, tooltip):
        if keyboard_mode:
            offsets = [self.buffer.props.cursor_position]
        else:
            bx, by = self.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, x, y)
            found, itr = self.get_iter_at_location(bx, by)
            if not found:
                return False
            # The room made for a joiner isn't the joiner's own.
            offset = itr.get_offset()
            offsets = [offset, offset + 1] + ([offset - 1] if offset else [])
        for offset in offsets:
            char = self.buffer.get_iter_at_offset(offset).get_char()
            if char not in invisiblechars.MARKERS:
                continue
            left, _y, width, _height = self.marker_area(self.buffer.get_iter_at_offset(offset))
            if keyboard_mode or left <= bx < left + max(width, 1):
                tooltip.set_text(invisiblechars.describe(char))
                return True
        return False

    def get_cursor_position(self):
        return self.buffer.props.cursor_position

    def hide_suggestion(self):
        if not self.suggestion_is_visible():
            return
        selection = self.buffer.get_selection_bounds()

        self.buffer.handler_block_by_func(self._on_delete_range)
        self.buffer.delete(*selection)
        self.buffer.handler_unblock_by_func(self._on_delete_range)

    def insert_translation(self, elem):
        """Insert C{elem}'s translation at the cursor, or offer its candidates
        to choose from. Returns C{False} if the insert waits on a choice."""
        candidates = elem.gui_info.get_insert_candidates()
        if candidates:
            self._show_insert_candidates(candidates)
            return False
        self.insert_elem(elem.translate())
        return True

    def insert_elem(self, translation, new_codes_only=False):
        """Insert C{translation}, text or a rich tree, at the cursor, over
        the selection if any, as one undo entry; with C{new_codes_only},
        the codes the rest of the target holds already go in as text."""
        if self.suggestion_is_visible():
            self.suggestion = None
        # Group the selection-delete with whatever follows into one undo entry.
        recording = self.undo_controller and not self.undo_controller.model.recording
        if recording:
            self.undo_controller.record_start()

        selection = self.buffer.get_selection_bounds()
        if selection:
            self.buffer.delete(*selection)

        while Gtk.events_pending():
            Gtk.main_iteration()

        cursor_pos = self.buffer.props.cursor_position
        if isinstance(translation, StringElem):
            if new_codes_only:
                translation = inlinecodes.without_codes_of(translation, self.elem)
            self.add_default_gui_info(translation)
            insert_offset = self.elem.gui_info.gui_to_tree_index(cursor_pos)
            self.elem_before_edit = self.elem.copy()
            self.elem.insert(insert_offset, translation)
            self.elem.prune()

            self.emit('text-inserted', translation, cursor_pos, self.elem)

            if hasattr(translation, 'gui_info'):
                cursor_pos += translation.gui_info.length()
            else:
                cursor_pos += len(translation)
        else:
            self.buffer.insert_at_cursor(translation)
            cursor_pos += len(translation)
        if recording:
            self.undo_controller.record_stop()
        self.refresh_cursor_pos = cursor_pos
        self.refresh(update=True)

    def insert_text(self, text):
        """Insert plain C{text} at the cursor, over the selection if any, as
        one typed edit: one undo entry, and the unit is marked modified."""
        if self.suggestion_is_visible():
            self.suggestion = None
        self.buffer.begin_user_action()
        self.buffer.delete_selection(True, True)
        self.buffer.insert_interactive_at_cursor(text, -1, True)
        self.buffer.end_user_action()

    def _show_insert_candidates(self, candidates):
        # Deferred so the text layout is valid when positioning the popup.
        def show():
            if not self.get_mapped():
                return False
            bounds = self.buffer.get_selection_bounds()
            if bounds:
                start, end = (itr.get_offset() for itr in bounds)
            else:
                start = end = self.buffer.props.cursor_position
            self._list_kind = 'insert'
            self._insert_list = {'candidates': list(candidates), 'start': start, 'end': end}
            self._show_insert_list(list(candidates))
            return False

        GLib.idle_add(show)

    def _show_insert_list(self, candidates):
        insert_list = self._insert_list
        self.completion_popup.show_at(
            self,
            insert_list['start'],
            candidates,
            lambda text: self._insert_candidate(text, insert_list['start'], insert_list['end']),
        )

    def _filter_insert_list(self):
        """After a key while a term's translations are listed: list those
        that start with what was typed since, ignoring case, or close
        the list when none does or the cursor left the typed text."""
        insert_list = self._insert_list
        if self._list_kind != 'insert' or insert_list is None:
            return False
        if not self.completion_popup.is_showing():
            return False
        start = insert_list['start']
        cursor = self.buffer.props.cursor_position
        if cursor < start:
            self.completion_popup.dismiss()
            return False
        typed = self.get_text(start, cursor)
        if typed:
            insert_list['end'] = cursor
        folded = typed.casefold()
        matches = [c for c in insert_list['candidates'] if c.casefold().startswith(folded)]
        if not matches:
            self.completion_popup.dismiss()
        else:
            self._show_insert_list(matches)
        return False

    def _accept_suggestion(self, suggestion):
        """Insert suggestion's text; with a 'replace' text, that replaces
        as many characters typed before it (another case or digits)."""
        start = suggestion['offset']
        text = suggestion['text']
        replace = suggestion.get('replace')
        if not replace:
            self.buffer.insert(self.buffer.get_iter_at_offset(start), text)
            return
        recording = self.undo_controller and not self.undo_controller.model.recording
        if recording:
            self.undo_controller.record_start()
        start -= len(replace)
        self.buffer.delete(
            self.buffer.get_iter_at_offset(start),
            self.buffer.get_iter_at_offset(start + len(replace)),
        )
        self.buffer.insert(self.buffer.get_iter_at_offset(start), replace + text)
        if recording:
            self.undo_controller.record_stop()

    def _insert_candidate(self, text, start, end):
        # The selection-delete and the insert are one undo entry.
        recording = self.undo_controller and not self.undo_controller.model.recording
        if recording:
            self.undo_controller.record_start()
        if end > start:
            self.buffer.delete(
                self.buffer.get_iter_at_offset(start), self.buffer.get_iter_at_offset(end)
            )
        self.refresh_cursor_pos = start
        self.buffer.insert(self.buffer.get_iter_at_offset(start), text)
        if recording:
            self.undo_controller.record_stop()
        self.emit('changed')
        self.move_elem_selection(1)

    def move_elem_selection(self, offset):
        """Move the source placeable selection C{offset} places, stopping at
        the first and last placeable."""
        source = self.selector_textbox
        elems = source.selectable_elems()
        if not elems:
            return
        if source._has_valid_selection(elems):
            index = max(0, min(source.selected_elem_index + offset, len(elems) - 1))
        else:
            index = 0 if offset > 0 else len(elems) - 1
        source.select_elem(elem=elems[index])
        self.__color_selector_textboxes()

    def selectable_elems(self, complete=True):
        """The placeables that can be selected for insertion, in order;
        without complete, only those parsed so far (see L{finish_parsing})."""
        if self.elem is None:
            return []
        if complete:
            self.finish_parsing()
        return [e for e in self.elem.depth_first() if e.__class__ not in self.unselectables]

    def has_selectable_elems(self):
        """Whether there is a placeable to select, parsed or not."""
        return bool(self._pending_leaves or self.selectable_elems(complete=False))

    def select_first_elem(self):
        """Select the first placeable, unless one is already selected."""
        if not getattr(self.elem, 'gui_info', None):
            return  # Not rendered with placeables, so nothing to highlight.
        # The first leaf still unparsed has the first such placeable.
        self._parse_pending(1)
        elems = self.selectable_elems(complete=False)
        if elems and not self._has_valid_selection(elems):
            self.select_elem(elem=elems[0], elems=elems)

    def _has_valid_selection(self, elems):
        # The selection goes stale when the text is re-parsed (e.g. a
        # terminology rescan replaces the placeables).
        return self.selected_elem_index is not None and any(e is self.selected_elem for e in elems)

    @staticmethod
    def __set_selector_fg(widget, color):
        """Colour one selector's parent - override_color() is
        deprecated. Tracks its own provider on the widget itself,
        since each of self.selector_textboxes' parents needs its
        own (this recolours all of them on every selection
        change)."""
        style = widget.get_style_context()
        old = getattr(widget, '_selector_fg_provider', None)
        if old is not None:
            style.remove_provider(old)
        provider = Gtk.CssProvider()
        provider.load_from_data(('* { color: %s; }' % color).encode())
        style.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        widget._selector_fg_provider = provider

    def __color_selector_textboxes(self, *args):
        """Put a highlighting border around the current selector text box."""
        if not hasattr(self, 'selector_color'):
            self.selector_color = current_theme['selector_textbox']
        if not hasattr(self, 'nonselector_color'):
            fg = self.get_parent().get_style_context().get_color(Gtk.StateType.NORMAL)
            self.nonselector_color = theme.rgba_to_str(fg)

        for selector in self.selector_textboxes:
            if selector is self.selector_textbox:
                self.__set_selector_fg(selector.get_parent(), self.selector_color)
            else:
                self.__set_selector_fg(selector.get_parent(), self.nonselector_color)

    def place_cursor(self, cursor_pos):
        cursor_iter = self.buffer.get_iter_at_offset(cursor_pos)
        # logging.debug('setting cursor to position %d' % (cursor_pos))
        self.buffer.place_cursor(cursor_iter)
        # Make sure the cursor is visible to reduce jitters (with backspace at
        # the end of a long unit with scrollbar, for example). Unlike
        # scroll_to_iter(), this waits for the rebuilt buffer's line heights.
        self.scroll_mark_onscreen(self.buffer.get_insert())

    def refresh(self, preserve_selection=True, update=False):
        """Refresh the text box by setting its text to the current text."""
        if not self.props.visible:
            return  # Don't refresh if this text box is not going to be seen anyway
        # logging.debug('self.refresh_cursor_pos = %d' % (self.refresh_cursor_pos))
        if self.refresh_cursor_pos < 0:
            self.refresh_cursor_pos = self.buffer.props.cursor_position
        selection = [itr.get_offset() for itr in self.buffer.get_selection_bounds()]

        if self.elem is not None:
            self.set_text(self.elem, update=update)
        else:
            self.set_text(self.get_text())

        if preserve_selection and selection:
            self.buffer.select_range(
                self.buffer.get_iter_at_offset(selection[0]),
                self.buffer.get_iter_at_offset(selection[1]),
            )
        elif self.refresh_cursor_pos >= 0:
            self.place_cursor(self.refresh_cursor_pos)
        self.refresh_cursor_pos = -1

        self.emit('refreshed', self.elem)

    def select_elem(self, elem=None, offset=None, elems=None):
        """Select elem, or the placeable at offset among elems (by
        default every one)."""
        if elem is not None and offset is not None:
            raise ValueError('Only one of "elem" or "offset" may be specified.')

        if elem is None and offset is None:
            self._clear_selected_elem()
            return

        filtered_elems = self.selectable_elems() if elems is None else elems
        if not filtered_elems:
            return

        if elem is None:
            if self.selected_elem_index is not None and not (0 <= offset < len(filtered_elems)):
                # Clear selection when we go past the first or last placeable
                self.select_elem(None)
                self._remove_placeable_tags()
                self.apply_gui_info(self.elem)
                return
            return self.select_elem(elem=filtered_elems[offset % len(filtered_elems)])

        if elem not in filtered_elems:
            return

        self._show_selected_elem(elem, filtered_elems)

    def _clear_selected_elem(self):
        if self.selected_elem is not None:
            self.selected_elem.gui_info = None
            self.add_default_gui_info(self.selected_elem)
            self.selected_elem = None
        self.selected_elem_index = None
        self.emit('element-selected', self.selected_elem)

    def _show_selected_elem(self, elem, filtered_elems):
        # Reset the default tag for the previously selected element
        if self.selected_elem is not None:
            self.selected_elem.gui_info = None
            self.add_default_gui_info(self.selected_elem)

        self.selected_elem_index = next(
            (i for i, fe in enumerate(filtered_elems) if fe is elem), len(filtered_elems)
        )
        self.selected_elem = elem
        if not hasattr(elem, 'gui_info') or not elem.gui_info:
            elem.gui_info = placeablesguiinfo.StringElemGUI(
                elem,
                self,
                fg=current_theme['selected_placeable_fg'],
                bg=current_theme['selected_placeable_bg'],
            )
        else:
            elem.gui_info.fg = current_theme['selected_placeable_fg']
            elem.gui_info.bg = current_theme['selected_placeable_bg']
        self._remove_placeable_tags()
        self.apply_gui_info(self.elem, include_subtree=False)
        self.apply_gui_info(self.elem)
        self.apply_gui_info(elem, include_subtree=False)
        cursor_offset = self.elem.find(self.selected_elem)
        if not isinstance(elem.gui_info, placeablesguiinfo.NewlineGUI):
            # After a newline is the next line, away from its pilcrow.
            cursor_offset += len(self.selected_elem)
        self.place_cursor(cursor_offset)
        self.emit('element-selected', self.selected_elem)

    def show_suggestion(self, suggestion=None):
        if isinstance(suggestion, dict):
            self.suggestion = suggestion  # The setter shows it.
            return
        if self.suggestion is None:
            return
        iters = (self.buffer.get_iter_at_offset(self.suggestion['offset']),)
        self.buffer.handler_block_by_func(self._on_insert_text)
        self.buffer.insert(iters[0], self.suggestion['text'])
        self.buffer.handler_unblock_by_func(self._on_insert_text)
        iters = (
            self.buffer.get_iter_at_offset(self.suggestion['offset']),
            self.buffer.get_iter_at_offset(
                self.suggestion['offset'] + len(self.suggestion['text'])
            ),
        )
        self.buffer.select_range(*iters)
        _ensure_suggestion_css(self.get_screen())
        self.get_style_context().add_class('suggestion')

    def suggestion_is_visible(self):
        """Checks whether the current text suggestion is visible."""
        selection = self.buffer.get_selection_bounds()
        if not selection or self.suggestion is None:
            return False
        start_offset = selection[0].get_offset()
        text = self.buffer.get_text(*selection, include_hidden_chars=False)
        return (
            self.suggestion['text']
            and self.suggestion['text'] == text
            and self.suggestion['offset'] >= 0
            and self.suggestion['offset'] == start_offset
        )

    def update_tree(self):
        if not self.placeables_controller:
            return
        if self.elem is None:
            self.elem = StringElem('')

        self.add_default_gui_info(self.elem)

        self.buffer.handler_block_by_func(self._on_delete_range)
        self.buffer.handler_block_by_func(self._on_insert_text)
        self.buffer.handler_block_by_func(self._on_insert_text_after)
        self.elem.gui_info.render()
        self.show_suggestion()
        self.buffer.handler_unblock_by_func(self._on_delete_range)
        self.buffer.handler_unblock_by_func(self._on_insert_text)
        self.buffer.handler_unblock_by_func(self._on_insert_text_after)

        self._remove_placeable_tags()
        # At this point we have a tree of string elements with GUI info.
        self.apply_gui_info(self.elem)
        self.make_room()
        self._schedule_pending()

    def _lazy_parsers(self):
        """The parsers a long text's invisible characters wait for: the
        invisible characters' and those after it."""
        parsers = self.placeables_controller.get_parsers_for_textbox(self)
        if invisiblechars.InvisiblePlaceable.parse not in parsers:
            return []
        return parsers[parsers.index(invisiblechars.InvisiblePlaceable.parse) :]

    def _parse_start(self, text):
        """text parsed for placeables, but for a long text, the invisible
        characters only in its first L{EAGER_LENGTH} characters: the rest
        wait for L{_schedule_pending}."""
        parsers = self.placeables_controller.get_parsers_for_textbox(self)
        lazy = self._lazy_parsers()
        if len(text) <= EAGER_LENGTH or not lazy:
            return elem_parse(text, parsers)
        tree = elem_parse(text, [p for p in parsers if p not in lazy])
        for leaf in _unparsed_leaves(tree, EAGER_LENGTH):
            elem_parse(leaf, lazy)
        return tree

    def _schedule_pending(self):
        """Parse the invisible characters still unparsed in idle steps."""
        lazy = self._lazy_parsers() if self.elem is not None else []
        self._pending_leaves = deque(_unparsed_leaves(self.elem) if lazy else ())
        if self._pending_leaves and self._pending_id is None:
            self._pending_id = GLib.idle_add(self._parse_pending_step, priority=GLib.PRIORITY_LOW)
        elif not self._pending_leaves and self._pending_id is not None:
            GLib.source_remove(self._pending_id)
            self._pending_id = None

    def _parse_pending_step(self):
        deadline = time.perf_counter() + _PENDING_STEP
        while self._pending_leaves and time.perf_counter() < deadline:
            self._parse_pending(20)
        if self._pending_leaves:
            return True
        self._pending_id = None
        return False

    def _parse_pending(self, count=None):
        """Parse the next count leaves still unparsed (all if None)."""
        if not self._pending_leaves:
            return
        lazy = self._lazy_parsers()
        for _ in range(count or len(self._pending_leaves)):
            if not self._pending_leaves:
                break
            leaf = self._pending_leaves.popleft()
            # An edit may have parsed it already.
            if leaf.isleaf():
                elem_parse(leaf, lazy)
                leaf.gui_info = None
                self.add_default_gui_info(leaf)
        if self.selected_elem is not None and not self._pending_leaves:
            # Placeables found before it move it along.
            elems = self.selectable_elems(complete=False)
            self.selected_elem_index = next(
                (i for i, e in enumerate(elems) if e is self.selected_elem), None
            )

    def finish_parsing(self):
        """Parse every invisible character still unparsed now."""
        if self._pending_id is not None:
            GLib.source_remove(self._pending_id)
            self._pending_id = None
        if self._pending_leaves:
            self._parse_pending()

    # EVENT HANDLERS #
    def __on_controller_register(self, main_controller, controller):
        if controller is main_controller.placeables_controller:
            self.placeables_controller = controller
        elif controller is main_controller.undo_controller:
            self.undo_controller = controller

        if self.placeables_controller is not None and self.undo_controller is not None:
            main_controller.disconnect(self.__controller_connect_id)

    def _on_begin_user_action(self, buffer):
        if not self.undo_controller:
            # Maybe not ready yet, so we'll loose a bit of undo data
            return
        if not self.undo_controller.model.recording:
            self.undo_controller.record_start()

    def _on_end_user_action(self, buffer):
        if not self.undo_controller:
            return
        if self.undo_controller.model.recording:
            self.undo_controller.record_stop()
        # A typed placeable's widgets only reach the buffer in the deferred refresh.
        if (
            self.elem is not None
            and not self._structural_change_pending
            and not self._render_pending
            and not self._matches_tree(self.get_text(), self.buffer.get_char_count())
        ):
            logging.warning('Target text out of step with its placeables: %r', self.elem)
            self._structural_change_pending = True
        if self._structural_change_pending:
            self._structural_change_pending = False
            self.refresh(update=True)
        elif self.refresh_cursor_pos >= 0:
            # No structure changed, so the buffer already matches self.elem -
            # skip the rebuild, just keep the cursor onscreen. set_text()
            # would normally emit 'changed' as part of that rebuild - do it
            # directly instead, or the edit never reaches unit.target.
            self.place_cursor(self.refresh_cursor_pos)
            self.refresh_cursor_pos = -1
            self.emit("changed")

    def _delete_at_placeable_boundary(
        self, start_elem, start_elem_offset, cursor_pos, key_is_delete
    ):
        """Handle a single-character Backspace/Delete landing on one of
        the four boundary positions around a placeable, per
        _BOUNDARY_ACTIONS. Returns (done, deleted, parent, index, span);
        done is True if this fully handled the deletion (the generic
        range-delete fallback must then be skipped), and span is the
        buffer range of a deleted placeable."""
        start_elem_len = start_elem.gui_info.length()
        has_start_widget = has_end_widget = False
        if hasattr(start_elem, 'gui_info'):
            has_start_widget = start_elem.gui_info.has_start_widget()
            has_end_widget = start_elem.gui_info.has_end_widget()

        if cursor_pos == start_elem_offset:
            position = 'a'
        elif has_start_widget and cursor_pos == start_elem_offset + 1:
            position = 'b'
        elif has_end_widget and cursor_pos == start_elem_offset + start_elem_len - 1:
            position = 'c'
        elif cursor_pos == start_elem_offset + start_elem_len:
            position = 'd'
        else:
            return False, None, None, None, None

        action = _BOUNDARY_ACTIONS[position, key_is_delete]
        if not start_elem.iseditable and position in ('b', 'c'):
            action = 'placeable' if start_elem.isfragile else 'nothing'
        elif action == 'placeable' and start_elem.iseditable:
            # An editable placeable without a widget on that side loses a character.
            if not (has_start_widget if position == 'a' else has_end_widget):
                action = 'normal'

        if action == 'placeable':
            deleted = start_elem.copy()
            parent = self.elem.get_parent_elem(start_elem)
            index = self.elem.elem_offset(start_elem)
            self.elem.delete_elem(start_elem)
            self.refresh_cursor_pos = start_elem_offset
            span = (start_elem_offset, start_elem_offset + start_elem_len)
            return True, deleted, parent, index, span
        return action == 'nothing', None, None, None, None

    def _matches_tree(self, text, length):
        """Whether a buffer with C{text} and C{length} positions (text and
        widgets) shows self.elem as it is now."""
        self.add_default_gui_info(self.elem)
        return text == str(self.elem) and length == self.elem.gui_info.length()

    def _delete_inside_widget_leaf(self, leaf, start, end):
        """Delete tree range [start, end) from the content of C{leaf}, a
        placeable shown with widgets (<g>). Unlike delete_range(), this
        keeps the placeable when all of its content goes."""
        leaf_offset = self.elem.elem_offset(leaf)
        text = str(leaf)
        local_start, local_end = start - leaf_offset, end - leaf_offset
        if not 0 <= local_start <= local_end <= len(text):
            return self.elem.delete_range(start, end)
        leaf.sub = [text[:local_start] + text[local_end:]]
        return StringElem(text[local_start:local_end]), leaf, local_start

    def _reparse_at(self, tree_offset):
        """Reparse the plain leaves either side of C{tree_offset} after an
        edit there. @returns: Whether new placeables were found."""
        parsers = self.placeables_controller.get_parsers_for_textbox(self)
        found = False
        for offset in sorted({max(tree_offset - 1, 0), tree_offset}):
            leaf = self.elem.elem_at_offset(offset)
            if leaf is None or type(leaf) is not StringElem or not leaf.isleaf():
                continue
            elem_parse(leaf, parsers)
            if not leaf.isleaf():
                leaf.gui_info = None
                self.add_default_gui_info(leaf)
                found = True
        return found

    def _on_delete_range(self, buffer, start_iter, end_iter):
        if self.elem is None:
            return
        before = self.elem.copy()

        cursor_pos = self.refresh_cursor_pos
        if cursor_pos < 0:
            cursor_pos = self.buffer.props.cursor_position

        start_offset = start_iter.get_offset()
        end_offset = end_iter.get_offset()

        start_elem = self.elem.gui_info.elem_at_offset(start_offset)
        if start_elem is None:
            return
        start_elem_offset = self.elem.gui_info.index(start_elem)
        # end_elem can be None if end_offset == self.elem.gui_info.length()
        end_elem = self.elem.gui_info.elem_at_offset(end_offset)

        # Per definition of a selection, cursor_pos must be at either
        # start_offset or end_offset
        key_is_delete = cursor_pos == start_offset
        done = False
        deleted, parent, index = None, None, None

        if abs(start_offset - end_offset) == 1:
            done, deleted, parent, index, span = self._delete_at_placeable_boundary(
                start_elem, start_elem_offset, cursor_pos, key_is_delete
            )
            if span is not None:
                # Make GTK delete the whole placeable, widgets included.
                start_offset, end_offset = span
                start_iter.set_offset(start_offset)
                end_iter.set_offset(end_offset)
            elif done:
                # Nothing to delete here: GTK mustn't delete the widget either.
                buffer.stop_emission_by_name('delete-range')
                return

        structural = False
        start_tree_offset = None
        if not done:
            start_tree_offset = self.elem.gui_info.gui_to_tree_index(start_offset)
            deleted, parent, index, structural = self._delete_tree_range(
                start_elem, end_elem, start_tree_offset, end_offset
            )
            self.refresh_cursor_pos = start_offset

        if index is None:
            index = start_offset

        if deleted is not None:
            self._finish_delete(
                before, deleted, parent, index, start_iter, end_iter, structural, start_tree_offset
            )

    def _finish_delete(
        self, before, deleted, parent, index, start_iter, end_iter, structural, reparse_offset
    ):
        self.elem.prune()
        if reparse_offset is not None and self._reparse_at(reparse_offset):
            structural = True
        # The tree may lose more than GTK deletes (a whole fragile
        # placeable), or the deletion may change which placeables
        # there are: the buffer then needs rebuilding from the tree.
        remaining = self.get_text(None, start_iter) + self.get_text(end_iter, None)
        remaining_len = self.buffer.get_char_count() - (
            end_iter.get_offset() - start_iter.get_offset()
        )
        if structural or not self._matches_tree(remaining, remaining_len):
            self._structural_change_pending = True
        self.elem_before_edit = before
        self.emit(
            'text-deleted', deleted, parent, index, self.buffer.props.cursor_position, self.elem
        )

    def _delete_tree_range(self, start_elem, end_elem, start_tree_offset, end_offset):
        """Delete the tree range from start_tree_offset to buffer offset
        end_offset. Returns (deleted, parent, index, structural): index
        is the deletion's tree offset, structural whether placeables
        were unlocked."""
        end_tree_offset = self.elem.gui_info.gui_to_tree_index(end_offset)
        structural = False
        if unlock_partly_covered_leaves(self.elem, start_tree_offset, end_tree_offset):
            self.add_default_gui_info(self.elem)
            structural = True
        if (
            start_elem is end_elem
            and start_elem.isleaf()
            and start_elem.iseditable
            and (start_elem.gui_info.has_start_widget() or start_elem.gui_info.has_end_widget())
        ):
            deleted, parent, index = self._delete_inside_widget_leaf(
                start_elem, start_tree_offset, end_tree_offset
            )
        else:
            deleted, parent, index = self.elem.delete_range(start_tree_offset, end_tree_offset)

        if index is not None:
            parent_offset = self.elem.elem_offset(parent)
            if parent_offset < 0:
                # prune() merged the leaf it was deleted from into a sibling.
                index = start_tree_offset
            else:
                index = parent_offset + index
        return deleted, parent, index, structural

    def _on_insert_text(self, buffer, iter, ins_text, length):
        if self.elem is None:
            return

        # length counts UTF-8 bytes. Backspace after a combining mark (Indic
        # vowel signs, Arabic harakat) deletes the whole cluster, then
        # reinserts it with a length that leaves out the last character.
        ins_text = forceunicode(ins_text).encode('utf-8')[:length].decode('utf-8')
        if self.text_filter is not None:
            filtered = self.text_filter(ins_text)
            if filtered != ins_text:
                # Insert the filtered text instead; this handler runs again for it.
                buffer.stop_emission_by_name('insert-text')
                if filtered:
                    buffer.insert(iter, filtered)
                return
        before = self.elem.copy()
        buff_offset = iter.get_offset()
        gui_info = self.elem.gui_info
        tree_offset = gui_info.gui_to_tree_index(buff_offset)
        unlocked = unlock_partly_covered_leaves(self.elem, tree_offset, tree_offset)
        if unlocked:
            self.add_default_gui_info(self.elem)
        left = gui_info.elem_at_offset(buff_offset - 1)
        right = gui_info.elem_at_offset(buff_offset)

        # logging.debug('"%s[[%s]]%s" | elem=%s[%d] | left=%s right=%s' % (
        #    buffer.get_text(buffer.get_start_iter(), iter),
        #    ins_text,
        #    buffer.get_text(iter, buffer.get_end_iter()),
        #    repr(self.elem), buff_offset,
        #    repr(left), repr(right)
        # ))

        inside = self._insert_inside_placeable(iter, ins_text)
        succeeded = bool(inside)
        if (
            inside is None
            and not (left is None and right is None)
            and (left is not right or not str(left))
        ):
            succeeded = self.elem.insert_between(left, right, ins_text)
            # logging.debug('self.elem.insert_between(%s, %s, "%s"): %s' % (repr(left), repr(right), ins_text, succeeded))
        if not succeeded and inside is None:
            succeeded = self.elem.insert(tree_offset, ins_text)
            # logging.debug('self.elem.insert(%d, "%s"): %s' % (tree_offset, ins_text, succeeded))

        if succeeded:
            self.elem.prune()
            cursor_pos = self.refresh_cursor_pos
            if cursor_pos < 0:
                cursor_pos = self.buffer.props.cursor_position
            cursor_pos += len(ins_text)
            self.refresh_cursor_pos = cursor_pos
            self._live_insert_pending = (tree_offset, unlocked)
            # logging.debug('text-inserted: %s@%d of %s' % (ins_text, iter.get_offset(), repr(self.elem)))
            self.elem_before_edit = before
            self.emit('text-inserted', ins_text, buff_offset, self.elem)
        else:
            # GTK still inserts the text: rebuild the buffer from the tree.
            self._structural_change_pending = True

    def _insert_inside_placeable(self, itr, text):
        """Insert C{text} into the paired placeable (<g>) whose opening
        widget is just before C{itr} or closing widget just after it:
        the tree can't tell those positions from just outside it.
        @returns: None if C{itr} isn't at either, else whether the
        text was inserted."""
        gui_info = self.elem.gui_info
        offset = itr.get_offset()
        for widget_offset, widget_index in ((offset, 1), (offset - 1, 0)):
            if widget_offset < 0:
                continue
            anchor = self.buffer.get_iter_at_offset(widget_offset).get_child_anchor()
            if anchor is None:
                continue
            elem = gui_info.elem_at_offset(widget_offset)
            if (
                elem is None
                or len(elem.gui_info.widgets) < 2
                or not any(w is elem.gui_info.widgets[widget_index] for w in anchor.get_widgets())
            ):
                continue
            if not elem.iseditable:
                return False
            if elem.isleaf():
                content = str(elem)
                elem.sub = [content + text if widget_index else text + content]
                return True
            return elem.insert(len(elem) if widget_index else 0, text)
        return None

    def _on_delete_range_after(self, buffer, start, end):
        # The characters either side may have become a line's first or last.
        self.make_room(start.get_offset() - 1, start.get_offset() + 1)

    def _on_insert_text_after(self, buffer, iter, ins_text, length):
        typed = len(forceunicode(ins_text).encode('utf-8')[:length].decode('utf-8'))
        if self._pilcrow_tag is not None:
            # Text typed between two line breaks is inside their tag.
            start = buffer.get_iter_at_offset(iter.get_offset() - typed)
            buffer.remove_tag(self._pilcrow_tag, start, iter)
        # The characters either side may have become a line's first or last.
        self.make_room(iter.get_offset() - typed - 1, iter.get_offset() + 1)
        pending = getattr(self, '_live_insert_pending', None)
        self._live_insert_pending = None
        if pending is None or self.elem is None:
            return
        tree_offset, unlocked = pending

        affected_leaf = self.elem.elem_at_offset(tree_offset)
        if affected_leaf is None:
            return

        # Character count is unchanged by reparse - only node structure is -
        # so any GUI-length growth here is purely new widgets, and the
        # cursor needs correcting by exactly that.
        plain_len = len(str(affected_leaf))
        elem_parse(affected_leaf, self.placeables_controller.get_parsers_for_textbox(self))

        if affected_leaf.isleaf() and not unlocked:
            return

        affected_leaf.gui_info = None
        self.add_default_gui_info(affected_leaf)

        cursor_pos = self.refresh_cursor_pos
        if cursor_pos < 0:
            cursor_pos = self.buffer.props.cursor_position
        target_cursor_pos = cursor_pos + (affected_leaf.gui_info.length() - plain_len)
        self.refresh_cursor_pos = target_cursor_pos

        scheduled_elem = self.elem
        self._render_pending = True

        def do_refresh():
            self._render_pending = False
            if self.elem is scheduled_elem:
                self.refresh(update=True)
                self.place_cursor(target_cursor_pos)
            return False

        GLib.idle_add(do_refresh)

    def _on_key_pressed(self, widget, event, *args):
        evname = None

        if self._completion_popup is not None and self._completion_popup.is_showing():
            if self._on_completion_key(event):
                return True

        if self.suggestion_is_visible():
            if event.keyval == Gdk.KEY_Tab:
                self._accept_visible_suggestion()
                return True
            self.suggestion = None

        # Uncomment the following block to get nice textual logging of key presses in the textbox
        # keyname = '<unknown>'
        # for attr in dir(Gtk.keysyms):
        #    if getattr(Gtk.keysyms, attr) == event.keyval:
        #        keyname = attr
        # statenames = []
        # for attr in [a for a in ('MOD1_MASK', 'MOD2_MASK', 'MOD3_MASK', 'MOD4_MASK', 'MOD5_MASK', 'CONTROL_MASK', 'SHIFT_MASK', 'RELEASE_MASK', 'LOCK_MASK', 'SUPER_MASK', 'HYPER_MASK', 'META_MASK')]:
        #    if event.get_state() & getattr(Gtk.gdk, attr):
        #        statenames.append(attr)
        # statenames = '|'.join(statenames)
        # logging.debug('Key pressed: %s (%s)' % (keyname, statenames))
        # logging.debug('state (raw): %x' % (event.get_state(),))

        # Filter out unimportant flags that is present with other keyboard
        # layouts and input methods. The following has been encountered:
        # * MOD2_MASK - Num Lock (bug 926)
        # * LEAVE_NOTIFY_MASK - Arabic keyboard layout (?) (bug 926)
        # * 0x2000000 - IBus input method (bug 1281)
        filtered_state = event.get_state() & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.MOD1_MASK
            | Gdk.ModifierType.MOD4_MASK
            | Gdk.ModifierType.SHIFT_MASK
        )

        for name, keyslist in self.SPECIAL_KEYS.items():
            for keyval, state in keyslist:
                if event.keyval == keyval and filtered_state == state:
                    evname = name

        return self.emit('key-pressed', event, evname)

    _LIST_KEYS = {
        Gdk.KEY_Up: 'up',
        Gdk.KEY_KP_Up: 'up',
        Gdk.KEY_Down: 'down',
        Gdk.KEY_KP_Down: 'down',
        Gdk.KEY_Tab: 'tab',
        Gdk.KEY_Return: 'enter',
        Gdk.KEY_KP_Enter: 'enter',
        Gdk.KEY_Escape: 'escape',
    }

    def _on_completion_key(self, event):
        """Handle a key press while the completion popup is showing.
        Returns C{True} if the key was used by the popup."""
        if event.is_modifier:
            return True
        state = event.get_state() & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.MOD1_MASK
            | Gdk.ModifierType.MOD4_MASK
            | Gdk.ModifierType.SHIFT_MASK
        )
        key = None if state else self._LIST_KEYS.get(event.keyval)
        if key is not None:
            return self._on_list_key(key)
        popup = self._completion_popup
        typing = not state & (
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.MOD4_MASK
        )
        if self._list_kind == 'insert' and typing:
            # Typing filters the term's translations.
            GLib.idle_add(self._filter_insert_list)
        elif self._list_kind == 'suggestion':
            # The next character's completions refill the list.
            popup.dismiss_soon()
        else:
            popup.dismiss()
        return False

    def _on_list_key(self, key):
        """Up, Down, Tab, Enter or Escape while the list shows; whether
        the list used it."""
        popup = self._completion_popup
        if key in ('up', 'down'):
            popup.move_selection(-1 if key == 'up' else 1)
            return True
        # Enter accepts a completion only once picked with Up or Down, so
        # that Enter after typing a word still goes on to the next unit.
        if key == 'tab' or (key == 'enter' and (self._list_kind == 'insert' or popup.navigated)):
            popup.accept()
            return True
        popup.dismiss()
        if key == 'escape':
            if self._list_kind == 'suggestion':
                self.suggestion = None
            return True
        return False

    def _on_event_remove_suggestion(self, *args):
        if self._completion_popup is not None:
            self._completion_popup.dismiss()
        self.suggestion = None
        self.refresh_cursor_pos = -1

    # SPECIAL METHODS #
    def __repr__(self):
        return '<TextBox %x %s "%s">' % (id(self), self.role, str(self.elem))
