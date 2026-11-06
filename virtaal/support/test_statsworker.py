#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import logging
import os
import runpy
import sys
import time

import pytest
from gi.repository import GLib
from translate.filters import checks
from translate.storage import factory

from virtaal.support import statsdb, statsworker

# conftest.py replaces them for every other test.
fill_in_background = statsworker.fill_in_background
check_in_background = statsworker.check_in_background

PO = (
    'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
    'msgid "Hello"\nmsgstr "Hallo"\n\n'
    '#, fuzzy\nmsgid "World"\nmsgstr "Wêreld"\n\n'
    'msgid "Open"\nmsgstr ""\n'
)

XLIFF = '''<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
<file original="doc" source-language="en" target-language="af" datatype="plaintext"><body>
<trans-unit id="1" approved="yes"><source>One</source><target state="final">Een</target></trans-unit>
<trans-unit id="2"><source>Two</source><target state="needs-review-translation">Twee</target></trans-unit>
<trans-unit id="3"><source>Three</source></trans-unit>
<trans-unit id="4" translate="no"><source>Four</source></trans-unit>
<trans-unit id="5"><source>Five</source><target state="translated">Vyf</target></trans-unit>
</body></file></xliff>
'''


def _po(tmp_path, name='af.po'):
    path = tmp_path / name
    path.write_text(PO, encoding="utf-8")
    return str(path)


def _is_cached(path):
    cache = statsdb.StatsCache()
    cache.cur.execute("SELECT path FROM files WHERE path=?", (os.path.realpath(path),))
    return cache.cur.fetchone() is not None


# state_lists() #


def test_state_lists_match_the_stats_cache_for_po(tmp_path):
    path = _po(tmp_path)
    store = factory.getobject(path)

    expected = statsdb.StatsCache(str(tmp_path / 'stats.db')).filestatestats(
        path, store, extended=True
    )

    assert statsworker.state_lists(store) == expected


def test_state_lists_match_the_stats_cache_for_xliff_states(tmp_path):
    # XLIFF has more workflow states than PO, and untranslatable units.
    path = tmp_path / 'af.xlf'
    path.write_text(XLIFF, encoding="utf-8")
    store = factory.getobject(str(path))

    expected = statsdb.StatsCache(str(tmp_path / 'stats.db')).filestatestats(
        str(path), store, extended=True
    )

    assert statsworker.state_lists(store) == expected
    assert len(expected['extended']) > 2


# fill_in_background() / wait() #


def test_fill_in_background_caches_the_file_on_disk(tmp_path):
    path = _po(tmp_path)

    fill_in_background(path)
    statsworker.wait()

    assert _is_cached(path)


def test_fills_run_one_after_the_other(tmp_path):
    first, second = _po(tmp_path, 'one.po'), _po(tmp_path, 'two.po')

    fill_in_background(first)
    fill_in_background(second)

    assert statsworker._queued == [second]
    statsworker.wait()
    assert _is_cached(first) and _is_cached(second)


def test_the_main_loop_starts_the_next_fill(tmp_path):
    first, second = _po(tmp_path, 'one.po'), _po(tmp_path, 'two.po')

    fill_in_background(first)
    fill_in_background(second)
    deadline = time.monotonic() + 30
    while statsworker._running is not None and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)

    assert statsworker._running is None
    assert _is_cached(first) and _is_cached(second)


def test_fill_caches_several_files_and_waits(tmp_path):
    first, second = _po(tmp_path, 'one.po'), _po(tmp_path, 'twee ê.po')

    statsworker.fill([first, str(tmp_path / 'missing.po'), second])

    assert statsworker._running is None
    assert _is_cached(first) and _is_cached(second)


def test_a_failed_fill_is_logged_and_the_next_one_still_runs(tmp_path, caplog):
    path = _po(tmp_path)

    with caplog.at_level(logging.DEBUG):
        fill_in_background(str(tmp_path / 'missing.po'))
        fill_in_background(path)
        statsworker.wait()

    assert 'missing.po' in caplog.text
    assert _is_cached(path)


def test_no_fill_starts_while_the_stats_are_kept_in_memory(tmp_path, monkeypatch):
    # #4430: a child couldn't use the file either, and would warn again.
    monkeypatch.setattr(statsdb.StatsCache, 'unusable_files', {statsdb.StatsCache().defaultfile})
    started = []
    monkeypatch.setattr(
        statsworker.subprocess, 'Popen', lambda *args, **kwargs: started.append(args)
    )

    fill_in_background(_po(tmp_path))

    assert started == []
    assert statsworker._running is None
    assert statsworker._queued == []


def test_wait_returns_at_once_with_nothing_queued():
    statsworker.wait()


def test_poll_keeps_polling_while_the_fill_runs(monkeypatch):
    proc = type('Proc', (), {'poll': lambda self: None})()
    monkeypatch.setattr(statsworker, '_running', proc)

    assert statsworker._poll(proc) is True


def test_poll_stops_for_a_fill_that_is_no_longer_running(monkeypatch):
    monkeypatch.setattr(statsworker, '_running', None)

    assert statsworker._poll(object()) is False


def _run_child(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['statsworker', *args])
    monkeypatch.delitem(sys.modules, 'virtaal.support.statsworker')
    runpy.run_module('virtaal.support.statsworker', run_name='__main__')


def test_the_child_process_caches_the_file_in_the_given_database(tmp_path, monkeypatch):
    path = _po(tmp_path)
    statsfile = tmp_path / 'child.db'

    _run_child(monkeypatch, str(statsfile), path)

    cache = statsdb.StatsCache(str(statsfile))
    cache.cur.execute("SELECT count(*) FROM files")
    assert cache.cur.fetchone()[0] == 1


def test_the_child_process_exits_with_an_error_for_a_missing_file(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as exit_info:
        _run_child(monkeypatch, str(tmp_path / 'child.db'), str(tmp_path / 'missing.po'))

    assert exit_info.value.code == 1


# check_in_background() #


def _checker():
    checker = checks.StandardChecker()
    checker.config.updatetargetlanguage('af')
    return checker


def _wait_for_checks():
    deadline = time.monotonic() + 60
    while statsworker.checking() and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


def test_check_in_background_caches_each_files_failures(tmp_path):
    path = _po(tmp_path)
    (tmp_path / 'b.po').write_text(PO.replace('"Hallo"', '"hallo."'), encoding='utf-8')
    other = str(tmp_path / 'b.po')
    checker = _checker()
    done = []

    assert check_in_background([path, other], checker, done.append)
    _wait_for_checks()

    assert done == [True]
    cache = statsdb.StatsCache()
    assert cache.has_checks(path, checker)
    assert cache.has_checks(other, checker)
    assert cache.filechecks(other, checker)['check-endpunc'] == [1]


def test_has_checks_is_false_until_the_file_is_checked(tmp_path):
    path = _po(tmp_path)
    checker = _checker()
    cache = statsdb.StatsCache()

    assert not cache.has_checks(path, checker)
    cache.filestatestats(path)
    assert not cache.has_checks(path, checker)
    cache.filechecks(path, checker)
    assert cache.has_checks(path, checker)


def test_a_failed_check_reports_it(tmp_path):
    done = []

    check_in_background([str(tmp_path / 'missing.po')], _checker(), done.append)
    _wait_for_checks()

    assert done == [False]


class _Proc:
    def __init__(self, *args, **kwargs):
        self.terminated = False
        _Proc.started.append(self)

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self):
        return 0


@pytest.fixture
def fake_child(monkeypatch):
    _Proc.started = []
    monkeypatch.setattr(statsworker.subprocess, 'Popen', _Proc)
    monkeypatch.setattr(statsworker, '_checking', None)
    yield _Proc.started
    statsworker.stop_checking()


def test_checking_the_same_files_again_keeps_the_child(tmp_path, fake_child):
    path = _po(tmp_path)
    first, second = [], []

    check_in_background([path], _checker(), first.append)
    check_in_background([path], _checker(), second.append)

    assert len(fake_child) == 1
    assert statsworker._checking.done == second.append


def test_checking_other_files_replaces_the_child(tmp_path, fake_child):
    check_in_background([_po(tmp_path, 'one.po')], _checker(), print)
    check_in_background([_po(tmp_path, 'two.po')], _checker(), print)

    assert len(fake_child) == 2
    assert fake_child[0].terminated
    assert statsworker._checking is fake_child[1]


def test_no_child_checks_with_a_checker_it_cant_build(tmp_path, fake_child):
    class Custom(checks.StandardChecker):
        pass

    assert check_in_background([_po(tmp_path)], Custom(), print) is False
    assert fake_child == []


def test_the_child_process_checks_the_files_listed(tmp_path, monkeypatch):
    from virtaal.support import checkerspec

    path = _po(tmp_path)
    listfile = tmp_path / 'files.txt'
    listfile.write_text(path, encoding='utf-8')
    statsfile = str(tmp_path / 'child.db')
    spec = checkerspec.describe(_checker())

    with pytest.raises(SystemExit) as exit_info:
        _run_child(monkeypatch, statsfile, '--checks', json.dumps(spec), str(listfile))

    assert exit_info.value.code == 0
    assert statsdb.StatsCache(statsfile).has_checks(path, _checker())
