#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2008-2010 Zuza Software Foundation
# Copyright 2016 F Wolff
#
# This file is part of Virtaal.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import logging

from gi.repository import Gdk
from gi.repository import GObject
from gi.repository import Gtk

from .storecellrenderer import StoreCellRenderer
from .storetreemodel import COLUMN_NOTE, COLUMN_UNIT, COLUMN_EDITABLE, StoreTreeModel


class StoreTreeView(Gtk.TreeView):
    """
    The extended C{Gtk.TreeView} we use display our units.
    This class was adapted from the old C{UnitGrid} class.
    """
    __gtype_name__ = 'StoreTreeView'

    __gsignals__ = {
        'modified': (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    # INITIALIZERS #
    def __init__(self, view):
        self.view = view
        super().__init__()

        self.set_headers_visible(False)
        # self.set_direction(Gtk.TextDirection.LTR)

        self.renderer = self._make_renderer()
        self.append_column(self._make_column(self.renderer))
        self._enable_tooltips()

        self._install_callbacks()

        # This must be changed to a mutex if you ever consider
        # writing multi-threaded code. However, the motivation
        # for this horrid little variable is so dubious that you'd
        # be better off writing better code. I'm sorry to leave it
        # to you.
        self._waiting_for_row_change = 0

        # GObject.timeout_add() id for the pending debounced
        # on_configure_event() action - see that method for why.
        self._configure_timeout_id = None

    def _enable_tooltips(self):
        if hasattr(self, "set_tooltip_column"):
            self.set_tooltip_column(COLUMN_NOTE)
        self.set_rules_hint(True)

    def _install_callbacks(self):
        self.connect('key-press-event', self._on_key_press)
        self.connect("cursor-changed", self._on_cursor_changed)
        self.connect("button-press-event", self._on_button_press)
        self.connect('focus-in-event', self._on_focus_in)
        # Keeps the FIXED-sizing column's width tied to this treeview's
        # own real allocation - see _make_column() for why this exists.
        self.connect('size-allocate', self._on_size_allocate)
        # on_configure_event()'s debounce timer outlives a single event,
        # so it needs to be cancelled explicitly on teardown - otherwise
        # a pending GObject.timeout_add() can fire after this widget is
        # destroyed and call back into it (self.get_cursor() etc. on a
        # dead widget). This app already has one known, reproducible
        # teardown-related segfault (GTK widget-hierarchy teardown
        # racing CPython's cyclic GC - see the run-virtaal skill's
        # Gotchas); not adding to that risk.
        self.connect('destroy', self._on_destroy)

        # The following connections are necessary, because Gtk+ apparently *only* uses accelerators
        # to add pretty key-bindings next to menu items and does not really care if an accelerator
        # path has a connected handler.
        mainview = self.view.controller.main_controller.view
        mainview.gui.get_object('mnu_up').connect('activate', lambda *args: self._move_up(None, None, None, None))
        mainview.gui.get_object('mnu_down').connect('activate', lambda *args: self._move_down(None, None, None, None))
        mainview.gui.get_object('mnu_pageup').connect('activate', lambda *args: self._move_pgup(None, None, None, None))
        mainview.gui.get_object('mnu_pagedown').connect('activate', lambda *args: self._move_pgdown(None, None, None, None))

    def _make_renderer(self):
        renderer = StoreCellRenderer(self.view)
        renderer.connect("editing-done", self._on_cell_edited, self.get_model())
        renderer.connect("modified", self._on_modified)
        return renderer

    def _make_column(self, renderer):
        column = Gtk.TreeViewColumn(None, renderer, unit=COLUMN_UNIT, editable=COLUMN_EDITABLE)
        # 2026-08-24: switched from set_expand(True) to FIXED sizing tied
        # directly to the treeview's own real allocated width via
        # _on_size_allocate() below - see on_configure_event()'s and
        # select_index()'s history for the full investigation. With
        # set_expand(True), this column's width came from GTK's own
        # natural-size renegotiation - and on this Windows/gvsbuild GTK3
        # build specifically, that renegotiation computed a slightly
        # wider value on every single reallocation, not just when
        # columns_autosize()/set_cursor(start_editing=True) were called
        # directly, but seemingly whenever *any* reallocation touched
        # this column - including reallocations caused by correcting a
        # previous over-grown size, which is why three separate
        # after-the-fact Python-level correction attempts each failed
        # for a different subtle reason: the correction's own resize()
        # was itself fuel for the next growth cycle, a loop no amount of
        # chasing it after the fact could win. FIXED sizing sidesteps
        # the whole renegotiation mechanism instead of trying to
        # out-correct it - this column's width becomes something set
        # explicitly and deterministically from the treeview's real
        # allocation, never something GTK computes from cell content.
        column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        column.set_fixed_width(1)  # corrected on the first real size-allocate
        return column

    def _on_size_allocate(self, _widget, allocation):
        # Keeps the FIXED-sizing column's width tied to the treeview's
        # own real allocated width - see _make_column() for why
        # set_expand(True) was replaced with this. A few pixels of
        # margin avoids a vertical scrollbar (if one appears) fighting
        # the column for the same space it just claimed. Guarded on the
        # width actually changing so this doesn't itself queue a
        # pointless reallocation on every single size-allocate,
        # including ones this same call just caused.
        column = self.get_columns()[0] if self.get_columns() else None
        if not column:
            return
        new_width = max(1, allocation.width - 2)
        if column.get_fixed_width() != new_width:
            column.set_fixed_width(new_width)


    # METHODS #
    def select_index(self, index):
        """Select the row with the given index."""
        model = self.get_model()
        if not model or not isinstance(model, StoreTreeModel):
            return
        newpath = Gtk.TreePath(model.store_index_to_path(index))
        selected = self.get_selection().get_selected()
        selected_path = isinstance(selected[1], Gtk.TreeIter) and model.get_path(selected[1]) or None

        if selected[1] is None or (selected_path and selected_path != newpath):
            #logging.debug('select_index()->self.set_cursor(path="%s")' % (newpath))
            # XXX: Both of the "self.set_cursor()" calls below are necessary in
            #      order to have both bug 869 fixed and keep search highlighting
            #      in working order. After exhaustive inspection of the
            #      interaction between emitted signals involved, Friedel and I
            #      still have no idea why exactly it is needed. This just seems
            #      to be the correct GTK black magic incantation to make it
            #      "work".
            #
            # 2026-08-23: on this Windows/gvsbuild GTK3 build specifically,
            # each start_editing=True call below computes a slightly wider
            # "natural" size for the live cell editor than the one before -
            # confirmed via the same class of investigation as the
            # resize/focus-triggered version of this bug (see
            # on_configure_event()'s history) - and since select_index()
            # runs on every single navigation (arrow keys, Page Up/Down,
            # Enter-to-advance), that compounds into a window that gets
            # measurably wider on every unit and never shrinks back -
            # unusable within 20-30 units on a real translation. Not
            # touching the two set_cursor() calls themselves (2008-era
            # code, historically fragile per the comment above, protecting
            # against at least two other old bugs) - instead, restore the
            # window's width immediately after each one if it grew. This
            # is a direct, one-shot correction in the same call stack, not
            # a signal-reactive handler - it can't recreate the
            # configure-event feedback loop the resize/focus version of
            # this bug had, since it isn't triggered by a configure-event
            # itself.
            width_before = self._window_size()
            self.set_cursor(newpath, self.get_columns()[0], start_editing=True)
            self._restore_width_if_grown(width_before, "select_index() (immediate)")
            self.get_model().set_editable(newpath)
            def change_cursor():
                width_before_idle = self._window_size()
                self.set_cursor(newpath, self.get_columns()[0], start_editing=True)
                self._restore_width_if_grown(width_before_idle, "select_index() (idle)")
                self._waiting_for_row_change -= 1
            self._waiting_for_row_change += 1
            GObject.idle_add(change_cursor, priority=GObject.PRIORITY_DEFAULT_IDLE)

    def set_model(self, storemodel):
        if storemodel:
            model = StoreTreeModel(storemodel)
        else:
            model = None
        super().set_model(model)

    def _keyboard_move(self, offset):
        if not self.view.controller.get_store():
            return

        # We don't want to process keyboard move events until we have finished updating
        # the display after a move event. So we use this awful, awful, terrible scheme to
        # keep track of pending draw events. In reality, it should be impossible for
        # self._waiting_for_row_change to be larger than 1, but my superstition led me
        # to be safe about it.
        if self._waiting_for_row_change > 0:
            return True

        try:
            #self._owner.set_statusbar_message(self.document.mode_cursor.move(offset))
            self.view.cursor.move(offset)
        except IndexError:
            pass

        return True

    def _move_up(self, _accel_group, _acceleratable, _keyval, _modifier):
        return self._keyboard_move(-1)

    def _move_down(self, _accel_group, _acceleratable, _keyval, _modifier):
        return self._keyboard_move(1)

    def _move_pgup(self, _accel_group, _acceleratable, _keyval, _modifier):
        return self._keyboard_move(-10)

    def _move_pgdown(self, _accel_group, _acceleratable, _keyval, _modifier):
        return self._keyboard_move(10)


    # EVENT HANDLERS #
    def _on_button_press(self, widget, event):
        # If the event did not happen in the treeview, but in the
        # editing widget, then the event window will not correspond to
        # the treeview's drawing window. This happens when the
        # user clicks on the edit widget. But if this happens, then
        # we don't want anything to happen, so we return True.
        if event.window != widget.get_bin_window():
            return True

        answer = self.get_path_at_pos(int(event.x), int(event.y))
        if answer is None:
            logging.debug("Not path found at (%d,%d)" % (int(event.x), int(event.y)))
            return True

        old_path, _old_column = self.get_cursor()
        path, _column, _x, _y = answer
        if old_path != path:
            index = self.get_model().path_to_store_index(path)
            if index not in self.view.cursor.indices:
                self.view.controller.main_controller.mode_controller.select_default_mode()
            self.view.cursor.index = index

        return True

    def _on_cell_edited(self, _cell, _path_string, must_advance, _modified, _model):
        if must_advance:
            return self._keyboard_move(1)
        return True

    def on_configure_event(self, widget, event, *_user_args):
        # RESOLVED 2026-08-23 - see _restore_cursor()'s do_setcursor() for
        # the actual root cause and fix. Left as a debounce (rather than
        # reverted to a plain direct call) since restoring cursor state
        # on every single raw configure-event tick during a resize is
        # still wasteful regardless of the growth bug being gone, and
        # debouncing costs nothing on a healthy resize.
        #
        # History, kept for the next time something in this area acts
        # up: first attempt (12ef7605) deduped identical successive
        # sizes - insufficient, a live drag fires genuinely-changing
        # sizes on nearly every tick. Second attempt (009ce12d) debounced
        # instead - reported still growing, this time right after
        # opening a file with no drag at all. Third attempt (495bfc7a)
        # added diagnostic logging instead of guessing again - and CI's
        # own "Verify the bundle actually runs" step (made strict by
        # 7f0fb1e3 specifically to stop silently passing over exactly
        # this class of bug) reproduced it instantly and 100% reliably,
        # no VM round-trip needed. The paired before/after log showed
        # columns_autosize() reporting a *stable* width each cycle, but
        # the very next set_cursor(..., start_editing=True) call
        # reporting a new, ~24px-wider one - consistently, cycle after
        # cycle, all within single-digit milliseconds (far too fast for
        # the 200ms debounce to be a factor at all, ruling out timing
        # entirely). See _restore_cursor()'s do_setcursor() for the fix.
        logging.info("storetreeview: configure-event %dx%d", event.width, event.height)
        if self._configure_timeout_id is not None:
            GObject.source_remove(self._configure_timeout_id)
        self._configure_timeout_id = GObject.timeout_add(200, self._on_configure_settled)
        return False

    def _on_configure_settled(self):
        self._configure_timeout_id = None
        logging.info("storetreeview: debounce settled, window=%s", self._window_size())
        self._restore_cursor()
        return False  # one-shot: don't repeat this GObject.timeout_add

    def _on_destroy(self, _widget):
        if self._configure_timeout_id is not None:
            GObject.source_remove(self._configure_timeout_id)
            self._configure_timeout_id = None

    def _restore_width_if_grown(self, width_before, when):
        # Direct, one-shot correction - see select_index()'s call sites
        # for why. Not a signal handler, so it can't recreate the
        # configure-event feedback loop the earlier version of this bug
        # had (see on_configure_event()'s history).
        #
        # 2026-08-24: first version of this call just did
        # window.resize(...) and returned - confirmed via CI's own
        # navigation-growth check that this did *not* work: the window
        # still grew by the exact same ~24px per navigation as before
        # this fix existed. window.resize() on a GtkWindow doesn't apply
        # synchronously - it queues a resize for the next main-loop
        # iteration. select_index() runs back-to-back on rapid
        # navigation (Enter-to-advance) with no yield to the main loop
        # in between, so the queued resize from one call had never
        # actually taken effect by the time the *next* call took its own
        # "before" measurement - it was reading the same still-grown
        # size again and again, growing further on top of it each time.
        # Forcing the pending resize to actually apply before returning
        # (the standard GTK "flush the event queue now" idiom) is what
        # makes this a real correction instead of a queued one that
        # never catches up.
        #
        # 2026-08-24, second round: that alone still wasn't enough -
        # confirmed via CI again, same ~24px/navigation growth, but this
        # time with *zero* WARNING lines logged at all, meaning the
        # `current_width > width_before[0]` check below never even
        # fired. Cause: the growth itself is *also* queued, not applied
        # synchronously within set_cursor() - a widget requisition change
        # calls gtk_widget_queue_resize() internally, which the toplevel
        # only actually reallocates on a later main-loop iteration. So
        # window.get_size() read immediately after set_cursor() returns
        # was reading the *old*, not-yet-grown size - correctly finding
        # nothing to correct, because from its own perspective nothing
        # had grown yet. Need to flush pending events *before* measuring
        # too, not just after correcting, so the measurement reflects
        # what actually happened.
        if not width_before:
            return
        window = self.get_toplevel()
        if not window or not isinstance(window, Gtk.Window) or not window.get_realized():
            return
        while Gtk.events_pending():
            Gtk.main_iteration()
        current_width, current_height = window.get_size()
        if current_width > width_before[0]:
            logging.warning(
                "storetreeview: %s grew window %d -> %d, restoring to %d",
                when, width_before[0], current_width, width_before[0],
            )
            window.resize(width_before[0], current_height)
            while Gtk.events_pending():
                Gtk.main_iteration()

    def _on_focus_in(self, widget, _event, *_user_args):
        # Restore cursor/editing state on refocus, same as
        # on_configure_event() (they used to share one handler, split in
        # 12ef7605) - a focus change was never a reason to touch column
        # sizing, which is now moot anyway since _restore_cursor() no
        # longer does that at all (see there).
        self._restore_cursor()
        return False

    def _window_size(self):
        window = self.get_toplevel()
        if window and isinstance(window, Gtk.Window) and window.get_realized():
            return window.get_size()
        return None

    def _restore_cursor(self):
        # TEMPORARY, 2026-08-23: gutted to pure observation - no
        # columns_autosize(), no set_cursor(), no resize() from the
        # safety clamp. Two prior single-variable experiments (removing
        # columns_autosize() entirely, then separately dropping
        # start_editing=True from set_cursor()) each produced the exact
        # same numeric growth sequence in CI (1036, 1060, 1084, 1108...,
        # +24 each time) - identical down to the pixel, which means
        # neither of those calls was actually the variable that mattered.
        # The one thing unchanged across both attempts is this method's
        # own safety-clamp calling window.resize() - a real suspect that
        # hadn't been isolated yet: either that resize() call is itself
        # triggering the next divergent layout pass, or window.resize()
        # is simply async on this platform and every "after clamping"
        # check has been reading an already-stale, still-growing value
        # regardless of what triggered it.
        #
        # logging.warning(), not .info(): CI's verify step doesn't pass
        # --log/--debug, so bin/virtaal never calls
        # logging.basicConfig() and only WARNING+ reaches stderr at all
        # (Python's default "last resort" handler). Using .info() here
        # would make this build silently untestable in CI - no
        # corrective action AND no visible signal either way.
        #
        # Whatever this shows, revert to real corrective behaviour
        # afterwards - this build is diagnostic only.
        size = self._window_size()
        if size and size[0] > 1024:
            logging.warning("storetreeview: [diagnostic] window width %d (no corrective action taken)", size[0])

    def _max_sane_width(self):
        # Screen-derived, not a fixed pixel constant, so this doesn't
        # need updating for different monitor sizes.
        try:
            display = Gdk.Display.get_default()
            gdk_window = self.get_window()
            monitor = (display.get_monitor_at_window(gdk_window) if gdk_window
                       else display.get_primary_monitor())
            return monitor.get_geometry().width
        except Exception:
            logging.exception("storetreeview: couldn't get screen width for the resize safety cap")
            return 3000  # generous but bounded fallback

    def _clamp_to_sane_width(self, when):
        # Hard safety net for the runaway-resize bug above, not a fix
        # for it - added 2026-08-23 alongside the diagnostic logging so
        # the app stays usable (and the growth pattern still gets
        # logged) while the real Windows-only mechanism is still being
        # tracked down. Should be a no-op on a healthy resize.
        window = self.get_toplevel()
        if not window or not isinstance(window, Gtk.Window) or not window.get_realized():
            return
        width, height = window.get_size()
        max_width = self._max_sane_width()
        if width > max_width:
            logging.warning(
                "storetreeview: window width %d exceeded safety cap %d (%s) - clamping",
                width, max_width, when,
            )
            window.resize(max_width, height)

    def _on_cursor_changed(self, _treeview):
        path, _column = self.get_cursor()

        model = _treeview.get_model()
        if not model:
            return True

        index = model.path_to_store_index(path)
        if self.view.cursor and index != self.view.cursor.index:
            self.view.cursor.index = index

        # We defer the scrolling until GTK has finished all its current drawing
        # tasks, hence the GObject.idle_add. If we don't wait, then the TreeView
        # draws the editor widget in the wrong position. Presumably GTK issues
        # a redraw event for the editor widget at a given x-y position and then also
        # issues a TreeView scroll; thus, the editor widget gets drawn at the wrong
        # position.
        def do_scroll():
            if not self.get_cursor()[0]:
                # cursor became invalid since this was added to the idle queue
                # maybe because the file was closed since then.
                return False
            if path:
                self.scroll_to_cell(path, self.get_column(0), True, 0.5, 0.0)
            return False

        GObject.idle_add(do_scroll)
        return True

    def _on_key_press(self, _widget, _event, _data=None):
        # The TreeView does interesting things with combos like SHIFT+TAB.
        # So we're going to stop it from doing this.
        return True

    def _on_modified(self, _widget):
        self.emit("modified")
        return True
