#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Unit state lists from a store in memory, and filling the stats cache
in a child process so that opening or saving a file doesn't wait for it.

A child process, not a thread: the fill holds the GIL long enough to
make opening a large file twice as slow."""

import logging
import subprocess
import sys

from virtaal.support import statsdb

_running = None
# One fill at a time: SQLite allows a single writer.
_queued = []


def state_lists(store):
    """Return what C{StatsCache().filestatestats(..., extended=True)}
    returns, from the units in C{store}."""
    stats = statsdb.emptyfilestats()
    stats['extended'] = {}
    for index, unit in enumerate(store.units):
        if not unit.istranslatable():
            continue
        stats[statsdb.state_strings[statsdb.statefordb(unit)]].append(index)
        stats['extended'].setdefault(unit.get_state_id(), []).append(index)
        stats['total'].append(index)
    return stats


def fill_in_background(filename):
    """Cache the stats of the file on disk at C{filename}."""
    _queued.append(filename)
    if _running is None:
        _start_next()


def fill(filenames):
    """Cache the stats of several files on disk, and wait for it."""
    wait()
    statsdb.StatsCache()  # Sets the default stats file.
    if statsdb.StatsCache.defaultfile in statsdb.StatsCache.unusable_files:
        return
    from virtaal.support import helperprocess

    subprocess.run(
        helperprocess.command(__name__) + [statsdb.StatsCache.defaultfile, '-'],
        input='\n'.join(filenames).encode('utf-8'),
        env=helperprocess.env(),
        check=False,
    )


def wait():
    """Block until every queued fill is done. Call before using
    L{statsdb.StatsCache}, which a running fill may have locked."""
    while _running is not None:
        _running.wait()
        _finish(_running)


def _start_next():
    global _running
    _running = None
    if not _queued:
        return
    from gi.repository import GLib

    from virtaal.support import helperprocess

    statsdb.StatsCache()  # Sets the default stats file.
    if statsdb.StatsCache.defaultfile in statsdb.StatsCache.unusable_files:
        # A child couldn't use it either, and has no memory to share.
        _queued.clear()
        return
    filename = _queued.pop(0)
    _running = subprocess.Popen(
        helperprocess.command(__name__) + [statsdb.StatsCache.defaultfile, filename],
        env=helperprocess.env(),
    )
    _running.filename = filename
    GLib.timeout_add(200, _poll, _running)


def _poll(proc):
    if proc is not _running:
        return False
    if proc.poll() is None:
        return True
    _finish(proc)
    return False


def _finish(proc):
    if proc.returncode:
        logging.debug(
            'Could not cache the stats of %s (exit status %s)', proc.filename, proc.returncode
        )
    _start_next()


def _fill_files(statsfile, filenames):
    """Cache each file; returns the number that couldn't be."""
    failed = 0
    for filename in filenames:
        try:
            statsdb.StatsCache(statsfile).filestatestats(filename)
        except Exception:
            failed += 1
    return failed


if __name__ == '__main__':
    statsfile, filename = sys.argv[1:]
    # "-": one file name per line on stdin.
    if filename == '-':
        filenames = [line for line in sys.stdin.buffer.read().decode('utf-8').split('\n') if line]
    else:
        filenames = [filename]
    if _fill_files(statsfile, filenames):
        sys.exit(1)
