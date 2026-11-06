#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from gi.repository import GLib, Gtk

from virtaal.views.widgets.util import forall_widgets, get_children, run_dialog


def test_get_children_default_is_empty():
    assert get_children(Gtk.Label(label='leaf')) == []


def test_get_children_container_returns_its_children():
    box = Gtk.Box()
    child = Gtk.Label(label='child')
    box.add(child)

    assert get_children(box) == [child]


def test_forall_widgets_visits_parent_then_children():
    box = Gtk.Box()
    child = Gtk.Label(label='child')
    box.add(child)

    visited = []
    forall_widgets(visited.append, box)

    assert visited == [box, child]


class _FakeDialog:
    def __init__(self, transient_for=None):
        self.calls = []
        self.transient_for = transient_for

    def show(self):
        self.calls.append('show')

    def show_all(self):
        self.calls.append('show_all')

    def present(self):
        self.calls.append('present')

    def get_transient_for(self):
        return self.transient_for

    def run(self):
        self.calls.append('run')
        return Gtk.ResponseType.OK

    def hide(self):
        self.calls.append('hide')


class _FakeParent:
    def __init__(self, dialog):
        self.dialog = dialog

    def present(self):
        self.dialog.calls.append('parent')


def test_run_dialog_shows_presents_runs_then_hides(monkeypatch):
    monkeypatch.setattr(GLib, 'idle_add', lambda func, *args: func(*args))
    dialog = _FakeDialog()
    dialog.transient_for = _FakeParent(dialog)

    assert run_dialog(dialog) == Gtk.ResponseType.OK
    assert dialog.calls == ['show', 'present', 'run', 'hide', 'parent']


def test_run_dialog_can_show_all_and_needs_no_parent():
    dialog = _FakeDialog()

    run_dialog(dialog, show_all=True)

    assert dialog.calls == ['show_all', 'present', 'run', 'hide']


def test_run_dialog_presents_the_parent_from_idle(monkeypatch):
    # The parent only takes the focus back once the dialog is gone.
    queued = []
    monkeypatch.setattr(GLib, 'idle_add', lambda func, *args: queued.append(func))
    dialog = _FakeDialog()
    parent = _FakeParent(dialog)
    dialog.transient_for = parent

    run_dialog(dialog)

    assert dialog.calls[-1] == 'hide'
    assert queued == [parent.present]
