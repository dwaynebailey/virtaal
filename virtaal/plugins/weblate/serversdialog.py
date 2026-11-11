#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Weblate Servers dialog: add, edit and remove servers, each an
address and a token, test a connection, and import wlc's servers."""

from gi.repository import Gtk

from virtaal.common.pan_app import ngettext
from virtaal.support.libi18n.numbers import format_number
from virtaal.views.widgets.settingsdialog import SECRET, Field, SettingsDialog
from virtaal.views.widgets.util import run_dialog

from . import client as weblate
from .config import wlc_config_path

COL_URL = 0


def error_message(error):
    """What to tell the user about a WeblateError."""
    if error.kind == weblate.AUTH:
        # l10n: A Weblate server answered that the API token is wrong or expired
        return _('The server refused the token.')
    if error.kind == weblate.NETWORK:
        # l10n: %(reason)s is the network library's own message, in English
        return _("Couldn't connect to the server: %(reason)s") % {'reason': error.detail}
    if error.kind == weblate.NOT_WEBLATE:
        return _('No Weblate server answered at this address.')
    # l10n: %(status)s is an HTTP status code, such as 403 or 500
    message = _('The server replied with error %(status)s.') % {'status': error.status}
    if error.detail:
        message += '\n' + error.detail
    return message


def connected_message(count):
    # l10n: A Weblate server accepted the token; %(count)s is the number of projects it shows
    text = ngettext('Connected: %(count)s project', 'Connected: %(count)s projects', count)
    return text % {'count': format_number(count)}


class ServerDialog(SettingsDialog):
    """A server's address and token; OK only for a usable address."""

    def __init__(self, parent, url='', token=''):
        fields = (
            # l10n: The address of a Weblate server, such as https://hosted.weblate.org/
            Field('url', _('_URL:'), required=True),
            # l10n: The API token a Weblate server gives each user, on its profile page
            Field('token', _('_Token:'), SECRET, required=True),
        )
        super().__init__(parent, _('Weblate'), fields, {'url': url, 'token': token})

    def problem(self):
        if weblate.unencrypted(self.get_values()['url']):
            # l10n: Shown for an address starting http:// instead of https://
            return _('Use https: the token would be sent unencrypted.')
        return None

    def is_valid(self):
        return super().is_valid() and weblate.api_url(self.get_values()['url']) is not None


class ServersDialog:
    """Lists the servers in a ServerList and saves each change at once."""

    def __init__(self, servers, parent=None, client_factory=weblate.Client):
        self.servers = servers
        self.client_factory = client_factory
        self._client = None

        self.dialog = Gtk.Dialog(title=_('Weblate Servers'), transient_for=parent, modal=True)
        self.dialog.set_default_size(560, 320)
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

        self.store = Gtk.ListStore(str)
        self.treeview = Gtk.TreeView(model=self.store, headers_visible=False)
        self.treeview.append_column(Gtk.TreeViewColumn('', Gtk.CellRendererText(), text=COL_URL))
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
        self.btn_import = self._button(buttons, _('_Import from wlc'), self._on_import)

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
        return model[it][COL_URL] if it is not None else None

    def _refresh(self, select=None):
        select = select or self.selected()
        self.store.clear()
        for url in self.servers.urls():
            it = self.store.append([url])
            if url == select:
                self.treeview.get_selection().select_iter(it)
        if self.selected() is None and len(self.store):
            self.treeview.get_selection().select_path(Gtk.TreePath.new_first())
        self._update_buttons()

    def _update_buttons(self):
        chosen = self.selected() is not None
        for button in (self.btn_edit, self.btn_remove, self.btn_test):
            button.set_sensitive(chosen)
        self.btn_import.set_sensitive(wlc_config_path() is not None)

    def _show_status(self, text, busy=False):
        self.status.set_text(text)
        self.spinner.set_visible(busy)
        self.spinner.set_property('active', busy)

    def _save(self, select=None):
        self.servers.save()
        self._refresh(select)

    # Actions #

    def _ask(self, url='', token=''):
        """The new (URL, token), or None if cancelled."""
        values = ServerDialog(self.dialog, url, token).run()
        if values is None:
            return None
        return weblate.api_url(values['url']), values['token']

    def _on_add(self):
        found = self._ask()
        if found and self.servers.put(*found):
            self._save(found[0])
            self._on_test()

    def _on_edit(self):
        url = self.selected()
        if url is None:
            return
        found = self._ask(url, self.servers.token(url))
        if found and self.servers.put(*found, replace=url):
            self._save(found[0])

    def _on_remove(self):
        url = self.selected()
        if url is not None:
            self.servers.remove(url)
            self._save()
            self._show_status('')

    def _on_import(self):
        self.servers.import_wlc()
        self._save()

    def _on_test(self):
        url = self.selected()
        if url is None:
            return
        if self._client is not None:
            self._client.cancel()
        self._client = self.client_factory(url, self.servers.token(url))
        self._show_status(url, busy=True)
        self._client.test_connection(
            lambda count: self._show_status(connected_message(count)),
            lambda error: self._show_status(error_message(error)),
        )

    def run(self):
        run_dialog(self.dialog)
        if self._client is not None:
            self._client.cancel()
        self.dialog.destroy()
