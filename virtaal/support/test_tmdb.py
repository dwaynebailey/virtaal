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
from translate.storage import base, pypo, tmx, xliff

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
    assert db._user_version() == 1
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
