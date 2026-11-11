#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Translation platforms: servers with an API that hold projects to
translate, such as Weblate (#3337).

Each kind of platform is a Platform, added by its plug-in to the
registry while the plug-in is on. Everything here works through that
interface, so the server list (servers.py), its dialog
(serversdialog.py) and File > Open from Translation Server…
(browsedialog.py) serve every kind alike. A platform's server holds
projects; a project holds components (when has_components) and each
component, or the project itself, holds languages. Opening a language
downloads it into a work copy (workcopy.py) and opens that file.
"""

from collections import namedtuple

# Error kinds.
AUTH = 'auth'
"""The credentials were refused (401)."""
NETWORK = 'network'
"""No reply: no route, a refused connection, TLS, a timeout."""
NOT_PLATFORM = 'not-platform'
"""A reply, but not from the platform's API."""
HTTP = 'http'
"""Any other error status, including a rate limit too long to wait."""


class PlatformError:
    """Why a request failed: kind, the HTTP status if any, and the
    server's or curl's own words."""

    def __init__(self, kind, status=None, detail=''):
        self.kind = kind
        self.status = status
        self.detail = detail

    def __repr__(self):
        return '<PlatformError %s %s %r>' % (self.kind, self.status, self.detail)


Item = namedtuple('Item', 'name slug translated glossary', defaults=(None, False))
"""A project, component or language as a platform lists it: its name,
the slug (a language's code) that identifies it on the server, how
much of it is translated (0 to 1, or None) and whether it is a
glossary."""

RemoteUnit = namedtuple(
    'RemoteUnit', 'id position context source target state code last_updated read_only'
)
"""A unit as the platform holds it: its id there, its position in the
file, its context (PO's msgctxt, a monolingual file's key), its source
and target (lists of forms), its state as Virtaal's StateEnum (None if
it has none Virtaal shows), the platform's own state code, when it
last changed (the platform's text, or None) and whether no one may
change it there."""

Download = namedtuple('Download', 'filename data template units names')
"""A language of a component (or project) as downloaded: the file's
path as the platform has it ("po/af.po", "/" between parts) and its
bytes in the platform's own format, the template as (path, bytes) or
None, the RemoteUnits, and the names of the project and the component
({'project': ..., 'component': ...}) to show."""


class Session:
    """One server of a platform, as its address and credentials. Every
    method returns at once and later calls one of its callbacks on the
    GLib main loop."""

    def test_connection(self, on_done, on_error):
        """on_done(number of projects the credentials see), or
        on_error(PlatformError)."""
        raise NotImplementedError

    def projects(self, query, cursor, on_page, on_error):
        """One page of projects: the first when cursor is None, else the
        one cursor names. query is the filter's text when the platform
        supports_search, else None. on_page(items, next cursor or None)."""
        raise NotImplementedError

    def components(self, project, on_done, on_error):
        """on_done(items): every component of project."""
        raise NotImplementedError

    def languages(self, project, component, on_done, on_error):
        """on_done(items): the languages of component (None when the
        platform has no components), each with how much is translated."""
        raise NotImplementedError

    def download(self, project, component, language, on_done, on_error):
        """on_done(Download): the file of a language of component (None
        when the platform has no components), and its units."""
        raise NotImplementedError

    def cancel(self):
        """Drops the replies to every request made so far."""


class Platform:
    """A kind of translation platform. A subclass sets these and keeps
    its servers' credentials in its own file, owner-only."""

    id = ''
    """Saved with each server to name its kind, such as 'weblate'."""
    name = ''
    """The platform's name, as the user knows it."""
    has_components = True
    """Projects hold components, which hold languages; else projects
    hold languages."""
    supports_search = False
    """The server can filter its projects (Session.projects' query);
    else the filter matches the projects loaded so far."""
    import_label = None
    """The label of a button importing servers from elsewhere, or None."""

    def credential_fields(self):
        """The fields a server needs besides its URL: Field()s, keyed as
        the credentials dict is."""
        return ()

    def fields(self):
        from virtaal.views.widgets.settingsdialog import Field

        # l10n: The address of a translation server, such as https://hosted.weblate.org/
        return (Field('url', _('_URL:'), required=True), *self.credential_fields())

    def normalise_url(self, text):
        """The server's address as saved, for what a user typed, or None
        if it can't be used."""
        raise NotImplementedError

    def url_problem(self, text):
        """Why the address can't be used, to tell the user, or None."""
        return None

    def not_platform_message(self):
        """What to say when something else answers at an address."""
        raise NotImplementedError

    def load_servers(self):
        """([(URL, credentials dict)] in order, the URL last used or '')."""
        raise NotImplementedError

    def save_servers(self, servers, last_url):
        """Saves servers, a list as load_servers() gives it."""
        raise NotImplementedError

    def session(self, url, credentials):
        raise NotImplementedError

    def can_import(self):
        return False

    def import_servers(self):
        """[(URL, credentials dict)] found elsewhere, such as in another
        program's settings."""
        return []


class Registry:
    """The platforms whose plug-ins are on, and the menu item that opens
    from them, there while any is."""

    def __init__(self):
        self.platforms = []
        self.main_controller = None
        self.menuitem = None

    def add(self, platform, main_controller):
        self.main_controller = main_controller
        if platform not in self.platforms:
            self.platforms.append(platform)
        if self.menuitem is None:
            self._add_menu_item()

    def remove(self, platform):
        if platform in self.platforms:
            self.platforms.remove(platform)
        if not self.platforms and self.menuitem is not None:
            menu = self.menuitem.get_parent()
            if menu is not None:
                menu.remove(self.menuitem)
            self.menuitem = None
            self._sync_menubar()

    def _sync_menubar(self):
        getattr(self.main_controller.view, 'sync_menubar', lambda: None)()

    def _add_menu_item(self):
        from gi.repository import Gtk

        view = self.main_controller.view
        menu = view.gui.get_object('menu_file')
        after = view.gui.get_object('mnu_open_folder')
        # l10n: File menu: browse a translation server (Weblate, ...) and open a translation from it
        label = _('Open from Tra_nslation Server…')
        self.menuitem = Gtk.MenuItem.new_with_mnemonic(label)
        self.menuitem.connect('activate', lambda _item: self.browse())
        self.menuitem.show()
        menu.insert(self.menuitem, menu.get_children().index(after) + 1)
        self._sync_menubar()

    def _parent(self, parent):
        return parent or self.main_controller.view.main_window

    def servers(self):
        from .servers import ServerList

        return ServerList(self.platforms)

    def configure(self, parent=None, servers=None):
        from .serversdialog import ServersDialog

        ServersDialog(servers or self.servers(), self._parent(parent)).run()

    def browse(self, parent=None):
        """Browses a server, asking for one first if there is none, and
        opens the language chosen. Whether one was."""
        from .browsedialog import BrowseDialog

        servers = self.servers()
        if not servers.servers:
            self.configure(parent, servers)
            if not servers.servers:
                return False
        path = BrowseDialog(servers, self._parent(parent)).run()
        if path is None:
            return False
        return bool(self.main_controller.open_file(path))


registry = Registry()
