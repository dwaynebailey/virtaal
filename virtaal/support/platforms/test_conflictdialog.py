#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from gi.repository import Gtk

from virtaal.support.platforms import submit
from virtaal.support.platforms.submitdialog import ConflictDialog


def test_the_first_units_choice_has_the_focus(monkeypatch):
    conflicts = [
        submit.Conflict(0, 'Open', ['Maak oop'], ['Open']),
        submit.Conflict(1, 'Save', ['Stoor'], ['Bewaar']),
    ]
    dialog = ConflictDialog(conflicts, Gtk.Window())
    focused = []
    monkeypatch.setattr(Gtk.Dialog, 'run', lambda d: focused.append(d.get_focus()) or 0)

    dialog.run()

    assert focused == [dialog.buttons[0][submit.SKIP]]
