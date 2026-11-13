#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import locale

from gi.repository import Gdk, Gtk

from virtaal.views.widgets.popupmenubutton import PopupMenuButton

from .baseview import BaseView


class ChecksProjectView(BaseView):
    """Manages project type selection and other quality checks UI elements."""

    # INITIALIZERS #
    def __init__(self, controller):
        self.controller = controller
        self._checker_menu_items = {}
        self._create_project_button()

    def _create_project_button(self):
        self.btn_proj = PopupMenuButton(label=_('Project Type'))
        menu = Gtk.Menu()
        names = []
        for checkercode in self.controller.checker_info:
            checkername = self.controller._checker_code_to_name[checkercode]
            names.append(checkername)
        for checkername in sorted(names, key=locale.strxfrm):
            mitem = Gtk.MenuItem(label=checkername)
            mitem.show()
            mitem.connect('activate', self._on_menu_item_activate)
            menu.append(mitem)
            self._checker_menu_items[checkername] = mitem
        self.btn_proj.set_menu(menu)
        self.btn_proj.connect('key-press-event', self._on_key_press)

    # METHODS #
    def show(self):
        statusbar = self.controller.main_controller.view.status_bar
        for child in statusbar.get_children():
            if child is self.btn_proj:
                return
        statusbar.pack_start(self.btn_proj, False, True, 0)
        statusbar.show_all()

    def set_checker_name(self, cname):
        # l10n: The label indicating the checker style (GNOME/KDE/whatever)
        self.btn_proj.set_label(_('Checks: %(checker_name)s') % {'checker_name': cname})

    # EVENT HANDLER #
    def _on_key_press(self, _button, event):
        # Ctrl+Tab goes from the units' list here, then back to editing.
        mods = event.state & Gtk.accelerator_get_default_mod_mask()
        if event.keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab) and mods == Gdk.ModifierType.CONTROL_MASK:
            self.controller.main_controller.store_controller.view.edit_at_cursor()
            return True
        return False

    def _on_menu_item_activate(self, menuitem):
        self.controller.set_checker_by_name(menuitem.get_child().get_label())
