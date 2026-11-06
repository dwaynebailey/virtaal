#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Keep this process's recovery slot up to date, and resume an orphaned
one at start-up (#2110). See virtaal.support.recovery for the slot."""

import functools
import logging
import os
import time
import weakref

from gi.repository import GLib

from virtaal.support import recovery

# The slot's id for the one document a process edits.
DOCUMENT = '0'
# Position is written this long after the last move.
DEBOUNCE_MS = 1500
# A resumed session this long without a crash resets the crash-loop guard.
STABLE_SECONDS = 5
# Automatic resumes allowed in a row before the guard stops them.
MAX_RESUME_ATTEMPTS = 2


def _guarded(method):
    """Recovery must never get in the way of editing: log and carry on."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            logging.exception('Recovery: %s failed', method.__name__)
            return None

    return wrapper


class RecoveryController:
    """Owns this process's recovery slot."""

    def __init__(self, main_controller, root=None):
        self.main_controller = main_controller
        main_controller.recovery_controller = self
        self.store_controller = main_controller.store_controller
        self._root = root
        self.slot = None
        self._resume = None
        self._adopted = False
        self._blocked = None
        self._missing = None
        self._cursor = None
        self._cursor_handler = None
        self._dirty_at = None
        self._debounce_id = None
        self._buffers = weakref.WeakSet()
        self._unit_controller = None

        self.store_controller.connect('store-loaded', self._on_store_loaded)
        self.store_controller.connect('store-saved', self._on_store_saved)
        self.store_controller.connect('store-closed', self._on_store_closed)
        main_controller.connect('controller-registered', self._on_controller_registered)
        main_controller.connect('quit', self._on_quit)
        if main_controller.unit_controller is not None:
            self._on_controller_registered(main_controller, main_controller.unit_controller)

    # START-UP #
    @_guarded
    def choose_startup_file(self, startupfile, wanted=None):
        """The file to open at start-up: an orphaned slot's file when
        there is one to resume, otherwise startupfile. With a start-up
        file (wanted: its path), only a slot for that file resumes."""
        orphans = recovery.find_orphans(self._root)
        wanted = os.path.abspath(wanted or startupfile) if startupfile else None
        chosen = None
        for slot in orphans:
            doc = slot.document(DOCUMENT)
            if not doc or not doc.get('open_path'):
                continue
            if wanted is None or os.path.abspath(doc['open_path']) == wanted:
                chosen = slot
                break
        for slot in orphans:
            if slot is not chosen:
                slot.close()
        if chosen is None:
            return startupfile

        path = chosen.document(DOCUMENT)['open_path']
        if chosen.data['resume_attempts'] >= MAX_RESUME_ATTEMPTS:
            self._blocked = (chosen.path, path)
            chosen.close()
            return startupfile
        if not os.path.isfile(path):
            self._missing = path
            if chosen.has_unsaved_work():
                chosen.close()
            else:
                chosen.delete()
            return startupfile
        chosen.data['resume_attempts'] += 1
        chosen.write()
        self._resume = chosen
        return path

    def show_startup_notices(self):
        view = self.main_controller.view
        if self._blocked:
            _path, filename = self._blocked
            view.show_resume_blocked_notice(filename, self.reopen_blocked)
        if self._missing:
            view.show_startup_open_failure_notice(self._missing)

    @_guarded
    def startup_file_opened(self, opened):
        """After the start-up open: resume the chosen slot if it was
        the file that opened."""
        if self._resume is not None:
            # It failed to open: kept for another try.
            self._resume.close()
            self._resume = None
        if self._adopted:
            self._adopted = False
            self._resume_into_store()

    @_guarded
    def reopen_blocked(self):
        """Resume the slot the crash-loop guard held back, at the user's
        request."""
        if not self._blocked:
            return
        slot_path, filename = self._blocked
        self._blocked = None
        self.main_controller.view.hide_resume_blocked_notice()
        name = os.path.basename(slot_path)
        root = os.path.dirname(slot_path)
        orphans = recovery.find_orphans(root)
        chosen = next((slot for slot in orphans if slot.id == name), None)
        for slot in orphans:
            if slot is not chosen:
                slot.close()
        if chosen is None:
            self.main_controller.open_file(filename)
            return
        chosen.data['resume_attempts'] = 1
        chosen.write()
        self._resume = chosen
        opened = self.main_controller.open_file(chosen.document(DOCUMENT)['open_path'])
        self.startup_file_opened(opened)

    def _resume_into_store(self):
        slot = self.slot
        position = slot.data.get('position')
        slot.data['end_reason'] = None
        slot.write()
        if position:
            self._restore_position(position)
        GLib.timeout_add_seconds(STABLE_SECONDS, self._on_stable, slot)

    def _on_stable(self, slot):
        if slot is self.slot:
            self._write_guarded(reset_attempts=True)
        return False

    def _restore_position(self, position):
        store = self.store_controller.store
        index = recovery.locate(store, position, {})
        if index is None:
            index = position.get('unit_index')
            if not isinstance(index, int) or not 0 <= index < len(store):
                return
        self.store_controller.cursor.force_index(index)
        unit = store[index]
        target_n = position.get('target_index') or 0
        offset = position.get('text_offset') or 0
        attempts = [0]

        def place_text_cursor():
            attempts[0] += 1
            uc = self._unit_controller
            if self.store_controller.store is not store:
                return False
            view = uc and uc.view
            treeview = self.store_controller.view._treeview
            ready = (
                view is not None
                and uc.current_unit is unit
                and view.targets
                and not getattr(treeview, '_waiting_for_row_change', 0)
            )
            if not ready:
                return attempts[0] < 100
            n = max(0, min(target_n, len(view.targets) - 1))
            view.focused_target_n = n
            textbox = view.targets[n]
            textbox.place_cursor(max(0, min(offset, textbox.buffer.get_char_count())))
            return False

        GLib.timeout_add(50, place_text_cursor)

    # SLOT UPKEEP #
    @_guarded
    def _on_store_loaded(self, store_controller):
        path = store_controller.opened_filename
        resume = self._resume
        if resume is not None:
            self._resume = None
            doc = resume.document(DOCUMENT)
            if path and os.path.abspath(doc['open_path']) == path:
                self._drop_slot()
                self.slot = resume
                self._adopted = True
                self._track_cursor()
                return
            resume.close()
        doc = self.slot and self.slot.document(DOCUMENT)
        if doc and path and doc.get('open_path') == path:
            # Reloaded or reverted: the same file, freshly read.
            self._new_document(path)
            self._track_cursor()
            self._write()
            return
        self._drop_slot()
        if path is None:
            return
        self.slot = recovery.Slot.create(self._root)
        self._new_document(path)
        self._track_cursor()
        self._write()

    def _new_document(self, path):
        sc = self.store_controller
        bundle = sc.get_bundle_filename()
        self.slot.set_document(
            DOCUMENT,
            {
                'open_path': path,
                'target_path': bundle or sc.store.get_filename(),
                'force_saveas': self.main_controller.get_force_saveas(),
                'is_bundle': bundle is not None,
                'format': sc.store.get_store_type(),
            },
        )

    @_guarded
    def _on_store_saved(self, store_controller):
        doc = self.slot and self.slot.document(DOCUMENT)
        if not doc:
            return
        sc = self.store_controller
        target = sc.get_bundle_filename() or sc.store.get_filename()
        doc['target_path'] = target
        doc['open_path'] = os.path.abspath(target)
        doc['force_saveas'] = self.main_controller.get_force_saveas()
        self._write()

    @_guarded
    def _on_store_closed(self, store_controller):
        self._untrack_cursor()
        self._drop_slot()

    @_guarded
    def _on_quit(self, main_controller):
        self._cancel_debounce()
        self._drop_slot()

    def _drop_slot(self):
        """End the slot: the user closed the file or quit."""
        self._cancel_debounce()
        if self.slot is not None:
            self.slot.delete()
            self.slot = None

    # POSITION #
    def _on_controller_registered(self, main_controller, controller):
        if controller is main_controller.unit_controller and controller is not None:
            self._unit_controller = controller

    def _track_cursor(self):
        self._untrack_cursor()
        cursor = self.store_controller.cursor
        if cursor is None:
            return
        self._cursor = cursor
        self._cursor_handler = cursor.connect('cursor-changed', self._on_cursor_changed)

    def _untrack_cursor(self):
        if self._cursor is not None and self._cursor_handler is not None:
            self._cursor.disconnect(self._cursor_handler)
        self._cursor = None
        self._cursor_handler = None

    def _on_cursor_changed(self, cursor):
        self._watch_text_cursors()
        self.touch()

    def _watch_text_cursors(self):
        """Moving the text cursor without typing moves the position too."""
        view = self._unit_controller and self._unit_controller.view
        for textbox in getattr(view, 'targets', None) or ():
            buffer = textbox.buffer
            if buffer not in self._buffers:
                self._buffers.add(buffer)
                buffer.connect('notify::cursor-position', lambda *_a: self.touch())

    def touch(self):
        """Note a change worth writing; written once things go quiet."""
        if self.slot is None:
            return
        self._dirty_at = time.monotonic()
        if self._debounce_id is None:
            self._debounce_id = GLib.timeout_add(
                DEBOUNCE_MS, self._on_debounce, priority=GLib.PRIORITY_LOW
            )

    def _on_debounce(self):
        self._debounce_id = None
        if self._dirty_at is None:
            return False
        quiet_ms = (time.monotonic() - self._dirty_at) * 1000
        if quiet_ms < DEBOUNCE_MS:
            self._debounce_id = GLib.timeout_add(
                int(DEBOUNCE_MS - quiet_ms) + 1, self._on_debounce, priority=GLib.PRIORITY_LOW
            )
            return False
        self._write_guarded()
        return False

    def _cancel_debounce(self):
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = None
        self._dirty_at = None

    def _position(self):
        sc = self.store_controller
        cursor = sc.cursor
        if cursor is None or sc.store is None or cursor.index < 0:
            return None
        index = cursor.index
        unit = sc.store[index]
        position = dict(recovery.unit_key(unit, index), document=DOCUMENT)
        position['target_index'] = 0
        position['text_offset'] = 0
        view = self._unit_controller and self._unit_controller.view
        if view is not None and view.unit is unit and view.targets:
            n = view.focused_target_n or 0
            if n < len(view.targets):
                position['target_index'] = n
                position['text_offset'] = view.targets[n].get_cursor_position()
        return position

    @_guarded
    def _write_guarded(self, reset_attempts=False):
        self._write(reset_attempts)

    def _write(self, reset_attempts=False):
        if self.slot is None:
            return
        self._dirty_at = None
        if reset_attempts:
            self.slot.data['resume_attempts'] = 0
        position = self._position()
        if position is not None:
            self.slot.data['position'] = position
        self.slot.write()
