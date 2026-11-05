#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The local TM server's database, against real SQLite files. Failures
come from SQLite itself: an authorizer refuses the statement."""

import logging
import os
import sqlite3
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from translate.storage import base, xliff

from virtaal.support import tmdb
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
    scale_lock_waits(monkeypatch)
    with OtherProcess(db_path, seconds=0.15):
        db = tmdb.TMDB(db_path)

        db.add_dict({'source': SHORT, 'target': 'Maak dit oop', 'context': ''}, 'en', 'af')

    assert _count(db, 'targets') == 1


def test_a_unit_is_added_while_another_process_writes(db_path, monkeypatch):
    scale_lock_waits(monkeypatch)
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

    assert db._user_version() == 1
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
    assert db._user_version() == 1
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
    assert tmdb.TMDB(db_path)._user_version() == 1


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
from virtaal.support import tmdb
time.sleep(max(0, float(sys.argv[2]) - time.time()))
tmdb.TMDB(sys.argv[1])
"""


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
    assert db._user_version() == 1
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
