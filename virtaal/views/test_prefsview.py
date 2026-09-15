#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from virtaal.views import prefsview
from virtaal.views.baseview import BaseView
from virtaal.views.prefsview import PreferencesView


def _load_widgets():
    view = PreferencesView.__new__(PreferencesView)
    gui = BaseView.load_builder_file(["virtaal", "virtaal.ui"], root='PreferencesDlg', domain="virtaal")
    view._widgets = {
        'scrwnd_placeables': gui.get_object('scrwnd_placeables'),
        'scrwnd_plugins': gui.get_object('scrwnd_plugins'),
    }
    return view


def test_plugins_page_scrolled_window_propagates_natural_width():
    # A plugin's inline "Configure..." button got clipped/hidden - a
    # ScrolledWindow doesn't request its child's actual width by
    # default, it just shrinks it instead.
    view = _load_widgets()

    view._init_plugins_page()

    assert view._widgets['scrwnd_plugins'].get_property('propagate-natural-width')


def test_placeables_page_scrolled_window_propagates_natural_width():
    view = _load_widgets()

    view._init_placeables_page()

    assert view._widgets['scrwnd_placeables'].get_property('propagate-natural-width')


def test_plugins_page_scrolled_window_is_not_focusable():
    # The .ui file marks it focusable, which swallows Tab/Down meant
    # for the list inside it.
    view = _load_widgets()

    view._init_plugins_page()

    assert not view._widgets['scrwnd_plugins'].get_can_focus()


def test_placeables_page_scrolled_window_is_not_focusable():
    view = _load_widgets()

    view._init_placeables_page()

    assert not view._widgets['scrwnd_placeables'].get_can_focus()


def _make_plugin_items(count, enabled_name=None):
    return [
        {
            'name': 'Plugin %02d' % i,
            'desc': '',
            'enabled': 'Plugin %02d' % i == enabled_name,
            'data': {'internal_name': 'plugin%02d' % i},
            'config': None,
        }
        for i in range(count)
    ]


def test_plugin_data_restores_scroll_position_after_a_rebuild(monkeypatch):
    # plugin_data's setter rebuilds the whole ListStore on every
    # toggle (a plugin's own enabling can add/remove others from the
    # list) - replacing the model resets scroll to the top, and
    # reselecting the same row only scrolls as far as needed to reveal
    # it again, landing it wherever that happens to be rather than
    # back where the list was. Restoring it is deferred via
    # GLib.idle_add() - captured and called directly here rather than
    # actually pumping the real main loop, which depends on GTK's own
    # idle-driven row-height revalidation completing, something an
    # unrealized treeview (no window, no allocation) was seen to never
    # finish on a real macOS CI runner, hanging that run for 10+
    # minutes before being force-cancelled.
    idle_calls = []
    monkeypatch.setattr(prefsview.GLib, 'idle_add', lambda func, *args: idle_calls.append((func, args)))

    view = _load_widgets()
    view._init_plugins_page()

    view.plugin_data = _make_plugin_items(50, enabled_name='Plugin 25')
    view.plugins_select.select_item({'data': {'internal_name': 'plugin25'}})
    vadj = view.plugins_select.get_vadjustment()
    vadj.set_upper(2000)
    vadj.set_value(900)

    view.plugin_data = _make_plugin_items(50, enabled_name='Plugin 25')

    # One idle_add per plugin_data assignment above - the second one
    # is the rebuild whose scroll position this test cares about.
    assert len(idle_calls) == 2
    func, args = idle_calls[-1]
    func(*args)
    assert vadj.get_value() == 900
