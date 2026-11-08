#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from gi.repository import Atk, Gdk, GLib, GObject, Gtk, Pango

from virtaal.views import rendering


def popup_position(anchor, size, bounds, rtl=False):
    """Screen position for a popup of C{size} (width, height) next to the
    C{anchor} rectangle (x, y, width, height) - below it by default, above
    it when below doesn't fit - kept inside C{bounds} (x, y, width, height)
    when given."""
    ax, ay, _aw, ah = anchor
    width, height = size
    x = ax - width if rtl else ax
    y = ay + ah
    if bounds is None:
        return x, y

    bx, by, bw, bh = bounds
    if y + height > by + bh and ay - height >= by:
        y = ay - height
    x = max(bx, min(x, bx + bw - width))
    return x, y


def announce(accessible, text):
    """Have a screen reader say text, from accessible: ATK's
    notification (2.50 on), else its older announcement."""
    for signal, args in (('notification', (text, Atk.Live.POLITE)), ('announcement', (text,))):
        if GObject.signal_lookup(signal, accessible):
            accessible.emit(signal, *args)
            return True
    return False


def _window_geometry(toplevel):
    """(x, y, width, height) of toplevel as its configure-event gives it,
    or None before it has a window."""
    window = toplevel.get_window()
    if window is None:
        return None
    origin = window.get_origin()
    return (origin.x, origin.y, window.get_width(), window.get_height())


class CompletionPopup(Gtk.Window):
    """
    A list of candidate strings shown at a text view's cursor.

    It is a popup window, so it never takes keyboard focus: the text view
    keeps it and forwards navigation keys with L{move_selection},
    L{accept} and L{dismiss}.
    """

    MAX_VISIBLE_ROWS = 8
    SETTLE_MS = 200

    # INITIALIZERS #
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_type_hint(Gdk.WindowTypeHint.COMBO)
        self.set_destroy_with_parent(True)
        self.get_style_context().add_class('completion-popup')
        self._on_accept = None
        self._on_select = None
        self._textview = None
        self._offset = 0
        self._configure_id = None
        self._dismiss_id = None
        self._settle_id = None
        self._toplevel_geometry = None
        self._announced = None

        self._candidates = []
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.set_can_focus(False)
        self.listbox.connect('row-activated', self._on_row_activated)
        accessible = self.listbox.get_accessible()
        # l10n: The list of completions shown while typing a translation
        accessible.set_name(_('Suggestions'))
        accessible.set_role(Atk.Role.LIST)
        self.get_accessible().set_name(_('Suggestions'))

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.set_shadow_type(Gtk.ShadowType.IN)
        self.scrolled_window.add(self.listbox)
        self.add(self.scrolled_window)

    # METHODS #
    def is_showing(self):
        return self._on_accept is not None

    def show_at(self, textview, offset, candidates, on_accept, on_select=None, selected=0):
        """Show C{candidates} below the character at C{offset} in
        C{textview}; C{on_accept} is called with the chosen string, and
        C{on_select}, if given, with the index of each newly selected
        one. Shown already for C{textview}, the list is refilled in
        place, so typing that filters it doesn't make it flicker."""
        self._cancel_dismiss()
        if not candidates:
            self.dismiss()
            return
        if self._textview is not textview:
            self.dismiss()

        font = rendering.get_role_font_description(getattr(textview, 'role', None))
        self._fill(candidates, font or rendering.get_target_font_description())
        self._on_select = None
        self.select_index(selected)

        self._on_accept = on_accept
        self._on_select = on_select
        self._offset = offset
        if self._textview is None:
            self._textview = textview
            toplevel = textview.get_toplevel()
            if isinstance(toplevel, Gtk.Window):
                self.set_transient_for(toplevel)
                self.set_attached_to(textview)
                # Follow the main window when it moves or resizes (#3924).
                self._configure_id = toplevel.connect(
                    'configure-event', self._on_configure_toplevel
                )
                self._toplevel_geometry = _window_geometry(toplevel)

        self.scrolled_window.show_all()
        self._update_geometry(textview, offset)
        self.show()
        self._announce()

    def select_index(self, index):
        if not self._candidates:
            return
        index = max(0, min(index, len(self._candidates) - 1))
        row = self.listbox.get_row_at_index(index)
        changed = row is not self.listbox.get_selected_row()
        self.listbox.select_row(row)
        if changed and self._on_select is not None:
            self._on_select(index)
        if changed and self.is_showing():
            self._announce()
        alloc = row.get_allocation()
        if alloc.height > 1:
            self.scrolled_window.get_vadjustment().clamp_page(alloc.y, alloc.y + alloc.height)

    def _announce(self):
        """Have a screen reader say the selected candidate, and where it
        is in the list, when it changed: the list never has the focus
        that would make it speak."""
        index = self.selected_index()
        if index is None or self._textview is None:
            return
        candidate = self._candidates[index]
        if candidate == self._announced:
            return
        self._announced = candidate
        text = _(
            # l10n: Said by a screen reader for the suggestion picked in the
            # list of suggestions while typing: "Kanselleer, 2 of 3"
            '%(candidate)s, %(position)d of %(count)d'
        ) % {'candidate': candidate, 'position': index + 1, 'count': len(self._candidates)}
        announce(self._textview.get_accessible(), text)

    def selected_index(self):
        row = self.listbox.get_selected_row()
        return None if row is None else row.get_index()

    def move_selection(self, delta):
        index = self.selected_index()
        self.select_index(delta if index is None else index + delta)

    def accept(self):
        """Dismiss and pass the selected candidate to the accept callback."""
        index = self.selected_index()
        on_accept = self._on_accept
        self.dismiss()
        if on_accept is not None and index is not None:
            on_accept(self._candidates[index])

    def dismiss_soon(self):
        """Dismiss once pending idle work is done, unless shown again
        first: a typed character dismisses a list of completions that
        the next character's completions would refill."""
        if self._dismiss_id is None and self.is_showing():
            # After the next character's completions, which come at a
            # higher priority and show the list again.
            self._dismiss_id = GLib.idle_add(
                self._dismiss_now, priority=GLib.PRIORITY_DEFAULT_IDLE + 20
            )

    def _dismiss_now(self):
        self._dismiss_id = None
        self.dismiss()
        return False

    def _cancel_dismiss(self):
        if self._dismiss_id is not None:
            GLib.source_remove(self._dismiss_id)
            self._dismiss_id = None

    def dismiss(self):
        self._cancel_dismiss()
        if self._settle_id is not None:
            GLib.source_remove(self._settle_id)
            self._settle_id = None
        self._toplevel_geometry = None
        self._announced = None
        if self._configure_id is not None:
            self._textview.get_toplevel().disconnect(self._configure_id)
            self._configure_id = None
        self._on_accept = None
        self._on_select = None
        self._textview = None
        self.hide()
        # On macOS a hidden transient window can be re-shown with its parent
        # (e.g. after dragging the main window); dropping the native window
        # rules that out.
        if self.get_realized():
            self.unrealize()

    def _fill(self, candidates, font):
        for row in self.listbox.get_children():
            row.destroy()
        self._candidates = list(candidates)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_font_desc_new(font))
        for candidate in self._candidates:
            label = Gtk.Label(label=candidate, xalign=0)
            label.set_attributes(attrs)
            label.set_margin_start(4)
            label.set_margin_end(4)
            row = Gtk.ListBoxRow()
            row.set_can_focus(False)
            row.get_accessible().set_name(candidate)
            row.add(label)
            row.show_all()
            self.listbox.add(row)

    def _update_geometry(self, textview, offset):
        gdkwin = textview.get_window(Gtk.TextWindowType.WIDGET)
        if gdkwin is None:
            return
        rect = textview.get_iter_location(textview.get_buffer().get_iter_at_offset(offset))
        wx, wy = textview.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x, rect.y)
        origin = gdkwin.get_origin()
        anchor = (origin.x + wx, origin.y + wy, rect.width, rect.height)

        _minimum, natural = self.listbox.get_preferred_size()
        row_height = natural.height // max(len(self._candidates), 1)
        # A vertical scrollbar's minimum length leaves blank space under a
        # short list, so only scroll when needed.
        if len(self._candidates) > self.MAX_VISIBLE_ROWS:
            visible_height = row_height * self.MAX_VISIBLE_ROWS
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.scrolled_window.set_size_request(-1, visible_height)
        else:
            visible_height = natural.height
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            self.scrolled_window.set_size_request(-1, -1)
        border = self.scrolled_window.get_style_context().get_border(Gtk.StateFlags.NORMAL)
        width = natural.width + border.left + border.right
        height = visible_height + border.top + border.bottom
        # Shrink from a previous, longer list. A shown window doesn't
        # shrink everywhere (macOS), so one that changes size is shown
        # again; a list refilled at the same size isn't, so it doesn't
        # flicker as typing filters it.
        if self.get_visible() and tuple(self.get_size()) != (width, height):
            self.hide()
            # A fresh native window takes the new size for sure (X11 kept
            # the old one under load).
            self.unrealize()
        self.resize(width, height)

        bounds = None
        toplevel_window = textview.get_toplevel().get_window()
        if toplevel_window is not None:
            frame = toplevel_window.get_frame_extents()
            bounds = (frame.x, frame.y, frame.width, frame.height)

        rtl = textview.get_direction() == Gtk.TextDirection.RTL
        x, y = popup_position(anchor, (width, height), bounds, rtl)
        self.move(x, y)

    # EVENT HANDLERS #
    def _on_configure_toplevel(self, toplevel, event):
        # As the TM suggestions do (#3924): hidden while the main window
        # moves or resizes, and shown again where the cursor now is once
        # it has stayed put for SETTLE_MS.
        geometry = (event.x, event.y, event.width, event.height)
        if geometry == self._toplevel_geometry:
            return False
        self._toplevel_geometry = geometry
        self.hide()
        if self._settle_id is not None:
            GLib.source_remove(self._settle_id)
        self._settle_id = GLib.timeout_add(self.SETTLE_MS, self._follow)
        return False

    def _follow(self):
        self._settle_id = None
        if self._textview is not None and self.is_showing():
            self._update_geometry(self._textview, self._offset)
            self.show()
        return False

    def _on_row_activated(self, listbox, row):
        self.select_index(row.get_index())
        self.accept()
