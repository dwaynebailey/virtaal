#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Update from Server and sending finished units as you go, on a
Transifex work copy against the fake server: others' edits and reviews
come in, local changes stay, both changed is a conflict, a new string
downloads again, and finished units go in one bulk PATCH as they are
saved."""

from types import SimpleNamespace

import pytest
from translate.storage.workflow import StateEnum

from devsupport.testing.faketransifex import demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.transifex import client
from virtaal.plugins.transifex.test_submit import Copy
from virtaal.support.platforms import submit, trickle, update


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(pan_app, 'ui_language', 'en')
    monkeypatch.setattr(client.Client, 'POLL_FIRST', 0.01)


@pytest.fixture
def fake():
    server = demo().start()
    yield server
    server.stop()
    assert server.schema_problems == []


def _update(copy, decisions=None, download=True):
    done, asked = [], []

    def on_conflicts(found, resolve):
        asked.append(found)
        resolve(decisions or {})

    def on_download(plan, proceed):
        proceed({} if download else None)

    update.Update(
        copy.copy, copy.platform, copy.session(), on_conflicts, on_download, done.append
    ).start()
    run_until(lambda: done, timeout=30)
    return done[0], asked


def test_others_edits_and_reviews_come_in(fake):
    copy = Copy(fake)
    fake.edit(copy.resource, 'Print', 'Druk')
    fake.review(copy.resource, 'Save')

    result, asked = _update(copy)

    assert asked == [] and result.stop is None
    assert str(copy.unit('Print').target) == 'Druk'
    assert copy.unit('Save').get_state_n() == StateEnum.FINAL
    assert copy.copy.pending() == []


def test_a_local_change_stays_and_both_changed_is_a_conflict(fake):
    copy = Copy(fake)
    copy.edit('Help', 'Hulp')
    copy.edit('Save', 'Bewaar')
    copy.save()
    fake.edit(copy.resource, 'Save', 'Stoor dit')

    result, [found] = _update(copy, {})

    assert [(c.source, c.theirs) for c in found] == [('Save', ['Stoor dit'])]
    assert str(copy.unit('Help').target) == 'Hulp'
    assert str(copy.unit('Save').target) == 'Bewaar'
    assert result.skipped == 1


def test_a_new_string_downloads_the_file_again(fake):
    copy = Copy(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.add_string(copy.resource, 'Undo', 'Undo')

    result, _asked = _update(copy)

    assert result.stop is None
    assert len(fake.requests_to('/resource_translations_async_downloads', 'POST')) == 2
    copy.open()
    assert len(copy.copy.units) == 8 and copy.unit('Undo') is not None


class Trickling:
    """A Trickler for one Copy's server, its waits kept to run by hand."""

    def __init__(self, copy):
        self.copy = copy
        self.sent = []
        self.conflicts = []
        self.waits = []
        server = SimpleNamespace(send_finished=True, platform=copy.platform, session=copy.session)
        self.trickler = trickle.Trickler(
            lambda _copy: server, self.sent.append, self.conflicts.append
        )
        self.trickler.schedule = lambda seconds, callback: self.waits.append((seconds, callback))

    def save(self):
        self.copy.save()
        self.trickler.saved(self.copy.copy)
        run_until(lambda: not self.trickler.running, timeout=30)


def test_finished_units_go_together_as_they_are_saved(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    copy.edit('Help', 'Hulp')
    copy.edit('Print', 'Druk')
    copy.edit('Save', 'Bewaar', StateEnum.NEEDS_WORK)

    trickling.save()

    bulk = [r for r in fake.requests_to('/resource_translations', 'PATCH')]
    assert len(bulk) == 1
    assert sorted(i for i, _a in fake.patched) == sorted([copy.tx_id('Help'), copy.tx_id('Print')])
    # The draft waits for Submit.
    assert copy.copy.pending() == [copy.index('Save')]
    assert [r.sent for r in trickling.sent] == [2]


def test_a_finished_unit_changed_on_the_server_waits_for_submit(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    copy.edit('Save', 'Bewaar')
    fake.edit(copy.resource, 'Save', 'Stoor dit')

    trickling.save()

    assert fake.patched == [] and trickling.conflicts == [1]
    assert copy.entry('Save').get('conflict') is True
    result = copy.submit({copy.index('Save'): submit.KEEP_MINE})
    assert result.sent == 1 and copy.server('Save')['strings'] == {'other': 'Bewaar'}


def test_offline_the_queue_waits_and_goes_later(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    fake.offline = True
    copy.edit('Help', 'Hulp')

    trickling.save()

    assert copy.copy.record['queue'] == [copy.tx_id('Help')]
    fake.offline = False
    _wait, again = trickling.waits[-1]
    again()
    run_until(lambda: not trickling.trickler.running, timeout=30)
    assert copy.copy.record['queue'] == [] and copy.copy.pending() == []
