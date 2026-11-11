#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Update from Server: others' changes into a work copy (#3337). No GTK
here.

Update runs once the file is saved, through the Session of the work
copy's server, and is Submit's comparison the other way round:

1. What the server has: how many units (Session.unit_count()) and those
   changed since the last download or Update (changed_since(), less
   Submission.SLACK).
2. The set of units changed when the count differs, a changed unit is
   one the side record doesn't know, or a known unit's source text
   changed (a new source string, one removed or rewritten). Then the
   file is downloaded again (3a). Else each unit the server changed is
   compared with its baseline in the side record (3b).

3a. A new download. Before anything is written, plan() matches each
    unit changed here and not submitted to the new download by its id:
    - not there (its source string was removed, or a bilingual file's
      source changed, which makes it another unit): it can't be kept;
    - with other source text (a monolingual key's string changed): kept,
      as needing work, as Weblate marks it;
    - the server's text and state are still the baseline's: kept, with
      the new download as its baseline;
    - the server already has this unit's text and state: nothing to
      keep, the new download has it;
    - else a conflict.
    on_download(plan, proceed) must lead to proceed(None) to stop with
    nothing written, or proceed({index: KEEP_MINE, TAKE_THEIRS or SKIP})
    for plan's conflicts (indexes in the new download). The new file and
    side record then replace the old (workcopy.create()), and the result
    carries what to write back into the file once it is opened again:
    the units kept (and KEEP_MINE) with the new baseline, those skipped
    (SKIP) with their old baseline, so they come up again.

3b. Each unit the server changed:
    - its last_updated is the baseline's, or its text and state are:
      nothing changed but last_updated;
    - not changed here: the server's text and state into the file and as
      the baseline;
    - changed here to what the server has: that becomes the baseline;
    - changed here to something else: a conflict, to on_conflicts(
      conflicts, resolve) as Submit's: KEEP_MINE keeps the file's text,
      with the server's as its baseline, so the next Submit sends it
      over theirs; TAKE_THEIRS takes the server's; SKIP leaves it to
      come up again.

4. The side record notes when Update started (updated_at), the next
   Update's "since".
"""

from collections import namedtuple

from translate.storage.workflow import StateEnum

from . import states
from .submit import CANCELLED, FAILED, KEEP_MINE, SKIP, TAKE_THEIRS, Conflict, Submission, _now
from .submit import _shifted as shifted
from .workcopy import strings, text_hash

Carried = namedtuple('Carried', 'index target state baseline')
"""A change kept across a new download: the unit's index in the new
side record, the file's target (forms) and state, and the old baseline
entry to keep (a conflict skipped), or None for the new download's."""


class Plan:
    """What a new download keeps of the changes not submitted."""

    def __init__(self):
        self.kept = []
        """(old index, new index) of the changes kept."""
        self.lost = []
        """Old indexes of the changes that can't be kept."""
        self.reworded = []
        """(old index, new index) of the changes to units whose source
        text changed: kept, as needing work."""
        self.conflicts = []
        """Conflicts, their index in the new download's units."""
        self.same = 0
        """Changes the server already has."""


class Result:
    """What an Update did."""

    def __init__(self):
        self.taken = 0
        """Units changed in the file: the server's text and state taken,
        or rewritten by a new download. Not conflicts skipped or kept,
        nor units the file already had as the server."""
        self.adopted = 0
        """Units the server already had as in the file."""
        self.kept = 0
        """Conflicts kept as in the file, to send at the next Submit."""
        self.skipped = 0
        """Conflicts left for later."""
        self.downloaded = None
        """The new WorkCopy when the units changed and it was downloaded
        again; then carried holds what to write back into it."""
        self.carried = []
        """Carried changes, for downloaded."""
        self.lost = []
        """With downloaded, the sources of the changes it couldn't keep."""
        self.stop = None
        """None, CANCELLED or FAILED."""
        self.error = None
        self.file_changed = False
        """The file's units changed, so it needs saving."""

    @property
    def changed(self):
        """Whether the server had anything new for the file."""
        return bool(self.taken or self.kept or self.downloaded or self.file_changed)


def _by_id(units):
    return {unit['id']: r for r, unit in enumerate(units)}


def _unit_set_changed(copy, count, remote_units):
    if count is not None and count != len(copy.units):
        return True
    known = _by_id(copy.units)
    for remote in remote_units:
        r = known.get(remote.id)
        if r is None or text_hash(remote.source) != copy.units[r]['source_hash']:
            return True
    return False


def _touched(entry, remote):
    """Whether the server's unit is still the baseline's text and
    state, whatever its last_updated."""
    if remote.last_updated is not None and remote.last_updated == entry.get('last_updated'):
        return True
    return (text_hash(remote.target), remote.code) == (entry['target_hash'], entry['state'])


class Update:
    """One Update of copy (a WorkCopy attached to the open store) from
    its platform's server through session. on_done(Result) ends it."""

    def __init__(self, copy, platform, session, on_conflicts, on_download, on_done):
        self.copy = copy
        self.platform = platform
        self.session = session
        self.on_conflicts = on_conflicts
        self.on_download = on_download
        self.on_done = on_done
        self.result = Result()
        self.finished = False
        self._started_at = None
        self._count = None
        self._conflicts = {}
        self._download = None
        self._plan = None

    def _where(self):
        record = self.copy.record
        return record['project'], record.get('component'), record['language']

    def start(self):
        self._started_at = _now()
        project, component, language = self._where()
        self.session.unit_count(project, component, language, self._got_count, self._failed)

    def cancel(self):
        """Stops at once: the replies still to come are dropped."""
        self.session.cancel()
        self._finish(CANCELLED)

    def _got_count(self, count):
        self._count = count
        project, component, language = self._where()
        record = self.copy.record
        since = shifted(record.get('updated_at') or record.get('downloaded_at'), Submission.SLACK)
        self.session.changed_since(
            project, component, language, since, self._got_changes, self._failed
        )

    def _mine(self, r):
        target, state = self.copy.mine(r)
        return target, state, self.platform.state_code(state, target)

    def _got_changes(self, remote_units):
        if _unit_set_changed(self.copy, self._count, remote_units):
            project, component, language = self._where()
            self.session.download(project, component, language, self._downloaded, self._failed)
            return
        known = _by_id(self.copy.units)
        conflicts = []
        for remote in remote_units:
            r = known[remote.id]
            entry = self.copy.units[r]
            if _touched(entry, remote):
                entry['last_updated'] = remote.last_updated
                continue
            if r not in self.copy.matched:
                continue
            if not entry.get('changed'):
                self._take(r, remote)
                continue
            target, _state, code = self._mine(r)
            if (text_hash(remote.target), remote.code) == (text_hash(target), code):
                self.copy.adopt(r, remote)
                self.result.adopted += 1
            else:
                self._conflicts[r] = remote
                source = self.copy.matched[r].source
                conflicts.append(Conflict(r, str(source), target, list(remote.target)))
        self.copy.write_record()
        if conflicts:
            self.on_conflicts(conflicts, self.resolve)
        else:
            self._finish()

    def resolve(self, decisions):
        """The answers to on_conflicts(): {index: KEEP_MINE, TAKE_THEIRS
        or SKIP}, or None to stop with the rest left as they are."""
        if decisions is None:
            self.copy.write_record()
            self._finish(CANCELLED)
            return
        for r, remote in sorted(self._conflicts.items()):
            decision = decisions.get(r, SKIP)
            if decision == KEEP_MINE:
                self.copy.adopt(r, remote)
                # Still to send: the file differs from the new baseline.
                self.copy.units[r]['changed'] = True
                self.result.kept += 1
            elif decision == TAKE_THEIRS:
                self._take(r, remote)
            else:
                self.result.skipped += 1
        self.copy.write_record()
        self._finish()

    def _take(self, r, remote):
        changed = self.copy.take(r, remote)
        self.result.file_changed |= changed
        self.result.taken += changed

    # A new download #

    def plan(self, download):
        """The Plan for a new download (a Download)."""
        plan = Plan()
        new = {unit.id: n for n, unit in enumerate(download.units)}
        for r in self.copy.pending():
            entry = self.copy.units[r]
            n = new.get(entry['id'])
            remote = download.units[n] if n is not None else None
            if remote is None:
                plan.lost.append(r)
                continue
            if text_hash(remote.source) != entry['source_hash']:
                plan.reworded.append((r, n))
                continue
            target, _state, code = self._mine(r)
            if (text_hash(remote.target), remote.code) == (text_hash(target), code):
                plan.same += 1
            elif _touched(entry, remote):
                plan.kept.append((r, n))
            else:
                plan.kept.append((r, n))
                plan.conflicts.append(Conflict(n, str(remote.source[0]), target, remote.target))
        return plan

    def _downloaded(self, download):
        self._download = download
        self._plan = self.plan(download)
        self.on_download(self._plan, self.proceed)

    def proceed(self, decisions):
        """The answer to on_download(): None to stop, else the decisions
        on its plan's conflicts."""
        if decisions is None:
            self._finish(CANCELLED)
            return
        from . import workcopy

        conflicted = {c.index for c in self._plan.conflicts}
        carried = []
        for r, n in self._plan.kept:
            target, state = self.copy.mine(r)
            decision = decisions.get(n, SKIP) if n in conflicted else KEEP_MINE
            if decision == TAKE_THEIRS:
                # The new download has theirs.
                continue
            if n in conflicted:
                self.result.kept += decision == KEEP_MINE
                self.result.skipped += decision == SKIP
            baseline = dict(self.copy.units[r]) if decision == SKIP else None
            carried.append(Carried(n, target, state, baseline))
        for r, n in self._plan.reworded:
            target, state = self.copy.mine(r)
            if state is not None and state > StateEnum.NEEDS_WORK:
                state = StateEnum.NEEDS_WORK
            carried.append(Carried(n, target, state, None))
        self.result.lost = [str(self.copy.matched[r].source) for r in self._plan.lost]
        record = self.copy.record
        try:
            self.result.downloaded = workcopy.create(
                self.platform,
                record['server'],
                record['project'],
                record.get('component'),
                record['language'],
                self._download,
            )
        except OSError as error:
            from . import NETWORK, PlatformError

            self._finish(FAILED, PlatformError(NETWORK, detail=str(error)))
            return
        self.result.carried = carried
        self.result.taken += self._rewritten(carried)
        self.copy = self.result.downloaded
        self._finish()

    def _rewritten(self, carried):
        """How many units of the file the new download changes, with
        carried written back into it: a new unit with a translation,
        or one whose source, target or state is no longer the file's."""
        old = {entry['id']: r for r, entry in enumerate(self.copy.units) if r in self.copy.matched}
        back = {item.index: item for item in carried}
        count = 0
        for n, remote in enumerate(self._download.units):
            target, state = _written(back.get(n), remote)
            r = old.get(remote.id)
            if r is None:
                count += any(target)
                continue
            mine, my_state = self.copy.mine(r)
            source = strings(self.copy.matched[r].source)
            if (source, mine) != (list(remote.source) or [''], target):
                count += 1
            elif None not in (state, my_state) and state != my_state:
                count += 1
        return count

    def _failed(self, error):
        self._finish(FAILED, error)

    def _finish(self, stop=None, error=None):
        if self.finished:
            return
        self.finished = True
        self.result.stop = stop
        self.result.error = error
        if stop is None:
            self.copy.record['updated_at'] = self._started_at
            self.copy.write_record()
        self.on_done(self.result)


def _written(item, remote):
    """(target forms, state) a new download's unit ends with in the
    file: a carried change's (item), or the server's."""
    if item is not None:
        return list(item.target) or [''], item.state
    return list(remote.target) or [''], remote.state


def carry(copy, carried):
    """Writes carried changes (Carried) back into the units of copy, a new
    download attached to the open store. Whether any unit changed."""
    from translate.misc.multistring import multistring

    changed = False
    for item in carried:
        unit = copy.matched.get(item.index)
        if unit is None:
            continue
        forms = list(item.target) or ['']
        if strings(unit.target) != forms:
            unit.target = multistring(forms) if len(forms) > 1 else forms[0]
            changed = True
        if item.state is not None and unit.STATE and unit.get_state_n() != item.state:
            unit.set_state_n(item.state)
            changed = True
        if states.is_adopted(unit):
            copy.units[item.index]['local_state'] = item.state
        entry = copy.units[item.index]
        if item.baseline is not None:
            for key in ('target_hash', 'state', 'virtaal_state', 'last_updated'):
                entry[key] = item.baseline.get(key)
        entry['changed'] = True
    copy.write_record()
    return changed


class BatchResult(Result):
    """What an Update of several files did: their Results added up, each
    file's in results."""

    def __init__(self, results):
        super().__init__()
        self.results = results
        for result in results:
            self.taken += result.taken
            self.adopted += result.adopted
            self.kept += result.kept
            self.skipped += result.skipped
            self.file_changed |= result.file_changed
        # An error stops it; Cancel, only when it stopped every file.
        failed = next((r for r in results if r.stop == FAILED), None)
        if failed is None and results and all(r.stop is not None for r in results):
            failed = results[0]
        if failed is not None:
            self.stop, self.error = failed.stop, failed.error

    @property
    def changed(self):
        return any(result.changed for result in self.results)


class Batch:
    """One Update of several work copies, each attached to its file, as
    one: each file's Update runs in turn until it is done or has a
    question; then on_download(plans, proceed) asks once about every file
    whose strings changed on the server, plans being (copy, Plan), and
    proceed(whether to download them again); then on_conflicts(
    conflicts, resolve) asks about the conflicts of them all at once (a
    Conflict's index is (file number, its index), its where the file's
    label), as Submit's Batch. on_done(BatchResult) ends it.

    items are (copy, platform, session)."""

    def __init__(self, items, on_conflicts, on_download, on_done):
        self.on_conflicts = on_conflicts
        self.on_download = on_download
        self.on_done = on_done
        self.finished = False
        self.updates = []
        for n, (copy, platform, session) in enumerate(items):
            self.updates.append(
                Update(
                    copy,
                    platform,
                    session,
                    lambda found, resolve, n=n: self._held(n, found, resolve, None),
                    lambda plan, proceed, n=n: self._held(n, plan.conflicts, proceed, plan),
                    lambda result, n=n: self._on_done(n, result),
                )
            )
        self._waiting = {}
        """{file number: (conflicts, answer, Plan or None)}."""
        self._done = {}
        self._asking = False
        self.cancelled = False

    def start(self):
        self._next(0)

    def cancel(self):
        """Stops the file on its way at once, and the rest."""
        self.cancelled = True
        running = len(self._done) + len(self._waiting)
        if not self._asking and running < len(self.updates):
            self.updates[running].cancel()

    def _next(self, n):
        if n == len(self.updates):
            self._ask()
        elif self.cancelled:
            self.updates[n]._finish(CANCELLED)
        else:
            self.updates[n].start()

    def _held(self, n, conflicts, answer, plan):
        self._waiting[n] = (conflicts, answer, plan)
        self._next(n + 1)

    def _on_done(self, n, result):
        self._done[n] = result
        if self._asking:
            if len(self._done) == len(self.updates):
                self._finish()
        else:
            self._next(n + 1)

    def _ask(self):
        self._asking = True
        if not self._waiting:
            self._finish()
            return
        if self.cancelled:
            self.resolve(None)
            return
        plans = [
            (self.updates[n].copy, plan)
            for n, (_c, _a, plan) in sorted(self._waiting.items())
            if plan is not None and (plan.kept or plan.lost or plan.reworded)
        ]
        if plans:
            self.on_download(plans, self._downloading)
        else:
            self._downloading(True)

    def _downloading(self, again):
        if not again:
            for n, (_conflicts, proceed, plan) in list(self._waiting.items()):
                if plan is not None:
                    del self._waiting[n]
                    proceed(None)
        conflicts = []
        for n, (found, _answer, _plan) in sorted(self._waiting.items()):
            label = self.updates[n].copy.label()
            conflicts += [c._replace(index=(n, c.index), where=label) for c in found]
        if conflicts:
            self.on_conflicts(conflicts, self.resolve)
        else:
            self.resolve({})

    def resolve(self, decisions):
        """The answers to on_conflicts(), or None to stop with the rest
        left as they are."""
        waiting, self._waiting = self._waiting, {}
        for n, (_found, answer, _plan) in sorted(waiting.items()):
            if decisions is None:
                answer(None)
            else:
                answer({r: d for (m, r), d in decisions.items() if m == n})

    def _finish(self):
        if self.finished:
            return
        self.finished = True
        results = [self._done[n] for n in range(len(self.updates))]
        self.on_done(BatchResult(results))
