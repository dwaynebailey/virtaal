#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The dialog asking for a Git server's username and password or
token, for this session, or kept in the system's password store if the
user ticks it. Asked from a git thread, answered on the main loop."""

import threading

from gi.repository import GLib, Gtk

from virtaal.support.gitrepo import Credential
from virtaal.views.widgets.settingsdialog import BOOL, SECRET, Field, SettingsDialog


class SignInDialog(SettingsDialog):
    def __init__(self, parent, key, username='', refused=False, can_remember=True):
        fields = [
            # l10n: The user's name on a Git server, such as GitHub's or GitLab's
            Field('username', _('_Username:'), required=True),
            Field(
                'password',
                # l10n: A Git server's password, or an access token it gave the user to use in its place
                _('_Password or token:'),
                SECRET,
                required=True,
                reveal=_('S_how token'),
            ),
        ]
        if can_remember:
            # l10n: Keep this username and password in the computer's own store (Windows Credential Manager, the macOS Keychain, GNOME Keyring), not in Virtaal
            fields.append(Field('remember', _('_Remember in this computer’s password store'), BOOL))
        super().__init__(
            parent,
            # l10n: The title of the dialog asking for a Git server's username and password; %(server)s is its address, such as https://gitlab.com
            _('Sign In to %(server)s') % {'server': key},
            fields,
            {'username': username},
            # l10n: The button sending the username and password typed in
            ok_label=_('_Sign In'),
        )
        if refused:
            # l10n: A Git server refused the username and password or token the user gave
            text = _('The server refused this username and password or token.')
        else:
            # l10n: Above the fields of the dialog asking for a Git server's username and password
            text = _('The repository asks for a username and a password or token.')
        self.intro = Gtk.Label(label=text, wrap=True, xalign=0, max_width_chars=40)
        self.intro.set_margin_start(12)
        self.intro.set_margin_end(12)
        self.intro.set_margin_top(12)
        area = self.dialog.get_content_area()
        area.pack_start(self.intro, False, False, 0)
        area.reorder_child(self.intro, 0)
        self.intro.show()
        if username:
            self.entries['password'].grab_focus()

    def credential(self):
        """The answer, or None if cancelled."""
        values = self.run()
        if values is None:
            return None
        return Credential(values['username'], values['password'], values.get('remember', False))


def asker(parent_of, can_remember):
    """ask() for GitCredentials: the dialog over parent_of()'s window,
    run on the main loop while the git thread asking waits."""

    def ask(key, username, refused):
        def show():
            try:
                dialog = SignInDialog(parent_of(), key, username, refused, can_remember())
                answer.append(dialog.credential())
            finally:
                done.set()
            return False

        answer, done = [], threading.Event()
        if threading.current_thread() is threading.main_thread():
            show()
        else:
            GLib.idle_add(show)
            done.wait()
        return answer[0] if answer else None

    return ask
