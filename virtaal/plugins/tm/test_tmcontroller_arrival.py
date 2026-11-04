#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""When the TM is queried, by how the unit was reached."""

from types import SimpleNamespace

import pytest
from gi.repository import GLib

from virtaal.plugins.tm.test_tm_startup import MODELS
from virtaal.plugins.tm.test_tmcontroller import (
    _bare_controller,
    _controller_for_start_query,
    _fake_store_controller,
    _store_unit,
)
from virtaal.plugins.tm.tmcontroller import TMController
from virtaal.test.test_scaffolding import TestScaffolding


@pytest.mark.parametrize(
    'ini, timing, expected',
    [
        ('', (500, 83), (583, 150)),  # a held key's first repeat, and one more
        ('', None, (TMController.QUERY_DELAY, 150)),
        ('[suggestions]\nburst_threshold = 700\ntap_grace = 80\n', (500, 83), (700, 80)),
        ('[suggestions]\nburst_threshold = soon\ntap_grace = -5\n', (400, 30), (430, 150)),
    ],
)
def test_load_timing_from_the_desktop_and_tm_ini(monkeypatch, tmp_path, ini, timing, expected):
    monkeypatch.setattr('virtaal.common.pan_app.get_config_dir', lambda: str(tmp_path))
    monkeypatch.setattr('virtaal.support.keyrepeat.repeat_timing', lambda: timing)
    (tmp_path / 'tm.ini').write_text(ini)
    controller = _bare_controller()

    controller._load_timing()

    assert (controller.burst_threshold, controller.tap_grace) == expected


class _Clock:
    def __init__(self, monkeypatch):
        self.now = 100.0
        monkeypatch.setattr('virtaal.plugins.tm.tmcontroller.time.monotonic', lambda: self.now)

    def wait(self, ms):
        self.now += ms / 1000


def _arrival_controller(monkeypatch):
    clock = _Clock(monkeypatch)
    store_controller = SimpleNamespace(arrival=None)
    controller = _bare_controller(
        main_controller=SimpleNamespace(store_controller=store_controller),
        burst_threshold=583,
    )
    controller.view = SimpleNamespace(summoned=False)
    controller.update_suggestions = lambda: None
    unit = _store_unit('x')
    cursor = SimpleNamespace(deref=lambda: unit)

    def arrive(kind, after=1000):
        clock.wait(after)
        store_controller.arrival = kind
        controller._on_cursor_changed(cursor)
        store_controller.arrival = None
        return controller._arrival

    return controller, arrive


def test_arrival_kind_comes_from_the_store_controller(monkeypatch):
    controller, arrive = _arrival_controller(monkeypatch)

    assert [arrive(kind) for kind in ('step', 'commit', 'click', 'jump', 'typing', None)] == [
        'step',
        'commit',
        'click',
        'jump',
        'typing',
        None,
    ]


def test_a_key_move_soon_after_another_without_a_release_is_a_held_key(monkeypatch):
    controller, arrive = _arrival_controller(monkeypatch)

    assert arrive('step') == 'step'
    assert arrive('step', after=500) == 'burst'  # the first repeat
    assert arrive('step', after=83) == 'burst'
    assert arrive('commit', after=83) == 'burst'  # Enter held
    controller._on_release(None, SimpleNamespace(keyval=0))
    assert arrive('step', after=100) == 'step'  # tapped again
    assert arrive('step', after=600) == 'step'  # too late to be a repeat


def test_a_click_between_key_moves_does_not_make_a_burst(monkeypatch):
    controller, arrive = _arrival_controller(monkeypatch)

    arrive('step')
    arrive('click', after=100)

    assert arrive('step', after=100) == 'step'


def _waiting_controller(monkeypatch, kind, rested=True, ready=True):
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmcontroller.GLib.timeout_add',
        lambda delay, cb: scheduled.append(delay) or 'timeout-id',
    )
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmcontroller.GLib.idle_add',
        lambda cb: scheduled.append('idle') or 'idle-id',
    )
    monkeypatch.setattr('virtaal.plugins.tm.tmcontroller.GLib.source_remove', lambda i: None)
    controller = _controller_for_start_query(
        unit=SimpleNamespace(source='x'),
        _rested=rested,
        _arrival=kind,
        _arrival_pending=True,
        burst_threshold=583,
        tap_grace=150,
        _arrived_at=0.0,
    )
    unit_view = SimpleNamespace(connect=lambda *a: 'sig', disconnect=lambda sid: None)
    controller.main_controller = SimpleNamespace(unit_controller=SimpleNamespace(view=unit_view))
    controller._answers_ready = lambda unit: ready
    return controller, scheduled


@pytest.mark.parametrize(
    'kind, before, after',
    [
        ('commit', ['idle'], ['idle']),  # at work: at once
        ('step', [583], [583, 150]),  # once the key is let go
        ('burst', [300], [300, 300]),  # held: once it stops
        ('click', [300], [300, 'idle']),  # once the button is let go
        ('jump', [300], [300, 'idle']),
        ('typing', [300], [300]),  # typing a search: keys going up mean nothing
    ],
)
def test_the_wait_depends_on_how_the_unit_was_reached(monkeypatch, kind, before, after):
    controller, scheduled = _waiting_controller(monkeypatch, kind, ready=False)

    controller.start_query()
    assert scheduled == before
    controller._on_release(None, SimpleNamespace(keyval=0))
    controller._on_release(None, SimpleNamespace(keyval=0))  # only the first counts

    assert scheduled == after


def test_a_second_start_query_for_the_same_arrival_waits_the_same(monkeypatch):
    controller, scheduled = _waiting_controller(monkeypatch, 'step')

    controller.start_query()
    controller.start_query()

    assert scheduled == [583, 583]


def test_after_its_query_an_arrival_no_longer_sets_the_wait(monkeypatch):
    # A language change or another plural form queries again.
    controller, scheduled = _waiting_controller(monkeypatch, 'step')
    controller.view.clear = lambda: None
    controller.emit = lambda *a: None
    controller._prefetch_upcoming = lambda: None

    controller.send_tm_query()
    controller.start_query()

    assert scheduled == ['idle']


def test_a_release_with_no_query_waiting_does_nothing(monkeypatch):
    controller, scheduled = _waiting_controller(monkeypatch, 'step')

    assert controller._on_release(None, SimpleNamespace(keyval=0)) is False
    assert scheduled == []


def test_connect_plugin_listens_for_keys_and_buttons_let_go():
    controller = _bare_controller()
    connected = []
    window = SimpleNamespace(connect=lambda signal, handler: connected.append(signal) or signal)
    controller.main_controller = SimpleNamespace(
        store_controller=_fake_store_controller(),
        mode_controller=None,
        view=SimpleNamespace(main_window=window),
    )

    controller._connect_plugin()

    assert connected == ['key-release-event', 'button-release-event']
    assert controller._release_ids == connected


def test_destroy_stops_listening_for_keys_and_buttons_let_go():
    controller = _bare_controller(_release_ids=['key-id', 'button-id'], _store_loaded_id='id')
    disconnected = []
    controller.view = SimpleNamespace(hide=lambda: None, destroy=lambda: None)
    controller.main_controller = SimpleNamespace(
        store_controller=SimpleNamespace(disconnect=lambda sid: None),
        view=SimpleNamespace(main_window=SimpleNamespace(disconnect=disconnected.append)),
    )
    controller.plugin_controller = SimpleNamespace(shutdown=lambda: None)

    controller.destroy()

    assert disconnected == ['key-id', 'button-id']


class TestArrival(TestScaffolding):
    """On a real main controller with a file open."""

    def setup_class(self):
        TestScaffolding.setup_class(self)
        self.store_controller.open_file(self.testfile[1])

    @pytest.fixture(autouse=True)
    def _environment(self, monkeypatch, tmp_path):
        monkeypatch.setattr('virtaal.common.pan_app.get_config_dir', lambda: str(tmp_path))
        # Pumping the real main loop hangs in this suite.
        monkeypatch.setattr(GLib, 'idle_add', lambda func, *args: func(*args) and None)

    def test_a_move_tells_the_controller_how_the_unit_was_reached(self):
        disabled = [m for m in MODELS if m != 'currentfile']
        controller = TMController(self.main_controller, {'disabled_models': disabled})
        cursor = self.store_controller.cursor
        cursor.pos = 0

        try:
            with self.store_controller.arriving('commit'):
                cursor.move(1)
            assert controller._arrival == 'commit'
            assert self.store_controller.arrival is None
        finally:
            controller.destroy()
