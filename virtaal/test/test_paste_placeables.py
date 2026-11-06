#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A translation copied from one unit and pasted into another takes the
other unit's placeables, through the real unit controller and view."""

import pytest
from test_scaffolding import TestScaffolding

from virtaal.controllers.placeablescontroller import PlaceablesController

PO = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Open %s"
msgstr "Maak %s oop"

msgid "Open %d"
msgstr ""

msgid "Open it"
msgstr ""
'''


class TestPastePlaceables(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def store(self, tmp_path, monkeypatch):
        """The file open on its first unit, with the clipboard faked: the
        tests never touch the real one."""
        path = tmp_path / 'open.po'
        path.write_text(PO, encoding='utf-8')
        self.store_controller.open_file(str(path))
        self.units = self.store_controller.store.get_units()
        self.view = self.unit_controller.view
        self.clipboard = None
        monkeypatch.setattr(self.view, 'clipboard_text', lambda: self.clipboard)
        self.pasted = []
        paste_instead = self.view.paste_instead
        monkeypatch.setattr(
            self.view,
            'paste_instead',
            lambda n, text: (self.pasted.append(text), paste_instead(n, text)),
        )
        self.unit_controller.load_unit(self.units[0])
        yield
        self.store_controller.close_file()

    def copy_target(self):
        """Copy the current unit's whole translation."""
        target = self.view.targets[0]
        target.buffer.select_range(*target.buffer.get_bounds())
        self.clipboard = target.get_text()
        self.view._on_textbox_copying(target)
        self.view._on_textbox_copied(target)

    def paste(self, unit_index):
        self.unit_controller.load_unit(self.units[unit_index])
        self.unit_controller._adapt_pasted_placeables(None, self.units[unit_index], '', {}, 0)

    def test_a_copied_translation_takes_this_units_placeables(self):
        self.copy_target()
        self.unit_controller.load_unit(self.units[1])

        self.view.targets[0].emit('paste-clipboard')

        assert self.pasted == ['Maak %d oop']
        assert self.unit_controller.get_unit_target(0) == 'Maak %d oop'

    def test_a_copy_records_the_unit_and_source_it_came_from(self):
        self.copy_target()

        assert self.view.copied == (self.units[0], 'Open %s', 'Maak %s oop')

    def test_copying_with_nothing_selected_records_nothing(self):
        target = self.view.targets[0]
        target.buffer.place_cursor(target.buffer.get_start_iter())
        self.clipboard = 'From another program'

        self.view._on_textbox_copying(target)
        self.view._on_textbox_copied(target)

        assert self.view.copied is None

    def test_text_that_changed_on_the_clipboard_is_pasted_as_it_is(self):
        self.copy_target()
        self.clipboard = 'Maak %s toe'

        self.paste(1)

        assert self.pasted == []

    def test_placeables_that_dont_map_one_to_one_are_pasted_as_they_are(self):
        self.copy_target()

        self.paste(2)

        assert self.pasted == []

    def test_a_paste_into_the_unit_copied_from_is_left_alone(self):
        self.copy_target()

        self.paste(0)

        assert self.pasted == []

    def test_closing_the_file_forgets_the_copy(self):
        self.copy_target()

        self.store_controller.close_file()

        assert self.view.copied is None
