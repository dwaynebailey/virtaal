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
        path.write_text(text)
        return str(path)

    def _slots(self):
        return sorted(name for name in os.listdir(self.root) if '.' not in name)

    def _session(self, slot=None):
        slot = slot or self.recovery.slot
        with open(os.path.join(slot.path, 'session.json')) as f:
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

    def _resume(self, startupfile=''):
        path = self.recovery.choose_startup_file(startupfile)
        opened = False
        if path:
            self.store_controller.open_file(path)
            self.unit_controller.load_unit(self.store_controller.store[0])
            opened = True
        self.recovery.startup_file_opened(opened)
        if opened:
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

        assert self.store_controller.store.get_filename() == path
        assert self.store_controller.cursor.index == 3
        assert self._session()['resume_attempts'] == 1

    def test_a_missing_file_says_so_and_drops_a_slot_with_nothing_unsaved(self):
        path = str(self.tmp_path / 'gone.po')
        self._orphan(path)

        assert self.recovery.choose_startup_file('') == ''
        self.recovery.show_startup_notices()

        assert self.notices == [('missing', path)]
        assert self._slots() == []
