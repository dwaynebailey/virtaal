#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Moving through several files opened as one list of units."""

import pytest
from gi.repository import GLib
from test_scaffolding import TestScaffolding

from virtaal.controllers.placeablescontroller import PlaceablesController


def _po(path, units):
    body = ''.join('msgid "%s"\nmsgstr "%s"\n\n' % unit for unit in units)
    path.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Language: af\\n"\n\n%s' % body,
        encoding='utf-8',
    )
    return str(path)


def _drain():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class TestStreamNavigation(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        self.dir = tmp_path
        _po(tmp_path / 'a.po', [('One', ''), ('Two', 'Twee')])
        _po(tmp_path / 'b.po', [('Three', 'Drie'), ('Four', 'Vier')])
        _po(tmp_path / 'c.po', [('Five', ''), ('Six', '')])
        self.store_controller.close_file()
        yield
        self.store_controller.set_modified(False)
        self.store_controller.close_file()

    def _item(self, name):
        return self.main_controller.view.gui.get_object(name)

    def _open(self, *paths):
        self.store_controller.open_files([str(path) for path in paths or [self.dir]])
        _drain()

    def _activate(self, name):
        self._item(name).activate()
        _drain()

    def test_next_and_previous_file_go_to_each_files_first_unit(self):
        self._open()
        cursor = self.store_controller.cursor

        self._activate('mnu_next_file')
        assert cursor.index == 2
        self._activate('mnu_next_file')
        assert cursor.index == 4
        assert self.store_controller.store.segments[2].loaded
        self._activate('mnu_next_file')
        assert cursor.index == 4

        cursor.index = 5
        _drain()
        self._activate('mnu_prev_file')
        assert cursor.index == 2
        self._activate('mnu_prev_file')
        self._activate('mnu_prev_file')
        assert cursor.index == 0

    def test_next_file_stays_within_the_files_chosen(self):
        self._open()
        cursor = self.store_controller.cursor
        cursor.set_scope([(0, 2), (4, 6)])
        _drain()

        self._activate('mnu_next_file')

        assert cursor.index == 4

    def test_last_unit_is_the_end_of_the_stream(self):
        self._open()

        self._activate('mnu_last')

        assert self.store_controller.cursor.index == 5

    def test_the_file_items_are_only_for_several_files(self):
        self._open(self.dir / 'a.po')
        assert not self._item('mnu_next_file').get_sensitive()
        assert not self._item('mnu_prev_file').get_sensitive()

        self._open()
        assert self._item('mnu_next_file').get_sensitive()
        assert self._item('mnu_prev_file').get_sensitive()

        self.store_controller.close_file()
        assert not self._item('mnu_next_file').get_sensitive()
