#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Send finished units as you go: a server's option, off by default
(#3337). No GTK here.

With it on, each save of a work copy from that server queues the units
saved at Translated or above (finished()) and sends them in the
background, through Submit's own Submission: the same per-unit change,
conflict check and pacing. Drafts and Needs work units wait for Submit,
and Submit still sends whatever is left. Without it, Save never sends.

The queue is the side record's "queue" (the units' ids), written at
each save, so it outlasts a restart: opening the work copy again sends
what is left. A run that can't reach the server (or runs out of budget)
keeps its queue and tries again later, sooner after each save or
when the network comes back (network_changed()); the wait doubles from
RETRY_FIRST to RETRY_MAX.

A unit someone changed on the server too is never decided here: it
leaves the queue, marked "conflict", for the next Submit or Update to
ask about, and on_conflicts(count) says so. A unit the server refuses
leaves it with its reason, as Submit's.
"""

import time

from translate.storage.workflow import StateEnum

from .submit import BUDGET, FAILED, Submission, _later

RETRY_FIRST = 30
"""Seconds before the first try again after no reply."""
RETRY_MAX = 600
"""The longest wait before trying again."""


def finished(target, state):
    """Whether a unit with target (forms) and state (None for a format
    with no states) goes as you go: Translated or above."""
    return any(target) and (state is None or state >= StateEnum.UNREVIEWED)


class Trickler:
    """Sends the finished units of the work copies open, as each is
    saved: one at a time, the others waiting their turn.

    server_of(copy) is the copy's Server (servers.py) or None;
    on_sent(Result) follows each run that sent anything, on_conflicts(
    count) each that found units changed on the server too."""

    schedule = staticmethod(_later)
    """Calls callback() after seconds: GLib's timer, or a test's."""
    clock = staticmethod(time.monotonic)

    def __init__(self, server_of, on_sent, on_conflicts):
        self.server_of = server_of
        self.on_sent = on_sent
        self.on_conflicts = on_conflicts
        self.copy = None
        self.platform = None
        self.submission = None
        self.session = None
        self._retry = RETRY_FIRST
        self._timer = 0
        """Which retry is due: a newer one, or a stop, drops older ones."""
        self._next = []
        """The work copies waiting for the one sending."""
        self._waiting = []
        """The work copies whose queues wait to try again."""
        self._retry_due = False
        self._woken = None
        """When the network last brought a try."""

    @property
    def running(self):
        return self.submission is not None

    def _server(self, copy):
        server = self.server_of(copy) if copy is not None else None
        return server if server is not None and server.send_finished else None

    def queue(self, copy):
        """The indexes in copy's side record of the units queued."""
        ids = set(copy.record.get('queue') or ())
        return [r for r, entry in enumerate(copy.units) if entry['id'] in ids]

    def saved(self, copy):
        """copy (attached to the open store) was saved: queues its
        finished units, those still to send, and sends them."""
        if self._server(copy) is None:
            return
        queued = []
        for r in copy.pending():
            entry = copy.units[r]
            if entry.get('conflict') or entry.get('error'):
                continue
            if finished(*copy.mine(r)):
                queued.append(entry['id'])
        copy.record['queue'] = queued
        copy.write_record()
        self._retry = RETRY_FIRST
        self.start(copy)

    def start(self, copy):
        """Sends what copy's queue holds, after the run on its way."""
        server = self._server(copy)
        if server is None:
            return
        only = set(self.queue(copy))
        if not only:
            return
        if self.running:
            if copy is not self.copy and copy not in self._next:
                self._next.append(copy)
            return
        if copy in self._waiting:
            self._waiting.remove(copy)
        self.copy = copy
        self.platform = server.platform
        self.session = server.session()
        self.submission = Submission(
            copy, server.platform, self.session, _quiet, self._conflicts, self._done, only
        )
        self.submission.start()

    def stop(self):
        """Stops at once, the queue kept: Submit or Update takes over,
        or the file is closed. A reply on its way is dropped."""
        self._timer += 1
        self._waiting = []
        self._next = []
        self._retry_due = False
        if self.submission is not None:
            self.submission.cancel()
            self.submission.finished = True
            self.session.cancel()
        self.submission = self.session = None

    def _conflicts(self, conflicts, resolve):
        for conflict in conflicts:
            self.copy.units[conflict.index]['conflict'] = True
        resolve({})

    def _done(self, result):
        copy = self.copy
        self.submission = self.session = None
        queued = set(copy.record.get('queue') or ())
        left = [
            copy.units[r]['id']
            for r in copy.pending()
            if copy.units[r]['id'] in queued
            and not copy.units[r].get('conflict')
            and not copy.units[r].get('error')
        ]
        copy.record['queue'] = left
        copy.write_record()
        if result.sent or result.file_changed:
            self.on_sent(result)
        if result.skipped:
            self.on_conflicts(result.skipped)
        if left and result.stop in (FAILED, BUDGET):
            self._try_again(copy, result)
        else:
            self._retry = RETRY_FIRST
        if self._next and not self.running:
            self.start(self._next.pop(0))

    def _try_again(self, copy, result):
        if copy not in self._waiting:
            self._waiting.append(copy)
        if self._retry_due:
            # It goes with the retry already due.
            return
        wait = self._retry
        if result.stop == BUDGET and result.reset_in is not None:
            wait = max(wait, result.reset_in)
        self._retry = min(self._retry * 2, RETRY_MAX)
        self._timer += 1
        timer = self._timer
        self._retry_due = True

        def again():
            if timer == self._timer:
                self._wake()

        self.schedule(wait, again)

    def _wake(self):
        """Every queue waiting to try again, one after the other."""
        self._retry_due = False
        for copy in list(self._waiting):
            self.start(copy)

    def network_changed(self, available):
        """Gio.NetworkMonitor's network-changed: a queue waiting to try
        again goes now when the network is available, at most once each
        RETRY_FIRST, for a monitor that says so on every change."""
        if not available or not self._waiting or self.running:
            return
        now = self.clock()
        if self._woken is not None and now - self._woken < RETRY_FIRST:
            return
        self._woken = now
        self._timer += 1
        self._wake()


def network_monitor():
    """Gio's network monitor, or None where there is none."""
    try:
        from gi.repository import Gio

        return Gio.NetworkMonitor.get_default()
    except Exception:
        return None


def _quiet(_number, _total):
    """No progress shown: it runs in the background."""
