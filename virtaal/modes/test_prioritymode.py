#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from pathlib import Path
from types import SimpleNamespace

from gi.repository import Gtk
from translate.storage import po

from virtaal.modes.prioritymode import PriorityMode

PRIORITIES = """\
format: 1
priorities:
- {id: '2', name: Later, order: 2}
- {id: '1', name: First, order: 1, description: Do these first.}
domains:
  app:
    '1':
    - Open
    - "menu\\x04Quit"
    - '%d file'
    '2':
    - Save
    - Done
"""

# Open, menu|Quit and %d file are level 1; Save and Done level 2; Other
# is unlisted. Done and Open are translated, menu|Quit fuzzy.
CATALOG = """\
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
%s
msgid "Open"
msgstr "Oop"

#, fuzzy
msgctxt "menu"
msgid "Quit"
msgstr "Stop"

msgid "%%d file"
msgid_plural "%%d files"
msgstr[0] ""
msgstr[1] ""

msgid "Save"
msgstr ""

msgid "Done"
msgstr "Klaar"

msgid "Other"
msgstr ""
"""


class _Model:
    """The parts of a StoreModel the mode uses."""

    def __init__(self, path):
        self.path = str(path)
        self._trans_store = po.pofile.parsefile(self.path)
        self.units = [unit for unit in self._trans_store.units if not unit.isheader()]
        self.stats = {'untranslated': [], 'fuzzy': [], 'translated': []}
        for index, unit in enumerate(self.units):
            state = (
                'fuzzy'
                if unit.isfuzzy()
                else ('translated' if unit.istranslated() else 'untranslated')
            )
            self.stats[state].append(index)

    def get_filename(self):
        return self.path

    def __len__(self):
        return len(self.units)

    def __getitem__(self, index):
        return self.units[index]


def _catalog(folder, name='af.po', header=''):
    path = folder / name
    path.write_text(CATALOG % header, encoding='utf-8')
    return path


def _mode(model):
    mode = PriorityMode.__new__(PriorityMode)
    mode.filter_levels = []
    mode._menuitem_levels = {}
    mode._stats_changed_id = None
    mode.priority_file = None
    mode.level_units = {}
    mode._chosen = {}
    cursor = SimpleNamespace(model=model, indices=None)
    mode.controller = SimpleNamespace(
        main_controller=SimpleNamespace(
            store_controller=SimpleNamespace(
                cursor=cursor, connect=lambda signal, handler: 7, disconnect=lambda i: None
            )
        ),
        view=SimpleNamespace(mode_box=Gtk.Grid()),
    )
    return mode


def _menu(mode):
    return [
        (item.get_label(), getattr(item, 'get_active', bool)(), item.get_sensitive())
        for item in mode.btn_popup.menu
        if not isinstance(item, Gtk.SeparatorMenuItem)
    ]


def _tick(mode, name):
    for item, level in mode._menuitem_levels.items():
        if level.name == name:
            item.set_active(True)
    mode.update_indices()


# selected() #


def test_selected_lists_the_levels_in_order_with_their_progress(tmp_path):
    (tmp_path / 'app.priorities.yaml').write_text(PRIORITIES, encoding='utf-8')
    mode = _mode(_Model(_catalog(tmp_path)))

    mode.selected()

    assert _menu(mode) == [
        ('First (1/3)', False, True),
        ('Later (1/2)', False, True),
        ('_Load Priority File…', False, True),
    ]
    assert mode.btn_popup.get_label() == 'Select Priorities'
    assert mode.storecursor.indices == [0, 1, 2, 3, 4, 5]


def test_selected_finds_the_file_the_header_names(tmp_path):
    folder = tmp_path / 'files'
    folder.mkdir()
    (folder / 'named.yaml').write_text(PRIORITIES, encoding='utf-8')
    model = _Model(_catalog(folder, header='"X-Priority-File: named.yaml\\n"\n'))
    mode = _mode(model)

    mode.selected()

    assert mode.priority_file.path == str(folder / 'named.yaml')


def test_selected_without_a_priority_file_offers_to_load_one(tmp_path):
    folder = tmp_path / 'po'
    folder.mkdir()
    mode = _mode(_Model(_catalog(folder)))

    mode.selected()

    assert _menu(mode) == [
        ('No Priority File', False, False),
        ('_Load Priority File…', False, True),
    ]
    assert mode.btn_popup.get_label() == 'No Priority File'
    assert mode.storecursor.indices == [0, 1, 2, 3, 4, 5]


def test_selected_treats_a_malformed_file_as_none(tmp_path):
    folder = tmp_path / 'po'
    folder.mkdir()
    (folder / 'af.priorities.yaml').write_text('format: 1\npriorities: [oops', encoding='utf-8')
    mode = _mode(_Model(_catalog(folder)))

    mode.selected()

    assert mode.priority_file is None
    assert mode.btn_popup.get_label() == 'No Priority File'


def test_selected_greys_out_a_finished_level(tmp_path):
    (tmp_path / 'app.priorities.yaml').write_text(
        PRIORITIES.replace("    - Save\n", ""), encoding='utf-8'
    )
    mode = _mode(_Model(_catalog(tmp_path)))

    mode.selected()

    assert ('Later (1/1)', False, False) in _menu(mode)


# update_indices() #


def test_a_level_shows_exactly_its_untranslated_and_fuzzy_units(tmp_path):
    (tmp_path / 'app.priorities.yaml').write_text(PRIORITIES, encoding='utf-8')
    mode = _mode(_Model(_catalog(tmp_path)))
    mode.selected()

    _tick(mode, 'First')

    # menu|Quit (fuzzy) and the %d file plural; not Open (translated).
    assert mode.storecursor.indices == [1, 2]
    assert mode.btn_popup.get_label() == 'First'


def test_two_levels_show_both_levels_units(tmp_path):
    (tmp_path / 'app.priorities.yaml').write_text(PRIORITIES, encoding='utf-8')
    mode = _mode(_Model(_catalog(tmp_path)))
    mode.selected()

    _tick(mode, 'First')
    _tick(mode, 'Later')

    # Never Other, which the file doesn't list.
    assert mode.storecursor.indices == [1, 2, 3]
    assert mode.btn_popup.get_label() == 'First, Later'


def test_reselecting_keeps_the_ticked_level(tmp_path):
    (tmp_path / 'app.priorities.yaml').write_text(PRIORITIES, encoding='utf-8')
    mode = _mode(_Model(_catalog(tmp_path)))
    mode.selected()
    _tick(mode, 'Later')

    mode.selected()

    assert ('Later (1/2)', True, True) in _menu(mode)
    assert mode.storecursor.indices == [3]


# progress #


def test_progress_follows_a_translated_unit(tmp_path):
    (tmp_path / 'app.priorities.yaml').write_text(PRIORITIES, encoding='utf-8')
    mode = _mode(_Model(_catalog(tmp_path)))
    mode.selected()
    _tick(mode, 'Later')
    stats = mode.storecursor.model.stats
    stats['untranslated'].remove(3)
    stats['translated'].append(3)

    mode._on_stats_changed(None)

    # Still ticked, so still sensitive; the units under review stay.
    assert ('Later (2/2)', True, True) in _menu(mode)
    assert mode.storecursor.indices == [3]


# use_priority_file() #


def test_a_priority_file_loaded_by_hand_is_used_for_that_file(tmp_path):
    folder = tmp_path / 'po'
    folder.mkdir()
    chosen = tmp_path / 'chosen.yaml'
    chosen.write_text(PRIORITIES, encoding='utf-8')
    mode = _mode(_Model(_catalog(folder)))
    mode.selected()

    mode.use_priority_file(str(chosen))

    assert _menu(mode)[0] == ('First (1/3)', False, True)
    mode.selected()
    assert mode.priority_file.path == str(chosen)


def test_loading_a_malformed_file_by_hand_leaves_none(tmp_path):
    (tmp_path / 'app.priorities.yaml').write_text(PRIORITIES, encoding='utf-8')
    bad = tmp_path / 'bad.yaml'
    bad.write_text('format: 3\n', encoding='utf-8')
    mode = _mode(_Model(_catalog(tmp_path)))
    mode.selected()

    mode.use_priority_file(str(bad))

    assert mode.priority_file is None
    assert mode.btn_popup.get_label() == 'No Priority File'


# Virtaal's own files #


def test_virtaals_own_catalog_lists_its_levels():
    po_dir = Path(__file__).resolve().parents[2] / 'po'
    mode = _mode(_Model(po_dir / 'af.po'))

    mode.selected()

    labels = [label for label, _active, _sensitive in _menu(mode)]
    assert labels[0].startswith('Level 1 core (')
    assert labels[-2].startswith('Never asked (')
    assert sum(len(units) for units in mode.level_units.values()) > 0
