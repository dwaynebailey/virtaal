#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A Git project on the fake GitHub in Virtaal: Submit's question says
what goes to the pull request and offers a commit of its own with a
summary, and the pull request's state is a notice with its link once
the work copy is opened."""

import pytest
from gi.repository import Gtk
from test_scaffolding import TestScaffolding

from devsupport.testing.fakeforge import TOKEN, FakeForge
from devsupport.testing.gitremote import FakeStore, isolate_config, needs_dulwich
from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.gitproject import backend, forge
from virtaal.plugins.gitproject.credentials import GitCredentials
from virtaal.plugins.gitproject.test_backend import AF, po_file
from virtaal.support.platforms import Registry, submitdialog, workcopy
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.submitdialog import SubmitDialogs

PROJECT = 'team/program'


class NoStore:
    def usable(self):
        return False

    def backend(self):
        return None


@needs_dulwich
class TestForkInVirtaal(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _work(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pan_app, 'ui_language', 'en')
        monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
        monkeypatch.setitem(pan_app.settings.translator, 'name', 'Thandi')
        monkeypatch.setitem(pan_app.settings.translator, 'email', 'thandi@example.org')
        isolate_config(monkeypatch, tmp_path / 'home')
        monkeypatch.setattr(SubmitDialogs, '_tell', lambda _self, text, *a: self.told.append(text))
        self.told, self.asked = [], []
        monkeypatch.setattr(submitdialog, 'run_dialog', self._answer)
        self.github = FakeForge(tmp_path / 'github').start()
        self.github.add_repository(PROJECT, {'po/af.po': po_file('af', AF)})
        self.url = self.github.clone_url(PROJECT)
        self.platform = backend.GitPlatform(
            str(tmp_path / 'gitprojects.ini'), str(tmp_path / 'clones')
        )
        self.platform.credentials = GitCredentials(FakeStore(), helper=None)
        self.platform.tokens = forge.Tokens(forge.TokenStore(NoStore()))
        self.platform.tokens.session[self.github.host] = TOKEN
        self.platform.projects.github_hosts = {self.github.host: self.github.api}
        self.platform.projects.save()
        self.servers = ServerList([self.platform], str(tmp_path / 'platforms.ini'))
        self.servers.put(self.platform, self.url, {'branch': 'main', 'files': 'po/*.po'})
        self.servers.save()
        self.registry = Registry()
        self.registry._reminded = True
        monkeypatch.setattr(self.registry, 'servers', lambda: self.servers)
        self.registry.add(self.platform, self.main_controller)
        self.main_controller.store_controller.close_file()
        yield
        self.main_controller.store_controller.close_file()
        self.registry.remove(self.platform)
        self.github.stop()

    def _answer(self, dialog):
        """Submit's question: what it says and offers, ticked and filled
        in as self.choose has it."""
        area = dialog.get_message_area()
        texts = [w.get_text() for w in area.get_children() if isinstance(w, Gtk.Label)]
        checks = [w for w in area.get_children() if isinstance(w, Gtk.CheckButton)]
        entries = [
            e
            for box in area.get_children()
            if isinstance(box, Gtk.Box)
            for e in box.get_children()
            if isinstance(e, Gtk.Entry)
        ]
        self.asked.append((texts, [c.get_label() for c in checks]))
        choice = getattr(self, 'choose', None)
        if choice and checks:
            checks[0].set_active(choice[0])
            self.asked[-1] = (
                [w.get_text() for w in area.get_children() if isinstance(w, Gtk.Label)],
                self.asked[-1][1],
            )
            entries[0].set_text(choice[1])
        return Gtk.ResponseType.OK

    def _open(self):
        session = self.platform.session(self.url, {'branch': 'main', 'files': 'po/*.po'})
        found = []
        session.download('program', None, 'af', found.append, found.append)
        _wait(lambda: found)
        copy = workcopy.create(self.platform, self.url, 'program', None, 'af', found[0])
        assert self.main_controller.open_file(copy.path)
        return copy.path

    def _edit(self, source, text):
        sc = self.main_controller.store_controller
        uc = self.main_controller.unit_controller
        unit = next(u for u in sc.store.get_units() if str(u.source) == source)
        sc.cursor.force_index(sc.store.index_of(unit))
        uc.load_unit(unit)
        uc.set_unit_target(0, [text])
        uc.finish_current_unit()
        assert self.main_controller.save_file()

    def test_submit_says_it_opens_a_pull_request_then_offers_a_commit_of_its_own(self):
        path = self._open()
        self._edit('Save', 'Stoor')
        self.registry.submit()
        assert 'Submit pushes to your fork and opens a pull request.' in self.asked[0][0][1]
        assert self.asked[0][1] == []
        assert [p['number'] for p in self.github.pulls] == [1]

        _wait(lambda: self.registry.notice_text() is not None)
        assert self.registry.notice_text() == 'Your pull request #1 is open.'
        link = self.registry._notice.get_content_area().get_children()[1]
        url = '%s/%s/pull/1' % (self.github.base, PROJECT)
        assert (link.get_text(), 'href="%s"' % url in link.get_label()) == (url, True)

        self._edit('Quit', 'Verlaat')
        self.choose = (True, 'Menu strings')
        self.registry.submit()
        texts, checks = self.asked[1]
        assert checks == ['Add as a _separate commit']
        assert texts[1].endswith('Adds a commit to your pull request.')
        tip = self.github.refs(PROJECT)['refs/heads/main']
        assert self.github.subjects('thandi/program', 'refs/heads/virtaal/af', tip) == [
            'Menu strings',
            'Update Afrikaans (af) translation',
        ]

        # Opened again later: the notice says where the pull request is.
        self.main_controller.store_controller.close_file()
        self.registry._hide_notice()
        assert self.main_controller.open_file(path)
        _wait(lambda: self.registry.notice_text() is not None)
        self.github.merge(1)
        self.registry._hide_notice()
        self.main_controller.store_controller.close_file()
        assert self.main_controller.open_file(path)
        _wait(lambda: self.registry.notice_text() == 'Your pull request #1 was merged.')


def _wait(done):
    from devsupport.testing.fakeplatform import run_until

    run_until(done, timeout=30)
