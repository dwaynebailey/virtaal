#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Moving to a search match in a shown window: the unit editor is placed
on the match's row and the match is highlighted and selected."""

import time

import pytest
from gi.repository import Gdk, GLib
from test_scaffolding import TestScaffolding

from virtaal.controllers.placeablescontroller import PlaceablesController

PO = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Language: af\\n"

msgid "Open the file"
msgstr "Maak die lêer oop"

msgid "Close"
msgstr "Maak toe"

msgid "Save the file"
msgstr "Stoor die lêer"

msgid "Quit"
msgstr ""

msgid "Print the file"
msgstr "Druk die lêer"
'''


def _settle(treeview, seconds=0.5):
    """Run the main loop until the editor is placed and nothing is due."""
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end or treeview._waiting_for_row_change:
        context.iteration(False)
        if time.monotonic() > end + 10:
            break


def _highlighted(textbox):
    tag = textbox.buffer.get_tag_table().lookup('search_highlight')
    if tag is None:
        return ''
    text = textbox.get_text()
    return ''.join(
        c for i, c in enumerate(text) if textbox.buffer.get_iter_at_offset(i).has_tag(tag)
    )


def _off_primary(textbox):
    """Keep textbox's selection off PRIMARY, as an X client on the same
    display (another xdist worker) selecting text takes PRIMARY, and GTK
    then clears this one. Balanced with GtkTextView's own add on realize
    and remove on unrealize."""
    if getattr(textbox, '_off_primary', False):
        return
    textbox._off_primary = True

    def realized(widget):
        widget.get_buffer().remove_selection_clipboard(widget.get_clipboard(Gdk.SELECTION_PRIMARY))

    def unrealizing(widget):
        widget.get_buffer().add_selection_clipboard(widget.get_clipboard(Gdk.SELECTION_PRIMARY))

    textbox.connect_after('realize', realized)
    textbox.connect('unrealize', unrealizing)
    if textbox.get_realized():
        realized(textbox)


def _selected(textbox):
    bounds = textbox.buffer.get_selection_bounds()
    return textbox.buffer.get_text(*bounds, True) if bounds else ''


class TestSearchHighlight(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        path = tmp_path / 'search.po'
        path.write_text(PO, encoding='utf-8')
        window = self.main_controller.view.main_window
        window.resize(900, 600)
        window.show()
        self.store_controller.open_file(str(path))
        self.treeview = self.store_controller.view._treeview
        _settle(self.treeview)
        yield
        self.mode_controller.select_default_mode()
        self.store_controller.set_modified(False)
        self.store_controller.close_file()
        window.hide()

    def test_moving_to_each_match_highlights_and_selects_it_in_the_placed_editor(self):
        self.mode_controller.select_mode_by_name('Search')
        search = self.mode_controller.current_mode
        search.ent_search.set_text('lêer')
        # As typed: the search runs, and the search box takes the focus.
        _settle(self.treeview, 1.5)
        unit_view = self.unit_controller.view

        seen = []
        for _ in range(3):
            for textbox in unit_view.sources + unit_view.targets:
                _off_primary(textbox)
            search._move_match(1)
            _settle(self.treeview)
            target = unit_view.targets[0]
            path, _column = self.treeview.get_cursor()
            seen.append(
                (
                    self.store_controller.cursor.index,
                    path.get_indices()[0] if path else None,
                    unit_view.get_parent() is self.treeview,
                    _highlighted(target),
                    _selected(target),
                    self.main_controller.view.main_window.get_focus() is target,
                )
            )

        assert seen == [
            (2, 2, True, 'lêer', 'lêer', True),
            (4, 4, True, 'lêer', 'lêer', True),
            (0, 0, True, 'lêer', 'lêer', True),
        ]
