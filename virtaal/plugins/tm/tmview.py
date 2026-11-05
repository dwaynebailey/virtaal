#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import os

from gi.repository import Gdk, GLib, GObject, Gtk

from virtaal.common import GObjectWrapper, SignalTracker, pan_app
from virtaal.common.pan_app import ngettext
from virtaal.support.translate_compat import forceunicode
from virtaal.views.baseview import BaseView
from virtaal.views.widgets.popupwidgetbutton import PopupWidgetButton
from virtaal.views.widgets.wayland import set_popup_parent

from .tmwidgets import TMWindow


def _gtk_text(msgid):
    """GTK's own translation of msgid, kept out of virtaal.pot."""
    return GLib.dgettext('gtk30', msgid)


class TMView(BaseView, GObjectWrapper):
    """The fake drop-down menu in which the TM matches are displayed."""

    __gtype_name__ = 'TMView'
    __gsignals__ = {
        'tm-match-selected': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
    }

    # File > Import into Translation Memory…, and the model it imports into.
    mnu_import = None
    _import_model = None

    # INITIALIZERS #
    def __init__(self, controller, max_matches):
        GObjectWrapper.__init__(self)

        self.controller = controller
        self.isvisible = False
        # Show suggestions for the current unit even if it is translated or
        # suggestions are off - see TMController.summon().
        self.summoned = False
        self.max_matches = max_matches
        self._should_show_tmwindow = (
            False  # should it be displayed now (even if it doesn't, due to application focus?
        )
        self._signal_tracker = SignalTracker()

        self.tmwindow = TMWindow(self)
        main_window = self.controller.main_controller.view.main_window
        set_popup_parent(self.tmwindow, main_window)
        self._may_show_tmwindow = (
            main_window.props.is_active
        )  # is it allowed to display now (application is in focus)?
        # The main window's last geometry, and the timer that ends a move
        # or resize - see _on_configure_mainwindow().
        self._mainwindow_geometry = None
        self._move_settle_id = None
        # The unit editor's last allocation - see _on_editor_size_allocate().
        self._editor = None
        self._editor_allocation = None

        self._signal_tracker.connect(
            self.tmwindow.treeview, 'row-activated', self._on_row_activated
        )
        self._match_menu = self._create_match_menu()
        self._menu_match = None
        self._signal_tracker.connect(
            self.tmwindow.treeview, 'button-press-event', self._on_treeview_button_press
        )
        self._signal_tracker.connect(
            controller.main_controller.store_controller.view.parent_widget.get_vscrollbar(),
            'value-changed',
            self._on_store_view_scroll,
        )
        self._signal_tracker.connect(main_window, 'grab-notify', self._on_grab_notify_mainwindow)
        self._signal_tracker.connect(
            main_window, 'notify::is-active', self._on_active_notify_mainwindow
        )
        self._signal_tracker.connect(main_window, 'configure_event', self._on_configure_mainwindow)
        self._signal_tracker.connect(
            controller.main_controller.store_controller, 'store-closed', self._on_store_closed
        )
        self._signal_tracker.connect(
            controller.main_controller.store_controller, 'store-loaded', self._on_store_loaded
        )

        self._setup_key_bindings()
        self._setup_menu_items()

    def _setup_key_bindings(self):
        """Setup Gtk+ key bindings (accelerators)."""

        Gtk.AccelMap.add_entry("<Virtaal>/TM/Hide TM", Gdk.KEY_Escape, 0)

        self.accel_group = Gtk.AccelGroup()
        self.accel_group.connect_by_path("<Virtaal>/TM/Hide TM", self._on_hide_tm)

        # Connect Ctrl+n (1 <= n <= 9) to select match n.
        for i in range(1, 10):
            numstr = str(i)
            numkey = Gdk.KEY_0 + i
            Gtk.AccelMap.add_entry(
                "<Virtaal>/TM/Select match " + numstr, numkey, Gdk.ModifierType.CONTROL_MASK
            )
            self.accel_group.connect_by_path(
                "<Virtaal>/TM/Select match " + numstr, self._on_select_match
            )

        mainview = self.controller.main_controller.view
        mainview.add_accel_group(self.accel_group)

    def _setup_menu_items(self):
        mainview = self.controller.main_controller.view
        self.mnui_view = mainview.gui.get_object('menuitem_view')
        self.menu = self.mnui_view.get_submenu()

        self.mnu_suggestions = Gtk.CheckMenuItem.new_with_mnemonic(
            label=_('Translation _Suggestions')
        )
        self.mnu_suggestions.show()
        self.menu.append(self.mnu_suggestions)

        self.mnu_summon = Gtk.MenuItem.new_with_mnemonic(label=_('Suggestions for This _Unit'))
        self.mnu_summon.show()
        self.menu.append(self.mnu_summon)

        Gtk.AccelMap.add_entry(
            "<Virtaal>/TM/Toggle Show TM", Gdk.KEY_F9, Gdk.ModifierType.CONTROL_MASK
        )
        Gtk.AccelMap.add_entry("<Virtaal>/TM/Show TM for Unit", Gdk.KEY_F9, 0)
        accel_group = self.menu.get_accel_group()
        if accel_group is None:
            accel_group = self.accel_group
            self.menu.set_accel_group(self.accel_group)
        self.mnu_suggestions.set_accel_path("<Virtaal>/TM/Toggle Show TM")
        self.mnu_summon.set_accel_path("<Virtaal>/TM/Show TM for Unit")
        self.menu.set_accel_group(accel_group)

        self.mnu_suggestions.connect('toggled', self._on_toggle_show_tm)
        self.mnu_suggestions.set_active(True)
        self.mnu_summon.connect('activate', lambda *_args: self.controller.summon())

    def _create_match_menu(self):
        menu = Gtk.Menu()
        # GTK's own label, as in the target's context menu.
        mnu_copy = Gtk.MenuItem.new_with_mnemonic(label=_gtk_text('_Copy'))
        mnu_copy.connect('activate', lambda *_args: self.copy_match(self._menu_match))
        # l10n: Inserts the suggestion at the cursor in the translation, or over the selected text, keeping the rest of the translation.
        mnu_insert = Gtk.MenuItem.new_with_mnemonic(label=_('_Insert at Cursor'))
        mnu_insert.connect(
            'activate', lambda *_args: self.controller.insert_match(self._menu_match)
        )
        menu.append(mnu_copy)
        menu.append(mnu_insert)
        menu.show_all()
        menu.attach_to_widget(self.tmwindow.treeview, None)
        return menu

    # ACCESSORS #
    def _get_active(self):
        return self.mnu_suggestions.get_active()

    active = property(_get_active)

    # METHODS #
    def clear(self):
        """Clear the TM matches."""
        self.tmwindow.liststore.clear()
        self.hide()
        self._should_show_tmwindow = False

    def destroy(self):
        self._signal_tracker.disconnect_all()
        if self._move_settle_id:
            GLib.source_remove(self._move_settle_id)
            self._move_settle_id = None

        self.menu.remove(self.mnu_suggestions)
        self.menu.remove(self.mnu_summon)
        self.remove_import_menu_item()
        self._match_menu.destroy()

    def add_import_menu_item(self, model):
        """Offer File > Import into Translation Memory…, importing into
        C{model} (one with C{import_file()})."""
        self._import_model = model
        if self.mnu_import is not None:
            return
        mainview = self.controller.main_controller.view
        self.mnu_import = mainview.append_menu_item(
            # l10n: Adds the translations in files the user picks to the local
            # translation memory, without opening them
            _('_Import into Translation Memory…'),
            mainview.gui.get_object('menuitem_file'),
            after=mainview.gui.get_object('mnu_binary_export'),
        )
        self.mnu_import.connect('activate', self._on_import)
        mainview.sync_menubar()

    def remove_import_menu_item(self):
        self._import_model = None
        if self.mnu_import is None:
            return
        self.mnu_import.get_parent().remove(self.mnu_import)
        self.mnu_import = None
        self.controller.main_controller.view.sync_menubar()

    def _build_import_chooser(self):
        from virtaal.views.mainview import add_translation_file_filters

        mainview = self.controller.main_controller.view
        chooser = Gtk.FileChooserDialog(
            title=_('Import into Translation Memory'),
            transient_for=mainview.main_window,
            modal=True,
            action=Gtk.FileChooserAction.OPEN,
        )
        chooser.add_buttons(
            _gtk_text('_Cancel'),
            Gtk.ResponseType.CANCEL,
            _gtk_text('_Open'),
            Gtk.ResponseType.ACCEPT,
        )
        chooser.set_default_response(Gtk.ResponseType.ACCEPT)
        chooser.set_select_multiple(True)
        add_translation_file_filters(chooser)
        last_dir = pan_app.settings.general["lastdir"] or ""
        if os.path.isdir(last_dir):
            chooser.set_current_folder(last_dir)
        return chooser

    def choose_import_files(self):
        """The files the user picks to import, or an empty list."""
        chooser = self._build_import_chooser()
        try:
            if chooser.run() != Gtk.ResponseType.ACCEPT:
                return []
            filenames = chooser.get_filenames()
        finally:
            chooser.destroy()
        if filenames:
            pan_app.settings.general["lastdir"] = os.path.dirname(filenames[0])
        return filenames

    def _on_import(self, _widget=None):
        model = self._import_model
        filenames = self.choose_import_files()
        if model is None or not filenames:
            return
        waiting = [len(filenames)]
        added = [0]
        failed = []

        def done(path, count):
            added[0] += count
            finish()

        def error(path):
            failed.append(path)
            finish()

        def finish():
            waiting[0] -= 1
            if not waiting[0]:
                # Not from inside the HTTP client's callback: the error
                # dialog runs a main loop of its own.
                GLib.idle_add(self._show_import_result, len(filenames), added[0], failed)

        for filename in filenames:
            model.import_file(filename, done, error)

    def _show_import_result(self, total, added, failed):
        mainview = self.controller.main_controller.view
        if len(failed) < total:
            mainview.show_info_notice(
                # l10n: After File > Import into Translation Memory: how many
                # translations the translation memory did not have yet
                ngettext(
                    'Added %(count)d translation to the translation memory.',
                    'Added %(count)d translations to the translation memory.',
                    added,
                )
                % {'count': added}
            )
        if failed:
            mainview.show_error_dialog(
                message='\n'.join(
                    _("Couldn't import '%(file)s' into the translation memory.")
                    % {'file': os.path.basename(path)}
                    for path in failed
                )
            )
        return False

    def display_matches(self, matches):
        """Add the list of TM matches to those available and show the TM window."""
        liststore = self.tmwindow.liststore
        liststore.clear()
        for match in matches:
            tooltip = ''
            if len(liststore) < 9:
                tooltip = _('Ctrl+%(number_key)d') % {"number_key": len(liststore) + 1}
            liststore.append([match, tooltip])

        if len(liststore) > 0:
            self.show()
            self.update_geometry()

    def get_target_width(self):
        if not hasattr(self.controller, 'unit_view'):
            return -1
        n = self.controller.unit_view.focused_target_n
        textview = self.controller.unit_view.targets[n]
        return textview.get_allocation().width

    def hide(self):
        """Hide the TM window."""
        self.tmwindow.hide()
        self.isvisible = False

    def suspend(self):
        """Keep the TM window hidden while another popup (e.g. the target's
        completion list) is over the editor, until L{resume}."""
        self._on_mainwindow_focus_lost()

    def resume(self):
        self._on_mainwindow_focus_gained()

    def select_backends(self, parent):
        from virtaal.views.backendselect import select_backends

        select_backends(
            self.controller.main_controller,
            self.controller.plugin_controller,
            self.controller.config,
            'basetmmodel',
            # l10n: The 'sources' here refer to different translation memory plugins,
            # such as local tm, open-tran.eu, the current file, etc.
            title=_('Select sources of Translation Memory'),
            message=_('Select the sources that should be queried for translation memory'),
            size=(550, 580),
            parent=parent,
        )

    def copy_match(self, match_data):
        """Put the match's text on the clipboard."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(forceunicode(match_data['target']), -1)

    def popup_match_menu(self, path, event):
        """Show the Copy / Insert menu for the match in row C{path}."""
        liststore = self.tmwindow.liststore
        self._menu_match = liststore.get_value(liststore.get_iter(path), 0)
        self.tmwindow.treeview.get_selection().select_path(path)
        self._match_menu.popup_at_pointer(event)

    def select_match(self, match_data):
        """Select the match data as accepted by the user."""
        self.controller.select_match(match_data)

    def select_match_index(self, index):
        """Select the TM match with the given index (first match is 1).
        This method causes a row in the TM window's C{Gtk.TreeView} to be
        selected and activated. This runs this class's C{_on_select_match()}
        method which runs C{select_match()}."""
        if index < 0 or not self.isvisible:
            return

        logging.debug('Selecting index %d' % (index))
        liststore = self.tmwindow.liststore
        itr = liststore.get_iter_first()

        i = 1
        while itr and i < index and liststore.iter_is_valid(itr):
            itr = liststore.iter_next(itr)
            i += 1

        if not itr or not liststore.iter_is_valid(itr):
            return

        path = liststore.get_path(itr)
        self.tmwindow.treeview.get_selection().select_iter(itr)
        self.tmwindow.treeview.row_activated(path, self.tmwindow.tvc_match)

    def show(self, force=False):
        """Show the TM window."""
        if not (self.active or self.summoned) or (self.isvisible and not force):
            return  # This window is already visible
        if not len(self.tmwindow.liststore):
            return  # Nothing to show
        if not self._may_show_tmwindow:
            self._should_show_tmwindow = True
            return
        self._follow_editor()
        # Position before mapping, or it first appears where it last was.
        selected = self._get_selected_unit_view()
        if selected is not None:
            self.tmwindow.get_child().show_all()
            self.tmwindow.realize()
            self.tmwindow.update_geometry(selected)
        self.tmwindow.show_all()
        self.isvisible = True
        self._should_show_tmwindow = False

    def update_geometry(self):
        """Update the TM window's position and size."""

        def update():
            selected = self._get_selected_unit_view()
            if selected:
                self.tmwindow.update_geometry(selected)

        GLib.idle_add(update)

    def _follow_editor(self):
        editor = self.controller.main_controller.unit_controller.view._widgets.get('vbox_editor')
        if editor is None or editor is self._editor:
            return
        self._editor = editor
        self._signal_tracker.connect(editor, 'size-allocate', self._on_editor_size_allocate)

    def _selected_target_in_view(self):
        selected = self._get_selected_unit_view()
        scrolled = self.controller.main_controller.store_controller.view.parent_widget
        if selected is None or not selected.get_mapped():
            return False
        position = selected.translate_coordinates(scrolled, 0, 0)
        if position is None:
            return False
        top = position[1]
        return top + selected.get_allocated_height() > 0 and top < scrolled.get_allocated_height()

    def _get_selected_unit_view(self):
        n = self.controller.main_controller.unit_controller.view.focused_target_n
        if n is None:
            # There is no unit. Nothing to do.
            return None
        return self.controller.main_controller.unit_controller.view.targets[n]

    # EVENT HANDLERS #
    def _on_editor_size_allocate(self, editor, allocation):
        # The target grows and shrinks while typing.
        geometry = (allocation.y, allocation.height)
        if geometry == self._editor_allocation:
            return
        self._editor_allocation = geometry
        if self.isvisible:
            self.update_geometry()

    def _on_grab_notify_mainwindow(self, widget, was_grabbed):
        # A same-app modal dialog or menu takes a GTK grab without
        # moving real WM focus off the main window, so is-active can't
        # see it - that's what grab-notify handles. A real focus change
        # (e.g. Alt+Tab) never takes a grab; see
        # _on_active_notify_mainwindow below.
        if was_grabbed:
            self._on_mainwindow_focus_gained()
        else:
            if isinstance(Gtk.grab_get_current(), Gtk.Menu):
                # A plain popup menu (e.g. the Workflow/Quality-Check mode
                # buttons) also takes an implicit grab when it opens, same
                # as a modal dialog. Treating that as "a modal dialog
                # opened" and hiding this window fights with the menu's
                # own grab and can dismiss the menu prematurely (#3689).
                # Only an actual modal window should hide the TM window.
                return
            self._on_mainwindow_focus_lost()

    def _on_active_notify_mainwindow(self, widget, pspec):
        if widget.props.is_active:
            self._on_mainwindow_focus_gained()
        elif self._match_menu.get_visible():
            # A match's own menu takes the focus: keep the matches under it.
            return
        else:
            self._on_mainwindow_focus_lost()

    def _on_mainwindow_focus_gained(self):
        if self._move_settle_id:
            return  # shown again once the main window settles
        self._may_show_tmwindow = True
        if not self._should_show_tmwindow or self.isvisible:
            return
        if not self.controller.storecursor:
            return  # No store loaded
        self.show()

        selected = self._get_selected_unit_view()
        self.tmwindow.update_geometry(selected)

    def _on_mainwindow_focus_lost(self):
        self._may_show_tmwindow = False
        if not self.isvisible:
            return
        self.hide()
        self._should_show_tmwindow = True

    # How long the main window must stay put before a move or resize ends.
    MOVE_SETTLE_DELAY = 200

    def _on_configure_mainwindow(self, widget, event):
        geometry = (event.x, event.y, event.width, event.height)
        moved = self._mainwindow_geometry is not None and geometry != self._mainwindow_geometry
        self._mainwindow_geometry = geometry
        if moved:
            # The suggestions are a separate window that doesn't follow the
            # main one: hide them until it settles, as when focus is lost.
            self._on_mainwindow_focus_lost()
            if self._move_settle_id:
                GLib.source_remove(self._move_settle_id)
            self._move_settle_id = GLib.timeout_add(
                self.MOVE_SETTLE_DELAY, self._on_mainwindow_settled
            )
            return
        if self._should_show_tmwindow:
            # For some reason tvc_tm_source needs this help to recalculate its
            # size, otherwise it goes through the roof (rhs of the screen), and
            # the size calculation of the renderer isn't even called. See bug
            # 1809.
            self.tmwindow.tvc_tm_source.queue_resize()
            self.update_geometry()

    def _on_mainwindow_settled(self):
        self._move_settle_id = None
        if self.controller.main_controller.view.main_window.props.is_active:
            self._on_mainwindow_focus_gained()
        return GLib.SOURCE_REMOVE

    def _on_hide_tm(self, accel_group, acceleratable, keyval, modifier):
        # This accelerator runs before the focused widget sees Escape: close
        # a pop-up opened over the suggestions first.
        if PopupWidgetButton.hide_open_popup():
            return True
        # Escape is shared with Search: leave it alone when there's nothing to hide.
        if not self.isvisible:
            return False
        self.hide()
        return True

    def _on_row_activated(self, treeview, path, column):
        """Called when a TM match is selected in the TM window."""
        liststore = treeview.get_model()
        assert liststore is self.tmwindow.liststore
        itr = liststore.get_iter(path)
        match_data = liststore.get_value(itr, 0)

        self.select_match(match_data)

    def _on_treeview_button_press(self, treeview, event):
        if event.type != Gdk.EventType.BUTTON_PRESS or not event.triggers_context_menu():
            return False
        hit = treeview.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:
            return False
        self.popup_match_menu(hit[0], event)
        return True

    def _on_select_match(self, accel_group, acceleratable, keyval, modifier):
        self.select_match_index(int(keyval - Gdk.KEY_0))

    def _on_store_closed(self, storecontroller):
        self.hide()
        self.mnu_suggestions.set_sensitive(False)
        self.mnu_summon.set_sensitive(False)

    def _on_store_loaded(self, storecontroller):
        self.mnu_suggestions.set_sensitive(True)
        self.mnu_summon.set_sensitive(True)

    def _on_store_view_scroll(self, *args):
        # The list also scrolls to keep a growing target in view.
        if not self.isvisible:
            return
        if self._selected_target_in_view():
            self.update_geometry()
        else:
            self.hide()

    def _on_toggle_show_tm(self, *args):
        if not self.active and self.isvisible and not self.summoned:
            self.hide()
        elif self.active and not self.isvisible:
            self.controller.update_suggestions()
