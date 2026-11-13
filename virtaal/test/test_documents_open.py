#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Documents and translation bundles opened through the main controller:
saved as a bundle, exported, given languages, split and merged."""

import os
import zipfile

import pytest
from test_scaffolding import TestScaffolding
from translate.storage import factory

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.support import _docsamples, documents, packages
from virtaal.support.documents import DocumentBundle


class TestDocumentsOpen(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _work(self, monkeypatch, tmp_path, tmp_path_factory):
        self.state = tmp_path_factory.mktemp('state') / 'packages'
        monkeypatch.setattr(packages, 'packages_dir', lambda: str(self.state))
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        self.errors = []
        monkeypatch.setattr(self.main_controller, 'show_error', self.errors.append)
        self.view = self.main_controller.view
        self.notices = []
        monkeypatch.setattr(
            self.view, 'show_info_notice', lambda text, **_kw: self.notices.append(text)
        )
        self.saved_as = []
        monkeypatch.setattr(self.view, 'show_save_dialog', self._save_dialog)
        self.save_to = None
        self.dir = tmp_path
        lang = self.main_controller.lang_controller
        self.languages = (lang.source_lang, lang.target_lang)
        self.store_controller.close_file()
        self.main_controller.set_force_saveas(False)
        yield
        self.store_controller.close_file()
        self.main_controller.set_force_saveas(False)

    def _save_dialog(self, title, current_filename=None):
        self.saved_as.append(current_filename)
        if isinstance(self.save_to, list):
            return self.save_to.pop(0)
        return self.save_to

    def _odt(self, name, paragraphs, folder=None):
        path = (folder or self.dir) / name
        path.write_bytes(_docsamples.odt(paragraphs))
        return str(path)

    def _edit(self, index, text):
        sc = self.store_controller
        sc.cursor.force_index(index)
        unit = sc.store[index]
        self.unit_controller.load_unit(unit)
        self.unit_controller.set_unit_target(0, [text])
        self.unit_controller.finish_current_unit()

    def _target(self):
        return self.main_controller.lang_controller.target_lang.code

    def test_a_document_opens_and_its_first_save_is_save_as(self):
        report = self._odt('report.odt', ['Hello world.', 'Second *bold* part.'])
        before = sorted(os.listdir(self.dir))

        assert self.main_controller.open_file(report)

        sc = self.store_controller
        bundle = sc.bundle()
        assert bundle is not None and bundle.path is None
        assert sorted(os.listdir(self.dir)) == before
        assert self.main_controller.get_force_saveas()
        assert sc.get_store_title() == 'report.odt'
        assert self.view.gui.get_object('mnu_saveas').get_sensitive()
        assert self.view.gui.get_object('mnu_export_documents').get_sensitive()
        assert not self.view.gui.get_object('mnu_binary_export').get_sensitive()
        self._edit(0, 'Hallo wêreld.')
        self.save_to = str(self.dir / ('report.%s.virtaal' % self._target()))

        assert self.main_controller.save_file()

        assert self.saved_as == [self.save_to]
        assert not self.main_controller.get_force_saveas()
        with zipfile.ZipFile(self.save_to) as archive:
            translated = archive.read('targets/%s/report.odt' % self._target())
        assert _docsamples.odt_text(translated) == ['Hallo wêreld.', 'Second bold part.']
        assert sc.get_store_title() == os.path.basename(self.save_to)
        assert self.errors == []

    def test_export_writes_the_translated_document_beside_the_source(self):
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        self._edit(0, 'Hallo.')
        bundle = str(self.dir / 'report.virtaal')
        self.save_to = [bundle, str(self.dir / 'out.odt')]

        # The translations are saved first.
        assert self.main_controller.bundle_actions.export_documents()

        assert self.saved_as == [
            str(self.dir / ('report.%s.virtaal' % self._target())),
            str(self.dir / ('report.%s.odt' % self._target())),
        ]
        assert not self.store_controller.is_modified()
        assert _docsamples.odt_text((self.dir / 'out.odt').read_bytes()) == ['Hallo.']

    def test_export_refuses_to_replace_the_source(self):
        report = self._odt('report.odt', ['Hello world.'])
        data = (self.dir / 'report.odt').read_bytes()
        self.main_controller.open_file(report)
        self.save_to = [str(self.dir / 'report.virtaal'), report]

        assert not self.main_controller.bundle_actions.export_documents()

        assert len(self.errors) == 1
        assert (self.dir / 'report.odt').read_bytes() == data

    def test_a_saved_bundle_opens_again(self):
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        self._edit(0, 'Hallo.')
        self.save_to = str(self.dir / 'report.af.virtaal')
        self.main_controller.save_file()
        self.store_controller.close_file()

        assert self.main_controller.open_file(self.save_to)

        sc = self.store_controller
        assert sc.bundle().path == self.save_to
        assert not self.main_controller.get_force_saveas()
        assert str(sc.store[0].target) == 'Hallo.'

    def test_several_documents_open_as_a_master_with_no_language(self, monkeypatch):
        folder = self.dir / 'manual'
        folder.mkdir()
        intro = self._odt('intro.odt', ['Welcome.'], folder)
        usage = self._odt('usage.odt', ['Press the button.'], folder)

        assert self.main_controller.open_files([intro, usage])

        sc = self.store_controller
        assert sc.is_stream() and sc.bundle().read_only()
        assert [e.display_name for e in sc.all_files] == ['intro.odt', 'usage.odt']
        assert sc.store.get_unit(0).read_only
        assert any('no target language' in text for text in self.notices)
        assert not self.view.gui.get_object('mnu_export_documents').get_sensitive()

        # The South Africa case: three languages, split, one translated,
        # merged back, then a source document changes.
        chosen = iter(['zu', 'xh', 'st'])
        monkeypatch.setattr(self.view, 'ask_language', lambda *_args: next(chosen))
        for _language in range(3):
            assert self.main_controller.bundle_actions.add_language()
        bundle = sc.bundle()
        assert bundle.languages == ['zu', 'xh', 'st']
        assert sc.is_modified()
        self.save_to = str(self.dir / 'manual.virtaal')
        assert self.main_controller.save_file()

        out = self.dir / 'out'
        out.mkdir()
        monkeypatch.setattr(self.view, 'ask_folder', lambda *_args: str(out))
        assert self.main_controller.bundle_actions.split()
        assert sorted(os.listdir(out)) == [
            'manual.st.virtaal',
            'manual.xh.virtaal',
            'manual.zu.virtaal',
        ]

        returned = DocumentBundle.open(str(out / 'manual.zu.virtaal'), str(self.dir / 'zu-work'))
        for entry in returned.translations():
            store = factory.getobject(returned.work_path(entry['file']))
            for unit in store.units:
                if unit.source == 'Welcome.':
                    unit.target = 'Wamukelekile.'
            store.save()
        returned.save()
        returned.discard()

        monkeypatch.setattr(self.view, 'ask_bundles', lambda: [str(out / 'manual.zu.virtaal')])
        assert self.main_controller.bundle_actions.merge() == 1
        assert self.main_controller.save_file()
        merged = DocumentBundle.open(self.save_to, str(self.dir / 'check'))
        intro_zu = factory.getobject(
            merged.work_path(merged.translation('intro.odt', 'zu')['file'])
        )
        assert {u.source: str(u.target or '') for u in intro_zu.units}['Welcome.'] == (
            'Wamukelekile.'
        )
        assert self.errors == []

    def test_add_document_joins_every_language(self, monkeypatch):
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        notes = self._odt('notes.odt', ['A note.'])
        monkeypatch.setattr(self.view, 'ask_documents', lambda: [notes])

        assert self.main_controller.bundle_actions.add_document()

        sc = self.store_controller
        assert sc.is_stream()
        assert sorted(e.display_name for e in sc.all_files) == ['notes.odt', 'report.odt']
        assert sc.is_modified()

    def test_a_document_with_nothing_to_translate_shows_an_error(self):
        empty = self._odt('empty.odt', [])

        assert not self.main_controller.open_file(empty)

        assert len(self.errors) == 1
        assert 'nothing to translate' in self.errors[0]

    def test_a_translated_document_that_cant_be_made_is_reported(self, monkeypatch):
        from virtaal.support import converters

        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        self._edit(0, 'Hallo.')

        def broken(self, trans, template):
            raise ValueError('no good')

        monkeypatch.setattr(converters.OdfConverter, 'back', broken)
        self.save_to = str(self.dir / 'report.af.virtaal')

        assert self.main_controller.save_file()

        assert any('report.odt' in text and 'no good' in text for text in self.notices)
        assert os.path.exists(self.save_to)

    def test_large_documents_are_made_in_the_background(self, monkeypatch):
        import time

        from gi.repository import GLib

        monkeypatch.setattr(documents, 'INLINE_BYTES', 1)
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        self._edit(0, 'Hallo.')
        self.save_to = str(self.dir / 'report.af.virtaal')
        self.main_controller.save_file()
        member = 'targets/%s/report.odt' % self._target()
        with zipfile.ZipFile(self.save_to) as archive:
            assert member not in archive.namelist()

        deadline = time.monotonic() + 60
        while self.store_controller._regeneration is not None and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(0.02)

        with zipfile.ZipFile(self.save_to) as archive:
            assert _docsamples.odt_text(archive.read(member)) == ['Hallo.']

    def _master(self, monkeypatch, languages=('zu', 'xh')):
        folder = self.dir / 'manual'
        folder.mkdir()
        intro = self._odt('intro.odt', ['Welcome.'], folder)
        usage = self._odt('usage.odt', ['Press the button.'], folder)
        self.main_controller.open_files([intro, usage])
        chosen = iter(languages)
        monkeypatch.setattr(self.view, 'ask_language', lambda *_args: next(chosen))
        for _language in languages:
            self.main_controller.bundle_actions.add_language()
        self.save_to = str(self.dir / 'manual.virtaal')
        self.main_controller.save_file()
        return self.store_controller.bundle()

    def test_every_languages_documents_and_tipp_packages_from_the_files_panel(self, monkeypatch):
        self._master(monkeypatch)
        out = self.dir / 'out'
        monkeypatch.setattr(self.view, 'ask_folder', lambda *_args: str(out))
        from types import SimpleNamespace

        from virtaal.controllers.filepanelcontroller import FilePanelController

        panel = SimpleNamespace(main_controller=self.main_controller)
        FilePanelController.export_all(panel, 'export_documents')
        FilePanelController.export_all(panel, 'export_tipp')

        found = sorted(
            os.path.relpath(os.path.join(d, n), out) for d, _s, ns in os.walk(out) for n in ns
        )
        assert found == sorted(
            [
                os.path.join('xh', 'intro.odt'),
                os.path.join('xh', 'usage.odt'),
                os.path.join('zu', 'intro.odt'),
                os.path.join('zu', 'usage.odt'),
                'manual.xh.tipp',
                'manual.zu.tipp',
            ]
        )

    def test_export_of_the_language_shown_and_its_tipp_package(self, monkeypatch):
        self._master(monkeypatch)
        sc = self.store_controller
        language = sc.stream_language
        out = self.dir / 'out'
        monkeypatch.setattr(self.view, 'ask_folder', lambda *_args: str(out))
        self.save_to = str(self.dir / 'one.tipp')

        assert self.main_controller.bundle_actions.export_documents()
        assert self.main_controller.bundle_actions.export_tipp()

        assert sorted(os.listdir(out)) == ['intro.odt', 'usage.odt']
        assert self.saved_as[-1] == str(self.dir / 'manual' / ('manual.%s.tipp' % language))
        assert os.path.exists(self.save_to)

    def test_revert_opens_the_bundle_as_saved(self):
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        self.save_to = str(self.dir / 'report.af.virtaal')
        self.main_controller.save_file()
        self._edit(0, 'Hallo.')

        self.store_controller.revert_file()

        assert str(self.store_controller.store[0].target or '') == ''
        assert self.store_controller.bundle().path == self.save_to

    def test_merge_of_a_bundle_from_elsewhere_shows_an_error(self, monkeypatch):
        bundle = self._master(monkeypatch)
        out = self.dir / 'split'
        out.mkdir()
        bundle.split(str(out))
        bundle.save()
        other = documents.from_documents([self._odt('other.odt', ['Other.'])], 'en', 'zu')
        other_path = str(self.dir / 'other.zu.virtaal')
        other.save(other_path)
        monkeypatch.setattr(self.view, 'ask_bundles', lambda: [other_path])

        assert self.main_controller.bundle_actions.merge() == 0

        assert len(self.errors) == 1
        assert "wasn't split" in self.errors[0]

    def test_cancelling_the_merge_conflicts_changes_nothing(self, monkeypatch):
        bundle = self._master(monkeypatch, languages=('zu',))
        out = self.dir / 'split'
        out.mkdir()
        monkeypatch.setattr(self.view, 'ask_folder', lambda *_args: str(out))
        self.main_controller.bundle_actions.split()
        returned = DocumentBundle.open(str(out / 'manual.zu.virtaal'), str(self.dir / 'r'))
        entry = returned.translation('intro.odt', 'zu')
        store = factory.getobject(returned.work_path(entry['file']))
        store.units[0].target = 'Theirs.'
        store.save()
        returned.save()
        self._edit(0, 'Mine.')
        self.main_controller.save_file()
        monkeypatch.setattr(self.view, 'ask_bundles', lambda: [str(out / 'manual.zu.virtaal')])
        asked = []
        monkeypatch.setattr(
            self.view, 'ask_merge_conflicts', lambda conflicts: asked.append(conflicts)
        )

        assert self.main_controller.bundle_actions.merge() == 0

        assert [c.theirs for c in asked[0]] == [['Theirs.']]
        assert not bundle.modified
