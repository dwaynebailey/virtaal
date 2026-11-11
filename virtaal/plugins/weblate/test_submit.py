#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Submitting a Weblate work copy's changes to the fake server through
the real curl client: what is sent, states and plurals, the lock,
conflicts, refusals, the rate limit, and resuming a Submit cut short."""

from types import SimpleNamespace

import pytest
from translate.misc.multistring import multistring
from translate.storage import po
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.weblate import backend
from virtaal.support.platforms import NETWORK, submit, workcopy


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))


@pytest.fixture
def fake():
    server = demo().start()
    yield server
    server.stop()
    assert server.schema_problems == []


PLATFORM = backend.WeblatePlatform


class Copy:
    """A downloaded work copy, its file opened and attached as Virtaal
    opens it."""

    def __init__(self, fake, component='program'):
        self.fake = fake
        self.platform = PLATFORM.__new__(PLATFORM)
        download = []
        self.session().download(
            'hello', component, 'af', download.append, lambda e: download.append(e)
        )
        run_until(lambda: download)
        self.copy = workcopy.create(self.platform, fake.url, 'hello', component, 'af', download[0])
        self.open()

    def session(self):
        return backend.WeblateSession(PLATFORM.client_factory(self.fake.url, TOKEN))

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

    def server_unit(self, source):
        units = self.fake.units.values()
        return next(u for u in units if u['source'][0] == source and u['context'] == '')

    def edit(self, source, target=None, state=None):
        unit = self.unit(source)
        if target is not None:
            unit.target = target
        if state is not None:
            unit.set_state_n(state)

    def save(self):
        self.store.save()
        self.copy.saved()

    def submit(self, decisions=None, session=None, on_progress=None, **attrs):
        done = []
        conflicts = []

        def on_conflicts(found, resolve):
            conflicts.extend(found)
            resolve(decisions or {})

        submission = submit.Submission(
            self.copy,
            self.platform,
            session or self.session(),
            on_progress or (lambda *args: None),
            on_conflicts,
            done.append,
        )
        for name, value in attrs.items():
            setattr(submission, name, value)
        if on_progress is not None:
            on_progress.submission = submission
        submission.start()
        run_until(lambda: done, timeout=30)
        self.conflicts = conflicts
        return done[0]

    def entry(self, source):
        return self.copy.units[self.index(source)]


def _patched(fake):
    return [unit_id for unit_id, _data in fake.patched]


def test_only_the_changed_units_are_sent(fake):
    copy = Copy(fake)
    copy.edit('Help', 'Hulp')
    copy.edit('Save', 'Bewaar')
    copy.save()

    result = copy.submit()

    assert (result.total, result.sent, result.stop, result.left) == (2, 2, None, 0)
    help_unit, save = copy.server_unit('Help'), copy.server_unit('Save')
    assert fake.patched == [
        (save['id'], {'target': ['Bewaar'], 'state': 20}),
        (help_unit['id'], {'target': ['Hulp'], 'state': 20}),
    ]
    assert help_unit['target'] == ['Hulp'] and help_unit['state'] == 20
    entry = copy.entry('Help')
    assert entry['changed'] is False
    assert entry['target_hash'] == workcopy.text_hash(['Hulp'])
    assert entry['last_updated'] == help_unit['last_updated']
    assert copy.copy.submitted()
    # The record on disk says the same.
    assert workcopy.for_file(copy.copy.path).record == copy.copy.record


def test_nothing_changed_sends_nothing(fake):
    copy = Copy(fake)

    result = copy.submit()

    assert result.total == 0 and fake.patched == []
    assert fake.requests_to('/api/components/') == []


@pytest.mark.parametrize(
    'source, target, state, code',
    [
        ('Save', 'Stoor', StateEnum.FINAL, 30),
        ('Open', 'Maak oop', StateEnum.UNREVIEWED, 20),
        ('Save', 'Stoor', StateEnum.NEEDS_REVIEW, 12),
        ('Save', 'Stoor', StateEnum.REJECTED, 11),
        ('Save', 'Stoor', StateEnum.NEEDS_WORK, 10),
    ],
)
def test_the_state_goes_back_as_weblates(fake, source, target, state, code):
    copy = Copy(fake)
    copy.edit(source, state=state)
    copy.save()

    copy.submit()

    unit = copy.server_unit(source)
    assert fake.patched == [(unit['id'], {'target': [target], 'state': code})]
    assert unit['state'] == code
    assert copy.unit(source).get_state_n() == state


def test_an_older_weblate_gets_needs_editing_and_the_finer_state_stays(fake):
    fake.old_states = True
    copy = Copy(fake)
    copy.edit('Save', state=StateEnum.REJECTED)
    copy.save()

    result = copy.submit()

    assert result.sent == 1 and result.stop is None
    assert [data['state'] for _id, data in fake.patched] == [10]
    assert copy.unit('Save').get_state_n() == StateEnum.REJECTED
    copy.save()
    assert copy.copy.pending() == []


def test_a_plural_sends_all_its_forms(fake):
    fake.add_component('hello', 'plurals', 'Plurals')
    fake.add_translation('hello', 'plurals', 'af', 'Afrikaans')
    fake.fill_po('hello', 'plurals', 'af', [('', ['File', 'Files'], ['Lêer', 'Lêers'], 20)])
    copy = Copy(fake, 'plurals')
    unit = copy.units[0]
    unit.target = multistring(['%d lêer', '%d lêers'])
    copy.save()

    result = copy.submit()

    assert result.sent == 1 and result.failures == {}
    assert fake.patched[0][1] == {'target': ['%d lêer', '%d lêers'], 'state': 20}
    assert copy.copy.units[0]['target_hash'] == workcopy.text_hash(['%d lêer', '%d lêers'])


def test_a_locked_component_sends_nothing(fake):
    copy = Copy(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.lock('hello', 'program')

    result = copy.submit()

    assert result.stop == submit.LOCKED and result.sent == 0 and result.left == 1
    assert fake.patched == []
    assert copy.copy.pending() == [copy.index('Help')]


def _conflict(fake):
    copy = Copy(fake)
    copy.edit('Save', 'Bewaar')
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.edit(copy.server_unit('Save')['id'], 'Stoor dit')
    return copy


def test_a_unit_changed_on_the_server_is_a_conflict(fake):
    copy = _conflict(fake)

    result = copy.submit()

    (conflict,) = copy.conflicts
    assert (conflict.source, conflict.mine, conflict.theirs) == ('Save', ['Bewaar'], ['Stoor dit'])
    # Skipped by default: the other unit goes, this one waits.
    assert _patched(fake) == [copy.server_unit('Help')['id']]
    assert (result.sent, result.skipped, result.left) == (1, 1, 1)
    assert copy.copy.pending() == [copy.index('Save')]
    assert not copy.copy.submitted()
    assert copy.server_unit('Save')['target'] == ['Stoor dit']


def test_keep_mine_sends_over_theirs(fake):
    copy = _conflict(fake)

    result = copy.submit({copy.index('Save'): submit.KEEP_MINE})

    assert copy.server_unit('Save')['target'] == ['Bewaar']
    assert result.sent == 2 and result.left == 0
    entry = copy.entry('Save')
    assert entry['target_hash'] == workcopy.text_hash(['Bewaar'])
    assert entry['last_updated'] == copy.server_unit('Save')['last_updated']


def test_take_theirs_puts_their_text_in_the_file(fake):
    copy = _conflict(fake)
    before = copy.server_unit('Save')['last_updated']

    result = copy.submit({copy.index('Save'): submit.TAKE_THEIRS})

    assert copy.server_unit('Save')['id'] not in _patched(fake)
    assert str(copy.unit('Save').target) == 'Stoor dit'
    assert result.taken == 1 and result.file_changed and result.left == 0
    entry = copy.entry('Save')
    assert entry['target_hash'] == workcopy.text_hash(['Stoor dit'])
    assert entry['last_updated'] == before and entry['changed'] is False
    copy.save()
    assert copy.copy.pending() == [] and copy.copy.submitted()


def test_a_skipped_conflict_comes_up_again(fake):
    copy = _conflict(fake)
    copy.submit({copy.index('Save'): submit.SKIP})

    copy.submit()

    assert [c.source for c in copy.conflicts] == ['Save']


def test_the_same_text_on_the_server_is_no_conflict(fake):
    copy = Copy(fake)
    copy.edit('Save', 'Bewaar')
    copy.save()
    fake.edit(copy.server_unit('Save')['id'], 'Bewaar')

    result = copy.submit()

    assert copy.conflicts == [] and fake.patched == []
    assert result.sent == 1 and copy.copy.submitted()


def test_a_unit_only_touched_on_the_server_is_sent(fake):
    copy = Copy(fake)
    copy.edit('Save', 'Bewaar')
    copy.save()
    fake.edit(copy.server_unit('Save')['id'], 'Stoor')

    copy.submit()

    assert copy.conflicts == []
    assert copy.server_unit('Save')['target'] == ['Bewaar']


def test_a_refused_unit_is_reported_alone_and_not_tried_again(fake):
    copy = Copy(fake)
    copy.edit('Save', state=StateEnum.FINAL)
    copy.edit('Help', 'Hulp')
    copy.save()
    save = copy.server_unit('Save')['id']
    fake.forbid(save)

    result = copy.submit()

    assert result.sent == 1 and result.stop is None and result.left == 1
    reason = 'You do not have permission to perform this action.'
    assert result.failures == {copy.index('Save'): reason}
    assert copy.entry('Save')['error'] == reason and copy.entry('Save')['changed']
    assert len(fake.requests_to('/api/units/%d/' % save)) == 1
    assert not copy.copy.submitted()


def test_an_enforced_check_shows_the_state_the_server_stored(fake):
    copy = Copy(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.enforce(copy.server_unit('Help')['id'])

    result = copy.submit()

    assert result.file_changed
    assert copy.unit('Help').get_state_n() == StateEnum.NEEDS_WORK
    assert copy.entry('Help')['state'] == 10
    copy.save()
    assert copy.copy.pending() == []


def _edit_four(copy):
    for source, text in (('Save', 'Bewaar'), ('Help', 'Hulp'), ('Print', 'Druk af')):
        copy.edit(source, text)
    copy.edit('Open', state=StateEnum.UNREVIEWED)
    copy.save()


def test_a_429_is_waited_out(fake):
    fake.rate_limit = 2
    fake.rate_window = 1
    copy = Copy(fake)
    _edit_four(copy)

    result = copy.submit(RESERVE=0)

    assert result.sent == 4 and result.stop is None
    assert len(set(_patched(fake))) == 4


def test_a_budget_running_low_stops_and_submit_resumes(fake):
    copy = Copy(fake)
    _edit_four(copy)
    fake.rate_limit = 5
    fake._used.clear()

    result = copy.submit(RESERVE=1)

    # The lock, the changes, then units until 1 request is left.
    assert result.stop == submit.BUDGET and result.sent == 2 and result.left == 2
    assert 3500 < result.reset_in <= 3600
    assert len(copy.copy.pending()) == 2

    fake._used.clear()
    again = copy.submit(RESERVE=1)

    assert again.stop is None and again.sent == 2
    assert sorted(_patched(fake)) == sorted(set(_patched(fake)))
    assert copy.copy.pending() == []


def test_cancel_stops_and_submit_sends_only_the_rest(fake):
    copy = Copy(fake)
    _edit_four(copy)

    def progress(number, total):
        if number == 2:
            progress.submission.cancel()

    result = copy.submit(on_progress=progress)

    assert result.stop == submit.CANCELLED and result.sent == 2
    first = _patched(fake)
    assert len(first) == 2

    again = copy.submit()

    assert again.sent == 2 and again.stop is None
    assert len(fake.patched) == 4 and not set(first) & set(_patched(fake)[2:])
    assert copy.copy.submitted()


def test_a_lost_connection_stops_and_submit_resumes(fake):
    copy = Copy(fake)
    _edit_four(copy)
    fake.fail_after(1, None)

    result = copy.submit()

    assert result.stop == submit.FAILED and result.error.kind == NETWORK
    assert result.sent == 1 and len(copy.copy.pending()) == 3
    fake._fail_after = None

    copy.submit()

    assert len(fake.patched) == 4 and len(set(_patched(fake))) == 4


class _Crashing(backend.WeblateSession):
    """Sends units, but after the first two, the replies never arrive:
    Virtaal is gone."""

    def __init__(self, client, after):
        super().__init__(client)
        self.after = after

    def submit_unit(self, unit_id, target, code, on_done, on_error):
        if self.after <= 0:
            super().submit_unit(unit_id, target, code, lambda _code: None, on_error)
            self.lost = True
            return
        self.after -= 1
        super().submit_unit(unit_id, target, code, on_done, on_error)


def test_a_crash_mid_submit_resumes_with_the_rest(fake):
    copy = Copy(fake)
    _edit_four(copy)
    session = _Crashing(PLATFORM.client_factory(fake.url, TOKEN), 2)
    done = []
    submission = submit.Submission(
        copy.copy, copy.platform, session, lambda *a: None, None, done.append
    )
    submission.start()
    run_until(lambda: len(fake.patched) == 3)
    assert done == []

    # Started again: the record and file from disk.
    copy.open()
    assert len(copy.copy.pending()) == 2
    result = copy.submit()

    # The third reached the server before the crash: it isn't sent again.
    assert result.sent == 2 and result.stop is None
    assert len(fake.patched) == 4 and len(set(_patched(fake))) == 4
    assert copy.copy.submitted()


def test_the_conflict_rule():
    entry = {'target_hash': workcopy.text_hash(['a']), 'state': 20, 'last_updated': 't1'}

    def remote(target, code=20, when='t2'):
        return SimpleNamespace(target=[target], code=code, last_updated=when)

    mine = (workcopy.text_hash(['b']), 20)
    assert submit.classify(entry, mine, None) == submit.SEND
    assert submit.classify(entry, mine, remote('z', when='t1')) == submit.SEND
    assert submit.classify(entry, mine, remote('a')) == submit.SEND
    assert submit.classify(entry, mine, remote('b')) == submit.SAME
    assert submit.classify(entry, mine, remote('b', 30)) == submit.CONFLICT
    assert submit.classify(entry, mine, remote('a', 10)) == submit.CONFLICT
    assert submit.classify(entry, mine, remote('z', when=None)) == submit.CONFLICT


def test_without_the_servers_word_the_pacer_counts_against_the_default():
    now = [0.0]
    pacer = submit.Pacer((5, 3600), reserve=1, clock=lambda: now[0])
    for _n in range(4):
        assert pacer.delay(None) == 0
        pacer.note()
    assert pacer.delay(None) is None
    assert pacer.reset_in(None) == 3600
    now[0] = 3540
    assert pacer.delay(None) == 60
    now[0] = 3600
    assert pacer.delay(None) == 0
    pacer.note()
    assert pacer.delay(None) == 0
