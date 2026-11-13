#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""What the platform interface offers a platform beside Weblate's
defaults: storing fewer states than Virtaal shows (Platform.states),
sending many units at once (Session.submit_units()) and an address for
a new server (Platform.default_url)."""

from types import SimpleNamespace

import pytest
from translate.storage import po
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeplatform import run_until
from virtaal.common import pan_app
from virtaal.support.platforms import RemoteUnit, states, submit, submitdialog, workcopy
from virtaal.support.platforms.test_platforms import DummyPlatform, DummySession

THREE = (StateEnum.EMPTY, StateEnum.UNREVIEWED, StateEnum.FINAL)


class FewStates(DummyPlatform):
    """A dummy platform that stores only untranslated, translated and
    reviewed, as Transifex does."""

    states = THREE

    def __init__(self):
        super().__init__()
        self.held = {
            'a': RemoteUnit('a', 1, '', ['Alpha'], ['Alfa'], StateEnum.FINAL, 'final', '1', False),
            'b': RemoteUnit('b', 2, '', ['Beta'], [''], StateEnum.EMPTY, 'new', '1', False),
        }

    def state_code(self, state, target):
        stored = self.storable(state, target)
        if not any(target):
            return 'new'
        return 'final' if stored == StateEnum.FINAL else 'draft'


class FewStatesSession(DummySession):
    """Stores what it is sent as the server would: a code and the state
    it stands for, with a new last_updated."""

    def submit_unit(self, unit_id, target, code, on_done, on_error):
        held = self.platform.held
        state = {'new': StateEnum.EMPTY, 'draft': StateEnum.UNREVIEWED}.get(code, StateEnum.FINAL)
        held[unit_id] = held[unit_id]._replace(
            target=list(target), code=code, state=state, last_updated='3'
        )
        self.platform.changed.add(unit_id)
        self._later(on_done, code)


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(pan_app, 'ui_language', 'en')


def _copy(platform):
    download = []
    DummySession(platform, {}).download('alpha', None, 'af', download.append, None)
    run_until(lambda: download)
    copy = workcopy.create(platform, 'dummy://one', 'alpha', None, 'af', download[0])
    store = po.pofile.parsefile(copy.path)
    units = [u for u in store.units if not u.isheader()]
    copy.attach(SimpleNamespace(get_units=lambda: units, pair=None))
    return copy, store, units


def _submit(copy, platform, session=None):
    done = []
    submission = submit.Submission(
        copy,
        platform,
        session or FewStatesSession(platform, {}),
        lambda *a: None,
        None,
        done.append,
    )
    submission.start()
    run_until(lambda: done)
    return done[0]


# Platform.states #


@pytest.mark.parametrize(
    ('state', 'target', 'stored'),
    [
        (StateEnum.EMPTY, [''], StateEnum.EMPTY),
        (StateEnum.NEEDS_WORK, ['x'], StateEnum.UNREVIEWED),
        (StateEnum.REJECTED, ['x'], StateEnum.UNREVIEWED),
        (StateEnum.NEEDS_REVIEW, ['x'], StateEnum.UNREVIEWED),
        (StateEnum.UNREVIEWED, ['x'], StateEnum.UNREVIEWED),
        (StateEnum.FINAL, ['x'], StateEnum.FINAL),
        (StateEnum.NEEDS_WORK, [''], StateEnum.EMPTY),
        (None, ['x'], None),
    ],
)
def test_a_state_not_stored_goes_as_the_nearest_never_empty_with_text(state, target, stored):
    assert states.nearest(THREE, state, target) == stored


def test_by_default_a_platform_stores_every_state():
    platform = DummyPlatform()
    assert platform.states is None
    for state in states.PLATFORM_STATES:
        assert platform.storable(state, ['x']) == state


def test_needs_work_is_sent_as_translated_and_kept_here():
    platform = FewStates()
    copy, store, units = _copy(platform)
    units[1].target = 'Beta'
    units[1].set_state_n(StateEnum.NEEDS_WORK)
    store.save()
    copy.saved()

    assert submit.coarsened([copy], lambda _c: platform) == 1
    result = _submit(copy, platform)

    assert (result.sent, result.file_changed) == (1, False)
    assert platform.held['b'].code == 'draft'
    # The server reads back as translated: the finer state stays.
    assert copy.units[1]['virtaal_state'] == StateEnum.NEEDS_WORK
    assert copy.units[1]['last_updated'] == '3'
    assert units[1].get_state_n() == StateEnum.NEEDS_WORK
    copy.saved()
    assert copy.pending() == []


def test_states_every_platform_stores_are_not_counted():
    platform = DummyPlatform()
    copy, store, units = _copy(platform)
    units[1].target = 'Beta'
    store.save()
    copy.saved()
    assert submit.coarsened([copy], lambda _c: platform) == 0


def test_the_submit_question_says_when_a_state_is_coarsened(monkeypatch):
    shown = []

    def answer(dialog):
        shown.append(dialog.props.secondary_text)
        return 0

    monkeypatch.setattr(submitdialog, 'run_dialog', answer)
    dialogs = submitdialog.SubmitDialogs.__new__(submitdialog.SubmitDialogs)
    platform = FewStates()
    copy, _store, _units = _copy(platform)
    dialogs.parent = None
    dialogs.copy = copy
    dialogs.sending = [copy]
    dialogs.session = None
    dialogs.coarsened = 2

    assert not dialogs.confirm(2)
    assert shown == [
        'Alpha\n\n2 changes are in states Dummy doesn’t have, so they go as the nearest '
        'state it has; Virtaal keeps yours.'
    ]


# Session.submit_units #

WORDS = ['One', 'Two', 'Three', 'Four', 'Five']


class Batched(DummyPlatform):
    """A dummy platform that takes three units a request."""

    batch_size = 3

    def __init__(self):
        super().__init__()
        self.held = {
            w.lower(): RemoteUnit(w.lower(), n, '', [w], [''], StateEnum.EMPTY, 'new', '1', False)
            for n, w in enumerate(WORDS, 1)
        }
        self.batches = []
        self.refuse = set()
        self.fail_at = None

    def session(self, url, credentials):
        return BatchedSession(self, credentials)


class BatchedSession(FewStatesSession):
    def download(self, project, component, language, on_done, on_error):
        from virtaal.support.platforms import Download

        data = ''.join('msgid "%s"\nmsgstr ""\n\n' % w for w in WORDS).encode('utf-8')
        units = list(self.platform.held.values())
        self._later(on_done, Download('many.po', data, None, units, {'project': 'Many'}))

    def submit_units(self, units, on_each, on_done, on_error):
        from virtaal.support.platforms import HTTP, PlatformError

        platform = self.platform
        platform.batches.append([unit_id for unit_id, _t, _c in units])
        if platform.fail_at == len(platform.batches):
            self._later(on_error, PlatformError(HTTP, 500))
            return
        for index, (unit_id, target, code) in enumerate(units):
            if unit_id in platform.refuse:
                on_each(index, PlatformError(HTTP, 400, 'Too long.'))
                continue
            held = platform.held
            held[unit_id] = held[unit_id]._replace(target=list(target), code=code)
            on_each(index, code)
        self._later(on_done)


def _many(platform):
    download = []
    platform.session('dummy://one', {}).download('many', None, 'af', download.append, None)
    run_until(lambda: download)
    copy = workcopy.create(platform, 'dummy://one', 'many', None, 'af', download[0])
    store = po.pofile.parsefile(copy.path)
    units = [u for u in store.units if not u.isheader()]
    copy.attach(SimpleNamespace(get_units=lambda: units, pair=None))
    for unit in units:
        unit.target = str(unit.source).upper()
    store.save()
    copy.saved()
    return copy


def test_units_go_a_batch_at_a_time():
    platform = Batched()
    copy = _many(platform)
    progress = []
    done = []
    session = platform.session('', {})
    submission = submit.Submission(
        copy, platform, session, lambda n, t: progress.append((n, t)), None, done.append
    )
    submission.start()
    run_until(lambda: done)

    assert platform.batches == [['one', 'two', 'three'], ['four', 'five']]
    assert progress == [(1, 5), (4, 5)]
    assert (done[0].sent, done[0].stop) == (5, None)
    assert platform.held['four'].target == ['FOUR']
    assert copy.submitted()


def test_a_unit_refused_in_a_batch_is_noted_and_the_rest_go():
    platform = Batched()
    platform.refuse = {'two'}
    copy = _many(platform)

    result = _submit(copy, platform, platform.session('', {}))

    assert result.sent == 4
    assert list(result.failures.values()) == ['Too long.']
    assert copy.units[1]['error'] == 'Too long.'
    assert len(platform.batches) == 2


def test_a_failed_batch_stops_and_leaves_its_units_to_send():
    platform = Batched()
    platform.fail_at = 2
    copy = _many(platform)

    result = _submit(copy, platform, platform.session('', {}))

    assert (result.sent, result.stop) == (3, submit.FAILED)
    assert [copy.units[r]['id'] for r in copy.pending()] == ['four', 'five']
    platform.fail_at = None
    assert _submit(copy, platform, platform.session('', {})).sent == 2
    assert platform.batches[-1] == ['four', 'five']


def test_by_default_submit_units_sends_each_with_submit_unit():
    from virtaal.support.platforms import HTTP, NETWORK, PlatformError

    platform = DummyPlatform()
    sent = []

    class Each(DummySession):
        def submit_unit(self, unit_id, target, code, on_done, on_error):
            sent.append(unit_id)
            if unit_id == 'b':
                self._later(on_error, PlatformError(HTTP, 403))
            elif unit_id == 'c':
                self._later(on_error, PlatformError(NETWORK))
            else:
                self._later(on_done, code)

    outcomes, done, errors = [], [], []
    items = [
        ('a', ['A'], 'draft'),
        ('b', ['B'], 'draft'),
        ('c', ['C'], 'draft'),
        ('d', [''], 'new'),
    ]
    Each(platform, {}).submit_units(
        items, lambda i, o: outcomes.append((i, o)), lambda: done.append(1), errors.append
    )
    run_until(lambda: done or errors)

    assert sent == ['a', 'b', 'c']
    assert outcomes[0] == (0, 'draft') and outcomes[1][1].status == 403
    assert errors[0].kind == NETWORK and done == []


# Platform.default_url #


def test_a_new_server_starts_at_the_platforms_own_address(tmp_path, monkeypatch):
    from virtaal.support.platforms.servers import ServerList
    from virtaal.support.platforms.serversdialog import ServerDialog, ServersDialog

    class OneAddress(DummyPlatform):
        default_url = 'dummy://api'

    asked = []
    monkeypatch.setattr(ServerDialog, 'run', lambda self: asked.append(self.get_values()['url']))
    for platform in (DummyPlatform(), OneAddress()):
        servers = ServerList([platform], str(tmp_path / 'platforms.ini'))
        ServersDialog(servers).btn_add.clicked()

    assert asked == ['', 'dummy://api']
