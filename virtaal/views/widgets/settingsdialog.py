#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A dialog editing a backend's few settings, such as an API key, a
server's host and port (#1519, #1647) or a switch (BOOL)."""

from collections import namedtuple

from gi.repository import Gtk

TEXT, SECRET, PORT, BOOL = 'text', 'secret', 'port', 'bool'

Field = namedtuple('Field', 'key label kind required', defaults=(TEXT, False))
"""One setting: its config key, its label (with a mnemonic), its kind
and whether it may be empty. A PORT is always required; a BOOL is a
check box, its value True or False."""


def api_key_field():
    """The API key setting machine translation services share: empty
    turns the service off."""
    # l10n: The key a machine translation service gives each user
    return Field('api_key', _('API _key:'), SECRET)


def valid(field, text):
    """Whether text (stripped) is a usable value for field."""
    if field.kind == BOOL:
        return True
    if field.kind == PORT:
        return text.isascii() and text.isdigit() and 1 <= int(text) <= 65535
    return bool(text) or not field.required


class SettingsDialog:
    """OK is only sensitive while every field is valid. While problem()
    has something to say, it is shown below the fields."""

    def __init__(self, parent, title, fields, values):
        self.fields = tuple(fields)
        self.dialog = Gtk.Dialog(title=title, modal=True)
        if isinstance(parent, Gtk.Widget):
            self.dialog.set_transient_for(parent.get_toplevel())
        self.dialog.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        self.btn_ok = self.dialog.add_button(_("_OK"), Gtk.ResponseType.OK)
        self.dialog.set_default_response(Gtk.ResponseType.OK)

        grid = Gtk.Grid(row_spacing=6, column_spacing=12, border_width=12)
        self.entries = {}
        row = 0
        self.switches = {}
        for field in self.fields:
            if field.kind == BOOL:
                check = Gtk.CheckButton.new_with_mnemonic(field.label)
                check.set_active(bool(values.get(field.key)))
                grid.attach(check, 1, row, 1, 1)
                self.switches[field.key] = check
                row += 1
                continue
            label = Gtk.Label(label=field.label, use_underline=True, xalign=0)
            entry = Gtk.Entry(
                text=str(values.get(field.key, '')),
                activates_default=True,
                hexpand=True,
                width_chars=6 if field.kind == PORT else 30,
            )
            if field.kind == PORT:
                entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
            entry.connect('changed', lambda _entry: self._update_ok())
            label.set_mnemonic_widget(entry)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            self.entries[field.key] = entry
            row += 1
            if field.kind == SECRET:
                # Hidden from onlookers; shown to check a pasted key.
                entry.set_visibility(False)
                entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
                # l10n: Shows the API key's characters instead of dots
                show = Gtk.CheckButton.new_with_mnemonic(_('_Show key'))
                show.connect('toggled', lambda btn, e=entry: e.set_visibility(btn.get_active()))
                grid.attach(show, 1, row, 1, 1)
                row += 1
        self.problem_label = Gtk.Label(wrap=True, xalign=0, max_width_chars=40)
        grid.attach(self.problem_label, 0, row, 2, 1)
        self.dialog.get_content_area().add(grid)
        grid.show_all()
        self._update_ok()

    def get_values(self):
        """The fields' stripped text, and each BOOL's True or False, by
        config key."""
        values = {key: entry.get_text().strip() for key, entry in self.entries.items()}
        values.update((key, check.get_active()) for key, check in self.switches.items())
        return values

    def problem(self):
        """Why the values can't be used, to tell the user, or None."""
        return None

    def is_valid(self):
        values = self.get_values()
        return all(valid(field, values[field.key]) for field in self.fields) and not self.problem()

    def _update_ok(self):
        self.btn_ok.set_sensitive(self.is_valid())
        text = self.problem() or ''
        self.problem_label.set_text(text)
        self.problem_label.set_visible(bool(text))

    def run(self):
        """@returns: The new values by config key, or C{None} if cancelled."""
        self.dialog.show()
        self.dialog.present()
        response = self.dialog.run()
        values = self.get_values() if self.is_valid() else None
        self.dialog.destroy()
        if response == Gtk.ResponseType.OK:
            return values
        return None
