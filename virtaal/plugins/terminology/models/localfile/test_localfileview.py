#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from types import SimpleNamespace

from virtaal.plugins.terminology.models.localfile.localfileview import FileSelectDialog


def test_treeview_scrolled_window_is_not_focusable(monkeypatch):
    # The .ui file marks it focusable, which swallows Tab/Down meant
    # for the treeview inside it (same issue as prefsview.py's
    # plugin/placeables lists and weblookup.py's URL list).
    monkeypatch.setattr(FileSelectDialog, '_init_add_chooser', lambda self: None)

    dialog = FileSelectDialog(model=SimpleNamespace(controller=None, config={'files': []}))

    assert not dialog.tvw_termfiles.get_parent().get_can_focus()
