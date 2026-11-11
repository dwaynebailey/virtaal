#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Weblate: translate a Weblate server's translations in Virtaal (#3337).

Phase W1 connects and browses: Configure… lists the servers, and File >
Open from Weblate… browses one. Off until enabled (pan_app.Settings'
plugin_state).
"""

from gi.repository import Gtk

from virtaal.controllers.baseplugin import BasePlugin

from .config import ServerList


class Plugin(BasePlugin):
    display_name = _('Weblate')
    description = _('Translate projects hosted on Weblate servers')
    version = 0.1

    # INITIALIZERS #
    def __init__(self, internal_name, main_controller):
        self.internal_name = internal_name
        self.main_controller = main_controller
        self.configure_func = self.configure
        self.servers = ServerList()
        self._setup_menu_item()

    def _setup_menu_item(self):
        mainview = self.main_controller.view
        menu = mainview.gui.get_object('menu_file')
        after = mainview.gui.get_object('mnu_open_folder')
        self.menuitem = Gtk.MenuItem.new_with_mnemonic(_('Open from _Weblate…'))
        self.menuitem.connect('activate', lambda _item: self.browse())
        self.menuitem.show()
        menu.insert(self.menuitem, menu.get_children().index(after) + 1)
        getattr(mainview, 'sync_menubar', lambda: None)()

    # METHODS #
    def _parent(self, parent=None):
        return parent or self.main_controller.view.main_window

    def configure(self, parent=None):
        from .serversdialog import ServersDialog

        ServersDialog(self.servers, self._parent(parent)).run()

    def browse(self, parent=None):
        """Browses a server, asking for one first if there is none."""
        from .browsedialog import BrowseDialog

        self.servers.load()
        if not self.servers.urls():
            self.configure(parent)
            if not self.servers.urls():
                return
        BrowseDialog(self.servers, self._parent(parent)).run()

    def destroy(self):
        menu = self.menuitem.get_parent()
        if menu is not None:
            menu.remove(self.menuitem)
        getattr(self.main_controller.view, 'sync_menubar', lambda: None)()
