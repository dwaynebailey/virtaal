#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Check All Languages… and Find in All Languages…: a read-only scan of
the files of every language opened together."""

import logging
import os
import time

from virtaal.common import GObjectWrapper

from .basecontroller import BaseController

KIND_CHECK = 'check'
KIND_SEARCH = 'search'

# How long to wait for the units of another language to settle.
SETTLE_SECONDS = 2


def scan_files(all_files):
    """[path, language] for each file, in order."""
    return [[entry.path, entry.language] for entry in all_files]


def stream_index(segments, path, unit_index, position):
    """The stream index of the unit at C{unit_index} of the file
    C{path}, or None if the stream doesn't have that file."""
    path = os.path.abspath(path)
    for segment in segments:
        if segment.path != path:
            continue
        try:
            local = segment.valid.index(unit_index)
        except ValueError:
            # Changed on disk since the scan: the nearest by position.
            local = min(position, segment.count - 1)
        return segment.offset + local
    return None


class SetScanController(BaseController):
    """Runs the scan and keeps its windows, one per kind."""

    def __init__(self, main_controller):
        GObjectWrapper.__init__(self)
        self.main_controller = main_controller
        self.views = {}
        self.scan = None
        self._scan_kind = None
        self._files = []
        self._names = {}
        self._count = 0
        self._listed = 0
        self._unreadable = 0
        self._chosen_checks = None
        self._opened_paths = None
        # A move to a result's unit waiting for its language to open.
        self.jump = None

        gui = main_controller.view.gui
        self.menu_items = {
            KIND_CHECK: gui.get_object('mnu_check_all_languages'),
            KIND_SEARCH: gui.get_object('mnu_find_all_languages'),
        }
        for kind, item in self.menu_items.items():
            item.connect('activate', lambda _item, kind=kind: self.show(kind))
        sc = main_controller.store_controller
        sc.connect('store-loaded', self._on_store_loaded)
        sc.connect('store-closed', self._on_store_closed)

    @property
    def store_controller(self):
        return self.main_controller.store_controller

    def available(self):
        """Whether the files open are in more than one language."""
        sc = self.store_controller
        if not sc.is_stream() or sc.all_files is None:
            return False
        return len([code for code in sc.all_files.languages() if code]) > 1

    # METHODS #
    def show(self, kind):
        if not self.available():
            return
        view = self.views.get(kind)
        if view is None:
            from virtaal.support import setscan
            from virtaal.views.setscanview import SetScanView

            if self._chosen_checks is None:
                self._chosen_checks = list(setscan.DEFAULT_CHECKS)
            view = SetScanView(self, kind, self._check_names(), self._chosen_checks)
            self.views[kind] = view
            if kind == KIND_SEARCH:
                view.set_search_text(self._search_text())
        self._opened_paths = self.store_controller.opened_paths
        view.show()
        if kind == KIND_CHECK and self.scan is None and not view.liststore.iter_n_children(None):
            self.start(kind)

    def _check_names(self):
        from virtaal.support import setscan

        checks = self.main_controller.checks_controller
        return [(name, checks.get_check_name(name)) for name in setscan.CHECKS]

    def _search_text(self):
        mode = getattr(self.main_controller.mode_controller, 'current_mode', None)
        entry = getattr(mode, 'ent_search', None)
        return entry.get_text() if entry is not None else ''

    def start(self, kind):
        from virtaal.support import setscan

        self.stop()
        view = self.views[kind]
        sc = self.store_controller
        self._files = scan_files(sc.all_files)
        self._names = {entry.path: entry.display_name for entry in sc.all_files}
        job = {'kind': kind, 'files': self._files}
        if kind == KIND_CHECK:
            self._chosen_checks = view.chosen_checks()
            job['checker'] = getattr(self.main_controller.checks_controller, 'code', None)
            job['checks'] = self._chosen_checks
        else:
            job['search'] = view.search()
        self._count = self._listed = self._unreadable = 0
        self._scan_kind = kind
        view.started(len(self._files))
        self.scan = setscan.Scan(job, self._on_file, self._on_done)

    def stop(self):
        if self.scan is not None:
            self.scan.stop()

    def close(self, kind):
        if kind == self._scan_kind:
            self.stop()
        view = self.views.get(kind)
        if view is not None:
            view.close()

    def open_result(self, path, language, unit_index, position):
        """Go to a unit of a result, opening its language's files first."""
        from gi.repository import GLib

        from virtaal.models.langmodel import same_language

        sc = self.store_controller
        if not sc.is_stream():
            return
        mc = self.main_controller
        if language and not same_language(language, sc.stream_language or ''):
            if not mc.open_stream_language(language):
                return
            self.jump = (sc.store, path, unit_index, position, time.monotonic() + SETTLE_SECONDS)
            GLib.timeout_add(50, self._go_to, self.jump)
        else:
            self.jump = None
            self._go_to((sc.store, path, unit_index, position, 0))
        mc.view.main_window.present()

    def _go_to(self, jump):
        """Move once the units view has started editing the first unit:
        a move before that leaves the editor undrawn."""
        store, path, unit_index, position, until = jump
        sc = self.store_controller
        if jump is not self.jump and until:
            return False
        if sc.store is not store:
            self.jump = None
            return False
        if sc.view._treeview._waiting_for_row_change and time.monotonic() < until:
            return True
        self.jump = None
        index = stream_index(sc.store.segments, path, unit_index, position)
        if index is None:
            logging.debug('%s is not among the files open', path)
            return False
        sc.cursor.force_index(index)
        return False

    def _language_name(self, code):
        from virtaal.models.langmodel import LanguageModel

        if not code:
            return '?'
        return LanguageModel(code).name

    # EVENT HANDLERS #
    def _on_file(self, number, results, error):
        from virtaal.views.setscanview import summary_text

        view = self.views[self._scan_kind]
        view.update_progress(self.scan.done_files, self.scan.total)
        if error is not None:
            self._unreadable += 1
            logging.debug('Could not scan %s: %s', self._files[number][0], error)
        else:
            self._count += len(results)
            self._add_rows(view, number, results)
        # The count so far, as the files are read.
        view.set_summary(summary_text(self._scan_kind, self._count, self._listed, self._unreadable))

    def _add_rows(self, view, number, results):
        from virtaal.support import setscan

        room = setscan.MAX_RESULTS - self._listed
        if room <= 0 or not results:
            return
        path, code = self._files[number]
        name = self._names.get(path, os.path.basename(path))
        language = self._language_name(code)
        checks = self.main_controller.checks_controller
        rows = []
        for unit_index, position, check, message, source, target in results[:room]:
            label = checks.get_check_name(check) if check else ''
            rows.append(
                (label, language, name, position + 1, source, target, message or None)
                + (path, code or '', unit_index, position)
            )
        self._listed += len(rows)
        view.add_rows(rows)

    def _on_done(self, finished):
        from virtaal.views.setscanview import summary_text

        kind, self._scan_kind = self._scan_kind, None
        self.scan = None
        view = self.views.get(kind)
        if view is not None:
            view.finished(summary_text(kind, self._count, self._listed, self._unreadable), finished)

    def _on_store_loaded(self, sc):
        available = self.available()
        for item in self.menu_items.values():
            item.set_sensitive(available)
        if sc.opened_paths != self._opened_paths or not available:
            self._close_all()

    def _on_store_closed(self, _sc):
        for item in self.menu_items.values():
            item.set_sensitive(False)
        self._close_all()

    def _close_all(self):
        self.stop()
        for view in self.views.values():
            view.destroy()
        self.views = {}
        self._opened_paths = None
