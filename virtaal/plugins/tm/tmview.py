#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import os

from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from virtaal.common import GObjectWrapper, SignalTracker, pan_app
from virtaal.common.pan_app import ngettext
from virtaal.support.libi18n.numbers import format_percent
from virtaal.views.baseview import BaseView
from virtaal.views.widgets.popupwidgetbutton import PopupWidgetButton
from virtaal.views.widgets.wayland import set_popup_parent

from .tmcontroller import ALWAYS, ATTENTION, NEVER
from .tmwidgets import TMWindow


def match_shortcut(number):
    """The label of the shortcut that takes suggestion C{number}, as
    this platform shows it (Ctrl+1, ⌘1)."""
    found, key = Gtk.AccelMap.lookup_entry("<Virtaal>/TM/Select match %d" % number)
    if not found or not key.accel_key:
        return ''
    return Gtk.accelerator_get_label(key.accel_key, key.accel_mods)


def _gtk_text(msgid):
    """GTK's own translation of msgid, kept out of virtaal.pot."""
    return GLib.dgettext('gtk30', msgid)


_DIGITS = {Gdk.KEY_0 + n: n for n in range(1, 10)}
_DIGITS.update({Gdk.KEY_KP_0 + n: n for n in range(1, 10)})
_INSERT_MODS = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
_OTHER_MODS = (
    Gdk.ModifierType.MOD1_MASK
    | Gdk.ModifierType.META_MASK
    | Gdk.ModifierType.SUPER_MASK
    | Gdk.ModifierType.HYPER_MASK
)


def insert_shortcut_digit(event):
    """N for a press of Ctrl+Shift+N (1 <= N <= 9), else None. Shift
    turns the digit into a symbol on most layouts (! on US, & with no
    Shift on AZERTY), so the digit is the one on the pressed key."""
    state = event.state
    if state & _INSERT_MODS != _INSERT_MODS or state & _OTHER_MODS:
        return None
    if event.keyval in _DIGITS:
        return _DIGITS[event.keyval]
    keymap = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
    found, _keys, keyvals = keymap.get_entries_for_keycode(event.hardware_keycode)
    for keyval in keyvals if found else ():
        if keyval in _DIGITS:
            return _DIGITS[keyval]
    return None


class TMView(BaseView, GObjectWrapper):
    """The fake drop-down menu in which the TM matches are displayed."""

    __gtype_name__ = 'TMView'
    __gsignals__ = {
        'tm-match-selected': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
    }

    # File > Fetch Suggestions for This File… (#1679).
    mnu_fetch = None
    # File > Import into Translation Memory…, and the model it imports into.
    mnu_import = None
    _import_model = None
    # File > Add to Local TM on Save, the model it asks, and a count of
    # files opened and closed and of the user's changes to it, so a late
    # reply about an earlier one is dropped.
    mnu_add_on_save = None
    _exclude_model = None
    _add_on_save_id = None
    _opened = 0
    _add_on_save_file = None

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
        self._signal_tracker.connect(main_window, 'key-press-event', self._on_mainwindow_key_press)
        self._signal_tracker.connect(
            controller.main_controller.store_controller, 'store-closed', self._on_store_closed
        )
        self._signal_tracker.connect(
            controller.main_controller.store_controller, 'store-loaded', self._on_store_loaded
        )
        self._signal_tracker.connect(
            controller.main_controller.store_controller, 'store-saved', self._on_store_saved
        )

        self._setup_key_bindings()
        self._setup_menu_items()
        self._connect_target_menus()

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

    def _on_mainwindow_key_press(self, _window, event):
        """Ctrl+Shift+N inserts match N at the cursor. Not an accelerator:
        GTK only matches those on the shifted key's own symbol."""
        index = insert_shortcut_digit(event)
        if index is None:
            return False
        return self.insert_match_index(index)

    # The target's context menu: Insert Suggestion (Q15) #
    # Each target's selection when its menu was asked for: other items
    # (Look-up, Add Term) select the word under the pointer meanwhile.
    _menu_selection = None

    def _connect_target_menus(self):
        unit_controller = getattr(self.controller.main_controller, 'unit_controller', None)
        unit_view = getattr(unit_controller, 'view', None)
        if unit_view is None:
            return
        if unit_view.targets:
            self._connect_targets(unit_view, unit_view.targets)
        self._signal_tracker.connect(unit_view, 'targets-created', self._connect_targets)

    def _connect_targets(self, _unit_view, targets):
        for textbox in targets:
            self._signal_tracker.connect(textbox, 'button-press-event', self._on_target_press)
            self._signal_tracker.connect(textbox, 'popup-menu', self._keep_target_selection)
            self._signal_tracker.connect(textbox, 'populate-popup', self._on_target_populate_popup)

    def _on_target_press(self, textbox, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.triggers_context_menu():
            self._keep_target_selection(textbox)
        return False

    def _keep_target_selection(self, textbox):
        buffer = textbox.get_buffer()
        bounds = (
            buffer.get_selection_bounds() or (buffer.get_iter_at_mark(buffer.get_insert()),) * 2
        )
        self._menu_selection = (textbox, tuple(itr.get_offset() for itr in bounds))
        return False

    def match_label(self, match_data):
        """A short label for the match: its percentage, if any, and the
        text picking it gives, on one line."""
        text = ' '.join(self.controller.match_text(match_data).split())
        quality = match_data.get('quality')
        if quality is None:
            return text
        return '%s  %s' % (format_percent(int(quality) / 100, decimals=0), text)

    def _on_target_populate_popup(self, textbox, menu):
        liststore = self.tmwindow.liststore
        if not len(liststore) or not textbox.get_editable():
            return
        submenu = Gtk.Menu()
        for row in liststore:
            item = Gtk.MenuItem(label=self.match_label(row[0]))
            item.get_child().set_ellipsize(Pango.EllipsizeMode.END)
            item.get_child().set_max_width_chars(40)
            item.connect('activate', self._on_insert_from_menu, textbox, row[0])
            submenu.append(item)
        # l10n: A submenu of the target's context menu: the translation suggestions, to insert
        # one at the cursor or over the selected text
        item = Gtk.MenuItem.new_with_mnemonic(_('Insert _Suggestion'))
        item.set_submenu(submenu)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(item)
        menu.show_all()

    def _on_insert_from_menu(self, _item, textbox, match_data):
        kept, self._menu_selection = self._menu_selection, None
        if kept is not None and kept[0] is textbox:
            buffer = textbox.get_buffer()
            buffer.select_range(*(buffer.get_iter_at_offset(offset) for offset in kept[1]))
        self.controller.insert_match(match_data, textbox)

    def _translated_menu(self):
        """View > Suggestions for Translated Units' choices (#1396)."""
        menu, group = Gtk.Menu(), None
        self.mnu_translated_modes = {}
        for mode, label in (
            # l10n: Suggestions for every translated unit
            (ALWAYS, _('_Always')),
            # l10n: Suggestions for translated units that need work or review, or fail a quality check
            (ATTENTION, _('Needs A_ttention')),
            # l10n: Suggestions for translated units only on request (F9)
            (NEVER, _('_Never')),
        ):
            item = Gtk.RadioMenuItem.new_with_mnemonic_from_widget(group, label)
            item.set_active(mode == self.controller.translated_suggestions)
            item.connect('toggled', self._on_translated_mode_toggled, mode)
            menu.append(item)
            self.mnu_translated_modes[mode] = group = item
        return menu

    def _on_translated_mode_toggled(self, item, mode):
        if item.get_active():
            self.controller.set_translated_suggestions(mode)

    def _setup_menu_items(self):
        mainview = self.controller.main_controller.view
        self.mnui_view = mainview.gui.get_object('menuitem_view')
        self.menu = self.mnui_view.get_submenu()

        self.mnu_suggestions = Gtk.CheckMenuItem.new_with_mnemonic(
            label=_('Translation _Suggestions')
        )
        self.mnu_suggestions.show()
        self.menu.append(self.mnu_suggestions)

        self.mnu_translated = Gtk.MenuItem.new_with_mnemonic(
            # l10n: A submenu: which units already translated show suggestions unasked, not only on request (F9).
            label=_('Suggestions for _Translated Units')
        )
        self.mnu_translated.set_submenu(self._translated_menu())
        self.mnu_translated.show_all()
        self.menu.append(self.mnu_translated)

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

        self._add_fetch_menu_item(mainview)

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
        self.menu.remove(self.mnu_translated)
        self.menu.remove(self.mnu_summon)
        if self.mnu_fetch is not None:
            self.mnu_fetch.get_parent().remove(self.mnu_fetch)
            self.mnu_fetch = None
        self.remove_import_menu_item()
        self.remove_exclude_menu_item()
        self._match_menu.destroy()

    def _add_fetch_menu_item(self, mainview):
        """Offer File > Fetch Suggestions for This File… (#1679)."""
        menu = mainview.gui.get_object('menuitem_file').get_submenu()
        self.mnu_fetch = Gtk.MenuItem.new_with_mnemonic(
            # l10n: Gets suggestions from online services for every unit of the file still
            # to translate, for use without a connection
            _('Fetch Su_ggestions for This File…')
        )
        after = mainview.gui.get_object('mnu_binary_export')
        menu.insert(self.mnu_fetch, menu.get_children().index(after) + 1)
        store_controller = getattr(self.controller.main_controller, 'store_controller', None)
        get_store = getattr(store_controller, 'get_store', lambda: None)
        self.mnu_fetch.set_sensitive(get_store() is not None)
        self.mnu_fetch.connect('activate', self._on_fetch)
        self.mnu_fetch.show()
        getattr(mainview, 'sync_menubar', lambda: None)()

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

    def add_exclude_menu_item(self, model):
        """Offer File > Add to Local TM on Save for the open file, asking
        C{model} (one with C{store_status()} and C{set_exclude()})."""
        self._exclude_model = model
        if self.mnu_add_on_save is None:
            mainview = self.controller.main_controller.view
            menu = mainview.gui.get_object('menuitem_file').get_submenu()
            item = Gtk.CheckMenuItem.new_with_mnemonic(
                _(
                    # l10n: Ticked, saving the file adds its translations to the
                    # local translation memory; unticked, it leaves them out
                    'Add to Local _TM on Save'
                )
            )
            after = self.mnu_import or mainview.gui.get_object('mnu_binary_export')
            menu.insert(item, menu.get_children().index(after) + 1)
            item.show()
            self.mnu_add_on_save = item
            self._add_on_save_id = item.connect('toggled', self._on_toggle_add_on_save)
            mainview.sync_menubar()
        self._show_add_on_save()

    def remove_exclude_menu_item(self):
        self._exclude_model = None
        self._opened += 1
        if self.mnu_add_on_save is None:
            return
        self.mnu_add_on_save.get_parent().remove(self.mnu_add_on_save)
        self.mnu_add_on_save = None
        self.controller.main_controller.view.sync_menubar()

    def _set_add_on_save(self, active):
        """Tick or untick the item without acting on it."""
        item = self.mnu_add_on_save
        item.handler_block(self._add_on_save_id)
        try:
            item.set_active(active)
        finally:
            item.handler_unblock(self._add_on_save_id)

    def _show_add_on_save(self):
        """Show whether the local TM takes the open file's translations."""
        self._opened += 1
        if self.mnu_add_on_save is None:
            return
        store_controller = self.controller.main_controller.store_controller
        has_file = store_controller.get_store() is not None
        self.mnu_add_on_save.set_sensitive(has_file)
        self._set_add_on_save(True)
        self._add_on_save_file = store_controller.get_store_filename() if has_file else None
        if not has_file:
            return
        opened = self._opened

        def done(excluded, _units):
            if opened == self._opened:
                self._set_add_on_save(not excluded)

        self._exclude_model.store_status(store_controller, done, lambda: None)

    def _on_toggle_add_on_save(self, item):
        model = self._exclude_model
        store_controller = self.controller.main_controller.store_controller
        if model is None or store_controller.get_store() is None:
            return
        name = os.path.basename(store_controller.get_store_filename())
        # The answer to the question asked on opening the file is no
        # longer wanted: it would tick the item again.
        self._opened += 1
        opened = self._opened

        def later(func, *args):
            # Not from inside the HTTP client's callback: a dialog runs a
            # main loop of its own.
            def run():
                if opened == self._opened:
                    func(*args)
                return False

            GLib.idle_add(run)

        def failed(active, message):
            self._set_add_on_save(active)
            self.controller.main_controller.view.show_error_dialog(message=message)

        if item.get_active():
            model.set_exclude(
                store_controller,
                False,
                lambda: later(self._included, store_controller, model, name),
                lambda: later(
                    failed,
                    False,
                    _("Couldn’t add “%(file)s” to the translation memory.") % {'file': name},
                ),
            )
            return

        remove_failed = (
            failed,
            True,
            _("Couldn’t remove “%(file)s” from the translation memory.") % {'file': name},
        )

        def exclude(units):
            if units and not self.confirm_exclude(name, units):
                self._set_add_on_save(True)
                return
            model.set_exclude(store_controller, True, lambda: None, lambda: later(*remove_failed))

        model.store_status(
            store_controller,
            lambda _excluded, units: later(exclude, units),
            lambda: later(*remove_failed),
        )

    def _included(self, store_controller, model, name):
        if store_controller.is_modified():
            self.controller.main_controller.view.show_info_notice(
                _("“%(file)s” will be added to the translation memory when you save it.")
                % {'file': name}
            )
        else:
            # Saved as it is: add it now, as saving it would.
            model.push_store(store_controller)

    def confirm_exclude(self, name, units):
        """Whether the user wants the open file's C{units} translations
        removed from the TM, and the file kept out of it."""
        dialog = Gtk.MessageDialog(
            transient_for=self.controller.main_controller.view.main_window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=ngettext(
                # l10n: After unticking File > Add to Local TM on Save
                "Stop adding “%(file)s” to the translation memory and remove the "
                "%(count)d translation it added?",
                "Stop adding “%(file)s” to the translation memory and remove the "
                "%(count)d translations it added?",
                units,
            )
            % {'count': units, 'file': name},
        )
        dialog.format_secondary_text(
            _('Translations another file also added stay in the translation memory.')
        )
        dialog.add_button(_gtk_text('_Cancel'), Gtk.ResponseType.CANCEL)
        remove = dialog.add_button(_('_Remove'), Gtk.ResponseType.ACCEPT)
        remove.get_style_context().add_class('destructive-action')
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        try:
            return dialog.run() == Gtk.ResponseType.ACCEPT
        finally:
            dialog.destroy()

    def _build_import_chooser(self):
        from virtaal.support.trados_compat import TM_IMPORT_FORMATS
        from virtaal.views.mainview import add_translation_file_filters

        mainview = self.controller.main_controller.view
        chooser = Gtk.FileChooserNative.new(
            _('Import into Translation Memory'),
            mainview.main_window,
            Gtk.FileChooserAction.OPEN,
            None,
            None,
        )
        chooser.set_select_multiple(True)
        add_translation_file_filters(chooser, TM_IMPORT_FORMATS)
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
        skipped = [0]
        failed = []

        def done(path, count, unreadable=0):
            added[0] += count
            skipped[0] += unreadable
            finish()

        def error(path):
            failed.append(path)
            finish()

        def finish():
            waiting[0] -= 1
            if not waiting[0]:
                # Not from inside the HTTP client's callback: the error
                # dialog runs a main loop of its own.
                GLib.idle_add(
                    self._show_import_result, len(filenames), added[0], failed, skipped[0]
                )

        for filename in filenames:
            model.import_file(filename, done, error)

    def _show_import_result(self, total, added, failed, skipped=0):
        mainview = self.controller.main_controller.view
        if len(failed) < total:
            notice = ngettext(
                # l10n: After File > Import into Translation Memory: how many
                # translations the translation memory did not have yet
                'Added %(count)d translation to the translation memory.',
                'Added %(count)d translations to the translation memory.',
                added,
            ) % {'count': added}
            if skipped:
                notice += ' ' + (
                    ngettext(
                        # l10n: Follows the number of translations added: rows
                        # of a Microsoft glossary file that couldn't be read
                        "Skipped %(count)d row that couldn’t be read.",
                        "Skipped %(count)d rows that couldn’t be read.",
                        skipped,
                    )
                    % {'count': skipped}
                )
            mainview.show_info_notice(notice, for_file=False)
        if failed:
            mainview.show_error_dialog(
                message='\n'.join(
                    _("Couldn’t import “%(file)s” into the translation memory.")
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
            lines = []
            if len(liststore) < 9:
                shortcut = match_shortcut(len(liststore) + 1)
                if shortcut:
                    lines.append(shortcut)
            if match.get('tm_target'):
                lines.append(
                    _(
                        # l10n: Tooltip on a suggestion whose placeables or accelerator
                        # were changed to fit the current unit. %(target)s is the
                        # suggestion as the translation memory or machine
                        # translation service gave it.
                        'Adapted to this unit from: %(target)s'
                    )
                    % {'target': match['tm_target']}
                )
            liststore.append([match, GLib.markup_escape_text('\n'.join(lines))])

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

    def _on_fetch(self, _item=None):
        from .fetchdialog import FetchSuggestionsDialog

        models = self.controller.plugin_controller.plugins.values()
        parent = self.controller.main_controller.view.main_window
        FetchSuggestionsDialog(self.controller, models, parent).show()

    def select_backends(self, parent):
        from virtaal.views.backendselect import select_backends

        select_backends(
            self.controller.main_controller,
            self.controller.plugin_controller,
            self.controller.config,
            'basetmmodel',
            # l10n: The 'sources' here refer to different translation memory plugins,
            # such as local tm, open-tran.eu, the current file, etc.
            title=_('Select Translation Memory Sources'),
            message=_('Select the sources that should be queried for translation memory'),
            size=(550, 580),
            parent=parent,
        )

    def copy_match(self, match_data):
        """Put the match's text, as picking it gives it, on the clipboard."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self.controller.match_text(match_data), -1)

    def popup_match_menu(self, path, event):
        """Show the Copy / Insert menu for the match in row C{path}."""
        liststore = self.tmwindow.liststore
        self._menu_match = liststore.get_value(liststore.get_iter(path), 0)
        self.tmwindow.treeview.get_selection().select_path(path)
        self._match_menu.popup_at_pointer(event)

    def select_match(self, match_data):
        """Select the match data as accepted by the user."""
        self.controller.select_match(match_data)

    def _match_iter(self, index):
        """The shown TM window's row for match C{index} (from 1), or None."""
        if index < 1 or not self.isvisible:
            return None
        liststore = self.tmwindow.liststore
        if index > len(liststore):
            return None
        return liststore.iter_nth_child(None, index - 1)

    def select_match_index(self, index):
        """Select the TM match with the given index (first match is 1).
        This method causes a row in the TM window's C{Gtk.TreeView} to be
        selected and activated. This runs this class's C{_on_select_match()}
        method which runs C{select_match()}."""
        itr = self._match_iter(index)
        if itr is None:
            return

        logging.debug('Selecting index %d' % (index))
        liststore = self.tmwindow.liststore
        path = liststore.get_path(itr)
        self.tmwindow.treeview.get_selection().select_iter(itr)
        self.tmwindow.treeview.row_activated(path, self.tmwindow.tvc_match)

    def insert_match_index(self, index):
        """Insert match C{index} (from 1) at the cursor, as Insert at
        Cursor does. Whether there was one to insert."""
        itr = self._match_iter(index)
        if itr is None:
            return False
        self.controller.insert_match(self.tmwindow.liststore.get_value(itr, 0))
        return True

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
        # The target grows and shrinks while typing, and the editor moves
        # with its row without its allocation changing.
        window = editor.get_window() if editor is not None else None
        origin = window.get_origin() if window is not None else None
        geometry = (origin and (origin.x, origin.y), allocation.y, allocation.height)
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
        if self.mnu_fetch is not None:
            self.mnu_fetch.set_sensitive(False)
        self._show_add_on_save()

    def _on_store_loaded(self, storecontroller):
        self.mnu_suggestions.set_sensitive(True)
        self.mnu_summon.set_sensitive(True)
        if self.mnu_fetch is not None:
            self.mnu_fetch.set_sensitive(True)
        self._show_add_on_save()

    def _on_store_saved(self, storecontroller):
        # Saved under another name: a file of its own in the TM.
        if storecontroller.get_store_filename() == self._add_on_save_file:
            return
        item = self.mnu_add_on_save
        if item is not None and self._add_on_save_file and not item.get_active():
            # Kept out of the TM, it stays out under its new name: excluding
            # also removes anything its save added there.
            self._add_on_save_file = storecontroller.get_store_filename()
            self._opened += 1
            self._exclude_model.set_exclude(storecontroller, True, lambda: None, lambda: None)
            return
        self._show_add_on_save()

    def _on_store_view_scroll(self, *args):
        # The list also scrolls to keep a growing target in view.
        if not self.isvisible:
            return
        if self._selected_target_in_view():
            self.update_geometry()
        else:
            self.hide()

    def _on_toggle_show_tm(self, *args):
        self.mnu_translated.set_sensitive(self.active)
        if not self.active and self.isvisible and not self.summoned:
            self.hide()
        elif self.active and not self.isvisible:
            self.controller.update_suggestions()
