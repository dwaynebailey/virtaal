#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from virtaal.support import applespell
from virtaal.support.applespell import WarmUp


def _warm_up():
    spawned, timeouts = [], []
    warm_up = WarmUp(spawn=lambda argv, on_exit: spawned.append((argv, on_exit)),
                     timeout_add=lambda seconds, func: timeouts.append(func))
    return warm_up, spawned, timeouts


def test_warms_a_language_up_once():
    warm_up, spawned, timeouts = _warm_up()
    results = []

    warm_up.request('is', results.append)
    warm_up.request('is', results.append)
    assert len(spawned) == 1 and results == []
    argv, on_exit = spawned[0]
    assert argv[:3] == [applespell.OSASCRIPT, '-l', 'JavaScript'] and argv[-1] == 'is'

    on_exit(True)
    assert results == [True, True] and warm_up.is_ready('is')
    warm_up.request('is', results.append)
    assert results == [True, True, True] and len(spawned) == 1


def test_a_warm_up_that_takes_too_long_fails():
    warm_up, spawned, timeouts = _warm_up()
    results = []
    warm_up.request('nb', results.append)

    timeouts[0]()
    spawned[0][1](True)  # too late

    assert results == [False] and warm_up.has_failed('nb') and not warm_up.is_ready('nb')


def test_a_failed_warm_up():
    warm_up, spawned, timeouts = _warm_up()
    results = []
    warm_up.request('ko', results.append)

    spawned[0][1](False)

    assert results == [False] and warm_up.has_failed('ko')


def _exit_of(code):
    import sys
    import time

    from gi.repository import GLib
    results = []
    applespell._spawn([sys.executable, '-c', 'raise SystemExit(%d)' % code], results.append)
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 30
    while not results and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    return results


def test_spawn_reports_how_the_process_exited():
    assert _exit_of(0) == [True]
    assert _exit_of(3) == [False]


def test_primes_this_process_then_waits_for_its_answers_to_settle():
    primed, spawned, timeouts, results = [], [], [], []
    warm_up = WarmUp(prime=primed.append, spawn=lambda argv, on_exit: spawned.append(on_exit),
                     timeout_add=lambda seconds, func: timeouts.append((seconds, func)))
    warm_up.request('is', results.append)

    spawned[0](True)
    assert primed == ['is'] and results == []
    seconds, settle = timeouts[-1]
    assert seconds == applespell.SETTLE_SECONDS

    settle()
    assert results == [True] and warm_up.is_ready('is')
    timeouts[0][1]()  # the start's own timeout, too late
    assert results == [True]


def test_a_failed_prime():
    def prime(language):
        raise LookupError(language)
    spawned, results = [], []
    warm_up = WarmUp(prime=prime, spawn=lambda argv, on_exit: spawned.append(on_exit),
                     timeout_add=lambda seconds, func: None)
    warm_up.request('is', results.append)

    spawned[0](True)

    assert results == [False] and warm_up.has_failed('is')
