#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Translation Server Glossaries terminology source with work copies
opened from the fake Weblate (#3337): the project's glossary in the
work copy's language gives terms, kept for the next time and asked
again when due; a project or platform without glossaries gives none."""

from types import SimpleNamespace

import pytest
from test_scaffolding import TestScaffolding
from translate.storage.placeables.terminology import TerminologyPlaceable

from devsupport.testing.fakeweblate import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.terminology.models import platformglossary
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.support import platforms
from virtaal.support.platforms import Download, Registry, RemoteUnit, glossary, workcopy
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.test_platforms import DummyPlatform

TERMS = [('file', 'lêer'), ('save', 'stoor')]


class TestPlatformGlossary(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _work(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pan_app, 'ui_language', 'en')
        monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
        monkeypatch.setattr(pan_app, 'get_config_dir', lambda: str(tmp_path / 'config'))
        (tmp_path / 'config').mkdir()
        self.errors = []
        monkeypatch.setattr(self.main_controller, 'show_error', self.errors.append)
        self.fake = demo()
        self.fake.fill_glossary('hello', 'glossary', 'af', TERMS)
        self.fake.start()
        self.platform = WeblatePlatform(str(tmp_path / 'weblate.ini'))
        self.dummy = DummyPlatform()
        self.servers = ServerList([self.platform, self.dummy], str(tmp_path / 'platforms.ini'))
        self.servers.put(self.platform, self.fake.url, {'token': TOKEN})
        self.servers.put(self.dummy, 'dummy://one', {'user': 'me', 'password': 'secret'})
        self.registry = Registry()
        monkeypatch.setattr(self.registry, 'servers', lambda: self.servers)
        monkeypatch.setattr(platforms, 'registry', self.registry)
        self.registry.add(self.platform, self.main_controller)
        self.registry.add(self.dummy, self.main_controller)
        self.rescans = []
        controller = SimpleNamespace(
            main_controller=self.main_controller,
            rescan_current_unit=lambda: self.rescans.append(1),
        )
        self.store_controller.close_file()
        self.model = platformglossary.TerminologyModel('platformglossary', controller)
        yield
        self.model.destroy()
        self.store_controller.close_file()
        self.registry.remove(self.platform)
        self.registry.remove(self.dummy)
        self.fake.stop()
        assert self.fake.schema_problems == []
        assert self.model.matcher not in TerminologyPlaceable.matchers

    def _open(self, project='hello', component='program'):
        session = self.platform.session(self.fake.url, {'token': TOKEN})
        found = []
        session.download(project, component, 'af', found.append, found.append)
        run_until(lambda: found)
        copy = workcopy.create(
            self.platform, self.servers.servers[0].url, project, component, 'af', found[0]
        )
        assert self.main_controller.open_file(copy.path), self.errors
        return copy

    def _terms(self, text):
        return sorted((str(u.source), str(u.target)) for u in self.model.matcher.matches(text))

    def _downloads(self):
        return [r for r in self.fake.requests_to('/api/translations/hello/glossary/af/file/')]

    def test_a_work_copy_shows_its_projects_glossary(self):
        self._open()

        run_until(lambda: self.model.stores)

        assert self._terms('Save the file') == [('file', 'lêer'), ('save', 'stoor')]
        assert self.model.matcher in TerminologyPlaceable.matchers
        assert self.rescans

    def test_the_glossary_kept_shows_at_once_and_is_asked_again_when_due(self, monkeypatch):
        self._open()
        run_until(lambda: self.model.stores)
        self.store_controller.close_file()
        assert self.model.stores == []

        self._open()

        # Kept from before, and not due yet: no request.
        assert self._terms('Save') == [('save', 'stoor')]
        assert len(self._downloads()) == 1

        self.store_controller.close_file()
        monkeypatch.setattr(glossary, 'REFRESH_AGE', -1)
        term = next(u for u in self.fake.units.values() if u['source'] == ['save'])
        self.fake.edit(term['id'], 'bewaar')
        self._open()
        run_until(lambda: self._terms('Save') == [('save', 'bewaar')])
        assert self._downloads()[-1]['headers'].get('If-Modified-Since')

    def test_an_unchanged_glossary_is_not_downloaded_again(self, monkeypatch):
        self._open()
        run_until(lambda: self.model.stores)
        self.store_controller.close_file()
        monkeypatch.setattr(glossary, 'REFRESH_AGE', -1)

        self._open()
        run_until(lambda: len(self._downloads()) == 2 and not self.model._asking)

        assert self._terms('Save') == [('save', 'stoor')]

    def test_a_project_without_a_glossary_gives_no_terms(self):
        self.fake.add_translation('docs', 'guides/manual', 'af', 'Afrikaans', 1, 1)
        self.fake.fill_po('docs', 'guides/manual', 'af', [('', 'Save', 'Stoor', 20)])
        self._open('docs', 'guides/manual')

        run_until(lambda: not self.model._asking)

        assert self.model.stores == [] and self._terms('Save') == []
        assert self.errors == []

    def test_no_reply_leaves_no_terms_and_no_error(self):
        self.fake.offline = True
        self._open_offline()

        assert self.model.stores == [] and self.errors == []

    def _open_offline(self):
        units = [RemoteUnit(1, 1, '', ['Save'], ['Stoor'], 100, 20, None, False)]
        download = Download('po/af.po', b'msgid "Save"\nmsgstr "Stoor"\n', None, units, {})
        copy = workcopy.create(
            self.platform, self.servers.servers[0].url, 'hello', 'program', 'af', download
        )
        assert self.main_controller.open_file(copy.path), self.errors
        run_until(lambda: not self.model._asking, timeout=60)

    def test_the_dummy_platform_has_no_glossaries(self, tmp_path):
        units = [RemoteUnit('a', 1, '', ['Alpha'], ['Alfa'], 120, 'final', None, False)]
        data = b'msgid "Alpha"\nmsgstr "Alfa"\n'
        download = Download('alpha.po', data, None, units, {'project': 'Alpha', 'component': None})
        copy = workcopy.create(self.dummy, 'dummy://one', 'alpha', None, 'af', download)

        assert self.main_controller.open_file(copy.path), self.errors

        assert not self.dummy.can_glossary
        assert self.model.wanted() == [] and self.model.stores == []
        assert self.fake.requests == []
