#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Every item of a menu has its own access key, in English, with the
plug-ins' items in place."""

from types import SimpleNamespace

import pytest
from gi.repository import Gtk
from test_scaffolding import TestScaffolding

from virtaal.common.signaltracker import SignalTracker
from virtaal.plugins.terminology.models.localfile.localfileview import LocalFileView

CHECKED = ('menuitem_edit', 'menuitem_view', 'menuitem_nav', 'menuitem_help')


def access_key(label):
    """The letter after the first lone underscore, or C{None}."""
    i = 0
    while i < len(label) - 1:
        if label[i] == '_':
            if label[i + 1] != '_':
                return label[i + 1].lower()
            i += 1
        i += 1
    return None


def item_label(item):
    """The label as GTK reads it: with no underline set, an underscore
    is shown as it is and marks no key."""
    child = item.get_child()
    if not isinstance(child, Gtk.Label):
        return ''
    label = child.get_label()
    return label if child.get_use_underline() else label.replace('_', '__')


def clashes(menu):
    """C{{key: [labels]}} for the keys two items of C{menu} share, and the
    labels of items with none."""
    keys, missing = {}, []
    for item in menu.get_children():
        if isinstance(item, Gtk.SeparatorMenuItem):
            continue
        label = item_label(item)
        key = access_key(label)
        if key is None:
            missing.append(label)
        else:
            keys.setdefault(key, []).append(label)
    return {k: v for k, v in keys.items() if len(v) > 1}, missing


def submenus(menu):
    yield menu
    for item in menu.get_children():
        if item.get_submenu() is not None:
            yield from submenus(item.get_submenu())


def test_access_key_skips_doubled_underscores():
    assert access_key('Add _Term "a__b"…') == 't'
    assert access_key('a__b') is None


class TestAccessKeys(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        view = self.main_controller.view
        terminology = LocalFileView.__new__(LocalFileView)
        terminology.mainview = view
        terminology.controller = SimpleNamespace(main_controller=self.main_controller)
        terminology._signal_tracker = SignalTracker()
        terminology._setup_menus()
        self.terminology = terminology

    def teardown_class(self):
        self.terminology._signal_tracker.disconnect_all()
        TestScaffolding.teardown_class(self)

    @pytest.mark.parametrize('name', CHECKED)
    def test_each_item_has_its_own_access_key(self, name):
        top = self.main_controller.view.gui.get_object(name)
        for menu in submenus(top.get_submenu()):
            assert clashes(menu) == ({}, [])
