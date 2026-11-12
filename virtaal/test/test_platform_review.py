#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Review in the unit editor (#3337), on files opened from the fake
Weblate: others' suggestions to accept or dismiss, comments with the
notes, adding a comment and reporting a source problem from the context
menu, Reviewed offered only to who may approve, an older server showing
none of it quietly, and a platform without review showing nothing."""

import pytest
from gi.repository import Gtk
from test_scaffolding import TestScaffolding
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import TOKEN, demo_review, run_until
from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.support.platforms import Download, Registry, RemoteUnit, reviewview, workcopy
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.test_platforms import DummyPlatform


class TestPlatformReview(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _work(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pan_app, 'ui_language', 'en')
        monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        self.errors = []
        monkeypatch.setattr(self.main_controller, 'show_error', self.errors.append)
        self.fake = demo_review().start()
        self.platform = WeblatePlatform(str(tmp_path / 'weblate.ini'))
        self.dummy = DummyPlatform()
        self.servers = ServerList([self.platform, self.dummy], str(tmp_path / 'platforms.ini'))
        self.servers.put(self.platform, self.fake.url, {'token': TOKEN})
        self.servers.put(self.dummy, 'dummy://one', {'user': 'me', 'password': 'secret'})
        self.registry = Registry()
        monkeypatch.setattr(self.registry, 'servers', lambda: self.servers)
        self.registry.add(self.platform, self.main_controller)
        self.registry.add(self.dummy, self.main_controller)
        self.panel = self.registry.review
        self.store_controller.close_file()
        yield
        self.store_controller.close_file()
        self.registry.remove(self.platform)
        self.registry.remove(self.dummy)
        self.fake.stop()
        assert self.fake.schema_problems == []

    def _open(self):
        session = self.platform.session(self.fake.url, {'token': TOKEN})
        found = []
        session.download('hello', 'program', 'af', found.append, found.append)
        run_until(lambda: found)
        copy = workcopy.create(
            self.platform, self.servers.servers[0].url, 'hello', 'program', 'af', found[0]
        )
        assert self.main_controller.open_file(copy.path), self.errors
        return copy

    def _unit(self, source):
        store = self.store_controller.store
        return next(u for u in store.get_units() if str(u.source) == source)

    def _show(self, source):
        sc = self.store_controller
        unit = self._unit(source)
        sc.cursor.force_index(sc.store.index_of(unit))
        self.unit_controller.load_unit(unit)
        return unit

    def _asked(self, source):
        """Shows the unit, and waits for its server's answer."""
        unit = self._show(source)
        copy, r = self.panel.where(unit)
        run_until(lambda: self.panel.reviews.known(copy, r) is not None)
        return unit

    def _notes(self):
        return self.unit_controller.view._widgets['notes']['translator'].get_text()

    def _rows(self):
        """(text, buttons) of each suggestion shown."""
        rows = []
        for row in self.panel.box.get_children():
            label, *buttons = row.get_children()
            rows.append((label.get_text(), [b.get_label() for b in buttons]))
        return rows

    def _server_unit(self, source):
        return next(u for u in self.fake.units.values() if u['source'] == [source])

    def _menu(self, box=None):
        menu = Gtk.Menu()
        box = box or self.unit_controller.view.targets[0]
        box.emit('populate-popup', menu)
        return menu, [item.get_label() for item in menu.get_children() if item.get_label()]

    def test_a_unit_shows_its_suggestions_and_comments(self):
        self._open()

        self._asked('Save')

        assert self._rows() == [
            ('Suggestion from anna: Berg', ['Accept', 'Reject']),
            ('Suggestion from ben: Bewaar', ['Accept', 'Reject']),
        ]
        assert self.panel.box.get_visible()
        assert 'Comment from anna: Most apps say "Stoor" here.' in self._notes()
        assert self.errors == []

    def test_a_comment_on_the_source_text_shows_too(self):
        self._open()

        self._asked('Print')

        assert 'Comment from ben: Is this the verb or the noun?' in self._notes()
        assert not self.panel.box.get_visible()

    def test_the_next_unit_shows_only_its_own(self):
        self._open()
        self._asked('Save')

        self._asked('Help')

        assert self._rows() == [] and not self.panel.box.get_visible()
        assert 'Comment from' not in self._notes()

    def test_accept_makes_a_suggestion_the_units_text(self):
        self._open()
        unit = self._asked('Save')

        self.panel.box.get_children()[1].get_children()[1].clicked()

        assert self.unit_controller.view.get_target_n(0) == 'Bewaar'
        self.unit_controller.finish_current_unit()
        assert str(unit.target) == 'Bewaar'
        # Locally: sent with the next Submit.
        assert self.fake.patched == []

    def test_dismiss_rejects_a_suggestion_on_the_server(self):
        self._open()
        self._asked('Save')

        self.panel.box.get_children()[0].get_children()[2].clicked()
        run_until(lambda: len(self._rows()) == 1)

        assert self._rows() == [('Suggestion from ben: Bewaar', ['Accept', 'Reject'])]
        assert [s['target'] for s in self.fake.suggestions.values()] == [['Bewaar']]

    def test_a_refused_dismissal_is_said(self):
        self.fake.revoke('suggestion.delete')
        self._open()
        self._asked('Save')

        self.panel.box.get_children()[0].get_children()[2].clicked()
        run_until(lambda: self.errors)

        assert self.errors[0].startswith('The server replied with error 403.')
        assert len(self.fake.suggestions) == 2

    def test_without_the_right_to_review_there_is_no_dismiss(self):
        self._open()
        self.store_controller.work_copy.refuse('review')
        self._asked('Save')

        assert [buttons for _text, buttons in self._rows()] == [['Accept'], ['Accept']]

    def test_reviewed_is_offered_only_to_who_may_approve(self):
        self._open()
        self._show('Save')
        state = self.unit_controller.view._widgets['state']
        assert 'Reviewed' not in state.unselectable

        self.store_controller.work_copy.refuse('review')
        self._show('Help')
        self._show('Save')

        assert 'Reviewed' in state.unselectable

    def test_a_project_without_reviews_offers_no_reviewed(self):
        self.fake.projects['hello']['project']['translation_review'] = False
        self._open()

        self._show('Save')

        assert 'Reviewed' in self.unit_controller.view._widgets['state'].unselectable
        assert self._unit('Save').get_state_n() == StateEnum.UNREVIEWED

    def test_the_context_menu_adds_a_comment(self, monkeypatch):
        self._open()
        self._asked('Help')
        asked = []

        def ask(_self, source):
            asked.append(source)
            return 'Hulp, of Help?'

        monkeypatch.setattr(reviewview.ReviewPanel, 'ask_comment', ask)
        _menu, labels = self._menu()
        assert labels[-2:] == ['Add _Comment…', 'Report a _Source Problem…']

        assert self.panel.add_comment(about_source=False)
        run_until(lambda: 'Hulp, of Help?' in self._notes())

        assert asked == [None]
        (comment,) = [c for c in self.fake.comments.values() if c['comment'] == 'Hulp, of Help?']
        assert comment['scope'] == 'translation'
        assert comment['unit_id'] == self._server_unit('Help')['id']

    def test_a_source_problem_is_reported_from_the_source_box(self, monkeypatch):
        self._open()
        self._asked('Print')
        asked = []

        def ask(_self, source):
            asked.append(source)
            return 'Typo?'

        monkeypatch.setattr(reviewview.ReviewPanel, 'ask_comment', ask)
        menu, _labels = self._menu(self.unit_controller.view.sources[0])
        menu.get_children()[-1].activate()
        run_until(lambda: 'Comment from tester: Typo?' in self._notes())

        assert asked == ['Print']
        (comment,) = [c for c in self.fake.comments.values() if c['comment'] == 'Typo?']
        assert comment['scope'] == 'report'

    def test_a_cancelled_comment_sends_nothing(self, monkeypatch):
        self._open()
        self._asked('Help')
        monkeypatch.setattr(reviewview.ReviewPanel, 'ask_comment', lambda _self, _source: None)
        before = len(self.fake.comments)

        assert not self.panel.add_comment(about_source=True)

        assert len(self.fake.comments) == before

    def test_a_change_submitted_as_a_suggestion_shows_as_one(self, monkeypatch):
        from virtaal.support.platforms.submitdialog import SubmitDialogs

        monkeypatch.setattr(SubmitDialogs, 'confirm', lambda _self, _count: True)
        self.servers.servers[0].as_suggestions = True
        self._open()
        self._asked('Help')
        self.unit_controller.set_unit_target(0, 'Hulp')
        self.unit_controller.finish_current_unit()

        result = self.registry.submit()
        run_until(lambda: self._rows())

        assert result.sent == 1 and self.fake.patched == []
        assert self._rows() == [('Suggestion from tester: Hulp', ['Accept', 'Reject'])]
        assert self._server_unit('Help')['target'] == ['']

    def test_an_older_server_shows_none_of_it_quietly(self):
        self.fake.version = (5, 6)
        self._open()

        self._asked('Save')

        assert self._rows() == [] and 'Comment from' not in self._notes()
        assert self.errors == []

    def test_the_comment_dialog_needs_some_text(self):
        dialog = reviewview.CommentDialog(None, 'Print')

        assert dialog.dialog.get_title() == 'Report a Source Problem'
        assert not dialog.ok.get_sensitive()
        dialog.set_text('  Typo  ')
        assert dialog.ok.get_sensitive() and dialog.get_text() == 'Typo'
        dialog.dialog.destroy()
        assert reviewview.CommentDialog(None).dialog.get_title() == 'Add Comment'

    def test_a_platform_without_review_shows_nothing(self):
        units = [RemoteUnit('a', 1, '', ['Alpha'], ['Alfa'], 120, 'final', None, False)]
        data = b'msgid "Alpha"\nmsgstr "Alfa"\n'
        download = Download('alpha.po', data, None, units, {'project': 'Alpha', 'component': None})
        copy = workcopy.create(self.dummy, 'dummy://one', 'alpha', None, 'af', download)
        assert self.main_controller.open_file(copy.path), self.errors
        self.fake.requests.clear()

        unit = self._show('Alpha')

        assert self.panel.where(unit)[0] is not None
        assert not self.panel.reviews.wanted(self.panel.where(unit)[0])
        assert self.panel._timer is None and not self.panel.box.get_visible()
        assert self._menu()[1][-1:] != ['Report a _Source Problem…']
        assert self.fake.requests == []
