#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os
import sqlite3
from types import SimpleNamespace

import pytest
from translate.filters import checks
from translate.misc.multistring import multistring
from translate.storage import factory

from virtaal.models.storemodel import StoreModel
from virtaal.support import statsdb
from virtaal.support._testhelpers import OtherProcess, scale_lock_waits


def test_load_file_with_an_empty_source_string(tmp_path):
    # translate/virtaal#3249: an empty <source></source> in a .ts file
    # parses with unit.source == None, which used to crash statsdb's
    # own "source VARCHAR NOT NULL" constraint on load.
    ts_file = tmp_path / "empty_source.ts"
    ts_file.write_text("""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS language="da">
<context>
<name>Test</name>
<message>
<source></source>
<translation>foo</translation>
</message>
</context>
</TS>
""")
    model = StoreModel(str(ts_file), None)
    assert len(model) == 1


# wordsinunit() #


def test_wordsinunit_counts_every_plural_form():
    # multistring.strings puts the multistring itself first (which
    # counts as its own primary form) followed by the remaining plural
    # forms, so the primary form is counted once via that first
    # element - not double, just not obviously so from the plain
    # string list alone.
    unit = SimpleNamespace(
        source=multistring(['one word', 'two words here']),
        target=multistring(['single', 'three words total']),
        istranslated=lambda: True,
    )

    sourcewords, targetwords = statsdb.wordsinunit(unit)

    assert sourcewords == 2 + 3  # "one word" + "two words here"
    assert targetwords == 1 + 3  # "single" + "three words total"


# transaction() #


def test_transaction_rolls_back_and_reraises_on_failure():
    rolled_back = []
    committed = []

    class _FakeCache:
        con = SimpleNamespace(
            commit=lambda: committed.append(True), rollback=lambda: rolled_back.append(True)
        )

        @statsdb.transaction
        def boom(self):
            raise ValueError('failed mid-transaction')

    with pytest.raises(ValueError, match='failed mid-transaction'):
        _FakeCache().boom()

    assert rolled_back == [True]
    assert committed == []


# FileTotals / emptyfilechecks() #


def test_file_totals_delitem_removes_the_row(tmp_path):
    cache = statsdb.StatsCache(str(tmp_path / "stats.db"))
    cache.file_totals[1] = statsdb.FileTotals.new_record()

    del cache.file_totals[1]

    assert cache.file_totals[1].to_tuple() == statsdb.FileTotals.new_record().to_tuple()


def test_emptyfilechecks_is_empty():
    assert statsdb.emptyfilechecks() == {}


def test_emptyfiletotals_is_all_zero():
    totals = statsdb.emptyfiletotals()

    assert totals['total'] == 0
    assert totals['totalsourcewords'] == 0


# file_extended_totals() / filetotals(extended=True) #


def test_file_extended_totals_breaks_down_by_state(tmp_path):
    po_content = (
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Hello"\nmsgstr "Bonjour"\n\n'
        'msgid "World"\nmsgstr ""\n'
    )
    po_file = tmp_path / "test.po"
    po_file.write_text(po_content)
    cache = statsdb.StatsCache(str(tmp_path / "stats.db"))
    store = factory.getobject(str(po_file))

    stats = cache.file_extended_totals(str(po_file), store=store)

    assert stats['unreviewed']['units'] == 1
    assert stats['empty']['units'] == 1


def test_filetotals_with_extended_includes_the_breakdown(tmp_path):
    po_file = tmp_path / "test.po"
    po_file.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Hello"\nmsgstr "Bonjour"\n'
    )
    cache = statsdb.StatsCache(str(tmp_path / "stats.db"))
    store = factory.getobject(str(po_file))

    totals = cache.filetotals(str(po_file), store=store, extended=True)

    assert 'extended' in totals
    assert totals['extended']['unreviewed']['units'] == 1


# get_unit_stats() #


def test_get_unit_stats_warns_and_returns_empty_for_an_unknown_unit(tmp_path, caplog):
    import logging

    cache = statsdb.StatsCache(str(tmp_path / "stats.db"))

    with caplog.at_level(logging.WARNING):
        result = cache.get_unit_stats(999, 'no-such-unit')

    assert result == []
    assert any('inconsistent state' in r.message for r in caplog.records)


# file_fails_test() #


def test_file_fails_test_finds_the_dummy_noerror_entry(tmp_path):
    po_file = tmp_path / "test.po"
    po_file.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Hello"\nmsgstr "Bonjour"\n'
    )
    cache = statsdb.StatsCache(str(tmp_path / "stats.db"))
    checker = checks.UnitChecker()

    assert cache.file_fails_test(str(po_file), checker, 'noerror') is True
    assert cache.file_fails_test(str(po_file), checker, 'nonexistent-check') is False


# _getfileid()'s remaining branches #


def test_getfileid_calls_a_callable_store(tmp_path):
    po_file = tmp_path / "test.po"
    po_file.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Hello"\nmsgstr "Bonjour"\n'
    )
    cache = statsdb.StatsCache(str(tmp_path / "stats.db"))
    calls = []

    def make_store():
        calls.append(True)
        return factory.getobject(str(po_file))

    cache._getfileid(str(po_file), store=make_store)

    assert calls == [True]


# close() #


def test_close_closes_every_cached_connection_and_clears_the_registry(tmp_path):
    # Swap out the real, shared class-level cache pool for the
    # duration of this test - close() iterates and closes *every*
    # cached connection process-wide, not just this instance's, and
    # the real pool is shared with the rest of this test session.
    original_caches = statsdb.StatsCache._caches
    statsdb.StatsCache._caches = {}
    try:
        cache = statsdb.StatsCache(str(tmp_path / "stats.db"))

        cache.close()

        with pytest.raises(statsdb.dbapi2.ProgrammingError):
            cache.cur.execute("SELECT 1")
        assert statsdb.StatsCache._caches == {}
    finally:
        statsdb.StatsCache._caches = original_caches


@pytest.fixture
def fresh_caches(monkeypatch):
    # An empty connection registry (call it again for another), with every
    # connection it holds closed afterwards: dropping one leaks it.
    registries = []

    def fresh():
        registries.append({})
        monkeypatch.setattr(statsdb.StatsCache, '_caches', registries[-1])

    monkeypatch.setattr(statsdb.StatsCache, 'unusable_files', set())
    fresh()
    yield fresh
    for caches in registries:
        for threads in caches.values():
            for cache in threads.values():
                cache.con.close()


def test_a_second_connection_to_an_empty_database_keeps_it(tmp_path, fresh_caches):
    # As another process sees a database no file has been cached in yet.
    path = tmp_path / "stats.db"
    statsdb.StatsCache(str(path))
    fresh_caches()
    po_file = tmp_path / "af.po"
    po_file.write_text('msgid "Hello"\nmsgstr "Hallo"\n')

    stats = statsdb.StatsCache(str(path)).filestatestats(str(po_file))

    assert stats['translated'] == [0]
    assert path.exists()


@pytest.mark.parametrize(
    'build',
    [0, statsdb.toolkitversion.build, statsdb.CACHE_BUILD - 1],
    ids=['older toolkit', 'before revisions', 'older revision'],
)
def test_a_database_from_an_older_toolkit_or_virtaal_is_recreated(tmp_path, fresh_caches, build):
    path = tmp_path / "stats.db"
    po_file = tmp_path / "af.po"
    po_file.write_text('msgid "Hello"\nmsgstr "Hallo"\n')
    cache = statsdb.StatsCache(str(path))
    cache.filestatestats(str(po_file))
    cache.cur.execute("UPDATE files SET toolkitbuild=?", (build,))
    cache.con.commit()
    cache.con.close()
    fresh_caches()

    cache = statsdb.StatsCache(str(path))

    cache.cur.execute("SELECT count(*) FROM files")
    assert cache.cur.fetchone()[0] == 0


def _write_truncated_database(path):
    statsdb.StatsCache(str(path)).close()
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 3])


@pytest.mark.parametrize(
    'corrupt',
    [
        lambda path: path.write_text('not a database\n'),
        _write_truncated_database,
    ],
    ids=['not SQLite', 'truncated'],
)
def test_a_corrupt_database_is_replaced(tmp_path, fresh_caches, corrupt, caplog):
    # #4430: it's only a cache, so a corrupt one mustn't stop files opening.
    path = tmp_path / "stats.db"
    corrupt(path)
    fresh_caches()
    po_file = tmp_path / "af.po"
    po_file.write_text('msgid "Hello"\nmsgstr "Hallo"\n')

    stats = statsdb.StatsCache(str(path)).filestatestats(str(po_file))

    assert stats['translated'] == [0]
    assert path.read_bytes().startswith(b'SQLite format 3')
    assert "can't be read" in caplog.text


def test_a_database_path_that_cant_be_opened_falls_back_to_memory(tmp_path, fresh_caches, caplog):
    path = tmp_path / "stats.db"
    path.mkdir()
    (path / "keep").write_text('mine')
    po_file = tmp_path / "af.po"
    po_file.write_text('msgid "Hello"\nmsgstr "Hallo"\n')

    stats = statsdb.StatsCache(str(path)).filestatestats(str(po_file))

    assert stats['translated'] == [0]
    assert (path / "keep").read_text() == 'mine'
    assert [r.getMessage().split(',')[0] for r in caplog.records] == [
        'Keeping statistics in memory'
    ]


def test_an_unusable_database_path_is_only_reported_once(tmp_path, fresh_caches, caplog):
    # As another thread opens it after the first.
    path = tmp_path / "stats.db"
    path.mkdir()
    statsdb.StatsCache(str(path))
    caplog.clear()
    fresh_caches()

    cache = statsdb.StatsCache(str(path))

    cache.cur.execute("SELECT count(*) FROM files")
    assert caplog.records == []


def test_a_failed_open_is_not_kept_for_the_next_call(tmp_path, fresh_caches, monkeypatch):
    path = tmp_path / "stats.db"
    path.write_text('not a database\n')
    real_connect = statsdb.dbapi2.connect
    calls = []

    def connect(name, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            raise statsdb.dbapi2.OperationalError('disk I/O error')
        return real_connect(name, **kwargs)

    monkeypatch.setattr(statsdb.dbapi2, 'connect', connect)

    first = statsdb.StatsCache(str(path))

    assert first.con is not None
    assert statsdb.StatsCache(str(path)) is first


# Several Virtaal processes, and their stats workers, share stats.db (#1340).


def _po_file(tmp_path):
    po_file = tmp_path / "af.po"
    po_file.write_text('msgid "Hello"\nmsgstr "Hallo"\n')
    return str(po_file)


def _database_of_another_process(tmp_path):
    path = str(tmp_path / "stats.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE mine (x)")
    connection.commit()
    connection.close()
    return path


def _tables(path):
    connection = sqlite3.connect(path)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()


def test_a_database_another_process_is_writing_to_is_opened(tmp_path, fresh_caches, monkeypatch):
    scale_lock_waits(monkeypatch, factor=10)
    path = _database_of_another_process(tmp_path)
    with OtherProcess(path, seconds=0.15):
        stats = statsdb.StatsCache(path).filestatestats(_po_file(tmp_path))

    assert stats['translated'] == [0]
    assert {'mine', 'files'} <= _tables(path)
    assert path not in statsdb.StatsCache.unusable_files


def test_a_file_is_cached_while_another_process_writes(tmp_path, fresh_caches, monkeypatch):
    scale_lock_waits(monkeypatch, factor=10)
    path = str(tmp_path / "stats.db")
    cache = statsdb.StatsCache(path)
    with OtherProcess(path, seconds=0.15):
        stats = cache.filestatestats(_po_file(tmp_path))

    assert stats['translated'] == [0]


def test_a_file_is_cached_while_another_process_reads(tmp_path, fresh_caches, monkeypatch):
    scale_lock_waits(monkeypatch)
    path = str(tmp_path / "stats.db")
    cache = statsdb.StatsCache(path)
    with OtherProcess(path, write=False):
        stats = cache.filestatestats(_po_file(tmp_path))

    assert stats['translated'] == [0]


def test_a_database_locked_too_long_is_kept_and_stats_go_to_memory(
    tmp_path, fresh_caches, monkeypatch, caplog
):
    scale_lock_waits(monkeypatch)
    path = _database_of_another_process(tmp_path)
    with OtherProcess(path):
        stats = statsdb.StatsCache(path).filestatestats(_po_file(tmp_path))

    assert stats['translated'] == [0]
    assert _tables(path) == {'mine'}
    assert [r.getMessage().split(',')[0] for r in caplog.records] == [
        'Keeping statistics in memory'
    ]


def test_the_database_uses_a_write_ahead_log(tmp_path, fresh_caches):
    cache = statsdb.StatsCache(str(tmp_path / "stats.db"))

    assert cache.cur.execute("PRAGMA journal_mode").fetchone() == ('wal',)


def test_the_default_database_is_in_the_home_directory(tmp_path, monkeypatch, fresh_caches):
    monkeypatch.setattr(statsdb.StatsCache, 'defaultfile', None)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))

    statsdb.StatsCache()

    assert statsdb.StatsCache.defaultfile.startswith(os.path.realpath(str(tmp_path)))
    assert os.path.exists(statsdb.StatsCache.defaultfile)


def test_the_default_database_in_flatpak_is_under_xdg_cache_home(
    tmp_path, monkeypatch, fresh_caches
):
    from virtaal.common.platform import platform

    monkeypatch.setattr(statsdb.StatsCache, 'defaultfile', None)
    monkeypatch.setattr(platform, 'is_windows', False)
    monkeypatch.setattr(platform, 'is_flatpak', True)
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))

    statsdb.StatsCache()

    assert statsdb.StatsCache.defaultfile == os.path.join(
        os.path.realpath(str(tmp_path)), 'translate_toolkit', 'stats.db'
    )
