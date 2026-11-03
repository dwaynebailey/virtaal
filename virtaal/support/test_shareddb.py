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
