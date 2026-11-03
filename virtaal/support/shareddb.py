#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""SQLite files that several processes use at once (#1340): every Virtaal
process's TM server opens tm.db, and every process and its stats worker
stats.db."""

import contextlib
import logging
import os
from sqlite3 import dbapi2

logger = logging.getLogger(__name__)

BUSY_TIMEOUT = 30
"""Seconds to wait for another process's lock before "database is locked"."""


def connect(path):
    """Open the database at C{path}, waiting out other processes' locks."""
    connection = dbapi2.connect(path, timeout=BUSY_TIMEOUT)
    # A write-ahead log: readers don't hold up a writer, nor a writer
    # readers. It stays set in the file. Where SQLite can't use one (some
    # network or read-only filesystems) the file keeps its journal.
    try:
        (mode,) = connection.execute('PRAGMA journal_mode=WAL').fetchone()
    except dbapi2.OperationalError as e:
        mode = e
    except dbapi2.DatabaseError:
        connection.close()
        raise
    if mode != 'wal':
        logger.debug("%s doesn't use a write-ahead log: %s", path, mode)
    return connection


def is_locked(error):
    """Whether C{error} is another process's lock outlasting the wait."""
    # SQLITE_BUSY and SQLITE_LOCKED; sqlite_errorcode is Python 3.11+.
    return isinstance(error, dbapi2.OperationalError) and str(error).endswith('is locked')


def remove(path):
    """Delete the database at C{path} and SQLite's files beside it, which a
    new database there would otherwise take as its own."""
    os.unlink(path)
    for suffix in ('-wal', '-shm', '-journal'):
        # Missing, or (Windows) still open in another process.
        with contextlib.suppress(OSError):
            os.unlink(path + suffix)
