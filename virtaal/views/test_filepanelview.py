#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from types import SimpleNamespace

from gi.repository import Gtk

from virtaal.views.filepanelview import (
    FilePanelView,
    build_tree,
    language_label,
    scope_ranges,
)


def test_build_tree_nests_folders_and_numbers_files_in_order():
    tree = build_tree(['app/af.po', 'help/af/guide.po', 'help/af/faq.po', 'top.po'])

    assert tree['files'] == [('top.po', 3)]
    assert tree['folders']['app']['files'] == [('af.po', 0)]
    assert tree['folders']['help']['folders']['af']['files'] == [('guide.po', 1), ('faq.po', 2)]


def test_build_tree_reads_windows_separators():
    assert build_tree(['po\\af.po'])['folders']['po']['files'] == [('af.po', 0)]


def _segments(*counts):
    segments, offset = [], 0
    for count in counts:
        segments.append(SimpleNamespace(offset=offset, count=count))
        offset += count
    return segments


def test_scope_ranges_are_the_files_units():
    assert scope_ranges({2, 0}, _segments(3, 4, 5), 3) == [(0, 3), (7, 12)]


def test_scope_ranges_of_none_or_every_file_lift_the_limit():
    assert scope_ranges(set(), _segments(3, 4), 2) is None
    assert scope_ranges({0, 1}, _segments(3, 4), 2) is None


def test_language_label_names_the_language_and_its_files():
    assert language_label('Afrikaans', 1) == 'Afrikaans (1 file)'
    assert language_label('Zulu', 12) == 'Zulu (12 files)'


def test_the_panel_goes_beside_the_units_and_the_ribbon_box():
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    vbox.pack_start(Gtk.Label(label='mode bar'), False, False, 0)
    units = Gtk.ScrolledWindow()
    ribbon_box = Gtk.Box()
    ribbon_box.pack_start(units, True, True, 0)
    vbox.pack_start(ribbon_box, True, True, 0)
    vbox.pack_start(Gtk.Label(label='status bar'), False, False, 0)
    objects = {'vbox_main': vbox, 'scrwnd_storeview': units}
    mainview = SimpleNamespace(gui=SimpleNamespace(get_object=objects.get))
    view = FilePanelView(SimpleNamespace(main_controller=SimpleNamespace(view=mainview)))

    assert vbox.get_children()[1] is view.paned
    assert view.paned.get_child2() is ribbon_box
    assert units.get_parent() is ribbon_box
    units.hide()
    assert not view.paned.get_visible()
    vbox.destroy()
