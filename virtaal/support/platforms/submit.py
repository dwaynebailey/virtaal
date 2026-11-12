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
   A refusal that says what the user may not do (PlatformError's
   refused: REVIEW, approving) is noted in the side record too, and
   Reviewed is offered no more on that project.
   A unit whose text is still that of a suggestion accepted in Virtaal
   (WorkCopy.accepted()) is accepted on the server instead
   (Session.accept_suggestion(), approved too when it is Reviewed), so
   the suggester keeps the credit; its state follows when it is another.
   A suggestion no longer there (GONE) is a refusal of the unit's own,
   which goes as an edit next time.
   For a server whose session sends as_suggestions, each unit goes as a
   suggestion instead (Session.submit_suggestion()), its state aside,
   and the server's text stays its baseline (WorkCopy.mark_suggested()).
6. Refresh. One more query, for the units changed since Submit started,
   gives the units sent their last_updated, and the state the server
   really stored when it is another kind (a check it enforces can store
   "needs editing" for "translated"), which the file then shows, and
   whether each fails a check there. A state the server has only a
   coarser one for (Rejected as "needs editing" on an older Weblate)
   stays as chosen.

Someone can still change a unit between step 2 and its turn in step 4:
the platform has no way to refuse a change made on an old baseline.
"""

import datetime
import time
from collections import namedtuple

from translate.storage.workflow import StateEnum

from . import GONE, HTTP, states
from .workcopy import text_hash

SEND, SAME, CONFLICT = 'send', 'same', 'conflict'
KEEP_MINE, TAKE_THEIRS, SKIP = 'mine', 'theirs', 'skip'

# Why a Submit stopped short.
LOCKED = 'locked'
CANCELLED = 'cancelled'
BUDGET = 'budget'
FAILED = 'failed'

_PER_UNIT = (400, 403)

Conflict = namedtuple('Conflict', 'index source mine theirs where', defaults=(None,))
"""A unit changed both here and on the server: its index in the side
record, its source, the target forms of each side, and with several
files, the label of the file it is in."""


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

    def __init__(
        self, copy, platform, session, on_progress, on_conflicts, on_done, only=None, pacer=None
    ):
        self.only = only
        """The indexes in the side record to send, if not every change."""
        self.copy = copy
        self.platform = platform
        self.session = session
        self.on_progress = on_progress
        self.on_conflicts = on_conflicts
        self.on_done = on_done
        self.pacer = pacer
        """The Pacer, shared by the Submissions of one server's files."""
        self.on_checked = None
        """If set, on_checked(conflicts, go) follows the check for
        changes on the server in place of on_conflicts(), and go(decisions)
        (or go(None) when there are none) sends: for a Batch to ask about
        every file's conflicts at once and send file by file."""
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
        if self.pacer is None:
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
        if self.on_checked is not None:
            self.on_checked(conflicts, self._go)
        elif conflicts:
            self.on_conflicts(conflicts, self.resolve)
        else:
            self._begin_sending()

    def _go(self, decisions):
        if decisions is not None:
            self.decide(decisions)
        self._begin_sending()

    def resolve(self, decisions):
        """The answers to on_conflicts(): {index: KEEP_MINE, TAKE_THEIRS
        or SKIP}."""
        self.decide(decisions)
        self._begin_sending()

    def decide(self, decisions):
        """Notes the answers to the conflicts, without sending."""
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
        self._remote = {}
        self.copy.write_record()

    @property
    def to_send(self):
        """How many units wait to be sent."""
        return len(self._queue)

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
        done = self._sending - len(self._queue)
        self.on_progress(done + 1, self._sending)
        self.pacer.note()
        self._send(self._queue[0])

    def _send(self, r):
        """Sends units[r], the first in the queue."""
        target, state, code = self._mine(r)

        def sent(stored):
            self._queue.pop(0)
            self.copy.mark_sent(r, target, code if stored is None else stored, state)
            self._sent[r] = code if stored is None else stored
            self.result.sent += 1
            self._send_next()

        def suggested(_reply):
            self._queue.pop(0)
            self.copy.mark_suggested(r, target)
            self.result.sent += 1
            self._send_next()

        unit_id = self.copy.units[r]['id']
        accepted = self.copy.accepted(r, target)
        if self.session.as_suggestions:
            self.session.submit_suggestion(unit_id, target, suggested, self._unit_failed(r))
        elif accepted is not None:
            self._accept(r, accepted, (target, state, code), sent)
        else:
            self.session.submit_unit(unit_id, target, code, sent, self._unit_failed(r))

    def _unit_failed(self, r):
        """on_error for a request about units[r]: a refusal of this unit
        alone is noted and Submit goes on; else it stops."""

        def failed(error):
            if error.status not in _PER_UNIT and error.kind != GONE:
                self._failed(error)
                return
            self._queue.pop(0)
            if error.kind == GONE:
                # Sent as an edit of its own next time.
                self.copy.forget_accepted(r)
                # l10n: Why a unit wasn't submitted: the suggestion accepted in Virtaal was accepted, rejected or withdrawn on the translation server meanwhile
                message = _('The suggestion is no longer on the server.')
            else:
                message = error.detail or str(error.status)
            self.copy.mark_failed(r, message)
            if error.refused:
                self.copy.refuse(error.refused)
            self.result.failures[r] = message
            self._send_next()

        return failed

    def _accept(self, r, suggestion_id, mine, sent):
        """Accepts the suggestion units[r] took on the server (approved
        too when it is Reviewed here), so its suggester keeps the
        credit; then sends its state too if it is another."""
        target, state, code = mine
        approve = state == StateEnum.FINAL
        stored = self.platform.state_code(
            StateEnum.FINAL if approve else StateEnum.UNREVIEWED, target
        )
        unit_id = self.copy.units[r]['id']

        def accepted(_reply):
            if code == stored:
                sent(stored)
            else:
                self.pacer.note()
                self.session.submit_unit(unit_id, target, code, sent, self._unit_failed(r))

        self.session.accept_suggestion(suggestion_id, approve, accepted, self._unit_failed(r))

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
                    self.copy.note_checks(r, remote)
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


_BREAKS = (BUDGET, FAILED, CANCELLED)
"""Why a file's Submit stopped that stops the files after it too: a
lock is the file's own."""


class BatchResult(Result):
    """What a Submit of several files did: their Results added up, each
    file's in results, and the stop that broke it off, if any."""

    def __init__(self, results):
        super().__init__()
        self.results = results
        for n, result in enumerate(results):
            self.total += result.total
            self.sent += result.sent
            self.taken += result.taken
            self.skipped += result.skipped
            self.file_changed |= result.file_changed
            self.failures.update(((n, r), reason) for r, reason in result.failures.items())
        stops = [r for r in results if r.stop is not None]
        first = next((r for r in stops if r.stop in _BREAKS), stops[0] if stops else None)
        if first is not None:
            self.stop, self.error, self.reset_in = first.stop, first.error, first.reset_in


class Batch:
    """One Submit of several work copies, each attached to its file, as
    one: every file is checked for changes on the server first, then
    on_conflicts(conflicts, resolve) asks about the conflicts of them
    all at once (a Conflict's index is (file number, index in its side
    record), its where the file's label), and resolve(decisions), or
    resolve(None) to cancel, leads to the units being sent file by
    file. The files of one session share its Pacer.

    items are (copy, platform, session); on_progress(number, total)
    counts across the files; on_done(BatchResult) ends it. A file that
    is locked is left out; out of budget, an error or Cancel stops the
    files after it too."""

    def __init__(self, items, on_progress, on_conflicts, on_done):
        self.on_progress = on_progress
        self.on_conflicts = on_conflicts
        self.on_done = on_done
        self.cancelled = False
        self.finished = False
        self.submissions = []
        pacers = {}
        for copy, platform, session in items:
            pacer = pacers.setdefault(
                id(session),
                Pacer(platform.rate_limit, Submission.RESERVE, Submission.MAX_WAIT),
            )
            submission = Submission(copy, platform, session, None, None, None, pacer=pacer)
            self.submissions.append(submission)
        self._checked = {}
        """{file number: (its conflicts, go)}, checked and waiting to send."""
        self._done = {}
        """{file number: its Result}."""
        self._sending = False
        self._offset = 0
        self._next_offset = 0
        self._total = 0

    def start(self):
        self._check(0)

    def cancel(self):
        self.cancelled = True
        for submission in self.submissions:
            submission.cancel()

    def _held(self):
        """Whether the files still to start are cancelled or broken off."""
        return self.cancelled or any(r.stop in _BREAKS for r in self._done.values())

    def _on_done(self, n, result):
        self._done[n] = result
        if self._sending:
            self._send(n + 1)
        else:
            self._check(n + 1)

    # Each file checked in turn #

    def _check(self, n):
        if n == len(self.submissions):
            self._ask()
            return
        submission = self.submissions[n]
        if self._held():
            submission.result.total = len(submission.copy.pending())
            submission.result.stop = CANCELLED
            self._on_done(n, submission.result)
            return
        submission.on_progress = self._progress
        submission.on_checked = lambda conflicts, go: self._on_checked(n, conflicts, go)
        submission.on_done = lambda result: self._on_done(n, result)
        submission.start()

    def _on_checked(self, n, conflicts, go):
        label = self.submissions[n].copy.label()
        found = [c._replace(index=(n, c.index), where=label) for c in conflicts]
        self._checked[n] = (found, go)
        self._check(n + 1)

    def _ask(self):
        conflicts = [c for found, _go in self._checked.values() for c in found]
        if conflicts and not self.cancelled:
            self.on_conflicts(conflicts, self.resolve)
        else:
            self.resolve({})

    def resolve(self, decisions):
        """The answers to on_conflicts(): {(file number, index): KEEP_MINE,
        TAKE_THEIRS or SKIP}, or None to cancel."""
        if decisions is None:
            self.cancel()
        for n in self._checked:
            mine = {r: d for (m, r), d in (decisions or {}).items() if m == n}
            self.submissions[n].decide(mine)
        self._total = sum(self.submissions[n].to_send for n in self._checked)
        self._sending = True
        self._send(0)

    # Then sent file by file #

    def _send(self, start):
        n = next((m for m in sorted(self._checked) if m >= start), None)
        if n is None:
            self._finish()
            return
        submission = self.submissions[n]
        if self._held():
            submission.cancel()
        _conflicts, go = self._checked.pop(n)
        self._offset = self._next_offset
        self._next_offset += submission.to_send
        go(None)

    def _progress(self, number, _total):
        self.on_progress(self._offset + number, self._total)

    def _finish(self):
        if self.finished:
            return
        self.finished = True
        results = [self._done.get(n, s.result) for n, s in enumerate(self.submissions)]
        self.on_done(BatchResult(results))


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
