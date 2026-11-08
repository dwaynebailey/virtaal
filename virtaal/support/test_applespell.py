#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import sys
import time

from gi.repository import GLib

from virtaal.support import applespell
from virtaal.support.applespell import WarmUp


def _warm_up(loaded=lambda language: True):
    spawned, timeouts, polls = [], [], []
    warm_up = WarmUp(loaded=loaded, spawn=lambda argv, on_exit: spawned.append((argv, on_exit)),
                     timeout_add=lambda seconds, func: timeouts.append(func),
                     poll_add=lambda ms, func: polls.append(func))
    return warm_up, spawned, timeouts, polls


def test_warms_a_language_up_once():
    warm_up, spawned, timeouts, polls = _warm_up()
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
    warm_up, spawned, timeouts, polls = _warm_up()
    results = []
    warm_up.request('nb', results.append)

    timeouts[0]()
    spawned[0][1](True)  # too late

    assert results == [False] and warm_up.has_failed('nb') and not warm_up.is_ready('nb')


def test_a_failed_warm_up():
    warm_up, spawned, timeouts, polls = _warm_up()
    results = []
    warm_up.request('ko', results.append)

    spawned[0][1](False)

    assert results == [False] and warm_up.has_failed('ko')


def test_waits_for_this_process_to_take_the_language():
    answers = [False, False, True]
    warm_up, spawned, timeouts, polls = _warm_up(loaded=lambda language: answers.pop(0))
    results = []
    warm_up.request('is', results.append)

    spawned[0][1](True)
    assert results == [] and len(polls) == 1
    assert polls[0]() is True and results == []
    assert polls[0]() is False and results == [True]
    assert warm_up.is_ready('is')


def test_gives_up_if_this_process_never_takes_the_language(monkeypatch):
    monkeypatch.setattr(applespell, 'LOAD_SECONDS', 1)
    monkeypatch.setattr(applespell, 'POLL_MILLISECONDS', 500)
    warm_up, spawned, timeouts, polls = _warm_up(loaded=lambda language: False)
    results = []
    warm_up.request('xx', results.append)

    spawned[0][1](True)
    polls[0]()

    assert results == [False] and warm_up.has_failed('xx')


def test_a_failing_question_counts_as_failed():
    def loaded(language):
        raise OSError(language)
    warm_up, spawned, timeouts, polls = _warm_up(loaded=loaded)
    results = []
    warm_up.request('is', results.append)

    spawned[0][1](True)

    assert results == [False]


def _exit_of(code):
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
