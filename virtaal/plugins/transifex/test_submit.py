#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Submitting a Transifex work copy's changes to the fake server through
the real curl client: what the bulk PATCH sends (strings by plural
category, reviewed), batches of 150, states Transifex doesn't have,
conflicts, refusals, the rate limit, and resuming a Submit cut short."""

from types import SimpleNamespace

import pytest
from translate.misc.multistring import multistring
from translate.storage import po
from translate.storage.workflow import StateEnum

from devsupport.testing.faketransifex import BULK, TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.transifex import backend, client
from virtaal.support.platforms import REVIEW, submit, workcopy

PROGRAM = 'hello-org/program'


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


class Copy:
    """A downloaded Transifex work copy, its file opened and attached as
    Virtaal opens it."""

    def __init__(self, fake, component='messages', project=PROGRAM, tmp=None, opened=True):
        self.fake = fake
        self.platform = backend.TransifexPlatform(str(tmp) if tmp else '/nonexistent/tx.ini')
        download = []
        self.session().download(project, component, 'af', download.append, download.append)
        run_until(lambda: download, timeout=30)
        self.download = download[0]
        self.copy = workcopy.create(self.platform, fake.url, project, component, 'af', download[0])
        self.resource = fake._resource(backend.resource_id(project, component))
        if opened:
            self.open()

    def session(self):
        return self.platform.session(self.fake.url, {'token': TOKEN})

    def open(self):
        """The file and record from disk, as after a restart."""
        self.copy = workcopy.for_file(self.copy.path)
        self.store = po.pofile.parsefile(self.copy.path)
        self.units = [u for u in self.store.units if not u.isheader()]
        self.copy.attach(SimpleNamespace(get_units=lambda: self.units, pair=None))

    def unit(self, source):
        return next(u for u in self.units if str(u.source) == source)

    def index(self, source):
        return next(r for r, u in self.copy.matched.items() if str(u.source) == source)

    def server(self, source, code='af'):
        """The fake's translation of the string with source text."""
        return self.fake.string(self.resource, source)['translations'].get(code) or {}

    def tx_id(self, source):
        string = self.fake.string(self.resource, source)
        return self.fake.translation_id(self.resource, string, 'af')

    def edit(self, source, target=None, state=None):
        unit = self.unit(source)
        if target is not None:
            unit.target = target
        if state is not None:
            unit.set_state_n(state)

    def save(self):
        self.store.save()
        self.copy.saved()

    def submit(self, decisions=None, session=None):
        done, conflicts = [], []

        def on_conflicts(found, resolve):
            conflicts.extend(found)
            resolve(decisions or {})

        submission = submit.Submission(
            self.copy,
            self.platform,
            session or self.session(),
            lambda *args: None,
            on_conflicts,
            done.append,
        )
        submission.start()
        run_until(lambda: done, timeout=30)
        self.conflicts = conflicts
        return done[0]

    def entry(self, source):
        return self.copy.units[self.index(source)]


def _bulk(fake):
    return [
        r for r in fake.requests_to('/resource_translations', 'PATCH') if r['path'].count('/') == 1
    ]


def test_the_changes_go_in_one_bulk_patch(fake):
    copy = Copy(fake)
    copy.edit('Print', 'Druk')
    copy.edit('Save', 'Bewaar')
    copy.save()

    result = copy.submit()

    assert (result.total, result.sent, result.stop, result.left) == (2, 2, None, 0)
    [request] = _bulk(fake)
    assert request['headers']['Content-Type'] == BULK
    assert fake.patched == [
        (copy.tx_id('Save'), {'strings': {'other': 'Bewaar'}, 'reviewed': False}),
        (copy.tx_id('Print'), {'strings': {'other': 'Druk'}, 'reviewed': False}),
    ]
    assert copy.server('Print')['strings'] == {'other': 'Druk'}
    entry = copy.entry('Print')
    assert entry['changed'] is False and entry['state'] == backend.TRANSLATED
    assert entry['last_updated'] == copy.server('Print')['translated']
    assert copy.copy.submitted()


def test_nothing_changed_sends_nothing(fake):
    copy = Copy(fake)

    assert copy.submit().total == 0
    assert fake.patched == []


@pytest.mark.parametrize(
    ('source', 'state', 'sent'),
    [
        ('Save', StateEnum.FINAL, {'strings': {'other': 'Stoor'}, 'reviewed': True}),
        ('Open', StateEnum.UNREVIEWED, {'strings': {'other': 'Maak oop'}, 'reviewed': False}),
    ],
)
def test_reviewed_goes_back_as_transifexs_reviewed(fake, source, state, sent):
    copy = Copy(fake)
    copy.edit(source, state=state)
    copy.save()

    copy.submit()

    assert fake.patched == [(copy.tx_id(source), sent)]
    assert copy.server(source)['reviewed'] is sent['reviewed']
    assert copy.unit(source).get_state_n() == state


@pytest.mark.parametrize(
    'state', [StateEnum.NEEDS_WORK, StateEnum.NEEDS_REVIEW, StateEnum.REJECTED]
)
def test_a_state_transifex_hasnt_goes_as_translated_and_stays_here(fake, state):
    copy = Copy(fake)
    copy.edit('Print', 'Druk', state=state)
    copy.save()
    assert submit.coarsened([copy.copy], lambda _c: copy.platform) == 1

    result = copy.submit()

    assert result.sent == 1 and result.failures == {} and not result.file_changed
    assert fake.patched == [
        (copy.tx_id('Print'), {'strings': {'other': 'Druk'}, 'reviewed': False})
    ]
    assert copy.unit('Print').get_state_n() == state
    copy.save()
    assert copy.copy.pending() == []
    copy.open()
    assert copy.unit('Print').get_state_n() == state


def test_a_plural_sends_each_category_of_the_language(fake):
    copy = Copy(fake)
    unit = copy.unit('%d file')
    unit.target = multistring(['%d lêertjie', '%d lêertjies'])
    copy.save()

    result = copy.submit()

    assert result.sent == 1 and result.failures == {}
    assert fake.patched[0][1]['strings'] == {'one': '%d lêertjie', 'other': '%d lêertjies'}


def test_an_emptied_unit_sends_null_strings(fake):
    copy = Copy(fake)
    copy.edit('Save', '')
    copy.save()

    copy.submit()

    assert fake.patched == [(copy.tx_id('Save'), {'strings': None})]
    assert copy.server('Save')['strings'] is None


def test_more_than_150_changes_go_150_at_a_time(fake):
    resource = fake.add_resource('hello-org', 'program', 'many', 'Many')
    for n in range(160):
        fake.add_string(resource, 'k%d' % n, 'String %d' % n)
    copy = Copy(fake, 'many')
    for n in range(160):
        copy.edit('String %d' % n, 'String %d af' % n)
    copy.save()

    result = copy.submit()

    assert (result.sent, result.stop) == (160, None)
    assert [len(client_json(r)['data']) for r in _bulk(fake)] == [150, 10]


def client_json(request):
    import json

    return json.loads(request['body'])


def test_a_refused_bulk_is_sent_unit_by_unit_to_find_the_refusal(fake):
    copy = Copy(fake)
    copy.edit('Print', 'Druk')
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.forbid(copy.resource, 'Help')

    result = copy.submit()

    assert result.sent == 1
    assert list(result.failures.values()) == ['You may not change this.']
    assert copy.entry('Help')['error'] == 'You may not change this.'
    assert copy.server('Print')['strings'] == {'other': 'Druk'}
    singles = fake.requests_to('/resource_translations/', 'PATCH')
    assert len(_bulk(fake)) == 1 and len(singles) == 2


def test_reviewing_without_the_right_is_refused_and_reviewed_no_longer_offered(fake):
    fake.may_review = False
    copy = Copy(fake)
    copy.edit('Save', state=StateEnum.FINAL)
    copy.save()

    result = copy.submit()

    assert result.sent == 0 and len(result.failures) == 1
    assert REVIEW in copy.copy.refused() and not copy.copy.may_review()


def _conflict(fake):
    copy = Copy(fake)
    copy.edit('Save', 'Bewaar')
    copy.edit('Print', 'Druk')
    copy.save()
    fake.edit(copy.resource, 'Save', 'Stoor dit')
    return copy


def test_a_string_changed_on_the_server_is_a_conflict(fake):
    copy = _conflict(fake)

    result = copy.submit()

    [conflict] = copy.conflicts
    assert (conflict.source, conflict.mine, conflict.theirs) == ('Save', ['Bewaar'], ['Stoor dit'])
    assert (result.sent, result.skipped) == (1, 1)
    assert copy.server('Save')['strings'] == {'other': 'Stoor dit'}


def test_keep_mine_sends_over_theirs_and_take_theirs_takes_it(fake):
    copy = _conflict(fake)
    keep = {copy.index('Save'): submit.KEEP_MINE}
    assert copy.submit(keep).sent == 2
    assert copy.server('Save')['strings'] == {'other': 'Bewaar'}

    fake.edit(copy.resource, 'Save', 'Stoor nou')
    copy.edit('Save', 'Berg')
    copy.save()
    result = copy.submit({copy.index('Save'): submit.TAKE_THEIRS})
    assert result.taken == 1 and str(copy.unit('Save').target) == 'Stoor nou'


def test_a_review_alone_on_the_server_is_seen_as_a_change(fake):
    # Only the date translated can be filtered on; the full read finds it.
    copy = Copy(fake)
    copy.edit('Save', 'Bewaar')
    copy.save()
    fake.review(copy.resource, 'Save')

    copy.submit()

    [conflict] = copy.conflicts
    assert conflict.theirs == ['Stoor']


def test_a_429_is_waited_out_by_its_text(fake, monkeypatch):
    waits = []
    real = client.Client.schedule

    def schedule(seconds, callback):
        waits.append(seconds)
        real(0.01, callback)

    monkeypatch.setattr(client.Client, 'schedule', staticmethod(schedule))
    copy = Copy(fake)
    copy.edit('Print', 'Druk')
    copy.save()
    fake.throttle_seconds = 7
    fake.rate_limit = 1

    result = copy.submit()

    assert result.sent == 1 and result.stop is None
    assert 6 < max(waits) <= 7


def test_a_long_429_stops_submit_resumable(fake):
    copy = Copy(fake)
    copy.edit('Print', 'Druk')
    copy.save()
    fake.throttle_seconds = 600
    fake.rate_limit = 0

    result = copy.submit()

    assert result.stop == submit.BUDGET and result.left == 1
    fake.rate_limit = None
    assert copy.submit().sent == 1


def test_no_reply_stops_and_the_next_submit_sends_the_rest(fake):
    copy = Copy(fake)
    copy.edit('Print', 'Druk')
    copy.save()
    fake.offline = True

    result = copy.submit()

    assert result.stop == submit.FAILED and result.left == 1
    fake.offline = False
    assert copy.submit().sent == 1


def test_the_platform_paces_by_its_own_limit_as_transifex_sends_no_budget(fake):
    copy = Copy(fake)
    assert copy.session().budget() is None
    assert copy.platform.rate_limit == (500, 60)
    assert copy.platform.batch_size == 150
