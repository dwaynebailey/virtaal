#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The panel under the units that searches the translation memory for a
word or phrase and lists what it found."""

import re

from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from virtaal.common import pan_app

from .baseview import BaseView

COL_SOURCE, COL_TARGET, COL_FOUND_IN, COL_TOOLTIP = range(4)

HEIGHT = 200
MIN_HEIGHT = 100
MIN_LENGTH = 2


def plain_label(text):
    """A label's text without its access key ("S_ource", "源(_O)"): the
    panel is in the main window, whose Alt keys belong to its menus."""
    text = re.sub(r'\s*\(_[^)]\)', '', text)
    return re.sub(r'_(.)', r'\1', text)


def gtk_text(msgid):
    """GTK's own translation of msgid, kept out of virtaal.pot."""
    return GLib.dgettext('gtk30', msgid)


def row_tooltip(source, target, tooltip):
    """A row's tooltip: its whole source and translation, then where it
    is from."""
    return '\n'.join(part for part in (source, target, tooltip and '\n' + tooltip) if part)


def swap_widget(old, new):
    """Put new in old's place in its Gtk.Box or Gtk.Paned."""
    parent = old.get_parent()
    if isinstance(parent, Gtk.Paned):
        first = parent.get_child1() is old
        resize = parent.child_get_property(old, 'resize')
        shrink = parent.child_get_property(old, 'shrink')
        parent.remove(old)
        (parent.pack1 if first else parent.pack2)(new, resize, shrink)
        return
    expand, fill, padding, pack_type = parent.query_child_packing(old)
    position = parent.child_get_property(old, 'position')
    parent.remove(old)
    parent.pack_start(new, expand, fill, padding)
    parent.set_child_packing(new, expand, fill, padding, pack_type)
    parent.reorder_child(new, position)


class MatchList(Gtk.TreeView):
    """The matches: Edit > Copy (Cmd+C on macOS, which never reaches the
    list) emits copy-clipboard."""

    __gsignals__ = {
        'copy-clipboard': (GObject.SignalFlags.RUN_LAST | GObject.SignalFlags.ACTION, None, ()),
    }


class ConcordancePanel(BaseView):
    """The search row, the matches and what can be done with one, in a
    Gtk.Paned under the units."""

    def __init__(self, controller):
        self.controller = controller
        self.mainview = controller.main_controller.view
        self._searched = False
        self._restore = True
        self._handle = None
        self._restore_source = None
        self._map_id = None
        self._placed = None
        self._build_widgets()
        self._place_under_units()
        self._key_id = self.mainview.main_window.connect('key-press-event', self._on_window_key)

    def _build_widgets(self):
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.widget.set_border_width(4)
        self.widget.pack_start(self._search_row(), False, False, 0)
        self.widget.pack_start(self._match_results(), True, True, 0)
        self.widget.pack_start(self._action_row(), False, False, 0)
        self.widget.set_size_request(-1, MIN_HEIGHT)
        self.widget.show_all()
        self.widget.set_no_show_all(True)
        self.widget.hide()

    def _search_row(self):
        """The entry and its options, on more lines when the window
        is narrow."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ent_search = Gtk.SearchEntry()
        self.ent_search.set_placeholder_text(_("Text to find"))
        self.ent_search.get_accessible().set_name(_("Text to find"))
        self.ent_search.set_width_chars(10)
        self.ent_search.set_valign(Gtk.Align.CENTER)
        self.ent_search.connect('activate', self._on_start)
        self.ent_search.connect('changed', self._on_search_changed)
        box.pack_start(self.ent_search, True, True, 0)
        options = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False)
        options.set_min_children_per_line(1)
        options.set_max_children_per_line(4)
        options.set_column_spacing(6)
        self.chk_source = Gtk.CheckButton(label=plain_label(_("S_ource")), active=True)
        self.chk_target = Gtk.CheckButton(label=plain_label(_("_Translation")), active=True)
        self.chk_case = Gtk.CheckButton(label=plain_label(_('C_ase sensitive')))
        # l10n: Check box in the Translation Memory Search panel: find only whole words
        self.chk_whole = Gtk.CheckButton(label=plain_label(_("_Whole words")))
        for button in (self.chk_source, self.chk_target, self.chk_case, self.chk_whole):
            button.connect('toggled', self._on_option_toggled)
            options.add(button)
        for child in options.get_children():
            child.set_can_focus(False)
        box.pack_start(options, False, False, 0)
        return box

    def _match_results(self):
        self.liststore = Gtk.ListStore(str, str, str, str)
        self.treeview = MatchList(model=self.liststore)
        self.treeview.set_enable_search(False)
        self.treeview.set_tooltip_column(COL_TOOLTIP)
        # l10n: The name screen readers give the list of the Translation Memory Search panel
        self.treeview.get_accessible().set_name(_("Search Translation Memory"))
        self.treeview.connect('copy-clipboard', self._on_copy)
        self.treeview.connect('row-activated', self._on_row_activated)
        self.treeview.connect('key-press-event', self._on_list_key)
        self.treeview.get_selection().connect('changed', self._on_selection_changed)
        for title, col in ((_("Source"), COL_SOURCE), (_("Translation"), COL_TARGET)):
            renderer = Gtk.CellRendererText(
                ellipsize=Pango.EllipsizeMode.END, single_paragraph_mode=True
            )
            column = Gtk.TreeViewColumn(title, renderer, markup=col)
            column.set_resizable(True)
            column.set_expand(True)
            self.treeview.append_column(column)
        renderer = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.MIDDLE)
        # l10n: Column heading in the Translation Memory Search panel: the file
        # or source a translation is from
        column = Gtk.TreeViewColumn(_("Found in"), renderer, text=COL_FOUND_IN)
        column.set_resizable(True)
        column.set_min_width(60)
        self.treeview.append_column(column)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.treeview)
        return scrolled

    def _action_row(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.lbl_summary = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.lbl_summary.set_width_chars(4)
        box.pack_start(self.lbl_summary, True, True, 0)
        # l10n: Button in the Translation Memory Search panel: puts the
        # translation chosen at the cursor
        self.btn_insert = Gtk.Button(label=plain_label(_("_Insert")))
        self.btn_copy = Gtk.Button(label=plain_label(gtk_text('_Copy')))
        # l10n: Button in the Translation Memory Search panel: moves to the unit of an open file
        self.btn_go_to = Gtk.Button(label=plain_label(_("_Go to Unit")))
        for button, action in (
            (self.btn_insert, self.controller.insert),
            (self.btn_copy, self.controller.copy),
            (self.btn_go_to, self.controller.go_to),
        ):
            button.set_sensitive(False)
            button.connect('clicked', lambda _b, act=action: act(self.selected_index()))
            box.pack_start(button, False, False, 0)
        return box

    def _units(self):
        """The units with the navigation ribbon beside them: the child of
        the main window's box, or of the files panel's Gtk.Paned."""
        vbox = self.mainview.gui.get_object('vbox_main')
        units = self.mainview.gui.get_object('scrwnd_storeview')
        while units.get_parent() is not vbox and not isinstance(units.get_parent(), Gtk.Paned):
            units = units.get_parent()
        return units

    def _place_under_units(self):
        storeview = self.mainview.gui.get_object('scrwnd_storeview')
        self.units = self._units()
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        swap_widget(self.units, self.paned)
        self.paned.pack1(self.units, True, False)
        self.paned.pack2(self.widget, False, False)
        self.paned.connect('size-allocate', self._on_paned_allocated)
        self.widget.connect('size-allocate', self._on_panel_allocated)
        # The units are hidden for the welcome screen: so is the panel.
        self._storeview = storeview
        self._visible_id = storeview.connect(
            'notify::visible', lambda w, _p: self.paned.set_visible(w.get_visible())
        )
        self.paned.set_visible(storeview.get_visible())

    # METHODS #
    def show(self):
        if not self.widget.get_visible():
            self._restore = True
            self.widget.show()
            self._apply_height()

    def hide(self):
        self.widget.hide()

    close = hide

    @property
    def shown(self):
        return self.widget.get_visible()

    def has_focus(self):
        focus = self.mainview.main_window.get_focus()
        return focus is not None and focus.is_ancestor(self.widget)

    def focus_entry(self):
        self._when_mapped(self.ent_search.grab_focus)

    def focus_results(self):
        def focus():
            self.treeview.grab_focus()
            if self.selected_index() is None and len(self.liststore):
                self.treeview.set_cursor(Gtk.TreePath(0), None, False)

        self._when_mapped(focus)

    def _when_mapped(self, func):
        """Run func now, or once the panel just shown is on screen: the
        key that showed it is released to the focus, which GTK can't
        give an unrealized widget."""
        if self._map_id is not None:
            self.widget.disconnect(self._map_id)
            self._map_id = None
        if self.widget.get_mapped():
            func()
            return

        def on_map(_widget):
            self.widget.disconnect(self._map_id)
            self._map_id = None
            func()

        self._map_id = self.widget.connect('map', on_map)

    def destroy(self):
        if self._restore_source is not None:
            GLib.source_remove(self._restore_source)
        self.mainview.main_window.disconnect(self._key_id)
        self._storeview.disconnect(self._visible_id)
        # The units, or another panel's Gtk.Paned put round them since.
        units = self.paned.get_child1()
        self.paned.remove(units)
        if self.paned.get_parent() is not None:
            swap_widget(self.paned, units)
        self.paned.destroy()

    def search(self):
        return {
            'text': self.ent_search.get_text(),
            'case': self.chk_case.get_active(),
            'parts': self._search_parts(),
            'whole_words': self.chk_whole.get_active(),
        }

    def _search_parts(self):
        parts = (('source', self.chk_source), ('target', self.chk_target))
        return [part for part, button in parts if button.get_active()]

    def set_search(self, text, side):
        """Search for text, on side "source" or "target" only, or on the
        sides chosen before for None."""
        self._searched = False
        self.ent_search.set_text(text)
        if side is not None:
            self.chk_source.set_active(side == 'source')
            self.chk_target.set_active(side == 'target')

    def can_start(self):
        text = self.ent_search.get_text().strip()
        return len(text) >= MIN_LENGTH and bool(self._search_parts())

    def started_search(self):
        self._searched = True
        self.set_rows([])
        self.lbl_summary.set_text('')

    def set_rows(self, rows):
        """List rows, (source markup, translation markup, found in,
        tooltip markup) each, keeping the row selected if there is one."""
        selected = self.selected_index()
        self.liststore.clear()
        for source, target, found_in, tooltip in rows:
            self.liststore.append([source, target, found_in, row_tooltip(source, target, tooltip)])
        if selected is not None and selected < len(self.liststore):
            self.treeview.get_selection().select_path(Gtk.TreePath(selected))
        elif self.treeview.has_focus() and len(self.liststore):
            self.treeview.set_cursor(Gtk.TreePath(0), None, False)
        self.update_match_buttons()

    def selected_index(self):
        model, itr = self.treeview.get_selection().get_selected()
        return None if itr is None else model.get_path(itr).get_indices()[0]

    def update_match_buttons(self):
        index = self.selected_index()
        self.btn_insert.set_sensitive(self.controller.can_insert(index))
        self.btn_copy.set_sensitive(index is not None)
        self.btn_go_to.set_sensitive(self.controller.can_go_to(index))

    def set_summary(self, summary):
        self.lbl_summary.set_text(summary)
        self.lbl_summary.set_tooltip_text(summary)

    def finished(self, summary, _finished):
        self.set_summary(summary)

    def _apply_height(self):
        """Give the panel the height it was last given."""
        total = self.paned.get_allocated_height()
        if not self._restore or not self.widget.get_visible() or total <= 1:
            return
        self._restore = False
        height = pan_app.config_int(
            pan_app.settings.general['concordancepanelheight'],
            HEIGHT,
            MIN_HEIGHT,
            32767,
            'concordancepanelheight',
        )
        handle = self._handle
        if handle is None:
            handle = self.paned.style_get_property('handle-size')
        self.paned.set_position(max(0, total - handle - height))

    def _restore_height(self):
        self._restore_source = None
        self._apply_height()
        return False

    # EVENT HANDLERS #
    def _on_paned_allocated(self, paned, _allocation):
        if self._restore:
            if self.widget.get_visible() and self._restore_source is None:
                self._restore_source = GLib.idle_add(self._restore_height)
            return
        units, panel = self.units.get_allocated_height(), self.widget.get_allocated_height()
        if self.widget.get_visible() and units > 1 and panel > 1:
            self._handle = max(0, paned.get_allocated_height() - units - panel)

    def _on_panel_allocated(self, widget, allocation):
        """Remember the height the user gave the panel."""
        if widget.get_mapped() and not self._restore and allocation.height > 1:
            pan_app.settings.general['concordancepanelheight'] = allocation.height
        if (allocation.y, allocation.height) != self._placed:
            self._placed = (allocation.y, allocation.height)
            self.controller.panel_moved()

    def _on_start(self, _widget):
        if self.can_start():
            self.controller.start()

    def _on_search_changed(self, _entry):
        self._searched = False

    def _on_option_toggled(self, _button):
        # Ticks narrow or widen the search shown.
        if self._searched and self.can_start():
            self.controller.start()

    def _on_selection_changed(self, _selection):
        self.update_match_buttons()

    def _on_row_activated(self, _treeview, path, _column):
        self.controller.insert(path.get_indices()[0])

    def _on_list_key(self, _treeview, event):
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        primary = Gdk.ModifierType.META_MASK | Gdk.ModifierType.CONTROL_MASK
        if event.keyval in (Gdk.KEY_c, Gdk.KEY_C) and state & primary:
            self.treeview.emit('copy-clipboard')
            return True
        return False

    def _on_copy(self, _treeview):
        self.controller.copy(self.selected_index())

    def _on_window_key(self, _window, event):
        """Escape in the panel goes back to the translation, before the
        main window's own Escape (closing Search, hiding suggestions)."""
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        if event.keyval != Gdk.KEY_Escape or state or not self.has_focus():
            return False
        self.controller.focus_editor()
        return True
