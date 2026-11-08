#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import os
import signal
import stat
import subprocess
import sys
import textwrap
import time

import pytest
from translate.storage import factory

from virtaal.common import pan_app
from virtaal.support import recovery
from virtaal.support.recovery import Journal, Lock, Slot

PO = b'''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"

msgid "Open"
msgstr ""

msgid "Close"
msgstr "Maak toe"

msgid "%d file"
msgid_plural "%d files"
msgstr[0] ""
msgstr[1] ""
'''

XLIFF = b'''<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
<file original="t" source-language="en" target-language="af" datatype="plaintext"><body>
<trans-unit id="1"><source>Open</source><target state="needs-translation">Oop</target></trans-unit>
</body></file></xliff>
'''


class _Store:
    """The part of StoreModel replay uses: units by index."""

    def __init__(self, units):
        self.units = units

    def __len__(self):
        return len(self.units)

    def __getitem__(self, index):
        return self.units[index]


def _po_store(tmp_path, data=PO):
    path = tmp_path / 'open.po'
    path.write_bytes(data)
    trans = factory.getobject(str(path))
    return _Store([u for u in trans.units if not u.isheader()]), trans


def _record(store, index, **fields):
    return dict(recovery.unit_key(store[index], index), fields=fields)


# recovery_root() #


@pytest.mark.skipif(os.name != 'posix', reason='POSIX modes')
def test_recovery_root_is_private_and_under_the_state_dir():
    root = recovery.recovery_root()

    assert os.path.dirname(root) == pan_app.get_state_dir()
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700


# Lock #


def test_a_held_lock_cant_be_taken_again(tmp_path):
    path = str(tmp_path / 'lock')
    lock = Lock.acquire(path)

    assert lock is not None
    assert Lock.acquire(path) is None
    lock.release()
    again = Lock.acquire(path)
    assert again is not None
    again.release()


_HOLDER = textwrap.dedent(
    '''
    import subprocess, sys, time
    sys.path.insert(0, sys.argv[2])
    from virtaal.support.recovery import Lock
    lock = Lock.acquire(sys.argv[1])
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    print(child.pid, flush=True)
    time.sleep(60)
    '''
)


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(recovery.__file__))))


@pytest.mark.skipif(os.name != 'posix', reason='SIGKILL')
def test_a_killed_process_frees_its_lock_even_while_its_child_lives(tmp_path):
    path = str(tmp_path / 'lock')
    holder = subprocess.Popen(
        [sys.executable, '-c', _HOLDER, path, _repo_root()],
        stdout=subprocess.PIPE,
        text=True,
    )
    child = int(holder.stdout.readline())
    try:
        assert Lock.acquire(path) is None
        holder.send_signal(signal.SIGKILL)
        holder.wait(10)

        lock = Lock.acquire(path)
        assert lock is not None
        lock.release()
    finally:
        holder.kill()
        holder.stdout.close()
        os.kill(child, signal.SIGKILL)


# Journal #


def test_journal_lines_are_read_back_in_order(tmp_path):
    journal = Journal(str(tmp_path / 'j.jsonl'))

    assert journal.append({'unit_index': 1}) == 1
    assert journal.append({'unit_index': 2}) == 2

    records = recovery.read_journal(journal.path)
    assert [(r['seq'], r['unit_index']) for r in records] == [(1, 1), (2, 2)]


def test_a_torn_last_line_is_dropped_and_cut_off_before_new_lines(tmp_path):
    path = tmp_path / 'j.jsonl'
    path.write_text('{"seq": 1, "unit_index": 0}\n{"seq": 2, "unit_ind')

    assert [r['seq'] for r in recovery.read_journal(str(path))] == [1]
    journal = Journal(str(path))
    assert journal.seq == 1
    journal.append({'unit_index': 5})
    journal.close()

    assert [r['seq'] for r in recovery.read_journal(str(path))] == [1, 2]


def test_rewrite_keeps_counting_from_the_last_seq(tmp_path):
    journal = Journal(str(tmp_path / 'j.jsonl'))
    journal.append({'unit_index': 0})
    journal.append({'unit_index': 1})

    journal.rewrite([r for r in journal.records() if r['seq'] == 2])
    journal.append({'unit_index': 2})

    assert [r['seq'] for r in journal.records()] == [2, 3]


def test_truncate_empties_the_journal(tmp_path):
    journal = Journal(str(tmp_path / 'j.jsonl'))
    journal.append({'unit_index': 0})

    journal.truncate()

    assert journal.records() == []


# Units and replay #


def test_unit_fields_round_trip_a_plural(tmp_path):
    store, _trans = _po_store(tmp_path)
    plural = store[2]
    apply_to = _po_store(tmp_path)[0][2]

    recovery.apply_fields(plural, {'targets': ['%d lêer', '%d lêers'], 'state': 100})
    recovery.apply_fields(apply_to, recovery.unit_fields(plural))

    assert apply_to.target.strings == ['%d lêer', '%d lêers']


def test_apply_fields_sets_the_state(tmp_path):
    path = tmp_path / 'open.xlf'
    path.write_bytes(XLIFF)
    unit = factory.getobject(str(path)).units[0]

    recovery.apply_fields(unit, {'targets': ['Maak oop'], 'state': 120})

    assert unit.target == 'Maak oop'
    assert unit.get_state_n() == 120


def test_recovered_fields_keep_the_dismissed_checks(tmp_path):
    from virtaal.support import dismissedchecks

    path = tmp_path / 'open.xlf'
    path.write_bytes(XLIFF)
    edited = factory.getobject(str(path)).units[0]
    edited.target = 'maak oop'
    dismissedchecks.set_dismissed(edited, 'startcaps')
    recovered = factory.getobject(str(path)).units[0]

    recovery.apply_fields(recovered, recovery.unit_fields(edited))

    assert dismissedchecks.dismissed(recovered) == {'startcaps'}
    recovery.apply_fields(recovered, dict(recovery.unit_fields(edited), dismissed=[]))
    assert dismissedchecks.dismissed(recovered) == set()


def test_apply_fields_ignores_keys_it_doesnt_know(tmp_path):
    store, _trans = _po_store(tmp_path)

    recovery.apply_fields(store[0], {'targets': ['Oop'], 'notes': 'later'})

    assert store[0].target == 'Oop'


def test_replay_applies_the_last_record_per_unit(tmp_path):
    store, _trans = _po_store(tmp_path)
    records = [
        dict(_record(store, 0, targets=['Oop']), seq=1),
        dict(_record(store, 1, targets=['Sluit']), seq=2),
        dict(_record(store, 0, targets=['Maak oop']), seq=3),
    ]

    changed, unplaced = recovery.replay(store, records)

    assert (changed, unplaced) == ([0, 1], 0)
    assert store[0].target == 'Maak oop'
    assert store[1].target == 'Sluit'


def test_replay_finds_a_unit_by_id_after_the_file_moved_it(tmp_path):
    store, _trans = _po_store(tmp_path)
    record = dict(_record(store, 1, targets=['Sluit']), seq=1)
    store.units.reverse()

    changed, unplaced = recovery.replay(store, [record])

    assert (changed, unplaced) == ([1], 0)
    assert store[1].source == 'Close'
    assert store[1].target == 'Sluit'


def test_replay_counts_a_unit_whose_source_changed_as_not_placed(tmp_path):
    store, _trans = _po_store(tmp_path)
    record = dict(_record(store, 0, targets=['Oop']), seq=1)
    store[0].source = 'Open now'

    changed, unplaced = recovery.replay(store, [record])

    assert (changed, unplaced) == ([], 1)
    assert store[0].target == ''


def test_replay_leaves_skipped_units_alone(tmp_path):
    store, _trans = _po_store(tmp_path)
    records = [dict(_record(store, 0, targets=['Oop']), seq=1)]

    changed, _unplaced = recovery.replay(store, records, skip={0})

    assert changed == []
    assert store[0].target == ''


# Slot #


def test_a_new_slot_is_locked_and_written(tmp_path):
    slot = Slot.create(str(tmp_path))

    with open(os.path.join(slot.path, 'session.json'), encoding='utf-8') as f:
        data = json.load(f)
    assert data['format'] == recovery.FORMAT
    assert data['end_reason'] is None
    assert data['resume_attempts'] == 0
    assert recovery.find_orphans(str(tmp_path)) == []
    slot.delete()


def test_a_released_slot_is_an_orphan(tmp_path):
    slot = Slot.create(str(tmp_path))
    slot.set_document('0', {'open_path': '/x.po'})
    slot.write()
    slot.close()

    orphans = recovery.find_orphans(str(tmp_path))

    assert [o.id for o in orphans] == [slot.id]
    assert orphans[0].document('0') == {'open_path': '/x.po'}
    for orphan in orphans:
        orphan.close()


def test_orphans_come_newest_first(tmp_path):
    older = Slot.create(str(tmp_path))
    older.close()
    time.sleep(0.01)
    newer = Slot.create(str(tmp_path))
    newer.close()

    orphans = recovery.find_orphans(str(tmp_path))

    assert [o.id for o in orphans] == [newer.id, older.id]
    for orphan in orphans:
        orphan.close()


def test_an_unreadable_slot_is_moved_aside(tmp_path):
    slot = Slot.create(str(tmp_path))
    slot.close()
    with open(os.path.join(slot.path, 'session.json'), 'w', encoding='utf-8') as f:
        f.write('{"documents": ')

    assert recovery.find_orphans(str(tmp_path)) == []
    assert os.path.isdir(slot.path + '.broken')
    assert recovery.find_orphans(str(tmp_path)) == []


def test_delete_removes_the_slot(tmp_path):
    slot = Slot.create(str(tmp_path))
    slot.set_document('0', {})
    slot.journal('0').append({'unit_index': 0})

    slot.delete()

    assert os.listdir(str(tmp_path)) == []


def test_has_unsaved_work_needs_a_journal_line_or_a_draft(tmp_path):
    slot = Slot.create(str(tmp_path))
    slot.set_document('0', {'draft': None})

    assert not slot.has_unsaved_work()
    slot.journal('0').append({'unit_index': 0})
    assert slot.has_unsaved_work()
    slot.delete()


def test_baseline_matches_until_the_file_changes(tmp_path):
    path = tmp_path / 'open.po'
    path.write_bytes(PO)
    baseline = recovery.disk_baseline(str(path))

    assert recovery.baseline_matches(baseline)
    path.write_bytes(PO.replace(b'Maak toe', b'Sluit   '))
    assert not recovery.baseline_matches(baseline)


def test_baseline_matches_a_touched_but_unchanged_file(tmp_path):
    path = tmp_path / 'open.po'
    path.write_bytes(PO)
    baseline = recovery.disk_baseline(str(path))

    os.utime(str(path), ns=(1, 1))

    assert recovery.baseline_matches(baseline)
