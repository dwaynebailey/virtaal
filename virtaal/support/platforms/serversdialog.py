#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Translation Servers dialog: add, edit and remove the servers of
every platform, test a connection, and import servers a platform finds
elsewhere (Weblate: wlc's)."""

from gi.repository import Gdk, GLib, Gtk

from virtaal.common.pan_app import ngettext
from virtaal.support.libi18n.numbers import format_number
from virtaal.views.widgets.settingsdialog import SettingsDialog
from virtaal.views.widgets.util import run_dialog

from . import AUTH, NETWORK, NOT_PLATFORM

COL_TYPE, COL_URL = 0, 1


def error_message(error, platform):
    """What to tell the user about a PlatformError."""
    if error.kind == AUTH:
        # l10n: A translation server answered that the API token is wrong or expired
        return _('The server refused the token.')
    if error.kind == NETWORK:
        # l10n: %(reason)s is the network library's own message, in English
        return _("Couldn't connect to the server: %(reason)s") % {'reason': error.detail}
    if error.kind == NOT_PLATFORM:
        return platform.not_platform_message()
    # l10n: %(status)s is an HTTP status code, such as 403 or 500
    message = _('The server replied with error %(status)s.') % {'status': error.status}
    if error.detail:
        message += '\n' + error.detail
    return message


def connected_message(count):
    # l10n: A translation server accepted the token; %(count)s is the number of projects it shows
    text = ngettext('Connected: %(count)s project', 'Connected: %(count)s projects', count)
    return text % {'count': format_number(count)}


def clock_time(seconds):
    """The time of day seconds from now, such as 14:05."""
    return GLib.DateTime.new_now_local().add_seconds(seconds).format('%H:%M')


def budget_message(budget):
    """What the server allows: "4,980 of 5,000 requests left until
    08:40.", or None when it doesn't say."""
    if budget is None or budget.limit is None:
        return None
    text = ngettext(
        # l10n: A translation server's limit of requests an hour; %(time)s is when it allows more, such as 14:05
        '%(left)s of %(limit)s request left until %(time)s.',
        '%(left)s of %(limit)s requests left until %(time)s.',
        budget.limit,
    )
    return text % {
        'left': format_number(budget.left),
        'limit': format_number(budget.limit),
        'time': clock_time(budget.reset_in),
    }


class ServerDialog(SettingsDialog):
    """A server's address and credentials, as its platform asks for
    them; OK only for a usable address."""

    def __init__(self, parent, platform, url='', credentials=None):
        self.platform = platform
        values = {'url': url, **(credentials or {})}
        super().__init__(parent, platform.name, platform.fields(), values)

    def problem(self):
        return self.platform.url_problem(self.get_values()['url'])

    def is_valid(self):
        url = self.get_values()['url']
        return super().is_valid() and self.platform.normalise_url(url) is not None


class ServersDialog:
    """Lists a ServerList's servers and saves each change at once."""

    def __init__(self, servers, parent=None):
        self.servers = servers
        self._session = None

        # l10n: The servers of translation platforms, such as Weblate
        self.dialog = Gtk.Dialog(title=_('Translation Servers'), transient_for=parent, modal=True)
        self.dialog.set_default_size(600, 320)
        self.dialog.add_button(_('_Close'), Gtk.ResponseType.CLOSE)

        notice = Gtk.Label(
            label=_(
                'Virtaal sends your token, and the translations you submit, '
                'to the servers you add here.'
            ),
            wrap=True,
            xalign=0,
        )
        notice.get_style_context().add_class('dim-label')

        self.store = Gtk.ListStore(str, str, object)
        self.treeview = Gtk.TreeView(model=self.store)
        for title, column in ((_('Type'), COL_TYPE), (_('URL'), COL_URL)):
            self.treeview.append_column(
                Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=column)
            )
        self.treeview.get_accessible().set_name(self.dialog.get_title())
        self.treeview.get_selection().connect('changed', lambda _sel: self._update_buttons())
        self.treeview.connect('row-activated', lambda *_args: self._on_edit())
        scrolled = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, hexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.treeview)

        buttons = Gtk.ButtonBox(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        buttons.set_layout(Gtk.ButtonBoxStyle.START)
        self.btn_add = self._button(buttons, _('_Add…'), self._on_add)
        self.btn_edit = self._button(buttons, _('_Edit…'), self._on_edit)
        self.btn_remove = self._button(buttons, _('_Remove'), self._on_remove)
        self.btn_test = self._button(buttons, _('_Test Connection'), self._on_test)
        self.import_buttons = {
            platform: self._button(
                buttons, platform.import_label, lambda p=platform: self._on_import(p)
            )
            for platform in servers.platforms
            if platform.import_label
        }

        self.spinner = Gtk.Spinner()
        self.status = Gtk.Label(wrap=True, xalign=0, selectable=True, hexpand=True)
        status_box = Gtk.Box(spacing=6)
        status_box.pack_start(self.spinner, False, False, 0)
        status_box.pack_start(self.status, True, True, 0)

        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        grid.attach(notice, 0, 0, 2, 1)
        grid.attach(scrolled, 0, 1, 1, 1)
        grid.attach(buttons, 1, 1, 1, 1)
        grid.attach(status_box, 0, 2, 2, 1)
        scrolled.set_vexpand(True)
        content = self.dialog.get_content_area()
        content.set_border_width(12)
        content.add(grid)
        grid.show_all()
        self.spinner.hide()

        self._refresh()

    def _button(self, box, label, handler):
        button = Gtk.Button.new_with_mnemonic(label)
        button.connect('clicked', lambda _btn: handler())
        box.add(button)
        return button

    # State #

    def selected(self):
        model, it = self.treeview.get_selection().get_selected()
        return model[it][2] if it is not None else None

    def _refresh(self, select=None):
        select = select or self.selected()
        self.store.clear()
        for server in self.servers.servers:
            it = self.store.append([server.platform.name, server.url, server])
            if server is select:
                self.treeview.get_selection().select_iter(it)
        if self.selected() is None and len(self.store):
            self.treeview.get_selection().select_path(Gtk.TreePath.new_first())
        self._update_buttons()

    def _update_buttons(self):
        chosen = self.selected() is not None
        for button in (self.btn_edit, self.btn_remove, self.btn_test):
            button.set_sensitive(chosen)
        for platform, button in self.import_buttons.items():
            button.set_sensitive(platform.can_import())

    def _show_status(self, text, busy=False):
        self.status.set_text(text)
        self.spinner.set_visible(busy)
        self.spinner.set_property('active', busy)

    def _save(self, select=None):
        self.servers.save()
        self._refresh(select)

    # Actions #

    def _ask(self, platform, url='', credentials=None):
        """The new (URL, credentials), or None if cancelled."""
        values = ServerDialog(self.dialog, platform, url, credentials).run()
        if values is None:
            return None
        url = values.pop('url')
        return url, values

    def _on_add(self):
        platforms = self.servers.platforms
        if len(platforms) == 1:
            self._add(platforms[0])
            return
        menu = Gtk.Menu()
        for platform in platforms:
            item = Gtk.MenuItem(label=platform.name)
            item.connect('activate', lambda _item, p=platform: self._add(p))
            menu.append(item)
        menu.show_all()
        menu.attach_to_widget(self.btn_add)
        self.add_menu = menu
        menu.popup_at_widget(self.btn_add, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)

    def _add(self, platform):
        found = self._ask(platform)
        server = found and self.servers.put(platform, *found)
        if server:
            self._save(server)
            self._on_test()

    def _on_edit(self):
        old = self.selected()
        if old is None:
            return
        found = self._ask(old.platform, old.url, old.credentials)
        server = found and self.servers.put(old.platform, *found, replace=old)
        if server:
            self._save(server)

    def _on_remove(self):
        server = self.selected()
        if server is not None:
            self.servers.remove(server)
            self._save()
            self._show_status('')

    def _on_import(self, platform):
        self.servers.import_servers(platform)
        self._save()

    def _on_test(self):
        server = self.selected()
        if server is None:
            return
        if self._session is not None:
            self._session.cancel()
        session = self._session = server.session()
        self._show_status(server.url, busy=True)

        def connected(count):
            lines = [connected_message(count), budget_message(session.budget())]
            self._show_status('\n'.join(line for line in lines if line))

        session.test_connection(
            connected, lambda error: self._show_status(error_message(error, server.platform))
        )

    def run(self):
        run_dialog(self.dialog)
        if self._session is not None:
            self._session.cancel()
        self.dialog.destroy()
