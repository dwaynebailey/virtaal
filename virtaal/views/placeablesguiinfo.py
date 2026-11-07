#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from gi.repository import Gtk, Pango
from translate.storage.placeables import StringElem, base, general, xliff

from virtaal.support import invisiblechars
from virtaal.views import theme
from virtaal.views.rendering import (
    get_role_font_description,
    make_pango_layout,
    set_widget_font,
)


def _count_anchors(buffer, itr):
    anchor_text = buffer.get_slice(buffer.get_start_iter(), itr, include_hidden_chars=True)
    return anchor_text.count('\ufffc')


class StringElemGUI:
    """
    A convenient container for all GUI properties of a L{StringElem}.
    """

    # MEMBERS #
    fg = '#000'  # See update_style
    """The current foreground colour.
        @see update_style"""
    bg = '#fff'
    """The current background colour.
        @see update_style"""

    cursor_allowed = True
    """Whether the cursor is allowed to enter this element."""

    # INITIALIZERS #
    def __init__(self, elem, textbox, **kwargs):
        if not isinstance(elem, StringElem):
            raise ValueError('"elem" parameter must be a StringElem.')
        self.elem = elem
        self.textbox = textbox
        self.widgets = []
        self.create_repr_widgets()

        attribs = ('fg', 'bg', 'cursor_allowed')
        for kw in kwargs:
            if kw in attribs:
                setattr(self, kw, kwargs[kw])

    # METHODS #
    def create_tags(self):
        tag = Gtk.TextTag()
        if self.fg:
            if isinstance(self.fg, str):
                tag.props.foreground = self.fg
            else:
                tag.props.foreground_rgba = self.fg

        if self.bg:
            if isinstance(self.bg, str):
                tag.props.background = self.bg
            else:
                tag.props.background_rgba = self.bg

        return [(tag, None, None)]

    def create_repr_widgets(self):
        """Creates the two widgets that are rendered before and after the
        contained string. The widgets should be placed in C{self.widgets}."""
        return None

    def copy(self):
        return self.__class__(
            elem=self.elem,
            textbox=self.textbox,
            fg=self.fg,
            bg=self.bg,
            cursor_allowed=self.cursor_allowed,
        )

    def elem_at_offset(self, offset, child_offset=0):
        """Find the C{StringElem} at the given offset.
        This method is used in Virtaal as a replacement for
        C{StringElem.elem_at_offset}, because this method takes the rendered
        widgets into account.

        @type  offset: int
        @param offset: The offset into C{self.textbox} to find the the element
        @type  child_offset: int
        @param child_offset: The offset of C{self.elem} into the buffer of
            C{self.textbox}. This is so recursive calls to child elements
            can be aware of where in the textbox it appears."""
        if offset < 0 or offset >= self.length():
            return None

        # First check if offset doesn't point to a widget that does not belong to self.elem
        if offset in (0, self.length() - 1):
            widget = self._widget_at(child_offset + offset)
            if widget is not None:
                if any(w is widget for w in self.widgets):
                    return self.elem
                if self.elem.isleaf():
                    # If there's a widget at {offset}, but it does not belong to this widget or
                    # any of its children (it's a leaf, so no StringElem children), the widget
                    # can't be part of the sub-tree with {self.elem} at the root.
                    return None

        if self.elem.isleaf():
            return self.elem

        pre_len = self.has_start_widget() and 1 or 0
        return self._child_elem_at_offset(offset - pre_len, child_offset + pre_len)

    def _widget_at(self, buffer_offset):
        """The first widget anchored at buffer_offset, if any."""
        anchor = self.textbox.buffer.get_iter_at_offset(buffer_offset).get_child_anchor()
        if anchor is None:
            return None
        widgets = anchor.get_widgets()
        return widgets[0] if widgets else None

    def _child_elem_at_offset(self, offset, child_offset):
        childlen = 0  # Length of the children already consumed
        for child in self.elem.sub:
            if not isinstance(child, StringElem):
                if offset <= len(child):
                    return self.elem
                childlen += len(child)
                continue
            if not hasattr(child, 'gui_info'):
                gui_info_class = self.textbox.placeables_controller.get_gui_info(child)
                child.gui_info = gui_info_class(elem=child, textbox=self.textbox)
            try:
                elem = child.gui_info.elem_at_offset(
                    offset - childlen, child_offset=child_offset + childlen
                )
                if elem is not None:
                    return elem
            except AttributeError:
                pass
            childlen += child.gui_info.length()
        return None

    def get_insert_candidates(self):
        """Strings to choose from when inserting this placeable, or C{None}
        to insert its translation directly."""
        return None

    def gui_to_tree_index(self, index):
        # The difference between a GUI offset and a tree offset is the iter-
        # consuming widgets in the text box. So we just iterate from the start
        # of the text buffer and count the positions without widgets.

        if index == 0:
            return 0

        if self.elem.isleaf() and len(self.widgets) == 0:
            return index

        # buffer might contain anchors
        buffer = self.textbox.buffer
        anchors = _count_anchors(buffer, buffer.get_iter_at_offset(index))
        return index - anchors

    def has_start_widget(self):
        return len(self.widgets) > 0 and self.widgets[0]

    def has_end_widget(self):
        return len(self.widgets) > 1 and self.widgets[1]

    def index(self, elem):
        """Replacement for C{StringElem.elem_offset()} to be aware of included
        widgets."""
        if elem is self.elem:
            return 0

        i = 0
        if self.has_start_widget():
            i = 1
        for child in self.elem.sub:
            if isinstance(child, StringElem):
                index = child.gui_info.index(elem)
                if index >= 0:
                    return index + i
                i -= index  # minus the child's length, see below
            else:
                i += len(child)
        # We basically calculated the length thus far, so pass it back as a
        # negative number to avoid having to call .length() as well. Big
        # performance win in very complex trees.
        if self.has_end_widget():
            i += 1
        return -i

    def iter_sub_with_index(self):
        i = 0
        if self.has_start_widget():
            i = 1
        for child in self.elem.sub:
            yield (child, i)
            if hasattr(child, 'gui_info'):
                i += child.gui_info.length()
            else:
                i += len(child)

    def length(self):
        """Calculate the length of the current element, taking into account
        possibly included widgets."""
        length = len([w for w in self.widgets if w is not None])
        for child in self.elem.sub:
            if isinstance(child, StringElem) and hasattr(child, 'gui_info'):
                length += child.gui_info.length()
            else:
                length += len(child)
        return length

    def render(self, offset=-1):
        """Render the string element string and its associated widgets."""
        buffer = self.textbox.buffer
        if offset < 0:
            offset = 0
            buffer.set_text('')

        if self.has_start_widget():
            anchor = buffer.create_child_anchor(buffer.get_iter_at_offset(offset))
            self.textbox.add_child_at_anchor(self.widgets[0], anchor)
            self.widgets[0].show()
            offset += 1

        for child in self.elem.sub:
            if isinstance(child, StringElem):
                child.gui_info.render(offset)
                offset += child.gui_info.length()
            else:
                buffer.insert(buffer.get_iter_at_offset(offset), child)
                offset += len(child)

        if self.has_end_widget():
            anchor = buffer.create_child_anchor(buffer.get_iter_at_offset(offset))
            self.textbox.add_child_at_anchor(self.widgets[1], anchor)
            self.widgets[1].show()
            offset += 1

        return offset

    def treeindex_to_iter(self, index, start_at=None, elem_len=None):
        """Convert the tree index to a gtk iterator. The optional start_at
        indicates a reference point (index, iter) from where to start looking,
        for example a previous index that is known to have occurred earlier.
        A caller converting many indexes can pass len(self.elem) as
        elem_len: computing it walks the whole tree (#4343)."""
        buffer = self.textbox.buffer
        if index == 0:
            return buffer.get_start_iter()
        if elem_len is None:
            elem_len = len(self.elem)
        if index >= elem_len and self.elem is self.textbox.elem:
            return buffer.get_end_iter()

        if self.elem.isleaf() and len(self.widgets) == 0:
            return buffer.get_iter_at_offset(index)

        if start_at:
            (char_counter, itr) = start_at
            itr = itr.copy()
            assert char_counter <= index
        else:
            itr = buffer.get_iter_at_offset(index)
            anchors = _count_anchors(buffer, itr)
            char_counter = index - anchors
        while char_counter <= index and not itr.is_end():
            anchor = itr.get_child_anchor()
            if anchor is None or not anchor.get_widgets():
                char_counter += 1
            itr.forward_char()
        itr.backward_char()
        return itr


class PhGUI(StringElemGUI):
    fg = theme.current_theme['markup_warning_fg']
    bg = theme.current_theme['ph_placeable_bg']


class BxGUI(StringElemGUI):
    bg = '#E6E6FA'

    def create_repr_widgets(self):
        self.widgets.append(Gtk.Label(label='(('))

        for lbl in self.widgets:
            font_desc = get_role_font_description(self.textbox.role)
            set_widget_font(lbl, font_desc)
            self.textbox.get_pango_context().set_font_description(font_desc)
            w, h = make_pango_layout(self.textbox, '((', 100).get_pixel_size()
            lbl.set_size_request(-1, int(h / 1.2))


class ExGUI(StringElemGUI):
    bg = '#E6E6FA'

    def create_repr_widgets(self):
        self.widgets.append(Gtk.Label(label='))'))

        for lbl in self.widgets:
            font_desc = get_role_font_description(self.textbox.role)
            set_widget_font(lbl, font_desc)
            self.textbox.get_pango_context().set_font_description(font_desc)
            w, h = make_pango_layout(self.textbox, '))', 100).get_pixel_size()
            lbl.set_size_request(-1, int(h / 1.2))


class NewlineGUI(StringElemGUI):
    """A newline, with a pilcrow the text box draws at the line's end:
    a widget per newline is slow for thousands of lines."""

    fg = theme.current_theme['subtle_fg']

    def create_tags(self):
        # Only for other colours than the pilcrow's own (selected).
        if self.fg == NewlineGUI.fg and self.bg == StringElemGUI.bg:
            return []
        tags = super().create_tags()
        for tag, _start, _end in tags:
            tag.colours_pilcrow = True
        return tags

    def render(self, offset=-1):
        end = super().render(offset)
        self.textbox.mark_pilcrow(end - self.length(), end)
        return end


class InvisibleGUI(StringElemGUI):
    """A character drawn as a marker by the text box (see
    L{invisiblechars}), with a tag only while selected."""

    fg = theme.current_theme['subtle_fg']

    def create_tags(self):
        if self.fg == InvisibleGUI.fg and self.bg == StringElemGUI.bg:
            return []
        tags = super().create_tags()
        for tag, _start, _end in tags:
            tag.colours_pilcrow = True
        return tags


class UrlGUI(StringElemGUI):
    fg = theme.current_theme['url_fg']

    def create_tags(self):
        tag = Gtk.TextTag()
        tag.props.foreground = self.fg
        tag.props.background = self.bg
        tag.props.underline = Pango.Underline.SINGLE
        return [(tag, None, None)]


class GPlaceableGUI(StringElemGUI):
    bg = '#ffd27f'

    def create_repr_widgets(self):
        self.widgets.append(Gtk.Label(label='<'))
        self.widgets.append(Gtk.Label(label='>'))
        if self.elem.id:
            self.widgets[0].set_text('<%s|' % (self.elem.id))

        for lbl in self.widgets:
            font_desc = get_role_font_description(self.textbox.role)
            set_widget_font(lbl, font_desc)
            self.textbox.get_pango_context().set_font_description(font_desc)
            w, h = make_pango_layout(self.textbox, '<foo>', 100).get_pixel_size()
            lbl.set_size_request(-1, int(h / 1.2))


class XPlaceableGUI(StringElemGUI):
    bg = '#ff7fef'

    def create_repr_widgets(self):
        lbl = Gtk.Label(label='[]')
        self.widgets.append(lbl)
        if self.elem.id:
            lbl.set_text('[%s]' % (self.elem.id))

        font_desc = get_role_font_description(self.textbox.role)
        set_widget_font(lbl, font_desc)
        self.textbox.get_pango_context().set_font_description(font_desc)
        w, h = make_pango_layout(self.textbox, '[foo]', 100).get_pixel_size()
        lbl.set_size_request(-1, int(h / 1.2))


class UnknownXMLGUI(StringElemGUI):
    bg = '#add8e6'

    def create_repr_widgets(self):
        self.widgets.append(Gtk.Label(label='{'))
        self.widgets.append(Gtk.Label(label='}'))

        info = ''
        if self.elem.xml_node.tag:
            tag = self.elem.xml_node.tag
            if tag.startswith('{'):
                # tag is namespaced
                tag = tag[tag.index('}') + 1 :]
            info += tag + '|'
        # Uncomment the if's below for more verbose placeables
        # if self.elem.id:
        #    info += 'id=%s|'  % (self.elem.id)
        # if self.elem.rid:
        #    info += 'rid=%s|' % (self.elem.rid)
        # if self.elem.xid:
        #    info += 'xid=%s|' % (self.elem.xid)
        if info:
            self.widgets[0].set_text('{%s' % (info))

        for lbl in self.widgets:
            set_widget_font(lbl, get_role_font_description(self.textbox.role))
            w, h = make_pango_layout(self.textbox, '{foo}', 100).get_pixel_size()
            lbl.set_size_request(-1, int(h / 1.2))


def _code_labels(tag, code_id):
    """The labels the editor shows either side of an inline code."""
    code_id = code_id or ''
    if tag == 'g':
        return ('<%s|' % code_id if code_id else '<'), '>'
    if tag == 'x':
        return '[%s]' % code_id, None
    if tag == 'bx':
        return '((', None
    if tag == 'ex':
        return '))', None
    return '{%s|' % tag, '}'


_CODE_GUIS = {'g': GPlaceableGUI, 'x': XPlaceableGUI, 'bx': BxGUI, 'ex': ExGUI}


def code_marks(spans):
    """The inline codes spans (see L{inlinecodes.code_spans}) as codes for
    L{markup.markuptext}, labelled and coloured as the editor shows them."""
    marks = []
    for start, end, tag, code_id in spans or ():
        bg = _CODE_GUIS.get(tag, UnknownXMLGUI).bg
        attr = 'background="%s" foreground="%s"' % (bg, theme.readable_fg(bg))
        marks.append((start, end, *_code_labels(tag, code_id), attr))
    return marks


def update_style(widget):
    _style = widget.get_style_context()
    fg = _style.get_color(Gtk.StateType.NORMAL)
    bg = theme.base_color(_style)
    StringElemGUI.fg = theme.rgba_to_str(fg)
    StringElemGUI.bg = theme.rgba_to_str(bg)
    PhGUI.fg = theme.current_theme['markup_warning_fg']
    PhGUI.bg = theme.current_theme['ph_placeable_bg']
    UrlGUI.fg = theme.current_theme['url_fg']
    NewlineGUI.fg = theme.current_theme['subtle_fg']
    InvisibleGUI.fg = theme.current_theme['subtle_fg']


element_gui_map = [
    (general.NewlinePlaceable, NewlineGUI),
    (invisiblechars.InvisiblePlaceable, InvisibleGUI),
    (general.UrlPlaceable, UrlGUI),
    (general.EmailPlaceable, UrlGUI),
    (base.Ph, PhGUI),
    (base.Bx, BxGUI),
    (base.Ex, ExGUI),
    (base.G, GPlaceableGUI),
    (base.X, XPlaceableGUI),
    (xliff.UnknownXML, UnknownXMLGUI),
]
