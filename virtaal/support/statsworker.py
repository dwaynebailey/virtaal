#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Unit state lists from a store in memory, and filling the stats cache
in a child process so that opening or saving a file doesn't wait for it.
Quality checks of files not open run in a child process too.

A child process, not a thread: the fill holds the GIL long enough to
make opening a large file twice as slow."""

import json
import logging
import os
import subprocess
import sys
import tempfile

from virtaal.support import statsdb

_running = None
# One fill at a time: SQLite allows a single writer.
_queued = []
# The child checking files, if one is.
_checking = None


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


def fill_files_in_background(filenames, done):
    """Cache the stats of several files on disk in a child process, then
    call C{done()} from the main loop. Returns the child, or None (and
    starts nothing) when the stats cache can't be shared with one.

    Files whose stats are cached can be read from the cache meanwhile:
    the child commits each file as it goes."""
    from gi.repository import GLib

    from virtaal.support import helperprocess

    statsdb.StatsCache()  # Sets the default stats file.
    if statsdb.StatsCache.defaultfile in statsdb.StatsCache.unusable_files:
        return None
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.txt', delete=False) as f:
        f.write('\n'.join(filenames))
    proc = subprocess.Popen(
        helperprocess.command(__name__) + [statsdb.StatsCache.defaultfile, '--list', f.name],
        env=helperprocess.env(),
    )
    proc.done = done
    proc.listfile = f.name
    GLib.timeout_add(200, _poll_files, proc)
    return proc


def _poll_files(proc):
    if proc.poll() is None:
        return True
    _remove(proc.listfile)
    if proc.returncode:
        logging.debug('Could not cache the stats of every file (exit status %s)', proc.returncode)
    if proc.done is not None:
        proc.done()
    return False


def stop_files(proc):
    """Stop a child L{fill_files_in_background()} started; its C{done()}
    isn't called."""
    proc.done = None
    if proc.poll() is None:
        proc.terminate()


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


def check_in_background(filenames, checker, done):
    """Cache C{checker}'s failures for each file on disk in a child
    process, then call C{done(succeeded)} from the main loop. Starts
    nothing and returns False for a checker a child can't build, else
    the child. Replaces a child checking other files, or with another
    checker."""
    global _checking
    from virtaal.support import checkerspec, helperprocess

    spec = checkerspec.describe(checker)
    statsdb.StatsCache()  # Sets the default stats file.
    if spec is None or statsdb.StatsCache.defaultfile in statsdb.StatsCache.unusable_files:
        return False
    key = (json.dumps(spec, sort_keys=True), tuple(filenames))
    if _checking is not None and _checking.key == key:
        _checking.done = done
        return _checking
    stop_checking()
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.txt', delete=False) as f:
        f.write('\n'.join(filenames))
    proc = subprocess.Popen(
        helperprocess.command(__name__)
        + [statsdb.StatsCache.defaultfile, '--checks', key[0], f.name],
        env=helperprocess.env(),
    )
    proc.key = key
    proc.done = done
    proc.listfile = f.name
    _checking = proc
    from gi.repository import GLib

    GLib.timeout_add(200, _poll_checking, proc)
    return proc


def checking():
    """Whether a child is checking files."""
    return _checking is not None


def stop_checking(child=None):
    """Stop the child checking files; with child, only if it is that one."""
    global _checking
    if child is not None and child is not _checking:
        return
    proc, _checking = _checking, None
    if proc is None:
        return
    proc.terminate()
    proc.wait()
    _remove(proc.listfile)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _poll_checking(proc):
    global _checking
    if proc is not _checking:
        return False
    if proc.poll() is None:
        return True
    _checking = None
    _remove(proc.listfile)
    if proc.returncode:
        logging.debug('Could not check every file (exit status %s)', proc.returncode)
    proc.done(proc.returncode == 0)
    return False


def _check_files(statsfile, spec, filenames):
    """Cache the failures of each file; returns the number that couldn't
    be checked: all of them for a checker that can't be built the same."""
    from virtaal.support import checkerspec

    checker = checkerspec.build(spec)
    if checker is None:
        return len(filenames)
    failed = 0
    for filename in filenames:
        try:
            statsdb.StatsCache(statsfile).filechecks(filename, checker)
        except Exception:
            failed += 1
    return failed


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
    if sys.argv[2:3] == ['--checks']:
        statsfile, _flag, spec, listfile = sys.argv[1:]
        with open(listfile, encoding='utf-8') as f:
            names = [line for line in f.read().split('\n') if line]
        if _check_files(statsfile, json.loads(spec), names):
            sys.exit(1)
        sys.exit(0)
    if sys.argv[2:3] == ['--list']:
        statsfile, _flag, listfile = sys.argv[1:]
        with open(listfile, encoding='utf-8') as f:
            names = [line for line in f.read().split('\n') if line]
        sys.exit(1 if _fill_files(statsfile, names) else 0)
    statsfile, filename = sys.argv[1:]
    if _fill_files(statsfile, [filename]):
        sys.exit(1)
