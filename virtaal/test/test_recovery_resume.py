#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The recovery slot kept by RecoveryController, on the real store and
unit controllers."""

import hashlib
import json
import os
import tempfile
import time

import pytest
from gi.repository import Gtk
from test_scaffolding import TestScaffolding

from virtaal.controllers import recoverycontroller
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.controllers.recoverycontroller import DOCUMENT, RecoveryController
from virtaal.support import recovery

PO = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Open"
msgstr ""

msgid "Close"
msgstr "Maak toe"

msgid "Save"
msgstr "Stoor"

msgid "Quit"
msgstr ""
'''


def _at(index, source, **extra):
    """A position at the unit with this source."""
    sha1 = hashlib.sha1(source.encode('utf-8')).hexdigest()
    return dict(unit_index=index, unit_id=source, source_sha1=sha1, **extra)


def _pump(until, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration()
        if until():
            return True
        time.sleep(0.01)
    return False


class RecoveryScaffolding(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)
        self.recovery = RecoveryController(self.main_controller, root=tempfile.mkdtemp())

    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'Afrikaans'),
        )
        self.notices = []
        view = self.main_controller.view
        monkeypatch.setattr(
            view,
            'show_resume_blocked_notice',
            lambda filename, on_reopen: self.notices.append(('blocked', filename, on_reopen)),
        )
        monkeypatch.setattr(
            view,
            'show_startup_open_failure_notice',
            lambda filename, reason=None: self.notices.append(('missing', filename)),
        )
        self.tmp_path = tmp_path
        self.store_controller.close_file()
        self.root = str(tmp_path / 'recovery')
        os.mkdir(self.root)
        self.recovery._root = self.root
        yield
        self.store_controller.close_file()

    def _po(self, name='open.po', text=PO):
        path = self.tmp_path / name
        path.write_text(text, encoding='utf-8')
        return str(path)

    def _slots(self):
        return sorted(name for name in os.listdir(self.root) if '.' not in name)

    def _session(self, slot=None):
        slot = slot or self.recovery.slot
        with open(os.path.join(slot.path, 'session.json'), encoding='utf-8') as f:
            return json.load(f)

    def _orphan(self, path, attempts=0, position=None, end_reason=None):
        """A slot as a Virtaal that didn't close cleanly left it."""
        slot = recovery.Slot.create(self.root)
        slot.set_document(DOCUMENT, {'open_path': path, 'target_path': path})
        slot.data.update(resume_attempts=attempts, position=position, end_reason=end_reason)
        slot.write()
        slot.close()
        return slot

    def _edit_at(self, index):
        """Load the unit at index in the editor, as the store view would."""
        self.store_controller.cursor.force_index(index)
        self.unit_controller.load_unit(self.store_controller.store[index])

    def _settle(self):
        """Until the resumed unit is selected."""
        wanted = self.recovery._text_cursor
        if wanted is not None:
            _pump(lambda: self.store_controller.cursor.deref() is wanted['unit'], seconds=2)

    def _resume(self, startupfile=''):
        path = self.recovery.choose_startup_file(startupfile)
        opened = False
        if path:
            self.store_controller.open_file(path)
            self.unit_controller.load_unit(self.store_controller.store[0])
            opened = True
        self.recovery.startup_file_opened(opened)
        if opened:
            self._settle()
            cursor = self.store_controller.cursor
            self.unit_controller.load_unit(self.store_controller.store[cursor.index])
        return path


class TestSlotUpkeep(RecoveryScaffolding):
    def test_opening_a_file_creates_a_slot_for_it(self):
        path = self._po()

        self.store_controller.open_file(path)

        assert self._slots() == [self.recovery.slot.id]
        doc = self._session()['documents'][DOCUMENT]
        assert doc['open_path'] == path
        assert doc['target_path'] == path
        assert self._session()['position']['unit_index'] == 0

    def test_the_slot_isnt_an_orphan_while_virtaal_runs(self):
        self.store_controller.open_file(self._po())

        assert recovery.find_orphans(self.root) == []

    def test_closing_the_file_deletes_the_slot(self):
        self.store_controller.open_file(self._po())

        self.store_controller.close_file()

        assert self._slots() == []
        assert self.recovery.slot is None

    def test_opening_another_file_replaces_the_slot(self):
        self.store_controller.open_file(self._po())
        first = self.recovery.slot.id

        self.store_controller.open_file(self._po('other.po'))

        assert self._slots() == [self.recovery.slot.id]
        assert self.recovery.slot.id != first

    def test_quitting_deletes_the_slot(self):
        self.store_controller.open_file(self._po())

        self.main_controller.emit('quit')

        assert self._slots() == []

    def test_a_template_slot_saves_to_the_po_name(self):
        path = self._po('open.pot')

        self.store_controller.open_file(path)

        doc = self._session()['documents'][DOCUMENT]
        assert doc['open_path'] == path
        assert doc['target_path'] == path[:-1]
        assert doc['force_saveas'] is True

    def test_saving_as_moves_the_slot_to_the_new_file(self):
        self.store_controller.open_file(self._po())
        self._edit_at(0)
        new = str(self.tmp_path / 'saved.po')

        self.store_controller.save_file(new)

        doc = self._session()['documents'][DOCUMENT]
        assert (doc['open_path'], doc['target_path']) == (new, new)

    def test_moving_writes_the_position_once_quiet(self, monkeypatch):
        monkeypatch.setattr(recoverycontroller, 'DEBOUNCE_MS', 10)
        self.store_controller.open_file(self._po())

        self.store_controller.cursor.force_index(2)

        assert _pump(lambda: self._session()['position']['unit_index'] == 2)
        position = self._session()['position']
        assert position['unit_id'] == 'Save'
        assert position['document'] == DOCUMENT

    def test_the_position_has_the_text_cursor(self):
        self.store_controller.open_file(self._po())
        self._edit_at(1)
        self.unit_controller.view.targets[0].place_cursor(3)

        self.recovery._write()

        position = self._session()['position']
        assert (position['target_index'], position['text_offset']) == (0, 3)


class TestResume(RecoveryScaffolding):
    def test_nothing_to_resume_keeps_the_start_up_file(self):
        assert self.recovery.choose_startup_file('') == ''
        assert self.recovery.choose_startup_file('given.po') == 'given.po'

    def test_an_orphan_is_resumed_at_its_unit_and_text_cursor(self):
        path = self._po()
        position = _at(1, 'Close', target_index=0, text_offset=4)
        self._orphan(path, position=position)

        assert self._resume() == path

        view = self.unit_controller.view
        assert self.store_controller.cursor.index == 1
        assert _pump(lambda: view.targets[0].get_cursor_position() == 4)
        assert self._session()['resume_attempts'] == 1

    def test_a_refocus_after_the_resume_puts_the_text_cursor_back(self):
        self._orphan(self._po(), position=_at(1, 'Close', target_index=0, text_offset=4))
        self._resume()
        view = self.unit_controller.view
        assert _pump(lambda: view.targets[0].get_cursor_position() == 4)

        # A plug-in refreshing the unit refocuses it at the start.
        view.focus_text_view(view.targets[0])

        assert _pump(lambda: view.targets[0].get_cursor_position() == 4)

    def test_after_a_key_press_a_refocus_is_left_alone(self):
        self._orphan(self._po(), position=_at(1, 'Close', target_index=0, text_offset=4))
        self._resume()
        view = self.unit_controller.view
        assert _pump(lambda: view.targets[0].get_cursor_position() == 4)

        self.recovery._on_user_input()
        view.focus_text_view(view.targets[0])

        assert not _pump(lambda: view.targets[0].get_cursor_position() == 4, seconds=0.5)

    def test_a_write_before_the_unit_is_selected_keeps_the_saved_position(self):
        path = self._po()
        self._orphan(path, position=_at(3, 'Quit'))
        self.recovery.choose_startup_file('')
        self.store_controller.open_file(path)
        self.recovery.startup_file_opened(True)

        self.recovery._write()

        assert self._session()['position']['unit_index'] == 3

    def test_the_resumed_slot_becomes_this_processs_slot(self):
        path = self._po()
        orphan = self._orphan(path)

        self._resume()

        assert self.recovery.slot.id == orphan.id
        assert self._slots() == [orphan.id]
        assert recovery.find_orphans(self.root) == []

    def test_the_unit_is_found_by_id_if_the_file_moved_it(self):
        path = self._po()
        self._orphan(path, position=_at(0, 'Save'))

        self._resume()

        assert self.store_controller.cursor.index == 2

    def test_a_start_up_file_resumes_only_its_own_slot(self):
        path = self._po()
        other = self._po('other.po')
        self._orphan(path, position=_at(2, 'Save'))

        assert self.recovery.choose_startup_file(other) == other
        self.recovery.startup_file_opened(False)
        orphans = recovery.find_orphans(self.root)
        assert len(orphans) == 1
        orphans[0].close()

        assert self._resume(path) == path
        assert self.store_controller.cursor.index == 2

    def test_the_newest_orphan_is_resumed(self):
        self._orphan(self._po())
        time.sleep(0.01)
        newer = self._po('newer.po')
        self._orphan(newer)

        assert self._resume() == newer

    def test_a_stable_session_resets_the_crash_loop_guard(self):
        self._orphan(self._po(), attempts=1)
        self._resume()
        assert self._session()['resume_attempts'] == 2

        self.recovery._on_stable(self.recovery.slot)

        assert self._session()['resume_attempts'] == 0

    def test_two_failed_resumes_show_the_welcome_screen_and_offer_the_file(self):
        path = self._po()
        orphan = self._orphan(path, attempts=2)

        assert self.recovery.choose_startup_file('') == ''
        self.recovery.show_startup_notices()

        assert [n[:2] for n in self.notices] == [('blocked', path)]
        assert self._slots() == [orphan.id]

    def test_reopen_resumes_the_held_back_file(self):
        path = self._po()
        self._orphan(path, attempts=2, position=_at(3, 'Quit'))
        self.recovery.choose_startup_file('')
        self.recovery.show_startup_notices()

        self.notices[0][2]()
        self._settle()

        assert self.store_controller.store.get_filename() == path
        assert self.store_controller.cursor.index == 3
        assert self._session()['resume_attempts'] == 1

    def test_reopen_after_opening_the_held_back_file_as_given_resumes_it(self, monkeypatch):
        path = self._po()
        orphan = self._orphan(path, attempts=2, position=_at(3, 'Quit'))
        assert self.recovery.choose_startup_file(path) == path
        self.store_controller.open_file(path)
        self.recovery.startup_file_opened(True)
        self.recovery.show_startup_notices()
        monkeypatch.setattr(self.main_controller, 'show_prompt', lambda *a, **k: pytest.fail())

        self.notices[0][2]()
        self._settle()

        assert self.recovery.slot.id == orphan.id
        assert self._slots() == [orphan.id]
        assert self.store_controller.cursor.index == 3

    def test_a_missing_file_says_so_and_drops_a_slot_with_nothing_unsaved(self):
        path = str(self.tmp_path / 'gone.po')
        self._orphan(path)

        assert self.recovery.choose_startup_file('') == ''
        self.recovery.show_startup_notices()

        assert self.notices == [('missing', path)]
        assert self._slots() == []


class UnsavedScaffolding(RecoveryScaffolding):
    @pytest.fixture(autouse=True)
    def _notices(self, monkeypatch):
        view = self.main_controller.view
        monkeypatch.setattr(
            view,
            'show_recovery_notice',
            lambda count, when, on_recover, on_discard: self.notices.append(
                ('recover', count, on_recover, on_discard)
            ),
        )
        monkeypatch.setattr(view, 'hide_recovery_notice', lambda: self.notices.append('hidden'))
        monkeypatch.setattr(
            view,
            'show_restored_notice',
            lambda count, unplaced=0: self.notices.append(('restored', count, unplaced)),
        )

    def _type(self, index, text):
        if self.unit_controller.current_unit is not self.store_controller.store[index]:
            self._edit_at(index)
        buffer = self.unit_controller.view.targets[0].buffer
        buffer.place_cursor(buffer.get_end_iter())
        buffer.insert_interactive_at_cursor(text, -1, True)

    def _journal(self):
        return self.recovery.slot.journal(DOCUMENT).records()

    def _crash(self):
        """Leave the slot as a killed Virtaal would."""
        self.recovery._write()
        slot, self.recovery.slot = self.recovery.slot, None
        slot.close()
        self.store_controller.close_file()
        return slot

    def _targets(self):
        return [str(self.store_controller.store[i].target) for i in range(4)]


class TestUnsavedWork(UnsavedScaffolding):
    def test_nothing_is_journalled_before_an_edit(self):
        self.store_controller.open_file(self._po())

        assert sorted(os.listdir(self.recovery.slot.path)) == ['lock', 'session.json']

    def test_leaving_an_edited_unit_journals_it(self):
        self.store_controller.open_file(self._po())

        self._type(0, 'Oop')
        self._edit_at(1)

        [record] = self._journal()
        assert (record['unit_index'], record['unit_id']) == (0, 'Open')
        assert record['fields']['targets'] == ['Oop']

    def test_the_unit_being_typed_in_is_the_draft(self):
        self.store_controller.open_file(self._po())
        self._type(1, ' nou')

        self.recovery._write()

        draft = self._session()['documents'][DOCUMENT]['draft']
        assert draft['unit_index'] == 1
        assert draft['fields']['targets'] == ['Maak toe nou']
        assert self._journal() == []

    def test_saving_starts_a_new_baseline(self):
        path = self._po()
        self.store_controller.open_file(path)
        self._type(0, 'Oop')
        self._edit_at(1)

        self.store_controller.save_file()

        assert self._journal() == []
        baseline = self._session()['documents'][DOCUMENT]['baseline']
        assert baseline == recovery.disk_baseline(path)

    def test_a_crash_offers_the_unsaved_work_without_applying_it(self):
        path = self._po()
        self.store_controller.open_file(path)
        self._type(0, 'Oop')
        self._edit_at(1)
        self._crash()

        self._resume()

        [(kind, count, _recover, _discard)] = self.notices
        assert (kind, count) == ('recover', 1)
        assert self._targets() == ['', 'Maak toe', 'Stoor', '']
        assert not self.store_controller.is_modified()

    def test_recover_reapplies_the_journal_and_the_draft_with_no_unit_done(self):
        self.store_controller.open_file(self._po())
        self._type(0, 'Oop')
        self._type(3, 'Maak klaar')
        self._crash()
        self._resume()
        done = []
        handler = self.unit_controller.connect('unit-done', lambda *a: done.append(a))
        try:
            self.notices[0][2]()
        finally:
            self.unit_controller.disconnect(handler)

        assert self._targets() == ['Oop', 'Maak toe', 'Stoor', 'Maak klaar']
        assert self.store_controller.is_modified()
        assert done == []
        stats = self.store_controller.store.stats
        assert (stats['fuzzy'], stats['translated']) == ([0, 3], [1, 2])

    def test_discard_drops_the_work_but_keeps_the_slot(self):
        self.store_controller.open_file(self._po())
        self._type(0, 'Oop')
        self._edit_at(2)
        slot = self._crash()
        self._resume()

        self.notices[0][3]()

        assert self._targets() == ['', 'Maak toe', 'Stoor', '']
        assert self._journal() == []
        assert self.recovery.slot.id == slot.id
        assert self.store_controller.cursor.index == 2

    def test_a_draft_older_than_the_journal_is_not_reapplied(self):
        self.store_controller.open_file(self._po())
        self._type(0, 'Oo')
        self.recovery._write()
        self._type(0, 'p')
        self._edit_at(1)
        slot = self.recovery.slot
        self.recovery.slot = None
        slot.close()
        self.store_controller.close_file()
        self._resume()

        self.notices[0][2]()

        assert self._targets()[0] == 'Oop'

    def test_edits_made_while_the_notice_waits_are_kept_by_recover(self):
        self.store_controller.open_file(self._po())
        self._type(0, 'Oop')
        self._edit_at(1)
        self._crash()
        self._resume()
        self._type(0, 'Maak oop')
        self._edit_at(2)

        self.notices[0][2]()

        assert self._targets()[0] == 'Maak oop'

    def test_quitting_while_the_notice_waits_keeps_the_work(self):
        self.store_controller.open_file(self._po())
        self._type(0, 'Oop')
        self._edit_at(1)
        slot = self._crash()
        self._resume()

        self.main_controller.emit('quit')

        [orphan] = recovery.find_orphans(self.root)
        assert orphan.id == slot.id
        assert len(recovery.read_journal(os.path.join(slot.path, '0.journal.jsonl'))) == 1
        orphan.close()

    def test_a_signal_end_reapplies_the_work_silently(self):
        self.store_controller.open_file(self._po())
        self._type(0, 'Oop')
        self._edit_at(1)
        self.recovery.slot.data['end_reason'] = 'signal'
        self._crash()

        self._resume()

        assert self.notices == [('restored', 1, 0)]
        assert self._targets()[0] == 'Oop'
        assert self.store_controller.is_modified()

    def test_the_crash_question_can_be_switched_off(self, monkeypatch):
        monkeypatch.setattr(recoverycontroller, 'ASK_BEFORE_REAPPLYING_AFTER_A_CRASH', False)
        self.store_controller.open_file(self._po())
        self._type(0, 'Oop')
        self._edit_at(1)
        self._crash()

        self._resume()

        assert self.notices == [('restored', 1, 0)]
        assert self._targets()[0] == 'Oop'

    def test_a_file_changed_since_is_restored_by_unit_and_saved_as(self):
        path = self._po()
        self.store_controller.open_file(path)
        self._type(2, ' nou')
        self._edit_at(0)
        self.recovery.slot.data['end_reason'] = 'signal'
        self._crash()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(PO.replace('msgid "Open"\nmsgstr ""\n\n', ''))

        self._resume()

        assert self.store_controller.store[1].target == 'Stoor nou'
        assert self.main_controller.get_force_saveas()

    def test_an_updated_template_is_position_only(self):
        self.store_controller.open_file(self._po())
        self.store_controller.update_file(self._po('new.pot', text=PO.replace('Quit', 'Exit')))

        doc = self._session()['documents'][DOCUMENT]
        assert doc['baseline'] == {'kind': 'none'}
        self._type(0, 'Oop')
        self._edit_at(1)
        assert self._journal() == []


class TestEndSession(UnsavedScaffolding):
    """A signal or the OS ending Virtaal."""

    @pytest.fixture(autouse=True)
    def _not_ending(self):
        yield
        self.recovery._ending = False

    def test_ending_keeps_the_slot_with_the_draft(self):
        self.store_controller.open_file(self._po())
        self._type(0, 'Oop')
        self._edit_at(1)
        self._type(1, ' nou')
        slot = self.recovery.slot

        self.recovery.end_session('signal')
        self.main_controller.emit('quit')

        [orphan] = recovery.find_orphans(self.root)
        assert orphan.id == slot.id
        assert orphan.data['end_reason'] == 'signal'
        assert orphan.document(DOCUMENT)['draft']['fields']['targets'] == ['Maak toe nou']
        assert orphan.data['position']['unit_index'] == 1
        orphan.close()

    def test_the_next_start_up_resumes_silently_with_the_work(self):
        self.store_controller.open_file(self._po())
        self._type(0, 'Oop')
        self._edit_at(1)
        self._type(1, ' nou')
        self.recovery.end_session('signal')
        self.main_controller.emit('quit')
        self.recovery._ending = False
        self.store_controller.close_file()

        self._resume()

        assert self.notices == [('restored', 2, 0)]
        assert self._targets()[:2] == ['Oop', 'Maak toe nou']
        assert self.store_controller.cursor.index == 1
        assert self._session()['end_reason'] is None

    def test_ending_with_nothing_unsaved_still_keeps_the_position(self):
        self.store_controller.open_file(self._po())
        self._edit_at(3)

        self.recovery.end_session('signal')
        self.main_controller.emit('quit')
        self.recovery._ending = False
        self.store_controller.close_file()
        self._resume()

        assert self.notices == []
        assert self.store_controller.cursor.index == 3

    def test_ending_while_the_crash_notice_waits_keeps_asking(self):
        self.store_controller.open_file(self._po())
        self._type(0, 'Oop')
        self._edit_at(1)
        self._crash()
        self._resume()

        self.recovery.end_session('signal')

        assert self._session()['end_reason'] is None


PO_B = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "New"
msgstr ""

msgid "Print"
msgstr "Druk"
'''


class TestStream(UnsavedScaffolding):
    """Several files open as one list: a document per modified file."""

    def _open_stream(self):
        self._po('a.po')
        self._po('b.po', PO_B)
        self.store_controller.open_files([str(self.tmp_path)])
        return [str(self.tmp_path / 'a.po'), str(self.tmp_path / 'b.po')]

    def _resume_stream(self):
        target = self.recovery.choose_startup_file('')
        if target:
            self.store_controller.open_files([target] if isinstance(target, str) else target)
            self.unit_controller.load_unit(self.store_controller.store[0])
        self.recovery.startup_file_opened(bool(target))
        if target:
            self._settle()
            cursor = self.store_controller.cursor
            self.unit_controller.load_unit(self.store_controller.store[cursor.index])
        return target

    def test_the_slot_names_the_paths_and_has_no_document_until_an_edit(self):
        self._open_stream()

        session = self._session()
        assert session['stream'] == {'paths': [str(self.tmp_path)]}
        assert session['documents'] == {}

    def test_each_edited_file_gets_its_own_journal(self):
        a, b = self._open_stream()
        self._type(0, 'Oop')
        self._type(4, 'Nuut')
        self._edit_at(5)
        self.recovery._write()

        docs = self._session()['documents']
        assert sorted(doc['open_path'] for doc in docs.values()) == [a, b]
        for doc_id, doc in docs.items():
            [record] = self.recovery.slot.journal(doc_id).records()
            assert record['unit_index'] == 0
            assert record['fields']['targets'] == (['Oop'] if doc['open_path'] == a else ['Nuut'])

    def test_the_position_names_the_file(self):
        _a, b = self._open_stream()
        self._edit_at(5)

        self.recovery._write()

        position = self._session()['position']
        assert (position['path'], position['unit_index'], position['unit_id']) == (b, 1, 'Print')

    def test_saving_drops_the_documents(self):
        self._open_stream()
        self._type(4, 'Nuut')
        self._edit_at(5)

        self.store_controller.save_file()

        assert self._session()['documents'] == {}

    def test_a_crash_resumes_at_the_file_and_unit_with_every_files_work(self):
        self._open_stream()
        self._type(0, 'Oop')
        self._type(4, 'Nuut')
        self._edit_at(5)
        self._crash()

        assert self._resume_stream() == str(self.tmp_path)
        [(kind, count, on_recover, _discard)] = self.notices
        assert (kind, count) == ('recover', 2)
        assert self.store_controller.cursor.index == 5

        on_recover()

        store = self.store_controller.store
        assert [str(store[i].target) for i in (0, 4)] == ['Oop', 'Nuut']
        assert sorted(e.display_name for e in store.modified_entries()) == ['a.po', 'b.po']
        assert self.store_controller.is_modified()

    def test_a_signal_end_restores_the_draft_in_its_own_file(self):
        self._open_stream()
        self._type(4, 'Nuut')
        self.recovery.end_session('signal')
        self.main_controller.emit('quit')
        self.recovery._ending = False
        self.store_controller.close_file()

        self._resume_stream()

        assert self.notices == [('restored', 1, 0)]
        assert str(self.store_controller.store[4].target) == 'Nuut'
        assert self.store_controller.cursor.index == 4
