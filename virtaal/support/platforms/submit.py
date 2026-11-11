#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Submitting a work copy's changes to its server (#3337). No GTK here.

Submit runs once the file is saved (Save itself never sends), through
the Session of the work copy's server:

1. The lock. A locked translation stops Submit before anything is sent.
2. The changes on the server: one query for the units changed since the
   download (less SLACK, for clocks that differ). Each changed unit of
   the file is then compared with its baseline in the side record
   (classify()):
   - the server's unit has the baseline's last_updated, or isn't among
     those changed: it is sent;
   - the server's text and state are still the baseline's (it was
     touched, not changed, or this Virtaal sent it): it is sent, with
     the newer last_updated as its baseline;
   - the server already has this unit's text and state: nothing is
     sent, and that becomes the baseline;
   - else someone changed it on the server: a conflict, not sent.
3. Conflicts, if any, go to on_conflicts(); each answer is KEEP_MINE
   (sent anyway, over theirs), TAKE_THEIRS (the server's text and state
   into the file, and as the baseline) or SKIP (left as it is, to come
   up again next time).
4. The units are sent one at a time. Each one sent is noted in the side
   record at once, so Submit, cut short by Cancel, the budget, an error
   or a crash, sends only the rest next time. A unit the server refuses
   (400, 403) is noted with its reason and not tried again until the
   next Submit. 423 (locked) and anything without a reply stop Submit.
5. Pacing. Before each unit, the budget the server reports
   (Session.budget(), else a count against the platform's rate_limit)
   must keep RESERVE requests back. Below that, Submit waits for the
   count to start again when that is at most MAX_WAIT seconds away,
   else it stops, resumable, saying when. The client itself waits out
   a 429's Retry-After.
6. Refresh. One more query, for the units changed since Submit started,
   gives the units sent their last_updated, and the state the server
   really stored when it is another kind (a check it enforces can store
   "needs editing" for "translated"), which the file then shows. A
   state the server has only a coarser one for (Rejected as "needs
   editing" on an older Weblate) stays as chosen.

Someone can still change a unit between step 2 and its turn in step 4:
the platform has no way to refuse a change made on an old baseline.
"""

import datetime
import time
from collections import namedtuple

from . import HTTP, states
from .workcopy import text_hash

SEND, SAME, CONFLICT = 'send', 'same', 'conflict'
KEEP_MINE, TAKE_THEIRS, SKIP = 'mine', 'theirs', 'skip'

# Why a Submit stopped short.
LOCKED = 'locked'
CANCELLED = 'cancelled'
BUDGET = 'budget'
FAILED = 'failed'

_PER_UNIT = (400, 403)

Conflict = namedtuple('Conflict', 'index source mine theirs')
"""A unit changed both here and on the server: its index in the side
record, its source, and the target forms of each side."""


def classify(entry, mine, remote):
    """SEND, SAME or CONFLICT for a changed unit: entry its side record
    entry (the baseline), mine its (text hash, state code) in the file,
    remote the server's RemoteUnit if it changed since the download."""
    if remote is None:
        return SEND
    if remote.last_updated is not None and remote.last_updated == entry.get('last_updated'):
        return SEND
    theirs = (text_hash(remote.target), remote.code)
    if theirs == (entry['target_hash'], entry['state']):
        return SEND
    if theirs == mine:
        return SAME
    return CONFLICT


def _later(seconds, callback):
    from gi.repository import GLib

    GLib.timeout_add(int(seconds * 1000), lambda: callback() and False)


def _shifted(text, seconds):
    """An ISO 8601 UTC time text, seconds earlier."""
    try:
        when = datetime.datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError):
        when = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return (when - datetime.timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')


class Pacer:
    """Whether the next request fits the budget: the server's, or for a
    server that doesn't say, a count of this Submit's requests against
    (requests, seconds) of its platform's rate_limit."""

    def __init__(self, limit=None, reserve=20, max_wait=120, clock=time.monotonic):
        self.limit = limit
        self.reserve = reserve
        self.max_wait = max_wait
        self.clock = clock
        self._start = None
        self._used = 0

    def note(self):
        """One request made."""
        now = self.clock()
        if self._start is None or (self.limit and now - self._start >= self.limit[1]):
            self._start, self._used = now, 0
        self._used += 1

    def _budget(self, budget):
        if budget is not None:
            return budget
        if not self.limit or self._start is None:
            return None
        requests, seconds = self.limit
        return requests - self._used, max(0.0, seconds - (self.clock() - self._start))

    def delay(self, budget):
        """Seconds to wait before the next request (0 for none), or None
        to stop; budget is the server's (left, seconds to reset) or
        None."""
        budget = self._budget(budget)
        if budget is None or budget[0] > self.reserve:
            return 0
        if budget[1] <= self.max_wait:
            return budget[1]
        return None

    def reset_in(self, budget):
        """Seconds until the budget starts again, or None if unknown."""
        budget = self._budget(budget)
        return None if budget is None else budget[1]


class Result:
    """What a Submit did."""

    def __init__(self):
        self.total = 0
        """Changed units there were to submit."""
        self.sent = 0
        """Units the server has now as in the file: sent, or already so."""
        self.taken = 0
        """Units changed on the server whose text replaced the file's."""
        self.skipped = 0
        """Conflicts left for later."""
        self.failures = {}
        """{index in the side record: the server's reason} of the units
        it refused."""
        self.stop = None
        """None, or why Submit stopped short: LOCKED, CANCELLED, BUDGET
        or FAILED."""
        self.error = None
        """The PlatformError that stopped it."""
        self.reset_in = None
        """With BUDGET, the seconds until the server's budget starts
        again, if known."""
        self.file_changed = False
        """The file's units changed (taken, or a state the server
        stored), so it needs saving."""

    @property
    def left(self):
        """Changed units not on the server yet."""
        return self.total - self.sent - self.taken


class Submission:
    """One Submit of copy (a WorkCopy attached to the open store) to its
    platform's server through session.

    on_progress(number, total) comes before each unit is sent;
    on_conflicts(conflicts, resolve) must lead to resolve({index:
    KEEP_MINE, TAKE_THEIRS or SKIP}) (an index left out is SKIP), at
    once or later; on_done(Result) ends it."""

    SLACK = 3600
    """Seconds by which the server's clock may differ from this one."""
    RESERVE = 20
    """Requests of the server's budget Submit leaves for other work."""
    MAX_WAIT = 120
    """The longest wait for the budget to start again; beyond it, Submit
    stops."""

    schedule = staticmethod(_later)

    def __init__(self, copy, platform, session, on_progress, on_conflicts, on_done, only=None):
        self.only = only
        """The indexes in the side record to send, if not every change."""
        self.copy = copy
        self.platform = platform
        self.session = session
        self.on_progress = on_progress
        self.on_conflicts = on_conflicts
        self.on_done = on_done
        self.pacer = None
        self.result = Result()
        self.cancelled = False
        self.finished = False
        self._queue = []
        self._sending = 0
        self._sent = {}
        self._remote = {}
        self._started_at = None

    def _where(self):
        record = self.copy.record
        return record['project'], record.get('component'), record['language']

    def start(self):
        self.pacer = Pacer(self.platform.rate_limit, self.RESERVE, self.MAX_WAIT)
        pending = [r for r in self.copy.pending() if self.only is None or r in self.only]
        self.result.total = len(pending)
        self._pending = pending
        self._started_at = _now()
        if not pending:
            self._finish()
            return
        project, component, _language = self._where()
        self.pacer.note()
        self.session.locked(project, component, self._got_lock, self._failed)

    def cancel(self):
        """Stops before the next unit; the one on its way is still
        noted when its reply comes."""
        self.cancelled = True

    def _stopped(self):
        if self.cancelled:
            self._finish(CANCELLED)
        return self.cancelled

    def _got_lock(self, locked):
        if locked:
            self._finish(LOCKED)
            return
        if self._stopped():
            return
        project, component, language = self._where()
        since = _shifted(self.copy.record.get('downloaded_at'), self.SLACK)
        self.pacer.note()
        self.session.changed_since(
            project, component, language, since, self._got_changes, self._failed
        )

    def _mine(self, r):
        target, state = self.copy.mine(r)
        return target, state, self.platform.state_code(state, target)

    def _got_changes(self, remote_units):
        if self._stopped():
            return
        by_id = {unit.id: unit for unit in remote_units}
        conflicts = []
        for r in self._pending:
            entry = self.copy.units[r]
            remote = by_id.get(entry['id'])
            target, _state, code = self._mine(r)
            kind = classify(entry, (text_hash(target), code), remote)
            if kind == SAME:
                self.copy.adopt(r, remote)
                self.result.sent += 1
            elif kind == CONFLICT:
                self._remote[r] = remote
                source = self.copy.matched[r].source
                conflicts.append(Conflict(r, str(source), target, list(remote.target)))
            else:
                if remote is not None:
                    self.copy.units[r]['last_updated'] = remote.last_updated
                self._queue.append(r)
        self.copy.write_record()
        if conflicts:
            self.on_conflicts(conflicts, self.resolve)
        else:
            self._begin_sending()

    def resolve(self, decisions):
        """The answers to on_conflicts(): {index: KEEP_MINE, TAKE_THEIRS
        or SKIP}."""
        for r, remote in sorted(self._remote.items()):
            decision = decisions.get(r, SKIP)
            if decision == KEEP_MINE:
                self.copy.units[r]['last_updated'] = remote.last_updated
                self._queue.append(r)
            elif decision == TAKE_THEIRS:
                self.result.file_changed |= self.copy.take(r, remote)
                self.result.taken += 1
            else:
                self.result.skipped += 1
        self.copy.write_record()
        self._begin_sending()

    def _begin_sending(self):
        self._queue.sort()
        self._sending = len(self._queue)
        self._send_next()

    def _send_next(self):
        if self._stopped():
            return
        if not self._queue:
            self._refresh()
            return
        delay = self.pacer.delay(self.session.budget())
        if delay is None:
            self.result.reset_in = self.pacer.reset_in(self.session.budget())
            self._finish(BUDGET)
            return
        if delay > 0:
            self.schedule(delay, self._send_next)
            return
        r = self._queue[0]
        target, state, code = self._mine(r)
        done = self._sending - len(self._queue)
        self.on_progress(done + 1, self._sending)
        self.pacer.note()

        def sent(stored):
            self._queue.pop(0)
            self.copy.mark_sent(r, target, code if stored is None else stored, state)
            self._sent[r] = code if stored is None else stored
            self.result.sent += 1
            self._send_next()

        def failed(error):
            if error.status in _PER_UNIT:
                self._queue.pop(0)
                message = error.detail or str(error.status)
                self.copy.mark_failed(r, message)
                self.result.failures[r] = message
                self._send_next()
            else:
                self._failed(error)

        self.session.submit_unit(self.copy.units[r]['id'], target, code, sent, failed)

    def _failed(self, error):
        if error.status == 423:
            self._finish(LOCKED)
        elif error.kind == HTTP and error.status == 429:
            self.result.reset_in = self.pacer.reset_in(self.session.budget())
            self._finish(BUDGET)
        else:
            self._finish(FAILED, error)

    def _refresh(self):
        """Reads back the units sent: their last_updated, and the state
        the server stored."""
        if not self._sent:
            self._finish()
            return
        project, component, language = self._where()

        def got(remote_units):
            by_id = {unit.id: unit for unit in remote_units}
            for r in self._sent:
                remote = by_id.get(self.copy.units[r]['id'])
                if remote is None:
                    continue
                target, state, _code = self._mine(r)
                same_text = text_hash(remote.target) == text_hash(target)
                # A state the server only has a coarser one for stays.
                if same_text and states.coarse(remote.state) == states.coarse(state):
                    self.copy.units[r]['last_updated'] = remote.last_updated
                else:
                    self.result.file_changed |= self.copy.take(r, remote)
            self._finish()

        since = _shifted(self._started_at, self.SLACK)
        self.pacer.note()
        self.session.changed_since(
            project, component, language, since, got, lambda _error: self._finish()
        )

    def _finish(self, stop=None, error=None):
        if self.finished:
            return
        self.finished = True
        self.result.stop = stop
        self.result.error = error
        if stop is None and not self.copy.pending():
            self.copy.mark_submitted()
        else:
            self.copy.write_record()
        self.on_done(self.result)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
