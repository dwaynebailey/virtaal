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
downloads it into a work copy (workcopy.py) and opens that file;
File > Submit to Server… sends its changed units back (submit.py, for
a platform that can_submit), and File > Update from Server… brings in
others' changes (update.py).
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


Budget = namedtuple('Budget', 'left reset_in limit', defaults=(None,))
"""A server's request budget as it last said: requests left, seconds
until that count starts again, and how many it allows in all (None if
it doesn't say)."""


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

    # Submit (a platform that can_submit) #

    def locked(self, project, component, on_done, on_error):
        """on_done(whether no one may change the translations of
        component, or of project when the platform has no components)."""
        raise NotImplementedError

    def unit_count(self, project, component, language, on_done, on_error):
        """on_done(how many units a language has now, as download() would
        give them), for Update to see that the set of units changed."""
        raise NotImplementedError

    def changed_since(self, project, component, language, since, on_done, on_error):
        """on_done(RemoteUnits): at least the units of a language that
        changed at or after since (UTC, ISO 8601), as they are now."""
        raise NotImplementedError

    def submit_unit(self, unit_id, target, code, on_done, on_error):
        """Changes one unit on the server: its target (a list of forms,
        all of a plural's) and its state as the platform's code.
        on_done(the code the server stored), or on_error(PlatformError):
        status 400 or 403 for this unit alone (the request or the
        permission), 423 locked, 429 out of budget, else no reply."""
        raise NotImplementedError

    def budget(self):
        """The Budget as the server last said, to this session or another
        of the same server and credentials, or None if it doesn't say."""
        return None

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
    can_submit = False
    """Its sessions take a work copy's changes back (Session's Submit
    methods)."""
    rate_limit = None
    """(requests, seconds) a server allows by default, for one that
    doesn't say what is left: Submit stays under it."""

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

    def state_code(self, state, target):
        """The platform's state code for one of Virtaal's states (a
        StateEnum) of a unit with target (its forms), to submit."""
        return state

    def can_import(self):
        return False

    def import_servers(self):
        """[(URL, credentials dict)] found elsewhere, such as in another
        program's settings."""
        return []


def pending_message(count, location):
    """ "3 changes to Hello/Program are not yet submitted." """
    from virtaal.common.pan_app import ngettext
    from virtaal.support.libi18n.numbers import format_number

    text = ngettext(
        # l10n: %(location)s is a translation server's project and component, such as "Hello/Program"
        '%(count)s change to %(location)s is not yet submitted.',
        '%(count)s changes to %(location)s are not yet submitted.',
        count,
    )
    return text % {'count': format_number(count), 'location': location}


class Registry:
    """The platforms whose plug-ins are on, and the menu items that open
    from them and send back and bring in a work copy's changes, there
    while any is.

    It also keeps unsubmitted work in view, without ever standing in the
    way: a notice when a work copy with saved changes not yet submitted
    is opened (with Submit and Update), when it is left for another file
    or closed (with Open), and once at start-up for the one saved last
    when Virtaal starts with no file. Quitting asks nothing: the changes
    are saved and wait in the work copy.

    Its Trickler (trickle.py) sends the finished units of a work copy
    as it is saved, for a server with that option on, and tries again
    when the network comes back."""

    def __init__(self):
        self.platforms = []
        self.main_controller = None
        self.menuitem = None
        self.submit_item = None
        self.update_item = None
        self._handlers = []
        self._notice = None
        self._shown = None
        """The work copy open, as last seen."""
        self._reminded = False
        from .trickle import Trickler

        self.trickler = Trickler(self._server_of, self._trickled, self._trickle_conflicts)

    def add(self, platform, main_controller):
        self.main_controller = main_controller
        if platform not in self.platforms:
            self.platforms.append(platform)
        if self.menuitem is None:
            self._add_menu_item()
        if not self._reminded:
            self._reminded = True
            from gi.repository import GLib

            GLib.idle_add(self._remind_at_start, priority=GLib.PRIORITY_LOW)

    def remove(self, platform):
        if platform in self.platforms:
            self.platforms.remove(platform)
        if not self.platforms and self.menuitem is not None:
            for item in (self.menuitem, self.submit_item, self.update_item):
                menu = item.get_parent()
                if menu is not None:
                    menu.remove(item)
            self.menuitem = self.submit_item = self.update_item = None
            for source, handler in self._handlers:
                source.disconnect(handler)
            self._handlers = []
            self.trickler.stop()
            self._hide_notice()
            self._sync_menubar()
        else:
            self.sync_submit()

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
        # l10n: File menu: send the changes of a file opened from a translation server back to it
        self.submit_item = Gtk.MenuItem.new_with_mnemonic(_('Su_bmit to Server…'))
        self.submit_item.connect('activate', lambda _item: self.submit())
        self.submit_item.show()
        save_as = view.gui.get_object('mnu_saveas')
        menu.insert(self.submit_item, menu.get_children().index(save_as) + 1)
        # l10n: File menu: bring others' changes from a translation server into a file opened from it
        self.update_item = Gtk.MenuItem.new_with_mnemonic(_('Up_date from Server…'))
        self.update_item.connect('activate', lambda _item: self.update())
        self.update_item.show()
        menu.insert(self.update_item, menu.get_children().index(self.submit_item) + 1)
        # Save follows the file's modified flag.
        self._watch(view.gui.get_object('mnu_save'), 'notify::sensitive')
        store_controller = getattr(self.main_controller, 'store_controller', None)
        for signal in ('store-loaded', 'store-saved', 'store-closed'):
            self._watch(store_controller, signal)
        self._watch_network()
        self.sync_submit()
        self._sync_menubar()

    def _watch_network(self):
        from . import trickle

        monitor = trickle.network_monitor()
        if monitor is not None:
            handler = monitor.connect(
                'network-changed', lambda _monitor, up: self.trickler.network_changed(up)
            )
            self._handlers.append((monitor, handler))

    def _watch(self, source, signal):
        if source is not None:
            handler = source.connect(signal, lambda *_args: self._changed(signal))
            self._handlers.append((source, handler))

    def _changed(self, signal):
        self.sync_submit()
        if signal not in ('store-loaded', 'store-saved', 'store-closed'):
            return
        self._sync_notice(signal)
        copy = self._open_work_copy()
        if signal == 'store-saved':
            self.trickler.saved(copy)
        else:
            self.trickler.stop()
            if copy is not None:
                self.trickler.start(copy)

    def _open_work_copy(self):
        """The work copy open, if its platform's plug-in is on and it
        can submit."""
        store_controller = getattr(self.main_controller, 'store_controller', None)
        copy = getattr(store_controller, 'work_copy', None)
        platform = copy and self.platform_of(copy)
        return copy if platform is not None and platform.can_submit else None

    def platform_of(self, copy):
        return next((p for p in self.platforms if p.id == copy.record['platform']), None)

    def sync_submit(self):
        """Submit is there for a work copy with changes, saved or not;
        Update for any work copy."""
        if self.submit_item is None:
            return
        copy = self._open_work_copy()
        store_controller = getattr(self.main_controller, 'store_controller', None)
        modified = store_controller is not None and store_controller.is_modified()
        self.submit_item.set_sensitive(copy is not None and (modified or bool(copy.pending())))
        self.update_item.set_sensitive(copy is not None)

    def submit(self, parent=None):
        """Saves the work copy open, then sends its changes to its
        server (submitdialog.py)."""
        from .submitdialog import SubmitDialogs

        copy = self._open_work_copy()
        if copy is None:
            return None
        self.trickler.stop()
        result = SubmitDialogs(self, copy, self._parent(parent)).run()
        self._sync_notice()
        return result

    def update(self, parent=None):
        """Saves the work copy open, then brings in what others changed
        on its server (updatedialog.py)."""
        from .updatedialog import UpdateDialogs

        copy = self._open_work_copy()
        if copy is None:
            return None
        self.trickler.stop()
        result = UpdateDialogs(self, copy, self._parent(parent)).run()
        self._sync_notice()
        copy = self._open_work_copy()
        if copy is not None:
            self.trickler.start(copy)
        return result

    # Send finished units as you go #

    def _server_of(self, copy):
        platform = self.platform_of(copy)
        return platform and self.servers().find(platform, copy.record['server'])

    def _trickled(self, result):
        """After finished units went in the background."""
        from .submitdialog import sent_message

        if result.file_changed:
            # A state the server stored, such as needs editing for a
            # failing check, shows; saving it is the user's.
            store_controller = self.main_controller.store_controller
            store_controller.view._treeview.queue_draw()
            store_controller.set_modified(True)
        view = self.main_controller.view
        view.set_statusbar_message(sent_message(result.sent))
        view.refresh_title()
        self.sync_submit()
        self._sync_notice()

    def _trickle_conflicts(self, count):
        from virtaal.common.pan_app import ngettext
        from virtaal.support.libi18n.numbers import format_number

        text = ngettext(
            # l10n: After units were sent to a translation server in the background, as they were saved
            '%(count)s finished unit was also changed on the server, and waits for Submit.',
            '%(count)s finished units were also changed on the server, and wait for Submit.',
            count,
        )
        self._show_notice(
            text % {'count': format_number(count)},
            [(_('Su_bmit to Server…'), lambda: self.submit())],
        )

    # Unsubmitted work #

    def _show_notice(self, text, actions):
        """A dismissable notice below the menu bar, in place of the last
        one this showed; actions are (label, function) buttons."""
        from gi.repository import Gtk

        self._hide_notice()
        vbox = self.main_controller.view.gui.get_object('vbox_main')
        if vbox is None:
            return
        infobar = Gtk.InfoBar(show_close_button=True)
        infobar.set_message_type(Gtk.MessageType.INFO)
        label = Gtk.Label(label=text, wrap=True, xalign=0)
        infobar.get_content_area().pack_start(label, True, True, 0)
        for number, (button, _action) in enumerate(actions):
            infobar.add_button(button, number)

        def on_response(_infobar, response):
            self._hide_notice()
            if 0 <= response < len(actions):
                actions[response][1]()

        infobar.connect('response', on_response)
        self._notice = infobar
        infobar.show_all()
        vbox.pack_start(infobar, False, False, 0)
        vbox.reorder_child(infobar, 1)

    def _hide_notice(self):
        if self._notice is not None:
            self._notice.destroy()
            self._notice = None

    def notice_text(self):
        """The text of the notice showing, or None."""
        if self._notice is None:
            return None
        return self._notice.get_content_area().get_children()[0].get_text()

    def _sync_notice(self, signal=None):
        """Opened: says how many changes wait, with Submit and Update.
        Saved or sent: keeps that count right. Left: says so once, with
        Open."""
        copy = self._open_work_copy()
        left = self._shown
        self._shown = copy
        if copy is not None:
            count = len(copy.pending())
            if signal == 'store-loaded' or (count and self._notice is not None):
                self._notice_open(copy, count)
            elif not count:
                self._hide_notice()
            return
        if signal in ('store-loaded', 'store-closed') and left is not None:
            from . import workcopy

            again = workcopy.for_file(left.path)
            if again is not None and again.unsubmitted():
                self._notice_left(again)
                return
        if signal == 'store-loaded':
            self._hide_notice()

    def _notice_open(self, copy, count):
        if not count:
            self._hide_notice()
            return
        self._show_notice(
            pending_message(count, copy.location()),
            [
                (_('Su_bmit to Server…'), lambda: self.submit()),
                (_('Up_date from Server…'), lambda: self.update()),
            ],
        )

    def _notice_left(self, copy):
        from .browsedialog import _gtk_text

        path = copy.path
        self._show_notice(
            pending_message(copy.unsubmitted(), copy.location()),
            # GTK's own "_Open", as the browser's button.
            [(_gtk_text('_Open'), lambda: self.main_controller.open_file(path))],
        )

    def _remind_at_start(self):
        """With no file open, the work copy saved last that holds changes
        not submitted, once."""
        from . import workcopy

        store_controller = getattr(self.main_controller, 'store_controller', None)
        if not self.platforms or getattr(store_controller, 'store', None) is not None:
            return False
        copies = workcopy.unsubmitted_copies({p.id for p in self.platforms})
        if copies:
            self._notice_left(copies[0])
        return False

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
