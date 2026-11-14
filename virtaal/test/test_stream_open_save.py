#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Several files opened as one list of units, through the real store
and unit controllers."""

import pytest
from test_scaffolding import TestScaffolding
from translate.storage import factory

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.models.streammodel import StreamModel


def _po(path, units, language='af'):
    body = ''.join('msgid "%s"\nmsgstr "%s"\n\n' % unit for unit in units)
    path.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Language: %s\\n"\n\n%s' % (language, body)
    )
    return str(path)


class TestStreamOpenSave(TestScaffolding):
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
        _po(tmp_path / 'b.po', [('Three', ''), ('Four', 'Vier')])
        _po(tmp_path / 'zu.po', [('Five', '')], language='zu')
        (tmp_path / 'a.pot').write_text('msgid ""\nmsgstr ""\n\nmsgid "One"\nmsgstr ""\n')
        self.store_controller.close_file()
        yield
        self.store_controller.close_file()

    def _edit(self, index, text):
        sc = self.store_controller
        sc.cursor.force_index(index)
        unit = sc.store[index]
        self.unit_controller.load_unit(unit)
        self.unit_controller.set_unit_target(0, [text])
        self.unit_controller.finish_current_unit()

    def test_a_directory_opens_the_files_of_one_language(self):
        self.store_controller.open_files([str(self.dir)])

        store = self.store_controller.store
        assert isinstance(store, StreamModel)
        assert self.store_controller.is_stream()
        assert [e.display_name for _o, e in store.file_offsets()] == ['a.po', 'b.po']
        assert [u.source for u in store] == ['One', 'Two', 'Three', 'Four']

    def test_one_file_opens_as_before(self):
        self.store_controller.open_files([str(self.dir / 'a.po')])

        assert not isinstance(self.store_controller.store, StreamModel)
        assert not self.store_controller.is_stream()
        assert self.store_controller.modified_stream_files() == []

    def test_moving_into_the_next_file_reads_it(self):
        self.store_controller.open_files([str(self.dir)])
        sc = self.store_controller

        sc.cursor.move(1)
        sc.cursor.move(1)

        assert sc.cursor.index == 2
        assert sc.store.segments[1].loaded
        assert sc.get_store_filename() == str(self.dir / 'b.po')
        assert sc.cursor.deref() is sc.store.segments[1].model.get_unit(0)

    def test_save_writes_every_modified_file(self):
        self.store_controller.open_files([str(self.dir)])
        self._edit(0, 'Een')
        self._edit(2, 'Drie')

        assert self.store_controller.is_modified()
        assert self.store_controller.modified_stream_files() == ['a.po', 'b.po']
        self.store_controller.save_file()

        assert factory.getobject(str(self.dir / 'a.po')).units[1].target == 'Een'
        assert factory.getobject(str(self.dir / 'b.po')).units[1].target == 'Drie'
        assert not self.store_controller.is_modified()
        assert self.store_controller.modified_stream_files() == []

    def test_pre_translate_fills_a_file_not_read_yet_and_saves_it(self, monkeypatch):
        from gi.repository import GLib

        from virtaal.models.streammodel import PeekUnit
        from virtaal.plugins.tm import pretranslate
        from virtaal.plugins.tm.tmcontroller import TMController

        calls = []
        monkeypatch.setattr(GLib, 'idle_add', lambda func, *a, **k: calls.append(func) or 1)
        self.store_controller.open_files([str(self.dir)])
        sc = self.store_controller
        assert not sc.store.segments[1].loaded
        tm = TMController.__new__(TMController)
        tm.abstract_placeables = False
        tm.main_controller = self.main_controller

        class _Model:
            display_name = 'A TM'

            def lookup(self, unit, on_done):
                found = {'One': 'Een', 'Three': 'Drie'}.get(unit.source)
                on_done([{'source': unit.source, 'target': found, 'quality': 100}] if found else [])

        def read(index, unit):
            return sc.store.get_unit(index) if isinstance(unit, PeekUnit) else unit

        results = []
        run = pretranslate.Pretranslation(
            tm,
            [_Model()],
            sc.store.get_units(),
            pretranslate.Options(),
            lambda *a: None,
            results.append,
            read,
        )
        run.start()
        while calls:
            calls.pop(0)()
        pretranslate.apply_result(self.main_controller, results[0])
        sc.save_file()

        b = factory.getobject(str(self.dir / 'b.po')).units[1]
        assert (b.target, b.isfuzzy()) == ('Drie', True)
        assert factory.getobject(str(self.dir / 'a.po')).units[1].target == 'Een'

    def test_an_edited_unit_is_found_by_identity_across_files(self):
        self.store_controller.open_files([str(self.dir)])
        sc = self.store_controller
        unit = sc.store[3]
        sc.cursor.force_index(0)

        sc.select_unit(unit)

        assert sc.cursor.index == 3

    def test_revert_reads_every_file_again(self):
        self.store_controller.open_files([str(self.dir)])
        self._edit(2, 'Drie')

        self.store_controller.revert_file()

        store = self.store_controller.store
        assert store.peek_unit(2).target == ''
        assert [s.loaded for s in store.segments] == [True, False]
        assert self.store_controller.is_stream()

    def test_update_from_a_template_is_refused(self):
        self.store_controller.open_files([str(self.dir)])

        with pytest.raises(ValueError):
            self.store_controller.update_file(str(self.dir / 'a.pot'))

    def test_another_language_of_the_folder_opens_its_files(self):
        self.store_controller.open_files([str(self.dir)])
        sc = self.store_controller
        assert sc.stream_language == 'af'
        assert sc.opened_folder() == str(self.dir)

        sc.open_stream_language('zu')

        assert [u.source for u in sc.store] == ['Five']
        assert sc.store.get_target_language() == 'zu'
        assert sorted(code for code in sc.all_files.languages()) == ['af', 'zu']
