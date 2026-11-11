#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Send finished units as you go, on a Weblate work copy against the
fake server: only finished units go, the queue outlasts a restart and
an offline spell, conflicts wait for Submit, and with the option off
Save never sends."""

from types import SimpleNamespace

import pytest
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.weblate.test_submit import Copy
from virtaal.support.platforms import trickle


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))


@pytest.fixture
def fake():
    server = demo().start()
    yield server
    server.stop()
    assert server.schema_problems == []


class Trickling:
    """A Trickler for one Copy's server, its waits kept to run by hand."""

    def __init__(self, copy, on=True):
        self.copy = copy
        self.sent = []
        self.conflicts = []
        self.waits = []
        self.server = SimpleNamespace(
            send_finished=on, platform=copy.platform, session=copy.session
        )
        self.trickler = trickle.Trickler(
            lambda _copy: self.server, self.sent.append, self.conflicts.append
        )
        self.trickler.schedule = lambda seconds, callback: self.waits.append((seconds, callback))

    def save(self):
        self.copy.save()
        self.trickler.saved(self.copy.copy)
        self.settle()

    def settle(self):
        run_until(lambda: not self.trickler.running, timeout=30)


def _patched(fake):
    return [unit_id for unit_id, _data in fake.patched]


def test_only_finished_units_go_as_they_are_saved(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    copy.edit('Help', 'Hulp')
    copy.edit('Save', 'Bewaar', StateEnum.NEEDS_WORK)

    trickling.save()

    assert _patched(fake) == [copy.server_unit('Help')['id']]
    assert copy.server_unit('Help')['target'] == ['Hulp']
    # The draft waits for Submit, and is the only change left.
    assert copy.copy.pending() == [copy.index('Save')]
    assert copy.copy.record['queue'] == []
    assert [r.sent for r in trickling.sent] == [1]


def test_with_the_option_off_save_never_sends(fake):
    copy = Copy(fake)
    trickling = Trickling(copy, on=False)
    copy.edit('Help', 'Hulp')
    asked = len(fake.requests)

    trickling.save()

    assert len(fake.requests) == asked
    assert fake.patched == [] and 'queue' not in copy.copy.record
    assert copy.copy.pending() == [copy.index('Help')]


def test_the_queue_outlasts_a_restart_and_an_offline_spell(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    help_id = copy.server_unit('Help')['id']
    fake.offline = True
    copy.edit('Help', 'Hulp')

    trickling.save()

    assert fake.patched == [] and copy.copy.record['queue'] == [help_id]
    [(wait, _again)] = trickling.waits
    assert wait == trickle.RETRY_FIRST

    # Virtaal restarts, still offline: the queue is read from the side
    # record, and waits longer each time.
    copy.open()
    trickling = Trickling(copy)
    trickling.trickler.start(copy.copy)
    trickling.settle()
    assert copy.copy.record['queue'] == [help_id]

    # Back online, the wait ends and the queue goes.
    fake.offline = False
    _wait, again = trickling.waits[-1]
    again()
    trickling.settle()

    assert _patched(fake) == [help_id]
    assert copy.copy.record['queue'] == [] and copy.copy.pending() == []
    assert copy.copy.submitted()


def test_waits_grow_until_the_server_answers(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    fake.offline = True
    copy.edit('Help', 'Hulp')
    trickling.save()

    for _n in range(6):
        trickling.waits[-1][1]()
        trickling.settle()

    waits = [wait for wait, _again in trickling.waits]
    assert waits == [30, 60, 120, 240, 480, 600, 600]


def test_a_conflict_is_left_for_submit_and_said(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    help_id = copy.server_unit('Help')['id']
    fake.edit(help_id, 'Help my')
    copy.edit('Help', 'Hulp')
    copy.edit('Open', 'Oop')

    trickling.save()

    assert _patched(fake) == [copy.server_unit('Open')['id']]
    assert copy.server_unit('Help')['target'] == ['Help my']
    assert trickling.conflicts == [1]
    assert copy.entry('Help')['conflict'] and copy.copy.record['queue'] == []
    # Saving again doesn't send it: Submit asks.
    trickling.save()
    assert _patched(fake) == [copy.server_unit('Open')['id']]
    copy.submit()
    assert [c.theirs for c in copy.conflicts] == [['Help my']]


def test_submit_sends_what_the_queue_left(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    copy.edit('Help', 'Hulp')
    copy.edit('Save', 'Bewaar', StateEnum.NEEDS_WORK)
    trickling.save()

    result = copy.submit()

    assert result.sent == 1 and copy.copy.pending() == []
    assert copy.server_unit('Save')['state'] == 10


def test_stopping_drops_the_run_and_keeps_the_queue(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    copy.edit('Help', 'Hulp')
    copy.save()
    trickling.trickler.saved(copy.copy)

    trickling.trickler.stop()

    assert not trickling.trickler.running
    assert copy.copy.record['queue'] == [copy.server_unit('Help')['id']]
