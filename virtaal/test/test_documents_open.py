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
        from virtaal.controllers.filepanelcontroller import FilePanelController

        self.panel = FilePanelController(self.main_controller)

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
        monkeypatch.setattr(self.view, 'ask_split_languages', lambda languages: languages)
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

        assert self.errors == [
            "other.zu.virtaal wasn't made by Split by Language.\n\n"
            'Choose a bundle split from this one.'
        ]

    def test_merge_of_an_older_split_says_to_update_it_first(self, monkeypatch):
        bundle = self._master(monkeypatch, languages=('zu',))
        old, new = self.dir / 'old', self.dir / 'new'
        old.mkdir()
        new.mkdir()
        [path] = bundle.split(str(old))
        bundle.split(str(new))
        bundle.save()
        monkeypatch.setattr(self.view, 'ask_bundles', lambda: [path])

        assert self.main_controller.bundle_actions.merge() == 0

        assert self.errors == [
            'manual.zu.virtaal is from an older split of Zulu.\n\n'
            'Open it and update it from the latest Zulu bundle first.'
        ]

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

    def test_a_changed_source_document_offers_to_update_every_language(self, monkeypatch):
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        self._edit(0, 'Hallo.')
        self.save_to = str(self.dir / 'report.af.virtaal')
        self.main_controller.save_file()
        self.store_controller.close_file()
        (self.dir / 'report.odt').write_bytes(_docsamples.odt(['Hello world.', 'New.']))
        offered = []
        monkeypatch.setattr(
            self.view,
            'show_info_notice',
            lambda text, action=None, **_kw: offered.append((text, action)),
        )

        self.main_controller.open_file(self.save_to)

        [(text, action)] = offered
        assert 'original document has changed' in text
        action[1]()
        sc = self.store_controller
        assert [str(u.source) for u in sc.store.get_units()] == ['Hello world.', 'New.']
        assert str(sc.store.get_units()[0].target) == 'Hallo.'
        assert sc.is_modified()

    def test_update_from_template_takes_a_new_version_of_the_document(self, monkeypatch):
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        new = self._odt('report-v2.odt', ['Hello world.', 'Added.'])
        monkeypatch.setattr(self.view, 'ask_documents', lambda: [new])

        self.view._on_file_update(None)

        assert [str(u.source) for u in self.store_controller.store.get_units()] == [
            'Hello world.',
            'Added.',
        ]

    def test_opening_a_document_again_offers_its_bundle(self, monkeypatch):
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        self._edit(0, 'Hallo.')
        self.save_to = str(self.dir / 'report.af.virtaal')
        self.main_controller.save_file()
        self.store_controller.close_file()
        asked = []
        monkeypatch.setattr(
            self.main_controller, 'show_prompt', lambda **kw: asked.append(kw['msg']) or True
        )

        assert self.main_controller.open_file(report)

        assert 'report.af.virtaal' in asked[0]
        assert self.store_controller.bundle().path == self.save_to
        assert str(self.store_controller.store[0].target) == 'Hallo.'

    def test_export_asks_before_replacing_an_edited_export(self, monkeypatch):
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        out = str(self.dir / 'out.odt')
        self.save_to = [str(self.dir / 'report.af.virtaal'), out, out, out]
        assert self.main_controller.bundle_actions.export_documents()
        asked = []
        monkeypatch.setattr(
            self.main_controller, 'show_prompt', lambda **kw: asked.append(kw['msg']) or False
        )

        assert self.main_controller.bundle_actions.export_documents()
        assert asked == []
        (self.dir / 'out.odt').write_bytes(b'edited')
        assert not self.main_controller.bundle_actions.export_documents()

        assert len(asked) == 1
        assert (self.dir / 'out.odt').read_bytes() == b'edited'

    def test_saving_a_bundle_changed_on_disk_forces_save_as(self):
        report = self._odt('report.odt', ['Hello world.'])
        self.main_controller.open_file(report)
        self.save_to = str(self.dir / 'report.af.virtaal')
        self.main_controller.save_file()
        self._edit(0, 'Hallo.')
        os.utime(self.save_to, ns=(0, 0))

        assert not self.main_controller.save_file()

        assert 'changed by another program' in self.errors[0]
        assert self.main_controller.get_force_saveas()
        self.save_to = str(self.dir / 'mine.virtaal')
        assert self.main_controller.save_file()
        assert os.path.exists(self.save_to)

    def test_the_files_panel_offers_every_languages_exports_for_a_bundle(self, monkeypatch):
        self._master(monkeypatch)

        menu = self.panel.view.all_languages_button.get_popup()
        labels = [item.get_label() for item in menu.get_children() if item.get_visible()]

        assert labels[-2:] == ['Export Translated Doc_uments…', 'Export as _TIPP…']
        assert self.panel.view.language_box.get_visible()

    def test_split_by_language_writes_the_languages_chosen(self, monkeypatch):
        bundle = self._master(monkeypatch)
        out = self.dir / 'split'
        out.mkdir()
        monkeypatch.setattr(self.view, 'ask_folder', lambda *_args: str(out))
        offered = []
        chosen = iter([None, ['xh']])

        def ask(languages):
            offered.append(languages)
            return next(chosen)

        monkeypatch.setattr(self.view, 'ask_split_languages', ask)

        assert not self.main_controller.bundle_actions.split()
        assert os.listdir(out) == []
        assert self.main_controller.bundle_actions.split()

        assert offered == [['zu', 'xh'], ['zu', 'xh']]
        assert os.listdir(out) == ['manual.xh.virtaal']
        assert list(bundle.manifest['splits']) == ['xh']

    def test_the_language_choice_lists_the_bundles_languages_ticked(self, monkeypatch):
        from gi.repository import Gtk

        from virtaal.views.widgets.selectdialog import SelectDialog

        shown = []

        def run(dialog, items=None, parent=None):
            shown.append(dialog.sview.get_all_items())
            for row in dialog.sview.get_model():
                row[0] = row[3] != 'zu'
            return Gtk.ResponseType.OK

        monkeypatch.setattr(SelectDialog, 'run', run)

        chosen = self.view.ask_split_languages(['zu', 'xh'])

        assert sorted((i['desc'], i['enabled']) for i in shown[0]) == [('xh', True), ('zu', True)]
        assert chosen == ['xh']

    def _translator(self):
        """A master of two documents into zu and xh, its zu bundle split
        and open, as its translator has it."""
        folder = self.dir / 'manual'
        folder.mkdir()
        intro = self._odt('intro.odt', ['Welcome.', 'Read this first.'], folder)
        usage = self._odt('usage.odt', ['Press the button.'], folder)
        master = documents.from_documents([intro, usage], 'en')
        master.add_language('zu')
        master.add_language('xh')
        master.save(str(self.dir / 'manual.virtaal'))
        path = self._resplit(master, 'v1')
        assert self.main_controller.open_file(path)
        return master, path

    def _resplit(self, master, folder):
        (self.dir / folder).mkdir()
        return master.split(str(self.dir / folder), ['zu'])[0]

    @staticmethod
    def _units(bundle, document):
        entry = bundle.translation(document, 'zu')
        store = factory.getobject(bundle.work_path(entry['file']))
        return {
            u.source: (str(u.target or ''), u.isfuzzy()) for u in store.units if u.istranslatable()
        }

    def test_a_split_bundle_updates_from_a_refreshed_one(self, monkeypatch):
        master, path = self._translator()
        sc = self.store_controller
        assert self.view.gui.get_object('mnu_refresh_bundle').get_sensitive()
        assert not self.view.gui.get_object('mnu_merge_bundles').get_sensitive()
        self._edit(0, 'Wamukelekile.')
        master.update_document(
            'intro.odt', _docsamples.odt(['Welcome.', 'Read this first, please.'])
        )
        master.add_document(self._odt('faq.odt', ['Any questions?'], self.dir / 'manual'))
        _translated = master.work_path(master.translation('faq.odt', 'zu')['file'])
        store = factory.getobject(_translated)
        store.units[0].target = 'Imibuzo?'
        store.save()
        refreshed = self._resplit(master, 'v2')
        monkeypatch.setattr(self.view, 'ask_bundles', lambda: [refreshed])

        assert self.main_controller.bundle_actions.update_from_refreshed()

        bundle = sc.bundle()
        assert [e.display_name for e in sc.all_files] == ['faq.odt', 'intro.odt', 'usage.odt']
        assert self._units(bundle, 'intro.odt') == {
            'Welcome.': ('Wamukelekile.', False),
            'Read this first, please.': ('', False),
        }
        assert self._units(bundle, 'faq.odt') == {'Any questions?': ('Imibuzo?', False)}
        assert sc.is_modified()
        assert self.notices[-1] == 'Updated the bundle, with 0 translations from the refreshed one.'
        assert self.main_controller.save_file()
        assert self.errors == []

        returned = DocumentBundle.open(path, str(self.dir / 'returned'))
        plan = master.plan_merge(returned)
        assert (plan.takes, plan.conflicts) == (1, [])

    def test_a_refresh_asks_about_conflicts_and_removed_work(self, monkeypatch):
        from virtaal.support.platforms.submit import TAKE_THEIRS

        master, path = self._translator()
        sc = self.store_controller
        self._edit(0, 'Mine.')
        self._edit(2, 'Inkinobho.')
        entry = master.translation('intro.odt', 'zu')
        store = factory.getobject(master.work_path(entry['file']))
        store.units[0].target = 'Theirs.'
        store.save()
        master.remove_document('usage.odt')
        refreshed = self._resplit(master, 'v2')
        monkeypatch.setattr(self.view, 'ask_bundles', lambda: [refreshed])
        prompts = []
        answers = iter([False, True, True])
        monkeypatch.setattr(
            self.main_controller,
            'show_prompt',
            lambda **kw: prompts.append(kw['msg']) or next(answers),
        )
        conflicts = []
        decisions = iter([None, {0: TAKE_THEIRS}])
        monkeypatch.setattr(
            self.view,
            'ask_refresh_conflicts',
            lambda found: conflicts.append(found) or next(decisions),
        )
        actions = self.main_controller.bundle_actions

        assert not actions.update_from_refreshed()  # No to removing usage.odt
        assert not actions.update_from_refreshed()  # Cancelled the conflicts
        assert sc.bundle().split_from()['number'] == 1
        assert actions.update_from_refreshed()

        assert len(prompts) == 3 and 'no longer has usage.odt' in prompts[0]
        assert [(c.mine, c.theirs) for c in conflicts[0]] == [(['Mine.'], ['Theirs.'])]
        bundle = sc.bundle()
        assert [d['name'] for d in bundle.documents()] == ['intro.odt']
        assert self._units(bundle, 'intro.odt')['Welcome.'] == ('Theirs.', False)
        assert self.notices[-1] == 'Updated the bundle, with 1 translation from the refreshed one.'

    def test_a_refresh_refuses_other_bundles_and_keeps_its_files(self, monkeypatch):
        master, path = self._translator()
        bundle = self.store_controller.bundle()
        actions = self.main_controller.bundle_actions

        for chosen in (master.path, path):
            monkeypatch.setattr(self.view, 'ask_bundles', lambda chosen=chosen: [chosen])
            assert not actions.update_from_refreshed()

        assert self.errors == [
            "manual.virtaal wasn't made by Split by Language.\n\n"
            'Choose the latest Zulu bundle split from the same project.',
            'manual.zu.virtaal is not newer than this bundle.\n\n'
            'Choose the latest Zulu bundle split from the same project.',
        ]

        assert all(os.path.isfile(f) for f in bundle.files())
