#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Recovery data for resuming after a crash or an OS shutdown.

Each running Virtaal owns one slot directory under recovery_root():

  lock                  held for the life of the process
  session.json          where the user was, how the session ended, and
                        one record per open document
  <doc>.journal.jsonl   one line per unit change since the document's
                        baseline (the file on disk as last read or saved)

A slot whose lock can be taken belongs to no running process: Virtaal
didn't close cleanly, so it can be resumed. Documents are keyed by an
id within the slot, so one process could track several files.

Nothing here touches the user's real file."""

import hashlib
import json
import logging
import os
import shutil
import time
import uuid

from virtaal.common.platform import platform
from virtaal.support import dismissedchecks

FORMAT = 1
LOCK_NAME = 'lock'
SESSION_NAME = 'session.json'
BROKEN_SUFFIX = '.broken'


def recovery_root():
    """The directory holding every slot, created private to the user."""
    from virtaal.common import pan_app

    name = 'recovery' if not (platform.is_mac or platform.is_windows) else 'Recovery'
    root = os.path.join(pan_app.get_state_dir(), name)
    os.makedirs(root, mode=0o700, exist_ok=True)
    if not platform.is_windows:
        os.chmod(root, 0o700)
    return root


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def disk_baseline(path):
    """The baseline record for the file at path, as it is now."""
    st = os.stat(path)
    return {
        'kind': 'disk',
        'path': path,
        'size': st.st_size,
        'mtime_ns': st.st_mtime_ns,
        'sha256': file_sha256(path),
    }


def baseline_matches(baseline):
    """Whether the disk baseline's file is still what was recorded."""
    path = baseline.get('path')
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return False
    if st.st_size != baseline.get('size'):
        return False
    # Not trusting an equal mtime: a quick rewrite can keep it.
    return file_sha256(path) == baseline.get('sha256')


def write_json_atomic(path, data):
    tmp = '%s.%s.tmp' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Lock:
    """An exclusive lock on a file, released by the OS however the
    process ends."""

    def __init__(self, fd):
        self._fd = fd

    @classmethod
    def acquire(cls, path):
        """The lock on path, or None if another process holds it."""
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if platform.is_windows:
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return cls(fd)

    def release(self):
        if self._fd is None:
            return
        try:
            if platform.is_windows:
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(self._fd)
        self._fd = None


def read_journal(path):
    """The journal's records in order. A torn or unreadable line (a
    write cut short by a crash or power loss) ends it."""
    records = []
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if not line.endswith('\n'):
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    break
                if not isinstance(record, dict) or 'seq' not in record:
                    break
                records.append(record)
    except FileNotFoundError:
        pass
    return records


class Journal:
    """Append-only unit changes. Writes reach the OS at once, so a
    crashed process loses nothing; sync() makes them survive power
    loss too."""

    def __init__(self, path):
        self.path = path
        records = read_journal(path)
        self.seq = records[-1]['seq'] if records else 0
        if os.path.exists(path):
            # Cut off a torn tail, so new lines don't follow half a line.
            with open(path, 'rb') as f:
                data = f.read()
            end = 0
            for _record in records:
                end = data.index(b'\n', end) + 1
            if end != len(data):
                with open(path, 'r+b') as f:
                    f.truncate(end)
        self._file = None
        self._unsynced = False

    def append(self, record):
        """Write record as the next line; returns its seq."""
        self.seq += 1
        record = dict(record, seq=self.seq, t=time.time())
        if self._file is None:
            self._file = open(self.path, 'a', encoding='utf-8')  # noqa: SIM115
        self._file.write(json.dumps(record, ensure_ascii=False) + '\n')
        self._file.flush()
        self._unsynced = True
        return self.seq

    def sync(self):
        if self._file is not None and self._unsynced:
            os.fsync(self._file.fileno())
        self._unsynced = False

    def records(self):
        return read_journal(self.path)

    def rewrite(self, records):
        """Replace the journal with records, atomically; seq keeps counting."""
        self.close()
        tmp = '%s.%s.tmp' % (self.path, os.getpid())
        with open(tmp, 'w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def truncate(self):
        if os.path.exists(self.path):
            self.rewrite([])

    def close(self):
        if self._file is not None:
            self.sync()
            self._file.close()
            self._file = None


# Units #


def _source_sha1(unit):
    return hashlib.sha1(str(unit.source or '').encode('utf-8')).hexdigest()


def unit_key(unit, index):
    """How a journal line names a unit: its index, its id and a hash of
    its source, so a file that changed underneath can be detected."""
    return {'unit_index': index, 'unit_id': unit.getid(), 'source_sha1': _source_sha1(unit)}


def unit_fields(unit, state=None):
    """What Virtaal can edit in a unit: its target forms, state and
    dismissed checks."""
    if unit.hasplural():
        targets = [str(s) for s in unit.target.strings] if unit.target else []
    else:
        targets = [str(unit.target or '')]
    fields = {'targets': targets}
    if unit.STATE:
        fields['state'] = unit.get_state_n() if state is None else state
    fields['dismissed'] = sorted(dismissedchecks.dismissed(unit))
    return fields


def apply_fields(unit, fields):
    """Set a unit's targets and state from fields; unknown keys are
    ignored."""
    targets = fields.get('targets')
    if targets is not None:
        if unit.hasplural():
            from translate.misc.multistring import multistring

            unit.target = multistring(list(targets))
        else:
            unit.target = targets[0] if targets else ''
    state = fields.get('state')
    if state is not None and unit.STATE:
        unit.set_state_n(state)
    dismissed = fields.get('dismissed')
    if dismissed is not None:
        have = dismissedchecks.dismissed(unit)
        for check in set(dismissed) ^ have:
            dismissedchecks.set_dismissed(unit, check, check not in have)


def _matches(unit, record):
    return unit.getid() == record.get('unit_id') and _source_sha1(unit) == record.get('source_sha1')


def locate(store, record, by_id=None):
    """The store index the record's unit is at now, or None. by_id is a
    dict this fills on first need, mapping unit id to index."""
    index = record.get('unit_index')
    if isinstance(index, int) and 0 <= index < len(store) and _matches(store[index], record):
        return index
    if by_id is None:
        return None
    if not by_id:
        for i in range(len(store)):
            by_id.setdefault(store[i].getid(), i)
    index = by_id.get(record.get('unit_id'))
    if index is not None and _matches(store[index], record):
        return index
    return None


def latest_per_unit(records):
    """The last record for each unit, in journal order."""
    latest = {}
    for record in records:
        latest[(record.get('unit_index'), record.get('unit_id'))] = record
    return sorted(latest.values(), key=lambda r: r['seq'])


def replay(store, records, skip=()):
    """Apply records to store's units, emitting nothing. Units at an
    index in skip are left alone.
    @returns: (sorted indices changed, number of records not placed)"""
    by_id = {}
    changed = set()
    unplaced = 0
    for record in latest_per_unit(records):
        index = locate(store, record, by_id)
        if index is None:
            unplaced += 1
            continue
        if index in skip:
            continue
        apply_fields(store[index], record.get('fields', {}))
        changed.add(index)
    return sorted(changed), unplaced


# Slots #


class Slot:
    """One process's recovery directory."""

    def __init__(self, path, data, lock):
        self.path = path
        self.data = data
        self._lock = lock
        self._journals = {}

    @property
    def id(self):
        return os.path.basename(self.path)

    @classmethod
    def create(cls, root=None):
        root = root or recovery_root()
        path = os.path.join(root, uuid.uuid4().hex)
        os.mkdir(path, 0o700)
        lock = Lock.acquire(os.path.join(path, LOCK_NAME))
        from virtaal.__version__ import ver

        data = {
            'format': FORMAT,
            'virtaal_version': ver,
            'created_at': time.time(),
            'updated_at': time.time(),
            'end_reason': None,
            'resume_attempts': 0,
            'position': None,
            'documents': {},
        }
        slot = cls(path, data, lock)
        slot.write()
        return slot

    @classmethod
    def load(cls, path, lock):
        """The slot at path, which the caller has locked. Raises
        ValueError if session.json is unreadable."""
        try:
            with open(os.path.join(path, SESSION_NAME), encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(data, dict) or not isinstance(data.get('documents'), dict):
            raise ValueError('not a recovery session')
        data.setdefault('resume_attempts', 0)
        data.setdefault('end_reason', None)
        return cls(path, data, lock)

    @property
    def updated_at(self):
        value = self.data.get('updated_at')
        return value if isinstance(value, (int, float)) else 0

    def document(self, doc_id):
        return self.data['documents'].get(doc_id)

    def set_document(self, doc_id, record):
        self.data['documents'][doc_id] = record
        self._journal_path(doc_id)

    def remove_document(self, doc_id):
        self.data['documents'].pop(doc_id, None)
        journal = self._journals.pop(doc_id, None)
        if journal:
            journal.close()
        try:
            os.remove(self._journal_path(doc_id))
        except FileNotFoundError:
            pass

    def _journal_path(self, doc_id):
        return os.path.join(self.path, '%s.journal.jsonl' % doc_id)

    def journal(self, doc_id):
        if doc_id not in self._journals:
            self._journals[doc_id] = Journal(self._journal_path(doc_id))
        return self._journals[doc_id]

    def has_unsaved_work(self):
        for doc_id, doc in self.data['documents'].items():
            if doc.get('draft') or read_journal(self._journal_path(doc_id)):
                return True
        return False

    def write(self):
        self.data['updated_at'] = time.time()
        write_json_atomic(os.path.join(self.path, SESSION_NAME), self.data)

    def sync(self):
        for journal in self._journals.values():
            journal.sync()

    def close(self):
        """Stop writing and let the slot go, keeping it on disk."""
        for journal in self._journals.values():
            journal.close()
        self._journals = {}
        if self._lock:
            self._lock.release()
            self._lock = None

    def delete(self):
        # Renamed first: a name with a dot is never taken for an orphan.
        doomed = '%s.%s.deleting' % (self.path, os.getpid())
        for journal in self._journals.values():
            journal.close()
        self._journals = {}
        if platform.is_windows:
            # Windows can't rename a directory with an open file in it.
            self.close()
        try:
            os.replace(self.path, doomed)
        except OSError:
            doomed = self.path
        self.close()
        shutil.rmtree(doomed, ignore_errors=True)


def find_orphans(root=None, exclude=()):
    """The slots no running Virtaal holds, newest first, each locked by
    this process. An unreadable slot is renamed to <slot>.broken."""
    root = root or recovery_root()
    slots = []
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return slots
    for name in names:
        path = os.path.join(root, name)
        if name in exclude or '.' in name or not os.path.isdir(path):
            continue
        lock = Lock.acquire(os.path.join(path, LOCK_NAME))
        if lock is None:
            continue
        try:
            slots.append(Slot.load(path, lock))
        except ValueError as exc:
            logging.warning('Unreadable recovery data %s moved aside: %s', path, exc)
            lock.release()
            try:
                os.replace(path, path + BROKEN_SUFFIX)
            except OSError:
                pass
    slots.sort(key=lambda slot: slot.updated_at, reverse=True)
    return slots
