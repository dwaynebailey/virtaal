#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Test doubles shared by the downloader and database tests."""

import sqlite3
import threading


class FakeClient:
    """An HTTPClient stand-in that records each get() call instead of
    making a request, so a test can drive a callback chain step by
    step."""

    def __init__(self):
        self.calls = []

    def get(self, url, callback, error_callback=None, download=False):
        self.calls.append((url, callback, error_callback))

    def last_url(self):
        return self.calls[-1][0]


def scale_lock_waits(monkeypatch, factor=100):
    """Make every SQLite lock wait C{factor} times shorter: the 5 s
    default is then 0.05 s."""
    connect = sqlite3.dbapi2.connect

    def scaled(*args, timeout=5.0, **kwargs):
        return connect(*args, timeout=timeout / factor, **kwargs)

    monkeypatch.setattr(sqlite3.dbapi2, 'connect', scaled)


class OtherProcess:
    """Another process's connection to C{path}, holding a write lock or
    an open read until C{seconds} have passed or it is closed."""

    def __init__(self, path, seconds=None, write=True):
        self.connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        if write:
            self.connection.execute('BEGIN IMMEDIATE')
        else:
            self.connection.execute('BEGIN')
            self.connection.execute('SELECT count(*) FROM sqlite_master').fetchall()
        self.timer = None
        if seconds is not None:
            self.timer = threading.Timer(seconds, self.close)
            self.timer.start()

    def close(self):
        if self.connection.in_transaction:
            self.connection.execute('COMMIT')
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        if self.timer:
            self.timer.join()
        else:
            self.close()
