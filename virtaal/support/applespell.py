#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Starting macOS's spell checker service before enchant's AppleSpell
provider uses it.

The service quits when idle. The first check after that waits for it to
start, about a second, then gives up ("NSSpellServer
findMisspelledWordInString timed out") and reports the word misspelt,
on the main thread. A JavaScript for Automation script, its own process,
starts it and loads the languages instead."""

import logging

from gi.repository import GLib

OSASCRIPT = '/usr/bin/osascript'
WARM_UP = (
    'function run(argv) {'
    ' ObjC.import("AppKit");'
    ' var checker = $.NSSpellChecker.sharedSpellChecker;'
    ' for (var i = 0; i < argv.length; i++) {'
    '  checker.setLanguage(argv[i]);'
    '  checker.checkSpellingOfStringStartingAt("a", 0);'
    ' }'
    ' return "ok";'
    '}'
)
# Longer than the service takes to start; a warm-up still running then
# counts as failed.
TIMEOUT_SECONDS = 15


class WarmUp:
    """Warms the service up for languages once; calls each on_done(ok)
        when done."""

    def __init__(self, spawn=None, timeout_add=None):
        self._spawn = spawn or _spawn
        self._timeout_add = timeout_add or GLib.timeout_add_seconds
        self._ready = set()
        self._failed = set()
        self._waiting = {}  # language -> callbacks

    def is_ready(self, language):
        return language in self._ready

    def has_failed(self, language):
        return language in self._failed

    def request(self, language, on_done):
        """Calls on_done(ok) once language is warm, or at once if it is."""
        if language in self._ready or language in self._failed:
            on_done(language in self._ready)
            return
        if language in self._waiting:
            self._waiting[language].append(on_done)
            return
        self._waiting[language] = [on_done]
        finished = []

        def done(ok):
            if finished:
                return
            finished.append(ok)
            (self._ready if ok else self._failed).add(language)
            if not ok:
                logging.warning("macOS's spell checker didn't start for %s", language)
            for callback in self._waiting.pop(language, []):
                callback(ok)
        try:
            self._spawn([OSASCRIPT, '-l', 'JavaScript', '-e', WARM_UP, language], done)
        except GLib.Error as e:
            logging.debug("Couldn't start %s: %s", OSASCRIPT, e)
            done(False)
            return
        self._timeout_add(TIMEOUT_SECONDS, lambda: done(False) or False)


def _spawn(argv, on_exit):
    """Runs argv without waiting; on_exit(succeeded) when it exits."""
    flags = (GLib.SpawnFlags.DO_NOT_REAP_CHILD | GLib.SpawnFlags.STDOUT_TO_DEV_NULL
             | GLib.SpawnFlags.STDERR_TO_DEV_NULL)
    pid, _stdin, _stdout, _stderr = GLib.spawn_async(argv, flags=flags)

    def exited(pid, status):
        GLib.spawn_close_pid(pid)
        check = getattr(GLib, 'spawn_check_wait_status', None) or GLib.spawn_check_exit_status
        try:
            ok = check(status)
        except GLib.Error:
            ok = False
        on_exit(ok)
    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, exited)
