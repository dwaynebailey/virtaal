#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The dialogs asking for a Git server's username and password or
token, and for GitHub's token (forge.py), for this session, or kept in
the system's password store if the user ticks it. Asked from a git
thread, answered on the main loop."""

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


class TokenDialog(SettingsDialog):
    """GitHub's personal access token, for submitting from a fork."""

    def __init__(self, parent, host, refused=False, can_remember=True):
        fields = [Field('token', _('_Token:'), SECRET, required=True, reveal=_('S_how token'))]
        if can_remember:
            fields.append(Field('remember', _('_Remember in this computer’s password store'), BOOL))
        super().__init__(
            parent, _('Sign In to %(server)s') % {'server': host}, fields, {}, _('_Sign In')
        )
        if refused:
            text = _('The server refused the token.')
        else:
            text = _(
                # l10n: Above the field of the dialog asking for a GitHub token; %(server)s is GitHub's address, such as github.com. Personal access token is GitHub's name for it, under Settings, Developer settings
                'Submitting to %(server)s needs a personal access token that may push to your '
                'fork and open pull requests.'
            ) % {'server': host}
        self.intro = Gtk.Label(label=text, wrap=True, xalign=0, max_width_chars=40)
        for side in ('start', 'end', 'top'):
            getattr(self.intro, 'set_margin_' + side)(12)
        area = self.dialog.get_content_area()
        area.pack_start(self.intro, False, False, 0)
        area.reorder_child(self.intro, 0)
        self.intro.show()

    def credential(self):
        """The answer (its password the token), or None if cancelled."""
        values = self.run()
        if values is None:
            return None
        return Credential('', values['token'], values.get('remember', False))


def token_asker(parent_of, can_remember):
    """ask() for forge.Tokens: the token dialog, as asker()'s."""
    return _on_main_loop(
        lambda host, refused: TokenDialog(parent_of(), host, refused, can_remember()).credential()
    )


def asker(parent_of, can_remember):
    """ask() for GitCredentials: the dialog over parent_of()'s window,
    run on the main loop while the git thread asking waits."""
    return _on_main_loop(
        lambda key, username, refused: SignInDialog(
            parent_of(), key, username, refused, can_remember()
        ).credential()
    )


def _on_main_loop(dialog):
    """ask(*args): dialog(*args)'s answer, run on the main loop while the
    thread asking waits."""

    def ask(*args):
        def show():
            try:
                answer.append(dialog(*args))
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
