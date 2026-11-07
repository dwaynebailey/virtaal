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

from gi.repository import GLib, Gtk

from virtaal.support import recovery

# After a crash, whether to ask before re-applying unsaved work (True),
# or re-apply it silently as after an OS shutdown (False). Resuming the
# position never asks.
ASK_BEFORE_REAPPLYING_AFTER_A_CRASH = True

# The slot's id for the one document a process edits.
DOCUMENT = '0'
# Position and the unit being typed in are written this long after the
# last move or keystroke.
DEBOUNCE_MS = 1500
# Journal lines are flushed to disk at most this often.
SYNC_MS = 2000
# Beyond this many re-applied units, recount the stats from scratch.
MAX_UNIT_STATS_UPDATES = 100
# A resumed session this long without a crash resets the crash-loop guard.
STABLE_SECONDS = 5
# Automatic resumes allowed in a row before the guard stops them.
MAX_RESUME_ATTEMPTS = 2
# Until the user types or clicks, the text cursor is put back for this
# long after a resume.
TEXT_CURSOR_HOLD_SECONDS = 15


_RECORD_KEYS = ('unit_index', 'unit_id', 'source_sha1', 'fields')


def _stream_document(path):
    """The slot's id for one file of several open together."""
    import hashlib

    return 'f' + hashlib.sha1(path.encode('utf-8')).hexdigest()[:16]


def _slot_target(slot):
    """What a slot reopens: a path, a list of paths, or None."""
    stream = slot.data.get('stream')
    if isinstance(stream, dict):
        paths = stream.get('paths')
        if isinstance(paths, list) and paths and all(isinstance(p, str) for p in paths):
            return paths
        return None
    doc = slot.document(DOCUMENT)
    return doc.get('open_path') if doc else None


def _as_paths(target):
    return [os.path.abspath(p) for p in (target if isinstance(target, list) else [target])]


def _startup_target(target):
    """A slot's target as Virtaal's start-up takes it."""
    if isinstance(target, list) and len(target) == 1:
        return target[0]
    return target


def _choose_orphan(orphans, wanted):
    """The first orphan with a target, and for wanted (absolute paths,
    or None for any) only one for the same paths. The rest are closed."""
    chosen = None
    for slot in orphans:
        target = _slot_target(slot)
        if not target:
            continue
        if wanted is None or _as_paths(target) == wanted:
            chosen = slot
            break
    for slot in orphans:
        if slot is not chosen:
            slot.close()
    return chosen


def _missing_paths(target):
    """Those of a slot target's paths no longer on disk."""
    if isinstance(target, list):
        return [p for p in target if not os.path.exists(p)]
    return [] if os.path.isfile(target) else [target]


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
        self._sync_id = None
        self._buffers = weakref.WeakSet()
        self._unit_controller = None
        self._text_cursor = None
        # A resumed unit not selected yet: the cursor isn't where the slot says.
        self._selecting = False
        self._watching_editor = False
        # Whether the document's units can be journalled and replayed.
        self._replayable = False
        self._baseline_ok = True
        # While the crash notice waits: the last journal seq it covers in
        # each document, and the units edited since as (document, unit
        # index in its file).
        self._pending_upto = None
        self._touched = set()
        # Recently selected units with their indices: unit-done comes
        # after the cursor has moved on.
        self._recent = []

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
        """The file to open at start-up: an orphaned slot's file, or its
        list of paths, when there is one to resume, otherwise
        startupfile. With a start-up file (wanted: its path, or a list of
        paths), only a slot for the same paths resumes."""
        wanted = _as_paths(wanted or startupfile) if startupfile else None
        chosen = _choose_orphan(recovery.find_orphans(self._root), wanted)
        if chosen is None:
            return startupfile

        paths = _slot_target(chosen)
        target = _startup_target(paths)
        if chosen.data['resume_attempts'] >= MAX_RESUME_ATTEMPTS:
            self._blocked = (chosen.path, target)
            chosen.close()
            return startupfile
        missing = _missing_paths(paths)
        if missing:
            self._missing = missing[0]
            if chosen.has_unsaved_work():
                chosen.close()
            else:
                chosen.delete()
            return startupfile
        chosen.data['resume_attempts'] += 1
        chosen.write()
        self._resume = chosen
        return target

    def show_startup_notices(self):
        view = self.main_controller.view
        if self._blocked:
            _path, target = self._blocked
            view.show_resume_blocked_notice(_as_paths(target)[0], self.reopen_blocked)
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
            if isinstance(filename, list) or os.path.isdir(filename):
                self.main_controller.open_files(_as_paths(filename))
            else:
                self.main_controller.open_file(filename)
            return
        chosen.data['resume_attempts'] = 1
        chosen.write()
        self._resume = chosen
        self.startup_file_opened(self._open_to_resume(_startup_target(_slot_target(chosen))))

    def _open_to_resume(self, path):
        sc = self.store_controller
        if isinstance(path, list) or os.path.isdir(path):
            return self.main_controller.open_files(_as_paths(path))
        if sc.store is not None and sc.opened_filename == path and not sc.is_modified():
            # The start-up file, opened as given: read it again unasked.
            sc.open_file(path)
            self.main_controller.mode_controller.refresh_mode()
            return True
        return self.main_controller.open_file(path)

    def _resume_into_store(self):
        slot = self.slot
        when = slot.updated_at
        end_reason = slot.data.get('end_reason')
        position = slot.data.get('position')
        by_doc, unplaced = self._replayable_records(slot)
        slot.data['end_reason'] = None
        slot.write()
        if by_doc:
            if end_reason or not ASK_BEFORE_REAPPLYING_AFTER_A_CRASH:
                changed, not_placed = self._apply(by_doc)
                self.main_controller.view.show_restored_notice(len(changed), unplaced + not_placed)
            else:
                self._pending_upto = {
                    doc_id: records[-1]['seq'] for doc_id, records in by_doc.items()
                }
                self._touched = set()
                count = sum(len(recovery.latest_per_unit(r)) for r in by_doc.values())
                self.main_controller.view.show_recovery_notice(
                    count, when, self.recover, self.discard
                )
        elif unplaced:
            self.main_controller.view.show_restored_notice(0, unplaced)
        if position:
            self._restore_position(position)
        GLib.timeout_add_seconds(STABLE_SECONDS, self._on_stable, slot)

    def _replayable_records(self, slot):
        """Each replayable document's journal, with its draft appended
        if newer, as (records by document, units left out). The drafts
        are cleared."""
        stream = self.store_controller.is_stream()
        by_doc = {}
        unplaced = 0
        for doc_id, doc in slot.data['documents'].items():
            if self._replayable or (stream and doc.get('baseline', {}).get('kind') == 'disk'):
                journal = slot.journal(doc_id)
                draft = doc.get('draft')
                if draft and draft.get('after_seq', 0) >= journal.seq:
                    journal.append({key: draft.get(key) for key in _RECORD_KEYS})
                records = journal.records()
                baseline_ok = recovery.baseline_matches(doc['baseline'])
                if stream and not baseline_ok:
                    # Changed since: there is no Save As to keep both.
                    unplaced += len(recovery.latest_per_unit(records))
                elif records:
                    by_doc[doc_id] = records
                if not stream:
                    self._baseline_ok = baseline_ok
            doc['draft'] = None
        return by_doc, unplaced

    # UNSAVED WORK #
    @_guarded
    def recover(self):
        """Re-apply the unsaved work the crash notice offered, except to
        units edited since."""
        if self._pending_upto is None:
            return
        pending, self._pending_upto = self._pending_upto, None
        self.main_controller.view.hide_recovery_notice()
        skip = set(self._touched)
        self._touched = set()
        current = self._unit_controller and self._unit_controller.current_unit
        if current is not None and getattr(current, '_modified', False):
            index = self._index_of(current)
            if index is not None:
                skip.add(self._document_of(index))
        by_doc = {
            doc_id: [r for r in self.slot.journal(doc_id).records() if r['seq'] <= upto]
            for doc_id, upto in pending.items()
        }
        changed, unplaced = self._apply(by_doc, skip)
        if unplaced:
            self.main_controller.view.show_restored_notice(len(changed), unplaced)

    @_guarded
    def discard(self):
        """Drop the unsaved work the crash notice offered; the position
        and later edits stay."""
        if self._pending_upto is None:
            return
        pending, self._pending_upto = self._pending_upto, None
        self._touched = set()
        self.main_controller.view.hide_recovery_notice()
        for doc_id, upto in pending.items():
            journal = self.slot.journal(doc_id)
            journal.rewrite([r for r in journal.records() if r['seq'] > upto])

    def _apply(self, by_doc, skip=()):
        """Replay each document's records into the open store, with no
        unit-done: the units are restored, not newly translated. skip
        holds (document, unit index in its file) pairs."""
        sc = self.store_controller
        store = sc.store
        changed = []
        unplaced = 0
        for doc_id, records in by_doc.items():
            units, offset = self._document_units(doc_id)
            if units is None:
                unplaced += len(recovery.latest_per_unit(records))
                continue
            local_skip = {index for doc, index in skip if doc == doc_id}
            doc_changed, doc_unplaced = recovery.replay(units, records, local_skip)
            unplaced += doc_unplaced
            changed += [offset + index for index in doc_changed]
            if doc_changed and sc.is_stream():
                store.mark_modified(offset)
        if not changed:
            return changed, unplaced
        if len(changed) > MAX_UNIT_STATS_UPDATES:
            store.update_stats()
        else:
            for index in changed:
                store.update_unit_stats(index)
        for index in changed:
            store.update_unit_checks(index)
        sc.emit('stats-changed')
        sc.emit('checks-changed')
        sc.set_modified(True)
        if not self._baseline_ok:
            # The file changed since: never overwrite it unasked.
            self.main_controller.set_force_saveas(True)
        self._refresh_units(changed)
        return changed, unplaced

    def _refresh_units(self, indices):
        sc = self.store_controller
        model = sc.view._treeview.get_model()
        if model is not None and hasattr(model, 'store_index_to_path'):
            for index in indices:
                path = model.store_index_to_path(index)
                if path is not None:
                    tree_path = Gtk.TreePath(path)
                    model.row_changed(tree_path, model.get_iter(tree_path))
        uc = self._unit_controller
        unit = uc and uc.current_unit
        if unit is not None and any(sc.store[index] is unit for index in indices):
            # Reload the editor without finishing the unit.
            uc.current_unit = None
            uc.view.unit = None
            uc.load_unit(unit)

    def _on_stable(self, slot):
        if slot is self.slot:
            self._write_guarded(reset_attempts=True)
        return False

    def _document_units(self, doc_id):
        """(the units doc_id's records index, their first store index),
        or (None, 0)."""
        sc = self.store_controller
        if not sc.is_stream():
            return sc.store, 0
        doc = self.slot.document(doc_id)
        return self._file_units(doc and doc.get('open_path'))

    def _file_units(self, path):
        store = self.store_controller.store
        for number, segment in enumerate(store.segments):
            if segment.path == path:
                return store.load_segment(number).model, segment.offset
        return None, 0

    def _document_of(self, index, create=False):
        """(document, unit index in its file) for a store index. With
        several files open, create makes the file's document."""
        sc = self.store_controller
        if not sc.is_stream():
            return DOCUMENT, index
        segment, local = sc.store.locate(index)
        doc_id = _stream_document(segment.path)
        if create and self.slot.document(doc_id) is None:
            self.slot.set_document(
                doc_id,
                {
                    'open_path': segment.path,
                    'target_path': segment.path,
                    'baseline': recovery.disk_baseline(segment.path),
                    'force_saveas': False,
                    'is_bundle': False,
                    'format': segment.model.get_store_type(),
                    'draft': None,
                },
            )
            self.slot.journal(doc_id).truncate()
        return doc_id, local

    def _restore_position(self, position):
        store = self.store_controller.store
        units, offset = store, 0
        if self.store_controller.is_stream():
            units, offset = self._file_units(position.get('path'))
            if units is None:
                return
        index = recovery.locate(units, position, {})
        if index is None:
            index = position.get('unit_index')
            if not isinstance(index, int) or not 0 <= index < len(units):
                return
        index += offset
        self._text_cursor = {
            'unit': store[index],
            'target_n': position.get('target_index') or 0,
            'offset': position.get('text_offset') or 0,
            'until': time.monotonic() + TEXT_CURSOR_HOLD_SECONDS,
        }
        self._watch_editor()
        self._selecting = True
        GLib.timeout_add(50, self._select_resumed_unit, store, index, self._text_cursor)

    def _select_resumed_unit(self, store, index, wanted):
        """Move to the unit once the store view has started editing the
        first one: a move before that leaves the editor undrawn."""
        sc = self.store_controller
        if sc.store is not store:
            return False
        if sc.view._treeview._waiting_for_row_change and time.monotonic() < wanted['until']:
            return True
        self._selecting = False
        sc.cursor.force_index(index)
        wanted['selected'] = True
        GLib.timeout_add(50, self._place_text_cursor)
        return False

    def _watch_editor(self):
        """Refocusing the editor (a plug-in loading after the resume
        refreshes the unit) puts the text cursor back at the start."""
        if self._watching_editor or self._unit_controller is None:
            return
        self._watching_editor = True
        self._unit_controller.view.connect('target-focused', self._on_target_focused)
        window = self.main_controller.view.main_window
        window.connect('key-press-event', self._on_user_input)
        # Before the clicked widget handles it.
        self._clicks = Gtk.GestureMultiPress(widget=window)
        self._clicks.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._clicks.connect('pressed', self._on_user_input)

    def _on_target_focused(self, view, target_n):
        if self._text_cursor is not None:
            GLib.timeout_add(20, self._place_text_cursor)

    def _on_user_input(self, *_args):
        self._text_cursor = None
        return False

    def _place_text_cursor(self):
        """Put the text cursor where it was, once the editor is ready.
        Returns True to be tried again."""
        wanted = self._text_cursor
        if wanted is None or time.monotonic() > wanted['until']:
            self._text_cursor = None
            return False
        if not wanted.get('selected'):
            # _select_resumed_unit() comes back here once it has moved.
            return False
        uc = self._unit_controller
        view = uc and uc.view
        if view is None or uc.current_unit is not wanted['unit']:
            if self.store_controller.cursor is None or (
                self.store_controller.cursor.deref() is not wanted['unit']
            ):
                # Moved on.
                self._text_cursor = None
                return False
            return True
        if not view.targets or self.store_controller.view._treeview._waiting_for_row_change:
            return True
        n = max(0, min(wanted['target_n'], len(view.targets) - 1))
        textbox = view.targets[n]
        offset = max(0, min(wanted['offset'], textbox.buffer.get_char_count()))
        if view.focused_target_n != n or textbox.get_cursor_position() != offset:
            view.focused_target_n = n
            textbox.place_cursor(offset)
        return False

    # SLOT UPKEEP #
    @_guarded
    def _on_store_loaded(self, store_controller):
        if store_controller.is_stream():
            self._on_stream_loaded(store_controller.opened_paths)
            return
        path = store_controller.opened_filename
        resume = self._resume
        if resume is not None:
            self._resume = None
            doc = resume.document(DOCUMENT)
            if path and doc and os.path.abspath(doc['open_path']) == path:
                self._drop_slot()
                self.slot = resume
                self._replayable = doc.get('baseline', {}).get('kind') == 'disk'
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

    def _on_stream_loaded(self, paths):
        resume = self._resume
        if resume is not None:
            self._resume = None
            target = _slot_target(resume)
            if isinstance(target, list) and _as_paths(target) == paths:
                self._drop_slot()
                self.slot = resume
                self._replayable = False
                self._adopted = True
                self._track_cursor()
                return
            resume.close()
        if not (self.slot and self.slot.data.get('stream', {}).get('paths') == paths):
            self._drop_slot()
            self.slot = recovery.Slot.create(self._root)
            self.slot.data['stream'] = {'paths': paths}
        # Opened or reverted: every file as read.
        self._selecting = False
        self._replayable = False
        self._baseline_ok = True
        self._end_pending()
        for doc_id in list(self.slot.data['documents']):
            self.slot.remove_document(doc_id)
        self._track_cursor()
        self._write()

    def _new_document(self, path):
        self._selecting = False
        sc = self.store_controller
        bundle = sc.get_bundle_filename()
        # Replay starts from the file as read, which a bundle's temporary
        # copy or a template update's merge isn't: position only.
        self._replayable = bundle is None and not sc.is_modified()
        self._baseline_ok = True
        self._end_pending()
        self.slot.set_document(
            DOCUMENT,
            {
                'open_path': path,
                'target_path': bundle or sc.store.get_filename(),
                'baseline': recovery.disk_baseline(path) if self._replayable else {'kind': 'none'},
                'force_saveas': self.main_controller.get_force_saveas(),
                'is_bundle': bundle is not None,
                'format': sc.store.get_store_type(),
                'draft': None,
            },
        )
        self.slot.journal(DOCUMENT).truncate()

    def _end_pending(self):
        if self._pending_upto is not None:
            self._pending_upto = None
            self._touched = set()
            self.main_controller.view.hide_recovery_notice()

    @_guarded
    def _on_store_saved(self, store_controller):
        if self.slot is not None and store_controller.is_stream():
            self._on_stream_saved()
            return
        doc = self.slot and self.slot.document(DOCUMENT)
        if not doc:
            return
        sc = self.store_controller
        bundle = sc.get_bundle_filename()
        target = bundle or sc.store.get_filename()
        doc['target_path'] = target
        doc['open_path'] = os.path.abspath(target)
        doc['force_saveas'] = self.main_controller.get_force_saveas()
        doc['draft'] = None
        journal = self.slot.journal(DOCUMENT)
        if self._replayable:
            # The saved file is the new baseline.
            doc['baseline'] = recovery.disk_baseline(doc['open_path'])
            self._baseline_ok = True
            if self._pending_upto is None:
                journal.truncate()
            else:
                journal.rewrite(self._still_pending(DOCUMENT, self._pending_upto.get(DOCUMENT)))
        self._write()

    def _still_pending(self, doc_id, upto):
        """The records the crash notice still offers, once saved."""
        if upto is None:
            return []
        return [
            r
            for r in self.slot.journal(doc_id).records()
            if r['seq'] <= upto and (doc_id, r.get('unit_index')) not in self._touched
        ]

    def _on_stream_saved(self):
        # Every modified file was written: each is its own new baseline.
        for doc_id in list(self.slot.data['documents']):
            upto = (self._pending_upto or {}).get(doc_id)
            if upto is None:
                self.slot.remove_document(doc_id)
                continue
            doc = self.slot.document(doc_id)
            doc['baseline'] = recovery.disk_baseline(doc['open_path'])
            doc['draft'] = None
            self.slot.journal(doc_id).rewrite(self._still_pending(doc_id, upto))
        self._write()

    @_guarded
    def _on_store_closed(self, store_controller):
        self._untrack_cursor()
        self._drop_slot()

    @_guarded
    def end_session(self, reason):
        """The OS or a signal is ending Virtaal, with no one to answer a
        dialog: write everything now and keep the slot, so the next
        start-up resumes silently with the unsaved work."""
        self._ending = True
        if self.slot is None:
            return
        if self.store_controller.is_modified():
            logging.warning('Ending on %s with unsaved changes; kept for the next start-up', reason)
        self._cancel_debounce()
        if self._pending_upto is None:
            # Work the crash notice still offers keeps asking.
            self.slot.data['end_reason'] = reason
        self._write()
        self.slot.sync()

    _ending = False

    @_guarded
    def _on_quit(self, main_controller):
        if self._ending:
            self._cancel_debounce()
            self._cancel_sync()
            if self.slot is not None:
                self.slot.close()
                self.slot = None
            return
        self._drop_slot(write=True)

    def _drop_slot(self, write=False):
        """End the slot: the user closed the file or quit. Unsaved work
        the crash notice still offers stays for the next start-up."""
        self._cancel_debounce()
        self._cancel_sync()
        if self.slot is None:
            return
        if self._pending_upto is not None:
            if write:
                self._write()
            self.slot.close()
        else:
            self.slot.delete()
        self.slot = None
        self._end_pending()
        self._recent = []

    # UNIT CHANGES #
    def _on_unit_modified(self, unit_controller, unit):
        self._text_cursor = None
        self.touch()

    @_guarded
    def _on_unit_done(self, unit_controller, unit, modified):
        if not modified or self.slot is None or not self._journals():
            return
        sc = self.store_controller
        if sc.store is None:
            return
        index = self._index_of(unit)
        if index is None:
            return
        doc_id, local = self._document_of(index, create=True)
        record = dict(recovery.unit_key(unit, local), fields=recovery.unit_fields(unit))
        self.slot.journal(doc_id).append(record)
        if self._pending_upto is not None:
            self._touched.add((doc_id, local))
        if self._sync_id is None:
            self._sync_id = GLib.timeout_add(SYNC_MS, self._on_sync, priority=GLib.PRIORITY_LOW)
        self.touch()

    def _journals(self):
        """Whether unit changes are journalled for replay."""
        return self._replayable or self.store_controller.is_stream()

    def _on_sync(self):
        self._sync_id = None
        if self.slot is not None:
            self.slot.sync()
        return False

    def _cancel_sync(self):
        if self._sync_id is not None:
            GLib.source_remove(self._sync_id)
            self._sync_id = None

    def _index_of(self, unit):
        sc = self.store_controller
        if sc.cursor is not None and sc.cursor.deref() is unit:
            return sc.cursor.index
        for recent, index in self._recent:
            if recent is unit:
                return index
        return sc._unit_index(unit)

    def _draft(self):
        """(document, draft) for the unit being edited, if it has changes
        no journal line has."""
        unit = self._unit_controller and self._unit_controller.current_unit
        if unit is None or not getattr(unit, '_modified', False):
            return None
        index = self._index_of(unit)
        if index is None:
            return None
        doc_id, local = self._document_of(index, create=True)
        state = getattr(unit, '_current_state', None)
        draft = dict(recovery.unit_key(unit, local), fields=recovery.unit_fields(unit, state))
        draft['after_seq'] = self.slot.journal(doc_id).seq
        return doc_id, draft

    # POSITION #
    def _on_controller_registered(self, main_controller, controller):
        if controller is main_controller.unit_controller and controller is not None:
            self._unit_controller = controller
            controller.connect('unit-modified', self._on_unit_modified)
            controller.connect('unit-done', self._on_unit_done)

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
        unit = cursor.deref()
        if unit is not None:
            self._recent = [(unit, cursor.index)] + self._recent[:3]
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
        doc_id, local = self._document_of(index)
        position = dict(recovery.unit_key(unit, local), document=doc_id)
        if sc.is_stream():
            position['path'] = sc.store.locate(index)[0].path
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
        position = None if self._selecting else self._position()
        if position is not None:
            self.slot.data['position'] = position
        if self._journals():
            for doc in self.slot.data['documents'].values():
                doc['draft'] = None
            draft = self._draft()
            if draft is not None:
                doc_id, record = draft
                self.slot.document(doc_id)['draft'] = record
        self.slot.write()
