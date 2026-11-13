#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

from gi.repository import GLib, Gtk

from virtaal.support import priorityfile
from virtaal.views.widgets.popupmenubutton import (
    POS_NW_SW,
    PopupMenuButton,
    close_on_enter,
)

from .basemode import BaseMode


class PriorityMode(BaseMode):
    """Include the untranslated and fuzzy units of the priority levels a
    project's priority file gives them (see docs/priority_files.rst)."""

    name = 'Priority'
    # l10n: A navigation mode: moves between the untranslated and fuzzy messages of the translation priority levels chosen
    display_name = _("Priority")
    widgets = []

    # INITIALIZERS #
    def __init__(self, controller):
        """Constructor.
        @type  controller: virtaal.controllers.ModeController
        @param controller: The ModeController that managing program modes."""
        self.controller = controller
        self.filter_levels = []
        self._menuitem_levels = {}
        self._stats_changed_id = None
        self.priority_file = None
        # {level id: [unit index, ...]} in the open file.
        self.level_units = {}
        # {translation file: priority file} loaded by hand.
        self._chosen = {}

    # METHODS #
    def selected(self):
        store_controller = self.controller.main_controller.store_controller
        self.storecursor = store_controller.cursor
        if not self._stats_changed_id:
            self._stats_changed_id = store_controller.connect(
                'stats-changed', self._on_stats_changed
            )
        self._find_priority_file()
        self._index_levels()
        self.filter_levels = [level for level in self.filter_levels if level in self.level_units]

        self._add_widgets()
        self._update_button_label()
        self.update_indices()

    def unselected(self):
        if self._stats_changed_id:
            self.controller.main_controller.store_controller.disconnect(self._stats_changed_id)
            self._stats_changed_id = None

    def update_indices(self):
        if not self.storecursor or not self.storecursor.model:
            return

        if not self.filter_levels:
            # No level selected is no filter: include every unit.
            self.storecursor.indices = list(range(len(self.storecursor.model)))
            return

        indices = set()
        for level in self.filter_levels:
            indices.update(self._incomplete(level))
        self.storecursor.indices = sorted(indices)

    def _model(self):
        return self.storecursor.model if self.storecursor else None

    def _stats(self):
        model = self._model()
        return (model.stats if model else None) or {}

    def _store_filename(self):
        model = self._model()
        filename = model.get_filename() if model else None
        return filename if filename and os.path.isfile(filename) else None

    def _find_priority_file(self):
        filename = self._store_filename()
        self.priority_file = None
        if filename is None:
            return
        if filename in self._chosen:
            self.priority_file = priorityfile.load(self._chosen[filename])
            if self.priority_file is not None:
                return
        store = getattr(self._model(), '_trans_store', None)
        header = None
        if hasattr(store, 'parseheader'):
            header = store.parseheader().get(priorityfile.HEADER)
        self.priority_file = priorityfile.find(filename, header)

    def _index_levels(self):
        self.level_units = {}
        model = self._model()
        if self.priority_file is None or not model:
            return
        keys = [priorityfile.unit_key(model[index]) for index in range(len(model))]
        for level_id, level_keys in self.priority_file.level_keys(set(keys)).items():
            self.level_units[level_id] = [
                index for index, key in enumerate(keys) if key in level_keys
            ]

    def _incomplete(self, level_id):
        stats = self._stats()
        incomplete = set(stats.get('untranslated', [])) | set(stats.get('fuzzy', []))
        return [index for index in self.level_units.get(level_id, []) if index in incomplete]

    def _level_label(self, level):
        units = self.level_units.get(level.id, [])
        translated = set(self._stats().get('translated', []))
        # l10n: A priority level in the Priority navigation mode, and its progress in this file: e.g. "Level 1 (40/52)"
        return _("%(name)s (%(translated)d/%(total)d)") % {
            'name': level.name,
            'translated': sum(1 for index in units if index in translated),
            'total': len(units),
        }

    def _add_widgets(self):
        # Destroy the previous popup button and its menu now rather
        # than just dropping the references - see WorkflowMode's.
        if hasattr(self, 'btn_popup'):
            self.btn_popup.menu.destroy()
            self.btn_popup.destroy()
            self._menuitem_levels = {}

        table = self.controller.view.mode_box
        self.btn_popup = PopupMenuButton(menu_pos=POS_NW_SW)
        self.btn_popup.set_relief(Gtk.ReliefStyle.NORMAL)
        menu = Gtk.Menu()
        close_on_enter(menu)
        self._fill_menu(menu)
        self.btn_popup.set_menu(menu)

        self.widgets = [self.btn_popup]

        self.btn_popup.set_vexpand(True)
        table.attach(self.btn_popup, 2, 0, 1, 1)

        table.show_all()

    def _fill_menu(self, menu):
        for menuitem in menu.get_children():
            # destroy(), not menu.remove() - see PopupMenuButton.set_menu().
            menuitem.destroy()
        self._menuitem_levels = {}
        if self.priority_file is None:
            # l10n: No file of translation priority levels was found for this file
            menuitem = Gtk.MenuItem(label=_('No Priority File'))
            menuitem.set_sensitive(False)
            menu.append(menuitem)
        else:
            for level in self.priority_file.levels:
                menuitem = Gtk.CheckMenuItem(label=self._level_label(level))
                if level.description:
                    menuitem.set_tooltip_text(level.description)
                menuitem.set_active(level.id in self.filter_levels)
                self._menuitem_levels[menuitem] = level
                menuitem.connect('toggled', self._on_level_menuitem_toggled)
                menu.append(menuitem)
            self._update_menu_sensitivity()
        menu.append(Gtk.SeparatorMenuItem())
        menuitem = Gtk.MenuItem.new_with_mnemonic(_('_Load Priority File…'))
        menuitem.connect('activate', self._on_load_activate)
        menu.append(menuitem)
        menu.show_all()

    def _update_menu_sensitivity(self):
        """Only levels with incomplete units can be ticked; a ticked one
        stays sensitive so it can be unticked."""
        for menuitem, level in self._menuitem_levels.items():
            menuitem.set_sensitive(bool(self._incomplete(level.id)) or menuitem.get_active())

    def _update_button_label(self):
        if self.priority_file is None:
            self.btn_popup.set_label(_('No Priority File'))
            self.btn_popup.set_tooltip_text(None)
            return
        self.btn_popup.set_tooltip_text(self.priority_file.path)
        names = [
            level.name for level in self.priority_file.levels if level.id in self.filter_levels
        ]
        if not names:
            # l10n: This is the button where the user can select units by translation priority
            self.btn_popup.set_label(_('Select Priorities'))
            return
        label = ', '.join(names[:3])
        if len(names) > 3:
            label += '...'
        self.btn_popup.set_label(label)

    def _choose_priority_file(self):
        """Ask for a priority file for the open file."""
        filename = self._store_filename()
        chooser = Gtk.FileChooserNative.new(
            _('Choose a Priority File'),
            self.controller.main_controller.view.main_window,
            Gtk.FileChooserAction.OPEN,
            None,
            None,
        )
        yaml_filter = Gtk.FileFilter()
        yaml_filter.set_name(_('Priority Files'))
        yaml_filter.add_pattern('*.yaml')
        yaml_filter.add_pattern('*.yml')
        chooser.add_filter(yaml_filter)
        all_filter = Gtk.FileFilter()
        all_filter.set_name(_("All Files"))
        all_filter.add_pattern('*')
        chooser.add_filter(all_filter)
        if filename:
            chooser.set_current_folder(os.path.dirname(filename))
        response = chooser.run()
        path = chooser.get_filename()
        chooser.destroy()
        # GtkFileChooserNative returns ACCEPT on a real accept, never OK.
        if response == Gtk.ResponseType.ACCEPT and path:
            self.use_priority_file(path)
        return GLib.SOURCE_REMOVE

    def use_priority_file(self, path):
        """Use the priority file at path for the open file, or none if it
        doesn't load."""
        self.priority_file = priorityfile.load(path)
        filename = self._store_filename()
        if filename and self.priority_file is not None:
            self._chosen[filename] = path
        self.filter_levels = []
        self._index_levels()
        self._fill_menu(self.btn_popup.menu)
        self._update_button_label()
        self.update_indices()

    # EVENT HANDLERS #
    def _on_level_menuitem_toggled(self, checkmenuitem):
        # Deferred like WorkflowMode's, out of the popup's own grab.
        self.filter_levels = [
            level.id for menuitem, level in self._menuitem_levels.items() if menuitem.get_active()
        ]
        GLib.idle_add(self._apply_filter_levels)
        self._update_button_label()
        self._update_menu_sensitivity()

    def _apply_filter_levels(self):
        self.update_indices()
        return False

    def _on_load_activate(self, _menuitem):
        # Once the popup has closed.
        GLib.idle_add(self._choose_priority_file)

    def _on_stats_changed(self, _store_controller):
        # The units under review stay until the selection changes.
        if not self._model():
            return
        for menuitem, level in self._menuitem_levels.items():
            menuitem.set_label(self._level_label(level))
        self._update_menu_sensitivity()
