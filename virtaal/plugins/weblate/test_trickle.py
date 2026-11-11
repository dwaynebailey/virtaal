#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Send finished units as you go, on a Weblate work copy against the
fake server: only finished units go, the queue outlasts a restart and
an offline spell and goes when the network is back, conflicts wait for
Submit, and with the option off Save never sends."""

from types import SimpleNamespace

import pytest
from gi.repository import GObject
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


class FakeMonitor(GObject.Object):
    """Gio.NetworkMonitor's network-changed alone."""

    __gsignals__ = {'network-changed': (GObject.SignalFlags.RUN_LAST, None, (bool,))}


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
        self.now = 1000.0
        self.trickler.clock = lambda: self.now
        self.monitor = FakeMonitor()
        self.monitor.connect(
            'network-changed', lambda _monitor, up: self.trickler.network_changed(up)
        )

    def network(self, available):
        self.monitor.emit('network-changed', available)
        self.settle()

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


def test_the_queue_goes_as_soon_as_the_network_is_back(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    help_id = copy.server_unit('Help')['id']
    fake.offline = True
    copy.edit('Help', 'Hulp')
    trickling.save()
    [(_wait, again)] = trickling.waits

    trickling.network(False)
    assert fake.patched == []

    fake.offline = False
    trickling.network(True)

    assert _patched(fake) == [help_id]
    assert copy.copy.record['queue'] == [] and copy.copy.pending() == []
    # The wait it replaced does nothing.
    asked = len(fake.requests)
    again()
    trickling.settle()
    assert len(fake.requests) == asked


def test_a_monitor_saying_available_often_tries_at_most_each_first_wait(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    fake.offline = True
    copy.edit('Help', 'Hulp')
    trickling.save()
    tries = []
    start = trickling.trickler.start
    trickling.trickler.start = lambda c: tries.append(c) or start(c)

    trickling.network(True)
    trickling.network(True)
    trickling.now += trickle.RETRY_FIRST - 1
    trickling.network(True)
    assert len(tries) == 1

    trickling.now += 1
    fake.offline = False
    trickling.network(True)
    assert len(tries) == 2
    assert _patched(fake) == [copy.server_unit('Help')['id']]


def test_the_network_coming_back_sends_nothing_without_a_wait(fake):
    copy = Copy(fake)
    trickling = Trickling(copy)
    copy.edit('Save', 'Bewaar', StateEnum.NEEDS_WORK)
    trickling.save()
    asked = len(fake.requests)

    trickling.network(True)

    assert len(fake.requests) == asked and fake.patched == []
    assert copy.copy.pending() == [copy.index('Save')]


def test_several_files_go_one_after_the_other_and_wait_together():
    from devsupport.testing.fakeweblate import demo_many

    with demo_many() as fake:
        program, installer = Copy(fake), Copy(fake, 'installer')
        trickling = Trickling(program)
        ids = [program.server_unit('Help')['id'], installer.server_unit('Cancel')['id']]
        program.edit('Help', 'Hulp')
        installer.edit('Cancel', 'Kanselleer')
        program.save()
        installer.save()

        # Saved together, as a stream saves: the second waits its turn.
        fake.offline = True
        trickling.trickler.saved(program.copy)
        trickling.trickler.saved(installer.copy)
        assert trickling.trickler._next == [installer.copy]
        run_until(lambda: not trickling.trickler.running and not trickling.trickler._next)

        # One wait for both.
        [(_wait, again)] = trickling.waits
        assert fake.patched == []
        fake.offline = False
        again()
        run_until(lambda: not trickling.trickler.running and not trickling.trickler._next)

        assert sorted(_patched(fake)) == sorted(ids)
        assert [r.sent for r in trickling.sent] == [1, 1]
        assert not program.copy.record['queue'] and not installer.copy.record['queue']
