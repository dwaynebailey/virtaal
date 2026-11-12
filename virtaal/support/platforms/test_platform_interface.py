#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""What the platform interface offers a platform beside Weblate's
defaults: storing fewer states than Virtaal shows (Platform.states)."""

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
        'Alpha\n\n2 changes are in states Dummy doesn\'t have, so they go as the nearest '
        'state it has; Virtaal keeps yours.'
    ]
