#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from gi.repository import Gdk, Gtk

from virtaal.common.platform import platform
from virtaal.test.test_scaffolding import TestScaffolding


class TestStoreView(TestScaffolding):
    def setup_method(self):
        self.store_controller.open_file(self.testfile[1])

    def teardown_method(self):
        self.store_controller.close_file()

    def test_open_file_loads_and_shows_the_store(self):
        view = self.store_controller.view

        assert view.store is self.store_controller.store
        assert view._treeview.get_model() is not None
        assert view.mnu_up.get_sensitive()

    def test_close_file_hides_the_view(self):
        view = self.store_controller.view

        self.store_controller.close_file()

        assert view.parent_widget.props.visible is False
        assert view.store is None
        assert not view.mnu_up.get_sensitive()

    def test_get_store_returns_the_current_store(self):
        view = self.store_controller.view

        assert view.get_store() is self.store_controller.store

    def test_get_unit_celleditor_delegates_to_the_controller(self, monkeypatch):
        view = self.store_controller.view
        calls = []
        monkeypatch.setattr(
            self.store_controller,
            'get_unit_celleditor',
            lambda unit: calls.append(unit) or 'editor',
        )

        result = view.get_unit_celleditor('some-unit')

        assert calls == ['some-unit']
        assert result == 'editor'

    def test_cursor_change_selects_the_new_index(self, monkeypatch):
        view = self.store_controller.view
        calls = []
        monkeypatch.setattr(view._treeview, 'select_index', lambda i, force=False: calls.append(i))

        view.cursor.index = 1

        assert calls[-1] == 1

    def test_on_style_set_applies_a_background_provider(self):
        view = self.store_controller.view
        first_provider = view._treeview_bg_provider

        view._on_style_set(self.main_controller.view.main_window)

        assert view._treeview_bg_provider is not None
        assert view._treeview_bg_provider is not first_provider

    def test_show_replaces_a_placeholder_child(self):
        view = self.store_controller.view
        view.parent_widget.remove(view._treeview)
        placeholder = Gtk.Label()
        view.parent_widget.add(placeholder)

        view.show()

        assert view._treeview.get_parent() is view.parent_widget
        assert placeholder.get_parent() is None

    def test_show_does_not_select_a_row_without_a_store(self, monkeypatch):
        view = self.store_controller.view
        self.store_controller.close_file()
        calls = []
        monkeypatch.setattr(view._treeview, 'select_index', lambda i, force=False: calls.append(i))

        view.show()

        assert calls == []

    # __init__()'s "main_window already visible" branch (line 40) is
    # deliberately left uncovered: exercising it needs a second,
    # independent MainController/main_window construction while
    # another one is potentially still alive in the same process -
    # confirmed live to segfault (real SIGSEGV inside GTK's own theme
    # reload, gtk_widget_get_screen on an invalid widget), not just an
    # intermittent pytest-xdist worker crash.

    def test_next_and_previous_file_have_ctrl_shift_page_down_and_up(self):
        for path, key in (
            ('<Virtaal>/Navigation/NextFile', Gdk.KEY_Page_Down),
            ('<Virtaal>/Navigation/PrevFile', Gdk.KEY_Page_Up),
        ):
            found, entry = Gtk.AccelMap.lookup_entry(path)
            assert found
            assert entry.accel_key == key
            if platform.is_mac:
                mods = Gdk.ModifierType.META_MASK | Gdk.ModifierType.MOD2_MASK
            else:
                mods = Gdk.ModifierType.CONTROL_MASK
            assert entry.accel_mods == mods | Gdk.ModifierType.SHIFT_MASK

    def test_next_and_previous_file_are_greyed_for_one_file(self):
        view = self.store_controller.view

        assert not view.mnu_next_file.get_sensitive()
        assert not view.mnu_prev_file.get_sensitive()


@pytest.mark.parametrize(
    ('current', 'last', 'expected'),
    [
        (3, False, 2),
        (3, True, 4),
        (0, True, 1),
        (7, False, 6),
        (7, True, 8),
    ],
)
def test_file_edge_is_the_first_or_last_index_in_the_current_file(current, last, expected):
    from virtaal.views.storeview import file_edge

    # Files start at 0, 2 and 6; the navigation list skips 5.
    assert file_edge([0, 1, 2, 3, 4, 6, 7, 8], [0, 2, 6], current, last) == expected


def test_file_edge_of_a_file_with_nothing_in_the_list_is_none():
    from virtaal.views.storeview import file_edge

    assert file_edge([0, 1, 6], [0, 2, 6], 3, False) is None
