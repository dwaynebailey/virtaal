#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The local TM server's database, against real SQLite files. Failures
come from SQLite itself: an authorizer refuses the statement."""

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from translate.search.lshtein import LevenshteinComparer
from translate.storage import base, pypo, tmx, xliff

from virtaal.support import abstraction, tmdb
from virtaal.support._testhelpers import OtherProcess, scale_lock_waits

LONG = 'Open the selected translation file'  # Over 3 words: full-text search.
SHORT = 'Open it'


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / 'tm.db')
    yield path
    # TMDB shares one connection per file and thread for the whole process.
    for connection, _cursor in tmdb.TMDB._tm_dbs.pop(path, {}).values():
        connection.close()


def _allow(*args):
    # Python 3.10 ignores set_authorizer(None): it keeps the old authorizer.
    return sqlite3.SQLITE_OK


def _refuse(*denied):
    """An authorizer refusing (action, table) pairs; table None means any."""

    def authorize(action, arg1, arg2, db_name, trigger):
        if (action, arg1) in denied or (action, None) in denied:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorize


@pytest.fixture
def refusing_connect(monkeypatch):
    """Make the next connections TMDB opens refuse some statements."""

    def refuse(*denied):
        connect = sqlite3.dbapi2.connect

        def connect_refusing(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connection.set_authorizer(_refuse(*denied))
            return connection

        monkeypatch.setattr(tmdb.dbapi2, 'connect', connect_refusing)

    return refuse


def _count(db, table):
    return db.cursor.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]


def _xliff_unit(source, target, source_lang='en', target_lang='af'):
    store = xliff.xlifffile(sourcelanguage=source_lang, targetlanguage=target_lang)
    unit = store.addsourceunit(source)
    unit.target = target
    return unit


def test_a_database_path_that_isnt_a_string_is_opened(tmp_path):
    path = tmp_path / 'tm.db'
    try:
        db = tmdb.TMDB(path)

        assert db.db_file == str(path)
        assert path.exists()
    finally:
        for connection, _cursor in tmdb.TMDB._tm_dbs.pop(str(path), {}).values():
            connection.close()


def test_a_failed_schema_setup_raises_and_leaves_the_connection_usable(db_path, refusing_connect):
    refusing_connect((sqlite3.SQLITE_CREATE_TABLE, None))

    with pytest.raises(sqlite3.DatabaseError):
        tmdb.TMDB(db_path)

    # Rolled back: the shared connection isn't left in a failed transaction.
    connection, cursor = next(iter(tmdb.TMDB._tm_dbs[db_path].values()))
    connection.set_authorizer(_allow)
    assert cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == []
    db = tmdb.TMDB(db_path)
    db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')
    assert db.translate_unit(SHORT, 'en', 'af')[0]['target'] == 'Maak dit oop'


def test_reopening_a_database_reuses_its_fulltext_index(db_path):
    first = tmdb.TMDB(db_path)
    first.add_dict(
        {'source': LONG, 'target': 'Maak die gekose lêer oop', 'context': ''}, 'en', 'af'
    )

    second = tmdb.TMDB(db_path)

    assert second.fulltext
    assert second.translate_unit(LONG, 'en', 'af')[0]['target'] == 'Maak die gekose lêer oop'
    assert second.preload_db() == 1


class _CursorWithoutFts3(sqlite3.Cursor):
    """What SQLite built without the FTS3 module says to an FTS3 table."""

    def executescript(self, script):
        if 'USING fts3' in script:
            raise sqlite3.OperationalError('no such module: fts3')
        return super().executescript(script)


class _ConnectionWithoutFts3(sqlite3.Connection):
    def cursor(self, factory=_CursorWithoutFts3):
        return super().cursor(factory)


def test_without_fts3_the_database_falls_back_to_plain_search(db_path, monkeypatch, caplog):
    # A database made where FTS3 works, then opened where it doesn't.
    tmdb.TMDB(db_path)
    for connection, _cursor in tmdb.TMDB._tm_dbs.pop(db_path).values():
        connection.close()
    connect = sqlite3.dbapi2.connect
    monkeypatch.setattr(
        tmdb.dbapi2,
        'connect',
        lambda path, **kwargs: connect(path, factory=_ConnectionWithoutFts3, **kwargs),
    )

    with caplog.at_level(logging.ERROR, logger=tmdb.logger.name):
        db = tmdb.TMDB(db_path)

    assert not db.fulltext
    assert 'failed to initialize fts3 support' in caplog.text
    # They would write to the full-text index.
    assert (
        db.cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall() == []
    )
    db.add_dict({'source': LONG, 'target': 'Maak die gekose lêer oop', 'context': ''}, 'en', 'af')
    assert db.translate_unit(LONG, 'en', 'af')[0]['target'] == 'Maak die gekose lêer oop'
    assert db.preload_db() == 1


# Several Virtaal processes, each with its own TM server, share tm.db (#1340).


def test_a_database_another_process_is_writing_to_is_opened(db_path, monkeypatch):
    scale_lock_waits(monkeypatch, factor=10)
    with OtherProcess(db_path, seconds=0.15):
        db = tmdb.TMDB(db_path)

        db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')

    assert _count(db, 'targets') == 1


def test_a_unit_is_added_while_another_process_writes(db_path, monkeypatch):
    scale_lock_waits(monkeypatch, factor=10)
    db = tmdb.TMDB(db_path)
    with OtherProcess(db_path, seconds=0.15):
        db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')

    assert _count(db, 'targets') == 1


def test_a_unit_is_added_while_another_process_reads(db_path, monkeypatch):
    scale_lock_waits(monkeypatch)
    db = tmdb.TMDB(db_path)
    with OtherProcess(db_path, write=False):
        db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')

    assert _count(db, 'targets') == 1


def test_the_fts3_check_leaves_the_shared_file_alone(db_path, monkeypatch):
    # Another process dropping its dummy table made it drop the triggers.
    tables = []

    def record(action, table, _module, db_name, _trigger):
        if action == sqlite3.SQLITE_CREATE_VTABLE:
            tables.append((db_name, table))
        return sqlite3.SQLITE_OK

    connect = sqlite3.dbapi2.connect

    def connect_recording(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(record)
        return connection

    monkeypatch.setattr(tmdb.dbapi2, 'connect', connect_recording)

    tmdb.TMDB(db_path)

    assert [db for db, table in tables if table == 'test_for_fts3'] == ['temp']


def test_a_database_locked_too_long_keeps_its_fulltext_triggers(db_path, monkeypatch):
    tmdb.TMDB(db_path)
    for connection, _cursor in tmdb.TMDB._tm_dbs.pop(db_path).values():
        connection.close()
    scale_lock_waits(monkeypatch)
    with OtherProcess(db_path):
        db = tmdb.TMDB(db_path)

    assert not db.fulltext
    triggers = "SELECT count(*) FROM sqlite_master WHERE type = 'trigger'"
    assert db.cursor.execute(triggers).fetchone() == (3,)


def test_the_database_uses_a_write_ahead_log(db_path):
    db = tmdb.TMDB(db_path)

    assert db.cursor.execute('PRAGMA journal_mode').fetchone() == ('wal',)


class _ConnectionWithoutWal(sqlite3.Connection):
    """Where SQLite can't use WAL it keeps the journal it has."""

    def execute(self, sql, *args):
        if sql.replace(' ', '').upper() == 'PRAGMAJOURNAL_MODE=WAL':
            return super().execute('PRAGMA journal_mode')
        return super().execute(sql, *args)


class _ConnectionFailingWal(sqlite3.Connection):
    def execute(self, sql, *args):
        if sql.replace(' ', '').upper() == 'PRAGMAJOURNAL_MODE=WAL':
            raise sqlite3.OperationalError('disk I/O error')
        return super().execute(sql, *args)


@pytest.mark.parametrize('factory', [_ConnectionWithoutWal, _ConnectionFailingWal])
def test_without_wal_the_database_keeps_its_journal(db_path, monkeypatch, factory):
    connect = sqlite3.dbapi2.connect
    monkeypatch.setattr(
        tmdb.dbapi2, 'connect', lambda path, **kwargs: connect(path, factory=factory, **kwargs)
    )
    db = tmdb.TMDB(db_path)

    db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')

    assert db.cursor.execute('PRAGMA journal_mode').fetchone() == ('delete',)
    assert _count(db, 'targets') == 1


def test_add_unit_takes_the_languages_from_the_units_file(db_path):
    db = tmdb.TMDB(db_path)

    db.add_unit(_xliff_unit(SHORT, 'Maak dit oop', 'en', 'af'))

    assert db.translate_unit(SHORT, 'en', 'af')[0]['target'] == 'Maak dit oop'


def test_add_unit_prefers_the_units_languages_to_the_passed_ones(db_path):
    db = tmdb.TMDB(db_path)

    db.add_unit(_xliff_unit(SHORT, 'Maak dit oop', 'en', 'af'), 'de', 'fr')

    assert db.translate_unit(SHORT, 'en', 'af')[0]['target'] == 'Maak dit oop'
    assert db.translate_unit(SHORT, 'de', 'fr') == []


@pytest.mark.parametrize(
    ('source_lang', 'target_lang', 'message'),
    [
        (None, 'af', 'undefined source language'),
        ('en', None, 'undefined target language'),
    ],
)
def test_add_unit_needs_both_languages(db_path, source_lang, target_lang, message):
    # A unit without a file has no languages of its own.
    unit = base.TranslationUnit(SHORT)
    unit.target = 'Maak dit oop'
    db = tmdb.TMDB(db_path)

    with pytest.raises(tmdb.LanguageError) as excinfo:
        db.add_unit(unit, source_lang, target_lang)

    assert str(excinfo.value) == message
    assert _count(db, 'sources') == 0


def test_a_failed_insert_rolls_back_the_whole_unit(db_path):
    db = tmdb.TMDB(db_path)
    db.connection.set_authorizer(_refuse((sqlite3.SQLITE_INSERT, 'targets')))
    try:
        with pytest.raises(sqlite3.DatabaseError):
            db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')
    finally:
        db.connection.set_authorizer(_allow)

    assert _count(db, 'sources') == 0


def test_a_failed_insert_without_commit_leaves_rolling_back_to_the_caller(db_path):
    db = tmdb.TMDB(db_path)
    db.connection.set_authorizer(_refuse((sqlite3.SQLITE_INSERT, 'targets')))
    try:
        with pytest.raises(sqlite3.DatabaseError):
            db.add_dict(
                {'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af', commit=False
            )
    finally:
        db.connection.set_authorizer(_allow)

    # The source row is still in the open transaction, for the caller to undo.
    assert _count(db, 'sources') == 1
    db.connection.rollback()
    assert _count(db, 'sources') == 0


@pytest.mark.parametrize('source', [SHORT, LONG])
def test_translate_unit_matches_any_of_several_languages(db_path, source):
    db = tmdb.TMDB(db_path)
    db.add_dict({'source': source, 'target': 'Afrikaans', 'context': ''}, 'en_GB', 'af_ZA')

    matches = db.translate_unit(source, ['de', 'EN_gb'], ['zu', 'af-ZA'])

    assert [match['target'] for match in matches] == ['Afrikaans']
    assert db.translate_unit(source, ['de', 'fr'], ['af_ZA']) == []


# The schema version and the migration of a file an older Virtaal made.

TESTFILES = Path(__file__).resolve().parents[2] / 'devsupport' / 'testfiles'


@pytest.fixture
def old_tmdb():
    """Virtaal 1.0's tmdb module, which knows no schema version."""
    path = TESTFILES / 'tmdb-1.0.py.txt'
    module = types.ModuleType('tmdb_1_0')
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), module.__dict__)
    yield module
    for connections in module.TMDB._tm_dbs.values():
        for connection, _cursor in connections.values():
            connection.close()


def _before_abstraction_2(monkeypatch):
    """Stop the migrations at version 8, the last to keep abstracts of
    abstraction 1."""
    monkeypatch.setattr(tmdb, 'SCHEMA_VERSION', 8)
    monkeypatch.delitem(tmdb.MIGRATIONS, 9)


def test_a_v8_file_recomputes_its_abstracts_for_abstraction_2(db_path, monkeypatch):
    with monkeypatch.context() as patch:
        _before_abstraction_2(patch)
        db = tmdb.TMDB(db_path)
        db.add_dict({'source': '{n} files', 'target': '{n} lêers', 'context': ''}, 'en', 'af')
        _close(db_path)

    db = tmdb.TMDB(db_path)

    assert _abstracts(db, 'sources') == [('{n} files', None)]
    assert db.backfill_abstracts() == 2
    assert _abstracts(db, 'sources') == [('{n} files', '\ue001 files')]


def _close(db_path):
    for connection, _cursor in tmdb.TMDB._tm_dbs.pop(db_path, {}).values():
        connection.close()


def _v0_file(old_tmdb, db_path, *pairs):
    old = old_tmdb.TMDB(db_path)
    for source, target in pairs:
        old.add_dict({'source': source, 'target': target, 'context': ''}, 'en', 'af')
    return old


def _columns(db, table):
    return [row[1] for row in db.cursor.execute('PRAGMA table_info(%s)' % table)]


def _origins(db):
    return db.cursor.execute(
        'SELECT store_id, lang, unit_key, tid FROM origins ORDER BY tid'
    ).fetchall()


def test_a_new_file_gets_the_current_schema(db_path):
    db = tmdb.TMDB(db_path)

    assert db._user_version() == tmdb.SCHEMA_VERSION
    assert db._has_table('stores')
    assert db._has_table('origins')
    assert 'abstract' in _columns(db, 'sources')
    assert 'abstract' in _columns(db, 'targets')
    assert _count(db, 'stores') == 0
    assert not os.path.exists(db_path[: -len('.db')] + '-v0.db')


def test_a_file_an_older_virtaal_made_is_migrated(db_path, old_tmdb):
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'), (LONG, 'Maak die gekose lêer oop'))

    db = tmdb.TMDB(db_path)

    assert db._user_version() == tmdb.SCHEMA_VERSION
    tids = [tid for (tid,) in db.cursor.execute('SELECT tid FROM targets ORDER BY tid')]
    assert _origins(db) == [(0, 'af', str(tid), tid) for tid in tids]
    assert db.cursor.execute('SELECT store_id, path, kind FROM stores').fetchall() == [
        (0, '', tmdb.STORE_LEGACY)
    ]
    assert db.cursor.execute('SELECT abstract FROM targets').fetchall() == [(None,), (None,)]
    assert db.translate_unit(LONG, 'en', 'af')[0]['target'] == 'Maak die gekose lêer oop'


def test_the_migration_keeps_a_copy_of_the_older_file(db_path, old_tmdb):
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'))

    tmdb.TMDB(db_path)

    backup = sqlite3.connect(db_path[: -len('.db')] + '-v0.db')
    try:
        assert backup.execute('PRAGMA user_version').fetchone() == (0,)
        assert backup.execute('SELECT text FROM targets').fetchall() == [('Maak dit oop',)]
        tables = backup.execute("SELECT name FROM sqlite_master WHERE name = 'stores'")
        assert tables.fetchall() == []
    finally:
        backup.close()


def test_a_backup_that_fails_does_not_stop_the_migration(db_path, old_tmdb, caplog):
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'))
    os.mkdir(db_path[: -len('.db')] + '-v0.db.%d.tmp' % os.getpid())

    with caplog.at_level(logging.WARNING, logger=tmdb.logger.name):
        db = tmdb.TMDB(db_path)

    assert 'Could not back up' in caplog.text
    assert db._user_version() == tmdb.SCHEMA_VERSION
    assert not os.path.exists(db_path[: -len('.db')] + '-v0.db')


def test_reopening_a_migrated_file_changes_nothing(db_path, old_tmdb):
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'))
    tmdb.TMDB(db_path)
    os.unlink(db_path[: -len('.db')] + '-v0.db')
    _close(db_path)

    db = tmdb.TMDB(db_path)

    assert len(_origins(db)) == 1
    assert _count(db, 'stores') == 1
    # The copy is of the older file only.
    assert not os.path.exists(db_path[: -len('.db')] + '-v0.db')


def test_a_failed_migration_leaves_the_older_file_as_it_was(db_path, old_tmdb, refusing_connect):
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'))
    refusing_connect((sqlite3.SQLITE_ALTER_TABLE, None))

    with pytest.raises(sqlite3.DatabaseError):
        tmdb.TMDB(db_path)

    connection, cursor = next(iter(tmdb.TMDB._tm_dbs[db_path].values()))
    connection.set_authorizer(_allow)
    assert cursor.execute('PRAGMA user_version').fetchone() == (0,)
    assert cursor.execute("SELECT name FROM sqlite_master WHERE name = 'stores'").fetchall() == []
    assert tmdb.TMDB(db_path)._user_version() == tmdb.SCHEMA_VERSION


def test_a_file_another_process_migrated_meanwhile_is_left_alone(db_path, old_tmdb, monkeypatch):
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'))
    tmdb.TMDB(db_path)
    _close(db_path)
    # Read as 0 before this process waited for the other's write lock.
    stale = [0]
    user_version = tmdb.TMDB._user_version
    monkeypatch.setattr(
        tmdb.TMDB, '_user_version', lambda self: stale.pop() if stale else user_version(self)
    )

    db = tmdb.TMDB(db_path)

    assert len(_origins(db)) == 1
    assert _columns(db, 'targets').count('abstract') == 1


_OPEN = """
import sys, time
from virtaal.support import abstraction, tmdb
time.sleep(max(0, float(sys.argv[2]) - time.time()))
tmdb.TMDB(sys.argv[1])
"""


# Two fresh interpreters importing translate-toolkit can take most of
# the default 30 s on a slow Windows runner.
@pytest.mark.timeout(150)
def test_two_processes_migrate_the_same_file_at_once(db_path, old_tmdb):
    _v0_file(old_tmdb, db_path, *((f'Unit {n}', f'Eenheid {n}') for n in range(200)))
    root = Path(__file__).resolve().parents[2]
    env = dict(
        os.environ, PYTHONPATH=os.pathsep.join([str(root), os.environ.get('PYTHONPATH', '')])
    )
    start = str(time.time() + 2)

    processes = [
        subprocess.Popen([sys.executable, '-c', _OPEN, db_path, start], cwd=root, env=env)
        for _ in range(2)
    ]

    assert [process.wait(timeout=60) for process in processes] == [0, 0]
    db = tmdb.TMDB(db_path)
    assert db._user_version() == tmdb.SCHEMA_VERSION
    assert _count(db, 'stores') == 1
    assert len(_origins(db)) == 200


def test_targets_an_older_virtaal_adds_go_to_the_legacy_store(db_path, old_tmdb):
    tmdb.TMDB(db_path)
    _close(db_path)
    old = old_tmdb.TMDB(db_path)

    old.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')
    assert old.translate_unit(SHORT, 'en', 'af')[0]['target'] == 'Maak dit oop'

    db = tmdb.TMDB(db_path)
    (tid,) = db.cursor.execute('SELECT tid FROM targets').fetchone()
    assert _origins(db) == [(0, 'af', str(tid), tid)]


def test_unattributed_targets_wait_while_another_process_writes(db_path, old_tmdb, monkeypatch):
    tmdb.TMDB(db_path)
    _close(db_path)
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'))
    scale_lock_waits(monkeypatch)

    with OtherProcess(db_path):
        db = tmdb.TMDB(db_path)

    assert _origins(db) == []
    assert db.translate_unit(SHORT, 'en', 'af')[0]['target'] == 'Maak dit oop'


def test_a_unit_added_without_a_file_goes_to_the_legacy_store(db_path):
    db = tmdb.TMDB(db_path)

    db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')
    db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')

    (tid,) = db.cursor.execute('SELECT tid FROM targets').fetchone()
    assert _origins(db) == [(0, 'af', str(tid), tid)]
    assert db.cursor.execute('SELECT kind FROM stores').fetchall() == [(tmdb.STORE_LEGACY,)]


# Stores: syncing a saved or imported file, removing it, its stats and export.

PO = '/home/user/project/af.po'
OTHER = '/home/user/other/af.po'


def _unit(source, target, key=None, context=''):
    unit = {'source': source, 'target': target, 'context': context}
    if key:
        unit['key'] = key
    return unit


def _targets(db):
    return sorted(text for (text,) in db.cursor.execute('SELECT text FROM targets'))


def _suggested(db, source):
    return [match['target'] for match in db.translate_unit(source, 'en', 'af')]


def test_a_sync_adds_each_unit_under_its_key(db_path):
    db = tmdb.TMDB(db_path)

    counts = db.sync_store(
        PO, [_unit(SHORT, 'Maak dit oop', 'k1'), _unit(LONG, 'Maak oop', 'k2')], 'en', 'af'
    )

    assert counts == {'added': 2, 'replaced': 0, 'removed': 0}
    keys = db.cursor.execute('SELECT unit_key FROM origins ORDER BY unit_key').fetchall()
    assert keys == [('k1',), ('k2',)]
    assert db.store_stats(PO, 'en', 'af')['units'] == 2


def test_a_resync_replaces_a_changed_target(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')

    counts = db.sync_store(PO, [_unit(SHORT, 'Open dit', 'k1')], 'en', 'af')

    assert counts == {'added': 0, 'replaced': 1, 'removed': 0}
    assert _suggested(db, SHORT) == ['Open dit']
    assert _targets(db) == ['Open dit']


def test_a_resync_removes_units_no_longer_translated(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(
        PO, [_unit(SHORT, 'Maak dit oop', 'k1'), _unit(LONG, 'Maak oop', 'k2')], 'en', 'af'
    )

    counts = db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')

    assert counts == {'added': 0, 'replaced': 0, 'removed': 1}
    assert _suggested(db, LONG) == []
    assert _count(db, 'sources') == 1
    assert _count(db, 'fulltext') == 1


def test_a_resync_keeps_a_target_another_store_holds(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')
    db.sync_store(OTHER, [_unit(SHORT, 'Maak dit oop', 'other')], 'en', 'af')

    db.sync_store(PO, [], 'en', 'af')

    assert _suggested(db, SHORT) == ['Maak dit oop']
    assert db.store_stats(OTHER, 'en', 'af')['units'] == 1


def test_a_resync_keeps_a_target_added_before_files_were_recorded(db_path):
    db = tmdb.TMDB(db_path)
    db.add_dict(_unit(SHORT, 'Maak dit oop'), 'en', 'af')
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')

    db.sync_store(PO, [_unit(SHORT, 'Open dit', 'k1')], 'en', 'af')

    assert sorted(_suggested(db, SHORT)) == ['Maak dit oop', 'Open dit']


def test_a_sync_without_replace_keeps_old_targets_in_the_legacy_store(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')

    db.sync_store(PO, [_unit(SHORT, 'Open dit', 'k1')], 'en', 'af', replace=False)

    assert sorted(_suggested(db, SHORT)) == ['Maak dit oop', 'Open dit']
    assert db.store_stats(PO, 'en', 'af')['units'] == 1
    assert db.store_stats('', 'en', 'af')['units'] == 1


def test_a_unit_without_a_key_is_keyed_by_its_context_and_source(db_path):
    db = tmdb.TMDB(db_path)

    db.sync_store(
        PO, [_unit(SHORT, 'Maak dit oop'), _unit(SHORT, 'Oop', context='menu')], 'en', 'af'
    )

    keys = db.cursor.execute('SELECT unit_key FROM origins ORDER BY unit_key').fetchall()
    assert keys == [(SHORT,), ('menu\x04' + SHORT,)]


def test_a_sync_needs_a_path(db_path):
    db = tmdb.TMDB(db_path)

    with pytest.raises(ValueError):
        db.sync_store('', [_unit(SHORT, 'Maak dit oop')], 'en', 'af')
    with pytest.raises(ValueError):
        db.import_store(pypo.pofile(), '', 'en', 'af')
    assert _count(db, 'stores') == 0


def test_a_failed_sync_changes_nothing(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')
    db.connection.set_authorizer(_refuse((sqlite3.SQLITE_DELETE, 'targets')))
    try:
        with pytest.raises(sqlite3.DatabaseError):
            db.sync_store(PO, [_unit(SHORT, 'Open dit', 'k1')], 'en', 'af')
    finally:
        db.connection.set_authorizer(_allow)

    assert _targets(db) == ['Maak dit oop']
    assert db.cursor.execute('SELECT unit_key FROM origins').fetchall() == [('k1',)]


def test_a_bundle_member_is_its_own_store(db_path):
    db = tmdb.TMDB(db_path)

    db.sync_store('/tmp/b.zip', [_unit(SHORT, 'Maak dit oop')], 'en', 'af', member='a.xlf')
    db.sync_store('/tmp/b.zip', [_unit(LONG, 'Maak oop')], 'en', 'af', member='b.xlf')

    assert db.store_stats('/tmp/b.zip', 'en', 'af', member='a.xlf')['units'] == 1
    assert db.store_stats('/tmp/b.zip', 'en', 'af', member='b.xlf')['units'] == 1
    assert db.store_stats('/tmp/b.zip', 'en', 'af') is None


def test_forgetting_a_store_keeps_targets_another_store_holds(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(
        PO, [_unit(SHORT, 'Maak dit oop', 'k1'), _unit(LONG, 'Maak oop', 'k2')], 'en', 'af'
    )
    db.sync_store(OTHER, [_unit(SHORT, 'Maak dit oop', 'other')], 'en', 'af')

    assert db.forget_store(PO, 'en', 'af') == 2

    assert _suggested(db, SHORT) == ['Maak dit oop']
    assert _suggested(db, LONG) == []
    assert db.store_stats(PO, 'en', 'af') is None
    assert db.forget_store(PO, 'en', 'af') is None


def test_forgetting_a_store_for_one_pair_keeps_its_other_pair(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')
    db.sync_store(PO, [_unit(SHORT, 'Vula', 'k1')], 'en', 'zu')

    db.forget_store(PO, 'en', 'af')

    assert db.store_stats(PO, 'en', 'zu')['units'] == 1
    assert db.translate_unit(SHORT, 'en', 'zu')[0]['target'] == 'Vula'


def test_forgetting_the_legacy_store(db_path, old_tmdb):
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'))
    db = tmdb.TMDB(db_path)

    assert db.forget_store('', 'en', 'af') == 1

    assert _count(db, 'targets') == 0
    assert _count(db, 'stores') == 0


def test_excluding_a_store_removes_what_it_added_and_stops_its_syncs(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')

    db.set_exclude(PO, True)

    assert _suggested(db, SHORT) == []
    assert db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af') is None
    assert _count(db, 'targets') == 0
    stats = db.store_stats(PO, 'en', 'af')
    assert stats['exclude'] is True
    assert stats['units'] == 0
    # Forgetting it keeps the setting.
    db.forget_store(PO, 'en', 'af')
    assert db.store_stats(PO, 'en', 'af')['exclude'] is True


def test_a_store_let_in_again_syncs(db_path):
    db = tmdb.TMDB(db_path)
    db.set_exclude(PO, True)

    db.set_exclude(PO, False)

    assert db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')['added'] == 1


def test_list_stores_gives_each_pairs_units_and_whether_the_file_is_there(db_path, tmp_path):
    there = tmp_path / 'af.po'
    there.write_text('')
    db = tmdb.TMDB(db_path)
    db.add_dict(_unit(LONG, 'Maak oop'), 'en', 'af')
    db.sync_store(str(there), [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')
    db.sync_store(str(there), [_unit(SHORT, 'Vula', 'k1')], 'en', 'zu')
    db.sync_store(OTHER, [], 'en', 'af', kind=tmdb.STORE_IMPORTED)

    stores = db.list_stores()

    assert [(s['path'], s['kind'], s['exists']) for s in stores] == [
        ('', tmdb.STORE_LEGACY, None),
        (str(there), tmdb.STORE_SAVED, True),
        (OTHER, tmdb.STORE_IMPORTED, False),
    ]
    assert [(p['target_lang'], p['units']) for p in stores[1]['pairs']] == [('af', 1), ('zu', 1)]
    assert stores[2]['pairs'] == []


def test_export_gives_the_pairs_entries_as_tmx(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')
    db.sync_store(OTHER, [_unit(LONG, 'Maak oop', context='menu')], 'en', 'af')
    db.sync_store(PO, [_unit(SHORT, 'Vula', 'k1')], 'en', 'zu')

    exported = tmx.tmxfile(db.export_tmx('en', 'af'))

    assert [(u.source, u.target, u.getcontext()) for u in exported.units] == [
        (SHORT, 'Maak dit oop', ''),
        (LONG, 'Maak oop', 'menu'),
    ]


def test_export_of_one_store(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en_US', 'af')
    db.sync_store(OTHER, [_unit(LONG, 'Maak oop')], 'en_US', 'af')

    exported = db.export_tmx('en_US', 'af', path=OTHER)

    assert [u.target for u in tmx.tmxfile(exported).units] == ['Maak oop']
    assert b'xml:lang="en-US"' in exported
    assert db.export_tmx('en_US', 'af', path='/nowhere.po') is None


def test_forget_unit_removes_the_target_from_every_store(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(
        PO, [_unit(SHORT, 'Maak dit oop', 'k1'), _unit(SHORT, 'Oop', context='x')], 'en', 'af'
    )
    db.sync_store(OTHER, [_unit(SHORT, 'Maak dit oop', 'other')], 'en', 'af')

    assert db.forget_unit(SHORT, 'Maak dit oop', 'en', 'af', context='') == 1

    assert _suggested(db, SHORT) == ['Oop']
    assert db.store_stats(OTHER, 'en', 'af')['units'] == 0


def test_import_store_syncs_a_files_translated_units(db_path, tmp_path):
    po = pypo.pofile.parsestring(
        b'msgid "Open it"\nmsgstr "Maak dit oop"\n\n'
        b'#, fuzzy\nmsgid "Close it"\nmsgstr "Maak dit toe"\n\n'
        b'msgctxt "menu"\nmsgid "Quit"\nmsgstr "Verlaat"\n'
    )
    db = tmdb.TMDB(db_path)

    counts = db.import_store(po, PO, 'en', 'af')

    assert counts == {'added': 2, 'replaced': 0, 'removed': 0}
    keys = db.cursor.execute('SELECT unit_key FROM origins ORDER BY unit_key').fetchall()
    assert keys == [('Open it',), ('menu\x04Quit',)]
    assert db.store_stats(PO, 'en', 'af')['kind'] == tmdb.STORE_IMPORTED


def test_import_store_files_units_under_their_own_languages(db_path):
    store = xliff.xlifffile(sourcelanguage='en', targetlanguage='zu')
    store.addsourceunit(SHORT).target = 'Vula'

    db = tmdb.TMDB(db_path)
    db.import_store(store, PO, 'en', 'af')

    assert db.translate_unit(SHORT, 'en', 'zu')[0]['target'] == 'Vula'
    assert db.store_stats(PO, 'en', 'af')['units'] == 0


TMX_HEADER = (
    b'<tmx version="1.4"><header srclang="en" adminlang="en" datatype="plaintext"'
    b' segtype="sentence" creationtool="test" creationtoolversion="1" o-tmf="test"/><body>'
)


def _tmx(*tus):
    return tmx.tmxfile.parsestring(TMX_HEADER + b''.join(tus) + b'</body></tmx>')


def _tuv(lang, text):
    return b'<tuv xml:lang="%s"><seg>%s</seg></tuv>' % (lang, text)


def test_import_store_files_a_tmx_under_its_tuvs_languages(db_path):
    store = _tmx(b'<tu>' + _tuv(b'en', SHORT.encode()) + _tuv(b'de', b'Offne es') + b'</tu>')

    db = tmdb.TMDB(db_path)
    counts = db.import_store(store, '/home/user/memory.tmx', 'en', 'af')

    assert counts['added'] == 1
    assert db.translate_unit(SHORT, 'en', 'de')[0]['target'] == 'Offne es'
    assert db.translate_unit(SHORT, 'en', 'af') == []


def test_import_store_takes_each_language_of_a_multilingual_tmx(db_path):
    store = _tmx(
        b'<tu>'
        + _tuv(b'en', SHORT.encode())
        + _tuv(b'de', b'Offne es')
        + _tuv(b'fr', b'Ouvre-le')
        + b'</tu>',
        b'<tu srclang="de">' + _tuv(b'fr', b'Bonjour') + _tuv(b'de', b'Guten Tag') + b'</tu>',
    )

    db = tmdb.TMDB(db_path)
    db.import_store(store, '/home/user/memory.tmx', 'en', 'af')

    assert db.translate_unit(SHORT, 'en', 'de')[0]['target'] == 'Offne es'
    assert db.translate_unit(SHORT, 'en', 'fr')[0]['target'] == 'Ouvre-le'
    assert db.translate_unit('Guten Tag', 'de', 'fr')[0]['target'] == 'Bonjour'


def test_import_store_reads_a_tmx_1_1_tuvs_lang(db_path):
    # TMX 1.1 and 1.2 name a tuv's language in lang, not xml:lang.
    store = tmx.tmxfile.parsestring(
        TMX_HEADER.replace(b'version="1.4"', b'version="1.1"')
        + b'<tu><tuv lang="de"><seg>Offne es</seg></tuv>'
        + b'<tuv lang="en"><seg>'
        + SHORT.encode()
        + b'</seg></tuv>'
        + b'<tuv lang="fr"><seg>Ouvre-le</seg></tuv></tu></body></tmx>'
    )

    db = tmdb.TMDB(db_path)
    db.import_store(store, '/home/user/memory.tmx', 'en', 'af')

    assert db.translate_unit(SHORT, 'en', 'de')[0]['target'] == 'Offne es'
    assert db.translate_unit(SHORT, 'en', 'fr')[0]['target'] == 'Ouvre-le'
    assert db.translate_unit(SHORT, 'en', 'af') == []


def test_import_store_finds_a_tmx_source_whatever_its_tags_case(db_path):
    store = tmx.tmxfile.parsestring(
        TMX_HEADER.replace(b'srclang="en"', b'srclang="EN_us"')
        + b'<tu>'
        + _tuv(b'de', b'Offne es')
        + _tuv(b'en-US', SHORT.encode())
        + b'</tu></body></tmx>'
    )

    db = tmdb.TMDB(db_path)
    db.import_store(store, '/home/user/memory.tmx', 'en', 'af')

    assert db.translate_unit(SHORT, 'en-US', 'de')[0]['target'] == 'Offne es'


def test_importing_a_file_with_nothing_translated_empties_its_store(db_path):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop')], 'en', 'af')

    counts = db.import_store(
        pypo.pofile.parsestring(b'msgid "Open it"\nmsgstr ""\n'), PO, 'en', 'af'
    )

    assert counts['removed'] == 1
    assert _count(db, 'targets') == 0


def test_an_older_virtaal_works_on_a_file_with_stores(db_path, old_tmdb):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(SHORT, 'Maak dit oop', 'k1')], 'en', 'af')
    _close(db_path)

    old = old_tmdb.TMDB(db_path)
    old.add_dict(_unit(LONG, 'Maak die gekose lêer oop'), 'en', 'af')
    old.add_dict(_unit(SHORT, 'Maak dit oop'), 'en', 'af')

    assert old.translate_unit(SHORT, 'en', 'af')[0]['target'] == 'Maak dit oop'
    assert old.translate_unit(LONG, 'en', 'af')[0]['target'] == 'Maak die gekose lêer oop'
    assert _count(old, 'targets') == 2
    db = tmdb.TMDB(db_path)
    assert db._user_version() == tmdb.SCHEMA_VERSION
    assert db.store_stats(PO, 'en', 'af')['units'] == 1
    assert db.store_stats('', 'en', 'af')['units'] == 1


@pytest.mark.parametrize(
    'stored, looked_up',
    [('en_US', 'en'), ('en', 'en_US'), ('es_419', 'es'), ('EN-us', 'en-US')],
)
def test_translate_unit_matches_a_language_across_a_region(db_path, stored, looked_up):
    db = tmdb.TMDB(db_path)
    db.add_dict({'source': SHORT, 'target': 'Translated', 'context': ''}, stored, 'de-DE')

    assert [m['target'] for m in db.translate_unit(SHORT, looked_up, 'de')] == ['Translated']


@pytest.mark.parametrize(
    'stored, looked_up', [('en_GB', 'en_US'), ('sr@latin', 'sr'), ('en', 'eng')]
)
def test_translate_unit_keeps_other_regions_and_scripts_apart(db_path, stored, looked_up):
    db = tmdb.TMDB(db_path)
    db.add_dict({'source': SHORT, 'target': 'Translated', 'context': ''}, stored, 'de')

    assert db.translate_unit(SHORT, looked_up, 'de') == []


# Abstracts: stored on insert, backfilled, and matched on.

DELETE = 'Delete %(count)s files'
DELETE_ABSTRACT = 'Delete  files'


def _abstracts(db, table):
    return db.cursor.execute('SELECT text, abstract FROM %s ORDER BY text' % table).fetchall()


def test_the_schema_stores_this_abstraction_algorithms_abstracts():
    assert tmdb.SCHEMA_ABSTRACTION[tmdb.SCHEMA_VERSION] == abstraction.ABSTRACTION_VERSION


def test_a_unit_added_stores_its_sources_and_targets_abstracts(db_path):
    db = tmdb.TMDB(db_path)

    db.add_dict({'source': DELETE, 'target': 'Verwyder %(count)s lêers', 'context': ''}, 'en', 'af')
    db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')

    assert _abstracts(db, 'sources') == [(DELETE, DELETE_ABSTRACT), (SHORT, SHORT)]
    assert _abstracts(db, 'targets') == [
        ('Maak dit oop', 'Maak dit oop'),
        ('Verwyder %(count)s lêers', 'Verwyder  lêers'),
    ]


def test_a_synced_store_stores_abstracts(db_path):
    db = tmdb.TMDB(db_path)

    db.sync_store(PO, [_unit('Save _As', 'Stoor _as', 'k1')], 'en', 'af')

    assert _abstracts(db, 'sources') == [('Save _As', 'Save As')]
    assert _abstracts(db, 'targets') == [('Stoor _as', 'Stoor as')]


def test_a_string_the_parsers_fail_on_stores_its_text(db_path, monkeypatch):
    def fail(text):
        raise ValueError(text)

    monkeypatch.setattr(tmdb, 'abstract', fail)
    db = tmdb.TMDB(db_path)

    db.add_dict({'source': DELETE, 'target': 'Verwyder', 'context': ''}, 'en', 'af')

    assert _abstracts(db, 'sources') == [(DELETE, DELETE)]


def test_the_backfill_fills_what_an_older_virtaal_added(db_path, old_tmdb):
    tmdb.TMDB(db_path)
    _close(db_path)
    old = old_tmdb.TMDB(db_path)
    old.add_dict(
        {'source': DELETE, 'target': 'Verwyder %(count)s lêers', 'context': ''}, 'en', 'af'
    )
    old.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')
    db = tmdb.TMDB(db_path)
    assert {a for _t, a in _abstracts(db, 'sources') + _abstracts(db, 'targets')} == {None}

    assert db.backfill_abstracts(batch_size=1) == 4

    assert _abstracts(db, 'sources') == [(DELETE, DELETE_ABSTRACT), (SHORT, SHORT)]
    assert [a for _t, a in _abstracts(db, 'targets')] == ['Maak dit oop', 'Verwyder  lêers']
    assert db.backfill_abstracts() == 0


def test_the_backfill_fills_a_migrated_file(db_path, old_tmdb):
    _v0_file(old_tmdb, db_path, (DELETE, 'Verwyder %(count)s lêers'))
    db = tmdb.TMDB(db_path)

    assert db.backfill_abstracts() == 2
    assert _abstracts(db, 'sources') == [(DELETE, DELETE_ABSTRACT)]


def test_the_backfill_waits_while_another_process_writes(db_path, old_tmdb, monkeypatch, caplog):
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'))
    scale_lock_waits(monkeypatch)
    db = tmdb.TMDB(db_path)

    with caplog.at_level(logging.WARNING, logger=tmdb.logger.name):
        with OtherProcess(db_path):
            assert db.backfill_abstracts() == 0

    assert 'Left TM abstracts for later' in caplog.text
    assert _abstracts(db, 'sources') == [(SHORT, None)]
    assert db.backfill_abstracts() == 2


def test_the_backfill_runs_in_a_thread_with_its_own_connection(db_path, old_tmdb):
    _v0_file(old_tmdb, db_path, (SHORT, 'Maak dit oop'))
    db = tmdb.TMDB(db_path)

    thread = db.start_backfill()
    thread.join(10)

    assert not thread.is_alive()
    assert _abstracts(db, 'sources') == [(SHORT, SHORT)]
    assert thread not in tmdb.TMDB._tm_dbs[db_path]


def test_an_in_memory_database_has_nothing_to_backfill():
    assert tmdb.TMDB(':memory:').start_backfill() is None


@pytest.fixture
def next_algorithm(monkeypatch):
    """The next schema version, for a changed abstraction algorithm, which
    upper-cases."""

    def apply():
        version = tmdb.SCHEMA_VERSION + 1
        monkeypatch.setattr(tmdb, 'SCHEMA_VERSION', version)
        monkeypatch.setitem(tmdb.MIGRATIONS, version, tmdb.CLEAR_ABSTRACTS)
        monkeypatch.setitem(tmdb.SCHEMA_ABSTRACTION, version, 2)
        monkeypatch.setattr(tmdb, 'abstract', lambda text: (text.upper(), []))

    return apply


def test_a_new_abstraction_algorithm_recomputes_every_abstract(db_path, next_algorithm):
    db = tmdb.TMDB(db_path)
    db.add_dict({'source': DELETE, 'target': 'Verwyder', 'context': ''}, 'en', 'af')
    _close(db_path)
    next_algorithm()

    db = tmdb.TMDB(db_path)

    assert db._user_version() == tmdb.SCHEMA_VERSION
    assert _abstracts(db, 'sources') == [(DELETE, None)]
    assert db.backfill_abstracts() == 2
    assert _abstracts(db, 'sources') == [(DELETE, DELETE.upper())]
    assert _abstracts(db, 'targets') == [('Verwyder', 'VERWYDER')]
    assert not os.path.exists(db_path[: -len('.db')] + '-v0.db')


def test_an_older_algorithm_writes_no_abstracts_into_a_newer_file(db_path):
    db = tmdb.TMDB(db_path)
    # A newer Virtaal's file: its user_version is past this one's.
    db.cursor.execute('PRAGMA user_version = %d' % (tmdb.SCHEMA_VERSION + 1))
    _close(db_path)
    db = tmdb.TMDB(db_path)

    db.add_dict({'source': DELETE, 'target': 'Verwyder', 'context': ''}, 'en', 'af')

    assert _abstracts(db, 'sources') == [(DELETE, None)]
    assert _abstracts(db, 'targets') == [('Verwyder', None)]
    assert db.backfill_abstracts() == 0
    assert _abstracts(db, 'sources') == [(DELETE, None)]


def test_a_match_differing_only_in_placeables_passes_the_default_minimum(db_path):
    # The TM plugin's default min_quality.
    db = tmdb.TMDB(db_path, min_similarity=70)
    db.add_dict(
        {'source': 'Delete {0} files', 'target': 'Verwyder {0} lêers', 'context': ''}, 'en', 'af'
    )

    (match,) = db.translate_unit(DELETE, 'en', 'af')

    assert match['source'] == 'Delete {0} files'
    assert match['target'] == 'Verwyder {0} lêers'
    assert (match['quality'], match['raw_quality']) == (99, 59)


def test_raw_matching_misses_it(db_path):
    db = tmdb.TMDB(db_path, min_similarity=70, abstract=False)
    db.add_dict(
        {'source': 'Delete {0} files', 'target': 'Verwyder {0} lêers', 'context': ''}, 'en', 'af'
    )

    assert db.translate_unit(DELETE, 'en', 'af') == []


@pytest.mark.parametrize(
    'query, stored',
    [
        # Outside the raw length window: 17 against 27.
        ('Click <b>here</b>', 'Click <strong>here</strong>'),
        # Over 3 words: the full-text search.
        ('Open the selected %(name)s file now', 'Open the selected {0} file now'),
    ],
)
def test_the_length_window_and_minimum_are_on_the_abstract(db_path, query, stored):
    db = tmdb.TMDB(db_path, min_similarity=90)
    db.add_dict({'source': stored, 'target': 'Vertaal', 'context': ''}, 'en', 'af')

    (match,) = db.translate_unit(query, 'en', 'af')

    assert (match['source'], match['quality']) == (stored, 99)


def test_an_abstract_not_yet_filled_is_matched_on_its_text(db_path, old_tmdb):
    _v0_file(old_tmdb, db_path, ('Delete {0} files', 'Verwyder {0} lêers'))
    db = tmdb.TMDB(db_path, min_similarity=70)

    (match,) = db.translate_unit(DELETE, 'en', 'af')

    assert match['quality'] == 99


def test_a_raw_match_the_abstract_window_would_miss_is_kept(db_path):
    # Abstracts of 1 and 3 characters; the raw strings are close.
    query = '%(very_long_name)s x'
    db = tmdb.TMDB(db_path, min_similarity=75)
    db.add_dict({'source': '%(very_long_name)s', 'target': 'T', 'context': ''}, 'en', 'af')

    (match,) = db.translate_unit(query, 'en', 'af')

    assert match['quality'] == match['raw_quality'] == 90


def test_matches_rank_on_the_adapted_quality(db_path):
    db = tmdb.TMDB(db_path, min_similarity=70, max_candidates=1)
    db.add_dict(
        {'source': 'Delete the files', 'target': 'Verwyder die lêers', 'context': ''}, 'en', 'af'
    )
    db.add_dict(
        {'source': 'Delete {0} files', 'target': 'Verwyder {0} lêers', 'context': ''}, 'en', 'af'
    )

    (match,) = db.translate_unit(DELETE, 'en', 'af')

    assert match['target'] == 'Verwyder {0} lêers'


# Inline codes: kept beside the plain text, which is matched on.

TAGGED_XLIFF = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
<file original="a" source-language="en" target-language="af" datatype="plaintext"><body>
<trans-unit id="1" approved="yes"><source>Click <g id="1">here</g> to open<x id="2"/> the files</source>
<target>Klik <g id="1">hier</g> en maak<x id="2"/> die lêers oop</target></trans-unit>
<trans-unit id="2" approved="yes"><source>Close it</source><target>Maak dit toe</target></trans-unit>
</body></file></xliff>"""
TAGGED = 'Click here to open the files'
TAGGED_MARKUP = (
    'Click <g id="1">here</g> to open<x id="2"/> the files',
    'Klik <g id="1">hier</g> en maak<x id="2"/> die lêers oop',
)


def _markups(db):
    return db.cursor.execute(
        'SELECT text, source_markup, target_markup FROM targets ORDER BY text'
    ).fetchall()


def _import_tagged(db):
    return db.import_store(
        xliff.xlifffile.parsestring(TAGGED_XLIFF.encode('utf-8')), '/x/tags.xlf', 'en', 'af'
    )


def test_a_v1_file_gains_the_markup_columns_and_keeps_its_abstracts(db_path, monkeypatch):
    _before_abstraction_2(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(tmdb, 'SCHEMA_VERSION', 1)
        patch.delitem(tmdb.MIGRATIONS, 2)
        patch.delitem(tmdb.MIGRATIONS, 3)
        patch.delitem(tmdb.MIGRATIONS, 4)
        patch.delitem(tmdb.MIGRATIONS, 5)
        db = tmdb.TMDB(db_path)
        db.cursor.execute(
            "INSERT INTO sources (text, context, lang, length, abstract) "
            "VALUES (?, '', 'en', ?, ?)",
            (DELETE, len(DELETE), DELETE_ABSTRACT),
        )
        db.cursor.execute(
            "INSERT INTO targets (sid, text, lang, abstract) VALUES (1, 'Verwyder', 'af', 'V')"
        )
        db.connection.commit()
        _close(db_path)

    db = tmdb.TMDB(db_path)

    assert db._user_version() == tmdb.SCHEMA_VERSION
    assert {'source_markup', 'target_markup'} <= set(_columns(db, 'targets'))
    assert _abstracts(db, 'sources') == [(DELETE, DELETE_ABSTRACT)]
    assert _abstracts(db, 'targets') == [('Verwyder', 'V')]
    assert _markups(db) == [('Verwyder', None, None)]
    assert db.translate_unit(DELETE, 'en', 'af')[0]['target'] == 'Verwyder'


def test_an_imported_xliff_keeps_its_inline_codes(db_path):
    db = tmdb.TMDB(db_path)

    _import_tagged(db)

    assert _markups(db) == [
        ('Klik hier en maak die lêers oop', *TAGGED_MARKUP),
        ('Maak dit toe', None, None),
    ]


def test_a_tmx_keeps_its_inline_codes(db_path):
    store = tmx.tmxfile.parsestring(
        b'<tmx version="1.4"><header srclang="en" datatype="plaintext" segtype="sentence" '
        b'adminlang="en" o-tmf="x" creationtool="x" creationtoolversion="1"/><body><tu>'
        b'<tuv xml:lang="en"><seg>Press <ph x="1">&lt;br/&gt;</ph> now</seg></tuv>'
        b'<tuv xml:lang="af"><seg>Druk nou</seg></tuv></tu></body></tmx>'
    )
    db = tmdb.TMDB(db_path)

    db.import_store(store, '/x/memory.tmx', 'en', 'af')

    assert _markups(db) == [('Druk nou', 'Press <ph id="1">&lt;br/&gt;</ph> now', 'Druk nou')]


def test_a_saved_unit_sends_its_inline_codes(db_path):
    db = tmdb.TMDB(db_path)
    unit = {**_unit(TAGGED, 'Klik hier en maak die lêers oop', 'k1')}
    unit['source_markup'], unit['target_markup'] = TAGGED_MARKUP

    db.sync_store(PO, [unit], 'en', 'af')

    assert _markups(db) == [('Klik hier en maak die lêers oop', *TAGGED_MARKUP)]


@pytest.mark.parametrize(
    'markup',
    [
        ('Click <g id="1">there</g> to open the files', TAGGED_MARKUP[1]),
        ('Click <b>here</b> to open the files', TAGGED_MARKUP[1]),
        (TAGGED_MARKUP[0], None),
    ],
)
def test_markup_that_isnt_the_units_text_is_not_kept(db_path, markup):
    db = tmdb.TMDB(db_path)
    unit = _unit(TAGGED, 'Klik hier en maak die lêers oop', 'k1')
    unit['source_markup'], unit['target_markup'] = markup

    db.sync_store(PO, [unit], 'en', 'af')

    assert _markups(db) == [('Klik hier en maak die lêers oop', None, None)]


def test_a_plain_unit_with_the_same_text_keeps_the_codes(db_path):
    db = tmdb.TMDB(db_path)
    _import_tagged(db)

    db.sync_store(PO, [_unit(TAGGED, 'Klik hier en maak die lêers oop', 'k1')], 'en', 'af')

    assert _markups(db)[0] == ('Klik hier en maak die lêers oop', *TAGGED_MARKUP)


@pytest.mark.parametrize('tagged_tm', [True, False])
def test_codes_dont_count_against_a_matchs_score(db_path, tagged_tm):
    db = tmdb.TMDB(db_path)
    if tagged_tm:
        _import_tagged(db)
    else:
        db.sync_store(PO, [_unit(TAGGED, 'Klik hier en maak die lêers oop', 'k1')], 'en', 'af')

    # A tagged XLIFF unit's query is its plain text.
    exact = db.translate_unit(TAGGED, 'en', 'af')
    fuzzy = db.translate_unit('Click here to open the file', 'en', 'af')

    assert [m['quality'] for m in exact] == [100]
    assert [m['quality'] for m in fuzzy] == [96]
    assert ('target_markup' in exact[0]) is tagged_tm
    if tagged_tm:
        assert (exact[0]['source_markup'], exact[0]['target_markup']) == TAGGED_MARKUP


# Native code (ph, bpt, ept, it text) left out of matching.

BOLD_TMX = (
    '<tu><tuv xml:lang="en"><seg><bpt i="1">&lt;b&gt;</bpt>Save<ept i="1">&lt;/b&gt;</ept> '
    'the file</seg></tuv><tuv xml:lang="af"><seg><bpt i="1">&lt;b&gt;</bpt>Stoor'
    '<ept i="1">&lt;/b&gt;</ept> die lêer</seg></tuv></tu>'
)
BOLD = '<b>Save</b> the file'
BOLD_MARKUP = (
    '<bpt id="1">&lt;b&gt;</bpt>Save<ept id="1">&lt;/b&gt;</ept> the file',
    '<bpt id="1">&lt;b&gt;</bpt>Stoor<ept id="1">&lt;/b&gt;</ept> die lêer',
)


def _qualities(db, unit_source, plain=None):
    return [m['quality'] for m in db.translate_unit(unit_source, 'en', 'af', plain)]


def test_a_tagged_query_matches_an_entry_on_its_text_without_native_code(db_path):
    db = tmdb.TMDB(db_path)
    db.import_store(_tmx(BOLD_TMX.encode('utf-8')), '/x/bold.tmx', 'en', 'af')

    # An XLIFF <g>Save</g> the file, with and without its plain source.
    assert _qualities(db, 'Save the file') == []
    assert _qualities(db, 'Save the file', 'Save the file') == [100]
    assert _qualities(db, 'Save the files', 'Save the files') == [93]
    # An XLIFF unit with the same bpt/ept codes.
    assert _qualities(db, BOLD, 'Save the file') == [100]


def test_an_untagged_query_matches_an_entry_with_codes_as_before(db_path):
    db = tmdb.TMDB(db_path)
    db.import_store(_tmx(BOLD_TMX.encode('utf-8')), '/x/bold.tmx', 'en', 'af')

    # A PO unit holding the HTML as text.
    (match,) = db.translate_unit(BOLD, 'en', 'af')

    assert (match['quality'], match['target']) == (100, '<b>Stoor</b> die lêer')


@pytest.mark.parametrize('source', [BOLD, 'Save the file'])
def test_a_tagged_query_matches_an_entry_without_codes_either_way(db_path, source):
    db = tmdb.TMDB(db_path)
    db.sync_store(PO, [_unit(source, 'Stoor die lêer', 'k1')], 'en', 'af')

    assert _qualities(db, BOLD, 'Save the file') == [100]


@pytest.mark.parametrize('abstract', [True, False])
def test_a_long_native_code_doesnt_keep_an_entry_out_of_the_candidates(db_path, abstract):
    image = b'&lt;img src="images/screenshots/main-window.png" alt="Main window"/&gt;'
    db = tmdb.TMDB(db_path, abstract=abstract)
    db.import_store(
        _tmx(
            b'<tu>'
            + _tuv(b'en', b'Open it<ph x="1">%s</ph>' % image)
            + _tuv(b'af', b'Maak dit oop<ph x="1">%s</ph>' % image)
            + b'</tu>'
        ),
        '/x/image.tmx',
        'en',
        'af',
    )

    assert db.cursor.execute('SELECT plain_length FROM targets').fetchall() == [(7,)]
    assert _qualities(db, SHORT, SHORT) == [100]


def test_a_v2_file_gets_its_plain_lengths_from_its_markup(db_path, monkeypatch):
    _before_abstraction_2(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(tmdb, 'SCHEMA_VERSION', 2)
        patch.delitem(tmdb.MIGRATIONS, 3)
        patch.delitem(tmdb.MIGRATIONS, 4)
        patch.delitem(tmdb.MIGRATIONS, 5)
        db = tmdb.TMDB(db_path)
        db.cursor.execute(
            "INSERT INTO sources (text, context, lang, length, abstract) "
            "VALUES (?, '', 'en', ?, ?)",
            (BOLD, len(BOLD), tmdb._abstract(BOLD)),
        )
        db.cursor.execute(
            "INSERT INTO targets (sid, text, lang, abstract, source_markup, target_markup) "
            "VALUES (1, '<b>Stoor</b> die lêer', 'af', 'S', ?, ?)",
            BOLD_MARKUP,
        )
        db.cursor.execute(
            "INSERT INTO targets (sid, text, lang, abstract) VALUES (1, 'Stoor dit', 'af', 'S')"
        )
        db.connection.commit()
        _close(db_path)

    db = tmdb.TMDB(db_path)

    assert db._user_version() == tmdb.SCHEMA_VERSION
    assert db.cursor.execute('SELECT text, plain_length FROM targets ORDER BY tid').fetchall() == [
        ('<b>Stoor</b> die lêer', len('Save the file')),
        ('Stoor dit', None),
    ]
    assert _abstracts(db, 'targets') == [('<b>Stoor</b> die lêer', 'S'), ('Stoor dit', 'S')]
    assert _qualities(db, 'Save the file', 'Save the file') == [100]


def test_export_writes_inline_codes_as_tmx_codes(db_path):
    db = tmdb.TMDB(db_path)
    _import_tagged(db)

    exported = db.export_tmx('en', 'af').decode('utf-8')

    assert '<seg>Click <bpt i="1"/>here<ept i="1"/> to open<ph x="2"/> the files</seg>' in exported
    assert '<seg>Klik <bpt i="1"/>hier<ept i="1"/> en maak<ph x="2"/> die lêers oop</seg>' in (
        exported
    )
    assert '<seg>Maak dit toe</seg>' in exported
    store = tmx.tmxfile(exported.encode('utf-8'))
    assert [u.target for u in store.units] == ['Klik hier en maak die lêers oop', 'Maak dit toe']


def test_an_exported_tmx_imports_with_its_codes(db_path, tmp_path):
    db = tmdb.TMDB(db_path)
    _import_tagged(db)
    exported = tmx.tmxfile(db.export_tmx('en', 'af'))
    other = str(tmp_path / 'other.db')
    try:
        again = tmdb.TMDB(other)
        again.import_store(exported, '/x/exported.tmx', 'en', 'af')

        assert _markups(again)[0] == (
            'Klik hier en maak die lêers oop',
            'Click <bpt id="1"></bpt>here<ept id="1"></ept> to open<ph id="2"></ph> the files',
            'Klik <bpt id="1"></bpt>hier<ept id="1"></ept> en maak<ph id="2"></ph> die lêers oop',
        )
    finally:
        _close(other)


# Plural units: every form, with the source plural to score forms after the first.

PLURAL_PO = (
    'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
    'msgid "Delete %d file"\nmsgid_plural "Delete %d files"\n'
    'msgstr[0] "Skrap %d lêer"\nmsgstr[1] "Skrap %d lêers"\n'
)
PLURAL_FORMS = ['Skrap %d lêer', 'Skrap %d lêers']


def _plural_po(text=PLURAL_PO):
    return pypo.pofile.parsestring(text.encode('utf-8'))


def _plurals(db):
    return db.cursor.execute(
        'SELECT text, plural_source, plural_targets FROM targets ORDER BY tid'
    ).fetchall()


def test_a_plural_match_has_every_form_and_the_source_plural(db_path):
    db = tmdb.TMDB(db_path)
    db.import_store(_plural_po(), '/x/af.po', 'en', 'af')

    (match,) = db.translate_unit('Delete %d file', 'en', 'af')

    assert match['target'] == 'Skrap %d lêer'
    assert match['plural_source'] == 'Delete %d files'
    assert match['plural_targets'] == PLURAL_FORMS


def test_add_store_keeps_every_form(db_path):
    db = tmdb.TMDB(db_path)

    db.add_store(_plural_po(), 'en', 'af')

    assert db.translate_unit('Delete %d file', 'en', 'af')[0]['plural_targets'] == PLURAL_FORMS


def test_a_synced_unit_dict_keeps_every_form(db_path):
    db = tmdb.TMDB(db_path)
    unit = {
        'source': 'Delete %d file',
        'target': PLURAL_FORMS[0],
        'key': 'k',
        'plural_source': 'Delete %d files',
        'plural_targets': PLURAL_FORMS,
    }

    db.sync_store('/x/af.po', [unit], 'en', 'af')
    db.sync_store(
        '/x/af.po', [{**unit, 'plural_targets': [PLURAL_FORMS[0], 'Skrap %d']}], 'en', 'af'
    )

    # The latest forms, under the same target.
    assert _plurals(db) == [(PLURAL_FORMS[0], 'Delete %d files', '["Skrap %d lêer", "Skrap %d"]')]


@pytest.mark.parametrize(
    'fields',
    [
        {'plural_source': 'Delete %d files', 'plural_targets': ['Skrap %d lêer', '']},
        {'plural_source': 'Delete %d files', 'plural_targets': ['Skrap %d lêer']},
        {'plural_source': 'Delete %d files', 'plural_targets': ['Iets anders', 'Skrap']},
        {'plural_source': 'Delete %d files', 'plural_targets': 'Skrap %d lêer'},
        {'plural_source': None, 'plural_targets': PLURAL_FORMS},
    ],
)
def test_unusable_plural_fields_are_left_out(db_path, fields):
    db = tmdb.TMDB(db_path)

    db.sync_store(
        '/x/af.po', [{'source': 'Delete %d file', 'target': 'Skrap %d lêer', **fields}], 'en', 'af'
    )

    assert _plurals(db) == [('Skrap %d lêer', None, None)]
    assert 'plural_targets' not in db.translate_unit('Delete %d file', 'en', 'af')[0]


@pytest.mark.parametrize('stored', ['[unclosed', '"Skrap %d lêers"'])
def test_a_match_with_unreadable_forms_has_none(db_path, stored):
    db = tmdb.TMDB(db_path)
    db.import_store(_plural_po(), '/x/af.po', 'en', 'af')
    db.cursor.execute('UPDATE targets SET plural_targets = ?', (stored,))

    (match,) = db.translate_unit('Delete %d file', 'en', 'af')

    assert match['target'] == 'Skrap %d lêer'
    assert 'plural_targets' not in match


def test_a_plural_with_an_empty_form_is_kept_as_its_singular(db_path):
    db = tmdb.TMDB(db_path)

    db.import_store(_plural_po(PLURAL_PO.replace('Skrap %d lêers', '')), '/x/af.po', 'en', 'af')

    assert _plurals(db) == [('Skrap %d lêer', None, None)]


def test_a_singular_unit_with_a_plurals_text_keeps_its_forms(db_path):
    db = tmdb.TMDB(db_path)
    db.import_store(_plural_po(), '/x/af.po', 'en', 'af')

    db.sync_store(
        '/x/other.po', [{'source': 'Delete %d file', 'target': 'Skrap %d lêer'}], 'en', 'af'
    )

    assert _plurals(db) == [
        (PLURAL_FORMS[0], 'Delete %d files', '["Skrap %d lêer", "Skrap %d lêers"]')
    ]


# TMX export: a unit per form, marked so an import joins them again.

THREE_FORMS_PO = (
    'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Plural-Forms: nplurals=3; plural=(n==1 ? 0 : n==2 ? 1 : 2);\\n"\n\n'
    'msgctxt "menu"\nmsgid "Delete %d file"\nmsgid_plural "Delete %d files"\n'
    'msgstr[0] "Skrap %d lêer"\nmsgstr[1] "Skrap %d lêertjies"\nmsgstr[2] "Skrap %d lêers"\n\n'
    'msgid "Open it"\nmsgstr "Maak dit oop"\n'
)
THREE_FORMS = ['Skrap %d lêer', 'Skrap %d lêertjies', 'Skrap %d lêers']


def _plural_props(unit):
    props = {p.get('type'): p.text for p in unit.xmlelement.iterfind(unit.namespaced('prop'))}
    return props.get(tmdb.PLURAL_GROUP_PROP), props.get(tmdb.PLURAL_FORM_PROP)


def test_export_writes_each_plural_form_as_a_marked_unit(db_path):
    db = tmdb.TMDB(db_path)
    db.import_store(_plural_po(THREE_FORMS_PO), '/x/af.po', 'en', 'af')

    exported = tmx.tmxfile(db.export_tmx('en', 'af'))

    key = tmdb.plural_group_key(
        ('en', 'af'), 'menu', 'Delete %d file', 'Delete %d files', THREE_FORMS
    )
    assert len(key) == 16
    assert [(u.source, u.target, u.getcontext(), _plural_props(u)) for u in exported.units] == [
        ('Delete %d file', THREE_FORMS[0], 'menu', (key, '0')),
        ('Delete %d files', THREE_FORMS[1], 'menu', (key, '1')),
        ('Delete %d files', THREE_FORMS[2], 'menu', (key, '2')),
        ('Open it', 'Maak dit oop', '', (None, None)),
    ]


def test_a_re_export_gives_the_same_plural_keys(db_path, tmp_path):
    db = tmdb.TMDB(db_path)
    db.import_store(_plural_po(), '/x/first.po', 'en', 'af')
    db.import_store(_plural_po(THREE_FORMS_PO), '/x/af.po', 'en', 'af')
    first = db.export_tmx('en', 'af')
    other = str(tmp_path / 'other.db')
    try:
        again = tmdb.TMDB(other)
        again.import_store(tmx.tmxfile(first), '/x/e.tmx')

        def marks(exported):
            return sorted(
                (u.source, u.target, _plural_props(u)) for u in tmx.tmxfile(exported).units
            )

        assert marks(again.export_tmx('en', 'af')) == marks(first)
        assert len({group for _s, _t, (group, _f) in marks(first) if group}) == 2
    finally:
        _close(other)


def _merged(*exports):
    """One TMX holding the units of every exported TMX."""
    merged = tmx.tmxfile(sourcelanguage='en', targetlanguage='af')
    for exported in exports:
        for unit in tmx.tmxfile(exported).units:
            merged.addunit(unit)
    return tmx.tmxfile(bytes(merged))


OTHER_FORMS_PO = THREE_FORMS_PO.replace('lêertjies', 'lêerlies').replace('"menu"', '"list"')


@pytest.mark.parametrize(
    'second',
    [THREE_FORMS_PO, THREE_FORMS_PO.replace('lêertjies', 'lêerlies'), OTHER_FORMS_PO],
    ids=['same', 'other-translation', 'other-context'],
)
def test_merged_exports_import_their_plurals_whole(db_path, tmp_path, second):
    exports = []
    for number, text in enumerate((THREE_FORMS_PO, second)):
        path = str(tmp_path / ('%d.db' % number))
        source = tmdb.TMDB(path)
        source.import_store(_plural_po(text), '/x/af.po', 'en', 'af')
        exports.append(source.export_tmx('en', 'af'))
        _close(path)

    db = tmdb.TMDB(db_path)
    db.import_store(_merged(*exports), '/x/merged.tmx')

    # Each plural joins whole; one with the same singular target as an
    # earlier one replaces it, as a plain unit would.
    plurals = [row for row in _plurals(db) if row[0] != 'Maak dit oop']
    assert plurals and all(plural == 'Delete %d files' for _text, plural, _forms in plurals)
    assert len(plurals) == (2 if second == OTHER_FORMS_PO else 1)
    assert json.loads(plurals[-1][2]) == _plural_po(second).units[1].target.strings


def test_old_counter_keys_still_join(db_path):
    db = tmdb.TMDB(db_path)
    db.import_store(
        _tmx_forms((*SINGULAR, 1, 0), (*PLURAL, 1, 1), ('Open it', 'Maak oop', 2, 0)),
        '/x/e.tmx',
    )

    assert _plurals(db) == [
        ('Skrap %d lêer', 'Delete %d files', '["Skrap %d lêer", "Skrap %d lêers"]'),
        ('Maak oop', None, None),
    ]


def test_merged_old_exports_with_colliding_counters_fall_back_to_plain_units(db_path):
    db = tmdb.TMDB(db_path)
    db.import_store(
        _tmx_forms(
            (*SINGULAR, 1, 0),
            (*PLURAL, 1, 1),
            ('Move %d file', 'Skuif %d lêer', 1, 0),
            ('Move %d files', 'Skuif %d lêers', 1, 1),
        ),
        '/x/e.tmx',
    )

    assert all(plural is None for _text, plural, _forms in _plurals(db))
    assert len(_plurals(db)) == 4


def test_an_exported_plural_imports_as_one_plural(db_path, tmp_path):
    db = tmdb.TMDB(db_path)
    db.import_store(_plural_po(THREE_FORMS_PO), '/x/af.po', 'en', 'af')
    other = str(tmp_path / 'other.db')
    try:
        again = tmdb.TMDB(other)
        counts = again.import_store(tmx.tmxfile(db.export_tmx('en', 'af')), '/x/e.tmx')

        assert counts['added'] == 2
        assert _plurals(again) == _plurals(db)
        (match,) = again.translate_unit('Delete %d file', 'en', 'af')
        assert match['plural_targets'] == THREE_FORMS
    finally:
        _close(other)


def _tmx_forms(*forms, props=True):
    """A TMX of one plural's forms, (source, target, group, form) each."""
    units = []
    for source, target, group, form in forms:
        marks = (
            '<prop type="x-plural-group">%s</prop><prop type="x-plural-form">%s</prop>'
            % (group, form)
            if props
            else ''
        )
        units.append(
            '<tu>%s<tuv xml:lang="en"><seg>%s</seg></tuv>'
            '<tuv xml:lang="af"><seg>%s</seg></tuv></tu>' % (marks, source, target)
        )
    return tmx.tmxfile.parsestring(
        (
            '<tmx version="1.4"><header srclang="en" segtype="sentence" adminlang="en" '
            'datatype="plaintext" o-tmf="x" creationtool="x" creationtoolversion="1"/>'
            '<body>%s</body></tmx>' % ''.join(units)
        ).encode('utf-8')
    )


SINGULAR = ('Delete %d file', 'Skrap %d lêer')
PLURAL = ('Delete %d files', 'Skrap %d lêers')


def test_a_tmx_without_plural_marks_imports_as_plain_units(db_path):
    db = tmdb.TMDB(db_path)

    db.import_store(_tmx_forms((*SINGULAR, 1, 0), (*PLURAL, 1, 1), props=False), '/x/e.tmx')

    assert _plurals(db) == [('Skrap %d lêer', None, None), ('Skrap %d lêers', None, None)]


@pytest.mark.parametrize(
    'forms',
    [
        [(*SINGULAR, 1, 0)],  # A form on its own.
        [(*PLURAL, 1, 1), ('Delete %d files', 'Skrap baie', 1, 2)],  # No first form.
        [(*SINGULAR, 1, 0), (*PLURAL, 1, 2)],  # A form missing.
        [(*SINGULAR, 1, 0), (*PLURAL, 1, 1), ('Other %d', 'Ander %d', 1, 2)],
        [(*SINGULAR, 1, 0), (*PLURAL, 1, 'one')],
        [(*SINGULAR, 1, 0), (*PLURAL, '', 1)],
    ],
)
def test_incomplete_plural_marks_import_as_plain_units(db_path, forms):
    db = tmdb.TMDB(db_path)

    db.import_store(_tmx_forms(*forms), '/x/e.tmx')

    assert all(plural is None for _text, plural, _forms in _plurals(db))
    assert len(_plurals(db)) == len(forms)


def test_a_synced_tmx_joins_its_marked_forms(db_path):
    db = tmdb.TMDB(db_path)
    units = [
        {'source': source, 'target': target, 'key': str(form), **tmdb.plural_marker(unit)}
        for unit, (source, target, _group, form) in zip(
            _tmx_forms((*SINGULAR, 7, 0), (*PLURAL, 7, 1)).units,
            [(*SINGULAR, 7, 0), (*PLURAL, 7, 1)],
            strict=True,
        )
    ]

    db.sync_store('/x/open.tmx', [{**units[0], 'target': ''}, units[1]], 'en', 'af')
    assert _plurals(db) == [('', None, None), ('Skrap %d lêers', None, None)]

    db.sync_store('/x/open.tmx', units, 'en', 'af')

    assert _plurals(db) == [
        ('Skrap %d lêer', 'Delete %d files', '["Skrap %d lêer", "Skrap %d lêers"]')
    ]


def test_a_v3_file_gains_the_plural_columns_and_keeps_its_abstracts(db_path, monkeypatch):
    _before_abstraction_2(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(tmdb, 'SCHEMA_VERSION', 3)
        patch.delitem(tmdb.MIGRATIONS, 4)
        patch.delitem(tmdb.MIGRATIONS, 5)
        db = tmdb.TMDB(db_path)
        for source, target, markup in (
            (BOLD, '<b>Stoor</b> die lêer', BOLD_MARKUP),
            ('Delete %d file', 'Skrap %d lêer', (None, None)),
        ):
            db.cursor.execute(
                "INSERT INTO sources (text, context, lang, length, abstract) "
                "VALUES (?, '', 'en', ?, ?)",
                (source, len(source), tmdb._abstract(source)),
            )
            db.cursor.execute(
                "INSERT INTO targets (sid, text, lang, abstract, source_markup, target_markup, "
                "plain_length) VALUES (?, ?, 'af', ?, ?, ?, ?)",
                (
                    db.cursor.lastrowid,
                    target,
                    tmdb._abstract(target),
                    *markup,
                    tmdb._plain_length(markup[0]),
                ),
            )
        db.connection.commit()
        _close(db_path)

    db = tmdb.TMDB(db_path)

    assert db._user_version() == tmdb.SCHEMA_VERSION
    assert {'plural_source', 'plural_targets'} <= set(_columns(db, 'targets'))
    # A v3 file kept only the first form.
    assert _plurals(db) == [('<b>Stoor</b> die lêer', None, None), ('Skrap %d lêer', None, None)]
    assert _abstracts(db, 'targets') == [
        ('<b>Stoor</b> die lêer', tmdb._abstract('<b>Stoor</b> die lêer')),
        ('Skrap %d lêer', tmdb._abstract('Skrap %d lêer')),
    ]
    assert _markups(db)[0][1:] == BOLD_MARKUP
    assert _qualities(db, 'Save the file', 'Save the file') == [100]

    db.import_store(_plural_po(), '/x/af.po', 'en', 'af')

    assert db.translate_unit('Delete %d file', 'en', 'af')[0]['plural_targets'] == PLURAL_FORMS


def test_a_v4_file_keys_monolingual_origins_by_their_bare_keys(db_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(tmdb, 'SCHEMA_VERSION', 4)
        patch.delitem(tmdb.MIGRATIONS, 5)
        db = tmdb.TMDB(db_path)
        units = [
            {'source': 'Quit', 'target': 'Verlaat', 'key': 'NoName\x04quit'},
            {'source': 'Open', 'target': 'Open', 'key': 'NoName\x04open'},
        ]
        db.sync_store('/x/af.properties', units, 'en', 'af')
        db.sync_store('/x/af.properties', units[:1], 'en', 'de')
        db.sync_store('/x/af.xlf', units, 'en', 'af')
        _close(db_path)

    db = tmdb.TMDB(db_path)

    assert db._user_version() == tmdb.SCHEMA_VERSION
    keys = db.cursor.execute(
        'SELECT s.path, o.lang, o.unit_key FROM origins o '
        'JOIN stores s ON s.store_id = o.store_id ORDER BY 1, 2, 3'
    ).fetchall()
    # An XLIFF file's own ids keep their file name.
    assert keys == [
        ('/x/af.properties', 'af', 'open'),
        ('/x/af.properties', 'af', 'quit'),
        ('/x/af.properties', 'de', 'quit'),
        ('/x/af.xlf', 'af', 'NoName\x04open'),
        ('/x/af.xlf', 'af', 'NoName\x04quit'),
    ]
    assert db.sync_store(
        '/x/af.properties',
        [
            {'source': 'Quit', 'target': 'Verlaat', 'key': 'quit'},
            {'source': 'Open', 'target': 'Open', 'key': 'open'},
        ],
        'en',
        'af',
    ) == {'added': 0, 'replaced': 0, 'removed': 0}


def test_a_bare_key_already_there_keeps_its_origin(db_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(tmdb, 'SCHEMA_VERSION', 4)
        patch.delitem(tmdb.MIGRATIONS, 5)
        db = tmdb.TMDB(db_path)
        db.sync_store(
            '/x/af.properties',
            [
                {'source': 'Quit', 'target': 'Verlaat', 'key': 'NoName\x04quit'},
                {'source': 'Quit', 'target': 'Sluit', 'key': 'quit'},
            ],
            'en',
            'af',
        )
        _close(db_path)

    db = tmdb.TMDB(db_path)

    rows = db.cursor.execute(
        'SELECT o.unit_key, t.text FROM origins o JOIN targets t ON t.tid = o.tid '
        'WHERE o.store_id != ?',
        (tmdb.LEGACY_STORE_ID,),
    ).fetchall()
    assert rows == [('quit', 'Sluit')]


# Suggestions fetched for a whole file (#1679)


@pytest.fixture
def cache(db_path):
    cache = tmdb.SuggestionCache(db_path)
    yield cache
    cache.close()


GOOGLE = [{'source': 'Open', 'target': 'Maak oop', 'tmsource': 'Google'}]


def test_a_v5_file_gets_a_table_for_fetched_suggestions(db_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(tmdb, 'SCHEMA_VERSION', 5)
        patch.delitem(tmdb.MIGRATIONS, 6)
        db = tmdb.TMDB(db_path)
        db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')
        assert not db._has_table('cached')
        _close(db_path)

    db = tmdb.TMDB(db_path)

    assert db._user_version() == tmdb.SCHEMA_VERSION
    assert _columns(db, 'cached') == [
        'service',
        'source_lang',
        'target_lang',
        'source',
        'matches',
        'fetched',
    ]
    assert _qualities(db, SHORT) == [100]


def test_the_cache_brings_an_older_file_up_to_date(db_path, monkeypatch):
    # The Virtaal process may open the file before any TM server does.
    with monkeypatch.context() as patch:
        patch.setattr(tmdb, 'SCHEMA_VERSION', 5)
        patch.delitem(tmdb.MIGRATIONS, 6)
        tmdb.TMDB(db_path)
        _close(db_path)

    cache = tmdb.SuggestionCache(db_path)
    try:
        assert cache._user_version() == tmdb.SCHEMA_VERSION
        cache.put('google_translate', 'en', 'af', {'Open': GOOGLE})
        assert cache.get('en', 'af', ['Open']) == {'google_translate': {'Open': GOOGLE}}
    finally:
        cache.close()


def test_cached_suggestions_are_kept_per_service_and_language_pair(cache):
    cache.put('google_translate', 'en', 'af', {'Open': GOOGLE, 'Close': []})
    cache.put('amagama', 'en', 'af', {'Open': [{'source': 'Open', 'target': 'Open'}]})
    cache.put('amagama', 'en', 'de', {'Open': [{'source': 'Open', 'target': 'Öffnen'}]})

    assert cache.get('en', 'af', ['Open', 'Close', 'Quit']) == {
        'google_translate': {'Open': GOOGLE, 'Close': []},
        'amagama': {'Open': [{'source': 'Open', 'target': 'Open'}]},
    }
    assert cache.get('en', 'de', ['Close']) == {}


def test_a_later_fetch_replaces_a_service_answer(cache):
    cache.put('google_translate', 'en', 'af', {'Open': []}, now=1)
    cache.put('google_translate', 'en', 'af', {'Open': GOOGLE}, now=2)

    assert cache.get('en', 'af', ['Open']) == {'google_translate': {'Open': GOOGLE}}


def test_cached_suggestions_are_looked_up_in_chunks(cache, monkeypatch):
    monkeypatch.setattr(tmdb.SuggestionCache, 'CHUNK', 2)
    sources = ['s%d' % n for n in range(5)]
    cache.put('amagama', 'en', 'af', {source: [] for source in sources})

    assert cache.get('en', 'af', sources) == {'amagama': {source: [] for source in sources}}


def test_translated_units_lose_their_cached_suggestions(cache):
    cache.put('google_translate', 'en', 'af', {'Open': GOOGLE, 'Close': []})
    cache.put('amagama', 'en', 'af', {'Open': []})
    cache.put('amagama', 'en', 'de', {'Open': []})

    cache.forget('en', 'af', ['Open'])

    assert cache.get('en', 'af', ['Open', 'Close']) == {'google_translate': {'Close': []}}
    assert cache.get('en', 'de', ['Open']) == {'amagama': {'Open': []}}


def test_cached_suggestions_expire(cache):
    day = 86400
    cache.put('amagama', 'en', 'af', {'Old': []}, now=100 * day)
    cache.put('amagama', 'en', 'af', {'New': []}, now=130 * day)

    cache.expire(30, now=130 * day + 1)

    assert cache.get('en', 'af', ['Old', 'New']) == {'amagama': {'New': []}}


def test_cached_suggestions_are_counted_and_cleared_by_service_for_every_pair(cache):
    assert cache.counts() == {}
    cache.put('amagama', 'en', 'af', {'Open': [], 'Close': []})
    cache.put('amagama', 'en', 'de', {'Open': []})
    cache.put('google_translate', 'en', 'de', {'Open': GOOGLE})

    assert cache.counts() == {'amagama': 3, 'google_translate': 1}
    cache.clear(['amagama'])

    assert cache.counts() == {'google_translate': 1}
    assert cache.get('en', 'af', ['Open', 'Close']) == {}
    assert cache.get('en', 'de', ['Open']) == {'google_translate': {'Open': GOOGLE}}


def test_cached_suggestions_are_not_the_users_translations(db_path, cache):
    cache.put('google_translate', 'en', 'af', {LONG: [{'source': LONG, 'target': 'Maak oop'}]})
    db = tmdb.TMDB(db_path)

    assert db.translate_unit(LONG, 'en', 'af') == []
    assert db.list_stores() == []
    assert b'Maak oop' not in db.export_tmx('en', 'af')
    assert _count(db, 'targets') == 0


def test_a_cache_entry_that_is_not_json_is_skipped(cache):
    cache.put('amagama', 'en', 'af', {'Open': []})
    cache.cursor.execute("UPDATE cached SET matches = 'not json'")
    cache.connection.commit()

    assert cache.get('en', 'af', ['Open']) == {}


def test_the_cache_gives_up_soon_on_another_process_lock(cache, db_path, monkeypatch):
    monkeypatch.setattr(tmdb.SuggestionCache, 'LOCK_WAIT', 0.05)
    cache.close()
    cache = tmdb.SuggestionCache(db_path)

    with OtherProcess(db_path), pytest.raises(sqlite3.OperationalError):
        cache.put('amagama', 'en', 'af', {'Open': []})

    cache.put('amagama', 'en', 'af', {'Open': []})
    assert cache.get('en', 'af', ['Open']) == {'amagama': {'Open': []}}
    cache.close()


# How a plural's forms are laid out (schema 7)

AF_LAYOUT = 'nplurals=2;plural=(n!=1)'


def _layouts(db):
    return [row[0] for row in db.cursor.execute('SELECT plural_layout FROM targets ORDER BY tid')]


def test_import_and_add_store_record_the_file_s_plural_forms(db_path):
    db = tmdb.TMDB(db_path)

    db.import_store(_plural_po(), '/x/af.po', 'en', 'af')
    db.add_store(_plural_po(THREE_FORMS_PO), 'en', 'af')

    assert _layouts(db) == [AF_LAYOUT, 'nplurals=3;plural=(n==1?0:n==2?1:2)', None]
    (match,) = db.translate_unit('Delete %d file', 'en', 'af')[:1]
    assert match['plural_layout'] in (AF_LAYOUT, 'nplurals=3;plural=(n==1?0:n==2?1:2)')


def test_a_synced_unit_dict_records_its_layout(db_path):
    db = tmdb.TMDB(db_path)
    unit = {
        'source': 'Delete %d file',
        'target': PLURAL_FORMS[0],
        'key': 'k',
        'plural_source': 'Delete %d files',
        'plural_targets': PLURAL_FORMS,
        'plural_layout': 'cldr',
    }

    db.sync_store('/x/af.json', [unit], 'en', 'af')

    assert _layouts(db) == ['cldr']
    assert db.translate_unit('Delete %d file', 'en', 'af')[0]['plural_layout'] == 'cldr'


def test_an_exported_plural_keeps_its_layout(db_path, tmp_path):
    db = tmdb.TMDB(db_path)
    db.import_store(_plural_po(THREE_FORMS_PO), '/x/af.po', 'en', 'af')
    other = str(tmp_path / 'other.db')
    try:
        again = tmdb.TMDB(other)
        again.import_store(tmx.tmxfile(db.export_tmx('en', 'af')), '/x/e.tmx')

        assert _layouts(again)[0] == 'nplurals=3;plural=(n==1?0:n==2?1:2)'
    finally:
        _close(other)


def test_a_v6_file_gains_plural_layouts_its_plurals_don_t_know(db_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(tmdb, 'SCHEMA_VERSION', 6)
        patch.delitem(tmdb.MIGRATIONS, 7)
        db = tmdb.TMDB(db_path)
        db.import_store(_plural_po(), '/x/af.po', 'en', 'af')
        _close(db_path)

    db = tmdb.TMDB(db_path)

    assert db._user_version() == tmdb.SCHEMA_VERSION
    # Not known: read as the language's usual layout when matched.
    assert _layouts(db) == [None]
    (match,) = db.translate_unit('Delete %d file', 'en', 'af')
    assert 'plural_layout' not in match and match['plural_targets'] == PLURAL_FORMS


@pytest.mark.parametrize(
    'unit, expected',
    [
        (types.SimpleNamespace(plural_tags=['one', 'other'], plural_ordinal=False), 'cldr'),
        (types.SimpleNamespace(plural_tags=['other'], plural_ordinal=True), 'cldr-ordinal'),
        (types.SimpleNamespace(_store=None), None),
    ],
)
def test_a_monolingual_plural_s_layout_is_cldr_s(unit, expected):
    assert tmdb.plural_layout(unit) == expected


# Filling in the layouts of plurals stored before schema 7

KDE_PO = (
    'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Plural-Forms: nplurals=4; plural=n==1 ? 3 : n%10==1 && n%100!=11 ? 0 : "\n'
    '"n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2;\\n"\n\n'
    'msgid "Delete %d file"\nmsgid_plural "Delete %d files"\n'
    'msgstr[0] "a"\nmsgstr[1] "b"\nmsgstr[2] "c"\nmsgstr[3] "d"\n'
)
KDE_LAYOUT = (
    'nplurals=4;plural=n==1?3:n%10==1&&n%100!=11?0:n%10>=2&&n%10<=4&&(n%100<10||n%100>=20)?1:2'
)


def _v6_plurals(db_path, monkeypatch, *paths):
    """A schema 6 file holding the KDE plural, imported from each of C{paths}."""
    with monkeypatch.context() as patch:
        patch.setattr(tmdb, 'SCHEMA_VERSION', 6)
        patch.delitem(tmdb.MIGRATIONS, 7)
        patch.delitem(tmdb.MIGRATIONS, 8)
        db = tmdb.TMDB(db_path)
        for path in paths:
            db.import_store(_plural_po(KDE_PO), path, 'en', 'ru')
        _close(db_path)
    return tmdb.TMDB(db_path)


def test_an_old_plural_takes_the_layout_its_po_file_still_has(db_path, monkeypatch, tmp_path):
    po_file = tmp_path / 'ru.po'
    po_file.write_text(KDE_PO, encoding='utf-8')
    before = po_file.read_bytes()
    db = _v6_plurals(db_path, monkeypatch, str(po_file))
    assert _layouts(db) == [None]

    assert db.backfill_plural_layouts() == 1

    assert _layouts(db) == [KDE_LAYOUT]
    assert db.translate_unit('Delete %d file', 'en', 'ru')[0]['plural_layout'] == KDE_LAYOUT
    assert po_file.read_bytes() == before


def test_an_old_plural_whose_file_is_gone_stays_unknown(db_path, monkeypatch, tmp_path):
    db = _v6_plurals(db_path, monkeypatch, str(tmp_path / 'gone.po'))

    assert db.backfill_plural_layouts() == 0

    assert _layouts(db) == [None]


def test_the_layout_backfill_runs_once(db_path, monkeypatch, tmp_path):
    po_file = tmp_path / 'ru.po'
    db = _v6_plurals(db_path, monkeypatch, str(po_file))
    db.backfill_plural_layouts()
    # The file turns up later: done is done.
    po_file.write_text(KDE_PO, encoding='utf-8')

    assert db.backfill_plural_layouts() == 0
    assert _layouts(db) == [None]


def test_the_layout_backfill_runs_beside_the_server(db_path, monkeypatch, tmp_path):
    import threading

    po_file = tmp_path / 'ru.po'
    po_file.write_text(KDE_PO, encoding='utf-8')
    db = _v6_plurals(db_path, monkeypatch, str(po_file))
    release = threading.Event()
    reading = threading.Event()
    real = tmdb.po_header_layout

    def slow(path):
        reading.set()
        release.wait(10)
        return real(path)

    monkeypatch.setattr(tmdb, 'po_header_layout', slow)

    thread = db.start_backfill()
    assert reading.wait(10) and thread.is_alive()
    # The server answers meanwhile.
    assert db.translate_unit('Delete %d file', 'en', 'ru')
    release.set()
    thread.join(10)

    assert _layouts(db) == [KDE_LAYOUT]


# A unit of thousands of words (#4732) #


def _long_text(lines):
    return '\n'.join('Line %d of the document, word%d' % (i, i) for i in range(lines))


def test_a_source_too_long_for_any_candidate_gets_none_without_an_error(db_path, caplog):
    db = tmdb.TMDB(db_path)
    text = _long_text(5000)
    db.add_list([{'source': text, 'target': text.upper(), 'context': ''}], 'en', 'af')

    with caplog.at_level(logging.WARNING):
        assert db.translate_unit(text, 'en', 'af') == []

    assert caplog.text == ''


def test_a_long_source_is_searched_on_some_of_its_words(db_path):
    db = tmdb.TMDB(db_path, max_length=10**6)
    text = _long_text(5000)
    db.add_list(
        [
            {'source': text, 'target': 'Lang', 'context': ''},
            {'source': 'Line of the document', 'target': 'Kort', 'context': ''},
        ],
        'en',
        'af',
    )

    (match,) = db.translate_unit(text + ' more', 'en', 'af')

    assert match['target'] == 'Lang'


def test_fulltext_words_are_each_word_once_the_longest_if_too_many(monkeypatch):
    monkeypatch.setattr(tmdb, 'FULLTEXT_WORDS', 3)

    assert tmdb._fulltext_words('an apple, an apple; pear') == ['apple', 'pear']
    assert tmdb._fulltext_words('abc defgh ijkl mnopqr abc stu') == ['defgh', 'ijkl', 'mnopqr']


def _scores(db, query, lang='en'):
    return [(m['source'], m['quality']) for m in db.translate_unit(query, lang, 'af')]


@pytest.mark.parametrize('fulltext', [True, False])
@pytest.mark.parametrize(
    ('stored', 'query', 'lang'),
    [
        ('Open file', 'OPEN FILE', 'en'),
        ('Open file', 'open file', 'en'),
        ('Straße', 'STRASSE', 'de'),
        ('Straße', 'Strasse', 'de'),
        ('Strasse', 'Straße', 'de'),
        ('café', 'CAFÉ', 'fr'),
        ('Kayıt', 'KAYIT', 'tr'),
        ('İptal', 'iptal', 'tr'),
        ('Αρχείο', 'ΑΡΧΕΙΟ', 'el'),
        (LONG, LONG.upper(), 'en'),
        ('Die Straße öffnet heute wieder', 'DIE STRASSE ÖFFNET HEUTE WIEDER', 'de'),
        ('Kayıt dosyası açıldı ve kaydedildi', 'KAYIT DOSYASI AÇILDI VE KAYDEDİLDİ', 'tr'),
        ('Το αρχείο ανοίγει τώρα αμέσως', 'ΤΟ ΑΡΧΕΙΟ ΑΝΟΙΓΕΙ ΤΩΡΑ ΑΜΕΣΩΣ', 'el'),
        ('ΤΟ ΑΡΧΕΙΟ ΑΝΟΙΓΕΙ ΤΩΡΑ ΑΜΕΣΩΣ', 'Το αρχείο ανοίγει τώρα αμέσως', 'el'),
        ('Größe ändern über Menü', 'GRÖSSE ÄNDERN ÜBER MENÜ', 'de'),
    ],
)
def test_a_source_differing_only_in_case_is_a_near_exact_match(
    db_path, stored, query, lang, fulltext
):
    db = tmdb.TMDB(db_path)
    db.add_dict({'source': stored, 'target': 'x', 'context': ''}, lang, 'af')
    db.fulltext = db.fulltext and fulltext

    assert _scores(db, query, lang) == [(stored, 97)]


@pytest.mark.parametrize(
    ('stored', 'query', 'lang'),
    [('Kayıt', 'KAYIT', 'en'), ('Αρχείο', 'ΑΡΧΕΙΟ', 'en'), ('café', 'cafe', 'fr')],
)
def test_other_differences_stay_fuzzy(db_path, stored, query, lang):
    db = tmdb.TMDB(db_path)
    db.add_dict({'source': stored, 'target': 'x', 'context': ''}, lang, 'af')

    assert all(quality < 90 for _source, quality in _scores(db, query, lang))


def test_an_exact_match_scores_above_one_differing_in_case(db_path):
    db = tmdb.TMDB(db_path)
    for source in ('OPEN FILE', 'Open file', 'Open files', 'Open the file'):
        db.add_dict({'source': source, 'target': source, 'context': ''}, 'en', 'af')

    assert _scores(db, 'Open file') == [('Open file', 100), ('OPEN FILE', 97), ('Open files', 90)]


def test_ordinary_fuzzy_matches_score_and_order_as_before(db_path):
    db = tmdb.TMDB(db_path)
    sources = ['Open the selected file', 'Open the selected files now', 'Save the selected file']
    for source in sources:
        db.add_dict({'source': source, 'target': source, 'context': ''}, 'en', 'af')
    query = 'Open the selected files'

    levenshtein = LevenshteinComparer(1000)
    expected = sorted(
        ((round(levenshtein.similarity(query, s, 0)), s) for s in sources), reverse=True
    )

    assert _scores(db, query) == [
        (source, quality) for quality, source in expected if quality >= 75
    ]


def test_fulltext_terms_add_each_words_forms_the_index_tells_apart():
    terms = tmdb._fulltext_terms(['STRASSE', 'open', 'KAYIT'], 'tr')

    assert terms == ['STRASSE', 'straße', 'open', 'KAYIT', 'kayıt']


def test_fulltext_terms_are_capped(monkeypatch):
    monkeypatch.setattr(tmdb, 'FULLTEXT_TERMS', 2)

    assert tmdb._fulltext_terms(['Straße', 'apple'], 'de') == ['Straße', 'STRASSE']
