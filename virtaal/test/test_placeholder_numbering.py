#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from io import BytesIO

from gi.repository import Gtk
from test_scaffolding import TestScaffolding
from translate.storage import po

from virtaal.controllers.placeablescontroller import PlaceablesController

HEADER = (
    b'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
    b'"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
)


class TestPlaceholderNumbering(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    def setup_method(self):
        self.notices = []
        view = self.main_controller.view
        view.show_info_notice = lambda text, action=None, on_close=None: (
            self.notices.append((text, action, on_close)) or len(self.notices)
        )
        view.hide_info_notice = lambda notice=None: self.notices.append(('hidden', notice))

    def _load(self, entry):
        unit = po.pofile(BytesIO(HEADER + entry)).units[-1]
        for textbox in self.unit_controller.view.targets:
            textbox._suggestion = None
        self.view = self.unit_controller.load_unit(unit)
        return unit

    def _offers(self):
        return [n for n in self.notices if n[0] != 'hidden']

    def test_a_reordered_c_format_target_is_offered_numbers(self):
        unit = self._load(b'#, c-format\nmsgid "%s of %d"\nmsgstr ""\n')
        target = self.view.targets[0]

        target.set_text('%d van %s')

        assert len(self._offers()) == 1
        self._offers()[0][1][1]()  # the notice's button
        assert target.get_text() == '%2$d van %1$s'
        assert unit.target == '%2$d van %1$s'
        assert self.notices[-1][0] == 'hidden'

    def test_numbering_is_one_undo_step(self):
        self._load(b'#, c-format\nmsgid "%s of %d"\nmsgstr "%d van %s"\n')
        target = self.view.targets[0]

        self.undo_controller.model.clear()

        self.view._numbering.number()

        assert target.get_text() == '%2$d van %1$s'
        entry = self.undo_controller.model.pop()
        assert self.undo_controller.model.pop() is None
        entry['action'](entry['unit'])
        assert str(target.elem) == '%d van %s'

    def test_an_in_order_target_is_not_offered_numbers(self):
        self._load(b'#, c-format\nmsgid "%s of %d"\nmsgstr ""\n')

        self.view.targets[0].set_text('%s van %d')

        assert self._offers() == []

    def test_an_unflagged_or_python_unit_is_not_offered_numbers(self):
        for entry in (
            b'msgid "%s of %d"\nmsgstr ""\n',
            b'#, python-format\nmsgid "%s of %d"\nmsgstr ""\n',
        ):
            self._load(entry)
            self.view.targets[0].set_text('%d van %s')

        assert self._offers() == []

    def test_a_closed_offer_stays_closed_for_the_unit(self):
        self._load(b'#, c-format\nmsgid "%s of %d"\nmsgstr ""\n')
        target = self.view.targets[0]
        target.set_text('%d van %s')
        self._offers()[0][2]()  # closed

        target.set_text('%d en %s')

        assert len(self._offers()) == 1

    def test_mixing_numbered_and_unnumbered_is_offered_numbers(self):
        self._load(b'#, c-format\nmsgid "%s of %d"\nmsgstr ""\n')
        target = self.view.targets[0]

        target.set_text('%2$d van %s')
        self.view._numbering.number()

        assert target.get_text() == '%2$d van %1$s'

    def test_each_plural_form_is_numbered(self):
        unit = self._load(
            b'#, c-format\nmsgid "%d file in %s"\nmsgid_plural "%d files in %s"\n'
            b'msgstr[0] "in %s: %d leer"\nmsgstr[1] "in %s: %d lere"\n'
        )

        self.view._numbering.number()

        assert unit.target.strings == ['in %2$s: %1$d leer', 'in %2$s: %1$d lere']

    def test_the_context_menu_offers_numbers_only_when_needed(self):
        self._load(b'#, c-format\nmsgid "%s of %d"\nmsgstr "%d van %s"\n')
        target = self.view.targets[0]
        menu = Gtk.Menu()
        try:
            self.view._on_populate_popup(target, menu)
            labels = [item.get_label() for item in menu.get_children() if item.get_label()]
            assert labels == ['Number the Placeholders']

            menu.get_children()[-1].activate()

            assert target.get_text() == '%2$d van %1$s'
            menu = Gtk.Menu()
            self.view._on_populate_popup(target, menu)
            assert menu.get_children() == []
        finally:
            menu.destroy()
