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


def _loaded_view(entries, templates, unpaired=(), **controller):
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    units = Gtk.ScrolledWindow()
    vbox.pack_start(units, True, True, 0)
    objects = {'vbox_main': vbox, 'scrwnd_storeview': units}
    mainview = SimpleNamespace(gui=SimpleNamespace(get_object=objects.get))
    view = FilePanelView(
        SimpleNamespace(main_controller=SimpleNamespace(view=mainview), **controller)
    )
    view.load(entries, templates=templates, unpaired=unpaired)
    return view, vbox


def _rows(model, it=None):
    rows = []
    it = model.iter_children(it)
    while it is not None:
        rows.append(tuple(model[it]))
        rows.extend(_rows(model, it))
        it = model.iter_next(it)
    return rows


def test_monolingual_sources_show_greyed_outside_the_stream():
    entry = SimpleNamespace(display_name='po/af.po', language='af', template=None)
    source = SimpleNamespace(display_name='i18n/en.properties', language='en', path='/p/en')
    view, vbox = _loaded_view([entry], [source])

    view.update_progress([(1, 0, 2)])

    rows = {row[0]: row for row in _rows(view.model)}
    assert rows['en.properties'][4] == [] and rows['en.properties'][7] is False
    assert rows['af.po'][4] == [0] and rows['af.po'][7] is True
    assert rows['af.po'][2] == 50
    vbox.destroy()


def test_a_paired_file_shows_its_template_greyed_under_it(tmp_path):
    from virtaal.models.fileset import FileSet

    for name, text in (('Messages.properties', 'a=A\n'), ('Messages_af.properties', 'a=B\n')):
        (tmp_path / 'app').mkdir(exist_ok=True)
        (tmp_path / 'app' / name).write_text(text, encoding='utf-8')
    (tmp_path / 'af.po').write_text('msgid "a"\nmsgstr "b"\n', encoding='utf-8')
    files = FileSet.from_paths([str(tmp_path)])
    view, vbox = _loaded_view(list(files), files.templates)

    view.update_progress([(1, 0, 1), (0, 0, 1)])

    rows = _rows(view.model)
    names = [row[0] for row in rows]
    # The template only under its file, not again on its own.
    assert names == ['app', 'Messages_af.properties', 'Messages.properties', 'af.po']
    target, template = rows[1], rows[2]
    assert target[4] == [1] and target[7] is True
    assert template[4] == [] and template[7] is False
    assert template[6] == 'This is the source-language file.'
    under = view.model.get_iter(Gtk.TreePath.new_from_string('0:0:0'))
    assert view.model[under][0] == 'Messages.properties'
    vbox.destroy()


def test_a_file_with_no_known_template_shows_greyed_and_asks_for_one_when_activated():
    entry = SimpleNamespace(display_name='po/af.po', language='af', template=None)
    source = SimpleNamespace(display_name='i18n/en.properties', language='en', path='/p/en')
    lone = SimpleNamespace(display_name='i18n/lone.properties', language=None, path='/p/lone')
    asked = []
    view, vbox = _loaded_view([entry], [source], [lone], pair_file=asked.append)

    view.update_progress([(1, 0, 2)])

    rows = {row[0]: row for row in _rows(view.model)}
    row = rows['lone.properties']
    # Greyed, with no progress and no units.
    assert row[4] == [] and row[7] is False and row[2] == 0
    assert row[6].startswith("Virtaal can't tell which file this one translates.")
    assert rows['en.properties'][6] == 'This is the source-language file.'
    assert rows['en.properties'][8] is None
    for name in ('lone.properties', 'en.properties', 'af.po'):
        it = next(it for it in _iters(view.model) if view.model[it][0] == name)
        view.treeview.row_activated(view.model.get_path(it), view.treeview.get_column(0))
    assert asked == ['/p/lone']
    vbox.destroy()


def _iters(model, it=None):
    it = model.iter_children(it)
    while it is not None:
        yield it
        yield from _iters(model, it)
        it = model.iter_next(it)
