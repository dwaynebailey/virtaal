#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

__all__ = ['forall_widgets', 'run_dialog']

import functools

from gi.repository import GLib, Gtk


@functools.singledispatch
def get_children(widget):
    return []


@get_children.register(Gtk.Container)
def get_children_container(widget):
    return widget.get_children()


def forall_widgets(f, widget):
    f(widget)
    for child in get_children(widget):
        forall_widgets(f, child)


def run_dialog(dialog, show_all=False):
    """Run C{dialog} modally, then hide it and give the focus back to the
    window it is transient for.

    @returns: The response."""
    # present() only raises and focuses a window that is already shown.
    if show_all:
        dialog.show_all()
    else:
        dialog.show()
    dialog.present()
    transient_for = dialog.get_transient_for()
    response = dialog.run()
    dialog.hide()
    if transient_for is not None:
        GLib.idle_add(transient_for.present)
    return response
