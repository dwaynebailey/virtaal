#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from gi.repository import Gtk

from virtaal.plugins.lookup.lookupview import LookupView


class _FakeBuffer:
    def __init__(self, text):
        self._text = text

    def get_has_selection(self):
        return bool(self._text)

    def get_selection_bounds(self):
        return (0, len(self._text), False)

    def get_text(self, *args, **kwargs):
        return self._text


class _FakeTextbox:
    def __init__(self, text, role='source'):
        self.buffer = _FakeBuffer(text)
        self.role = role


class _FakeLang:
    code = 'en'


class _FakeLangController:
    source_lang = _FakeLang()
    target_lang = _FakeLang()


class _FakeTopLevelModel:
    TOP_LEVEL = True

    def create_menu_items(self, *args):
        item = Gtk.MenuItem(label='Synonyms')
        return [item]


class _FakeNestedModel:
    TOP_LEVEL = False

    def create_menu_items(self, *args):
        item = Gtk.MenuItem(label='Google')
        return [item]


class _FakePluginController:
    def __init__(self, plugins):
        self.plugins = plugins


class _FakeController:
    def __init__(self, plugins):
        self.plugin_controller = _FakePluginController(plugins)


def _make_view(plugins):
    view = LookupView.__new__(LookupView)
    view.controller = _FakeController(plugins)
    view.lang_controller = _FakeLangController()
    return view


def test_top_level_model_items_go_directly_into_the_context_menu():
    # A thesaurus result is specific enough that nesting it one level
    # deeper, alongside unrelated web look-ups, would just make it
    # slower to reach - unlike weblookup, it sits as its own entry.
    view = _make_view({'thesaurus': _FakeTopLevelModel(), 'weblookup': _FakeNestedModel()})
    menu = Gtk.Menu()

    view._on_populate_popup(_FakeTextbox('word'), menu)

    top_level_labels = [i.get_label() for i in menu.get_children() if isinstance(i, Gtk.MenuItem) and i.get_label()]
    assert 'Synonyms' in top_level_labels
    assert 'Google' not in top_level_labels


def test_nested_model_items_stay_under_the_look_up_submenu():
    view = _make_view({'weblookup': _FakeNestedModel()})
    menu = Gtk.Menu()

    view._on_populate_popup(_FakeTextbox('word'), menu)

    lookup_item = next(i for i in menu.get_children() if isinstance(i, Gtk.MenuItem) and i.get_submenu())
    submenu_labels = [i.get_label() for i in lookup_item.get_submenu().get_children()]
    assert submenu_labels == ['Google']


def test_populate_popup_does_nothing_without_a_selection():
    view = _make_view({'thesaurus': _FakeTopLevelModel()})
    menu = Gtk.Menu()

    view._on_populate_popup(_FakeTextbox(''), menu)

    assert menu.get_children() == []
