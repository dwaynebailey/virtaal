#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import sqlite3

import pytest

from virtaal.support import shareddb


def test_remove_deletes_sqlites_files_beside_the_database(tmp_path):
    path = str(tmp_path / 'stats.db')
    for name in (path, path + '-wal', path + '-shm'):
        open(name, 'wb').close()

    shareddb.remove(path)

    assert list(tmp_path.iterdir()) == []


def test_a_file_that_isnt_a_database_raises_on_connect(tmp_path):
    path = tmp_path / 'stats.db'
    path.write_text('not a database\n')

    with pytest.raises(sqlite3.DatabaseError):
        shareddb.connect(str(path))


def test_set_aside_if_damaged_moves_a_damaged_file_and_its_journal(tmp_path):
    path = tmp_path / 'tm.db'
    path.write_bytes(b'not a database' * 300)
    (tmp_path / 'tm.db-wal').write_bytes(b'wal')

    kept = shareddb.set_aside_if_damaged(str(path))

    assert kept == str(tmp_path / 'tm-damaged.db')
    assert not path.exists()
    assert (tmp_path / 'tm-damaged.db').read_bytes() == b'not a database' * 300
    assert (tmp_path / 'tm-damaged.db-wal').read_bytes() == b'wal'
    with shareddb.connect(str(path)) as connection:
        connection.execute('SELECT count(*) FROM sqlite_master').fetchone()


def test_set_aside_if_damaged_never_replaces_an_earlier_damaged_file(tmp_path):
    path = tmp_path / 'tm.db'
    (tmp_path / 'tm-damaged.db').write_bytes(b'first')
    path.write_bytes(b'second' * 1000)

    kept = shareddb.set_aside_if_damaged(str(path))

    assert kept == str(tmp_path / 'tm-damaged-2.db')
    assert (tmp_path / 'tm-damaged.db').read_bytes() == b'first'


@pytest.mark.parametrize('content', [None, b'', 'database'])
def test_set_aside_if_damaged_leaves_a_missing_empty_or_good_file(tmp_path, content):
    path = tmp_path / 'tm.db'
    if content == 'database':
        sqlite3.connect(str(path)).execute('CREATE TABLE t (x)').connection.close()
    elif content is not None:
        path.write_bytes(content)

    assert shareddb.set_aside_if_damaged(str(path)) is None

    assert sorted(p.name for p in tmp_path.iterdir()) == ([] if content is None else ['tm.db'])


def test_set_aside_if_damaged_leaves_a_locked_file(tmp_path):
    path = tmp_path / 'tm.db'
    sqlite3.connect(str(path)).execute('CREATE TABLE t (x)').connection.close()
    locker = sqlite3.connect(str(path))
    locker.execute('BEGIN EXCLUSIVE')
    try:
        assert shareddb.set_aside_if_damaged(str(path), timeout=0) is None
    finally:
        locker.close()

    assert path.exists()
