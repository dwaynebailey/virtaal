#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import time

from gi.repository import GLib

from virtaal.common import GObjectWrapper

from .basecontroller import BaseController


def stream_languages(all_files):
    """(code, name, count) for each language of C{all_files}, most files
    first, then the files of no known language, if any, as
    L{UNKNOWN_LANGUAGE}."""
    from virtaal.models.fileset import UNKNOWN_LANGUAGE
    from virtaal.models.langmodel import LanguageModel

    counts = all_files.languages()
    found = [(code, n) for code, n in counts.items() if code]
    rows = [(code, LanguageModel(code).name, n) for code, n in found]
    rows.sort(key=lambda row: (-row[2], row[1].lower()))
    if counts.get(None):
        # l10n: In the files panel's language list, followed by the number of
        # files, for the files that don't say what language they are in
        rows.append((UNKNOWN_LANGUAGE, _("Unknown language"), counts[None]))
    return rows


def panel_shown_at_open(store_controller):
    """Open for a folder; for several files, available but closed."""
    return store_controller.opened_folder() is not None


class FilePanelController(BaseController):
    """The files panel, shown when several files or a folder are open."""

    # How long after the last change to update the files' progress.
    PROGRESS_DELAY_MS = 300
    # How long to wait for the units view before going to a paired file.
    GO_TO_SECONDS = 5

    def __init__(self, main_controller):
        GObjectWrapper.__init__(self)
        self.main_controller = main_controller
        main_controller.file_panel_controller = self
        self.view = None
        self._cursor = None
        self._cursor_handler = None
        self._progress_source = None
        self._opened_paths = None
        self._syncing_menu = False

        store_controller = main_controller.store_controller
        store_controller.connect('store-loaded', self._on_store_loaded)
        store_controller.connect('store-closed', self._on_store_closed)
        store_controller.connect('stats-changed', self._on_stats_changed)
        store_controller.connect('store-saved', self._on_stats_changed)
        store_controller.connect('files-changed', self._on_files_changed)
        store_controller.connect('files-filled', self._on_files_filled)
        self.menu_item = main_controller.view.gui.get_object('mnu_files_panel')
        self.menu_item.connect('toggled', self._on_menu_toggled)
        from .setscancontroller import SetScanController

        self.scan_controller = SetScanController(main_controller)

    @property
    def store_controller(self):
        return self.main_controller.store_controller

    # METHODS #
    def set_shown(self, shown):
        self._syncing_menu = True
        try:
            self.menu_item.set_active(shown)
        finally:
            self._syncing_menu = False
        if self.view is None:
            return
        if shown:
            self.view.show()
        else:
            self.view.hide()

    def files_selected(self, numbers):
        """Limit the units to those of the files C{numbers}; none, or
        every file, lifts the limit."""
        from virtaal.views.filepanelview import scope_ranges

        sc = self.store_controller
        if not sc.is_stream() or sc.cursor is None:
            return
        segments = sc.store.segments
        sc.cursor.set_scope(scope_ranges(numbers, segments, len(segments)))

    def language_chosen(self, language):
        sc = self.store_controller
        if not sc.is_stream() or language == sc.stream_language:
            return
        if not self.main_controller.open_stream_language(language):
            self.view.select_language(sc.stream_language)

    def scan_all(self, kind):
        """Check or search the files of every language."""
        self.scan_controller.show(kind)

    def export_all(self, kind):
        """Export every language's documents, or TIPP packages, of the
        translation bundle open."""
        getattr(self.main_controller.bundle_actions, kind)(all_languages=True)

    def pair_file(self, path):
        """Ask for the template of C{path}, a monolingual file the folder
        scan couldn't pair, and add it to the open files in its place,
        then go to it: nothing else is read again."""
        from virtaal.support.templatefinder import TemplateMatch

        sc = self.store_controller
        if not sc.is_stream():
            return
        chosen = self.main_controller.choose_template(path)
        if not chosen:
            return
        language = self._assigned_language(path)
        if language:
            from virtaal.support import baseline

            baseline.remember_template(path, chosen, language)
        segment = sc.pair_in_stream(path, TemplateMatch(chosen, language, 'chosen'))
        if segment is not None:
            until = time.monotonic() + self.GO_TO_SECONDS
            GLib.timeout_add(50, self._go_to, sc.store, segment.offset, until)

    def _assigned_language(self, path):
        """The language a file just paired takes when its path names none
        and the folder has several: the one shown. None otherwise."""
        from virtaal.models.fileset import UNKNOWN_LANGUAGE, path_language

        sc = self.store_controller
        root = sc.all_files.root if sc.all_files is not None else None
        known = [row for row in stream_languages(sc.all_files) if row[0] != UNKNOWN_LANGUAGE]
        if path_language(path, root) or len(known) < 2 or sc.stream_language == UNKNOWN_LANGUAGE:
            return None
        return sc.stream_language

    def _go_to(self, store, index, until):
        """Move once the units view has started editing the first unit:
        a move before that leaves the editor undrawn."""
        sc = self.store_controller
        if sc.store is not store:
            return False
        if sc.view._treeview._waiting_for_row_change and time.monotonic() < until:
            return True
        sc.cursor.force_index(index)
        return False

    def focus_units(self):
        unit_view = getattr(self.main_controller.unit_controller, 'view', None)
        if unit_view is not None and getattr(unit_view, 'targets', None):
            unit_view.targets[0].grab_focus()

    def _ensure_view(self):
        if self.view is None:
            from virtaal.views.filepanelview import FilePanelView

            self.view = FilePanelView(self)
        return self.view

    def _follow_cursor(self, cursor):
        if self._cursor_handler is not None:
            self._cursor.disconnect(self._cursor_handler)
            self._cursor_handler = None
        self._cursor = cursor
        if cursor is not None:
            self._cursor_handler = cursor.connect('cursor-changed', self._on_cursor_changed)

    def _notify_language(self, sc, languages):
        """Say which language's files are shown, when it isn't the
        target language the user had."""
        if len(languages) < 2 or not sc.stream_language_replaced:
            return
        name = next(name for code, name, _n in languages if code == sc.stream_language)
        # l10n: Shown when a folder holds files in several languages
        text = _("Showing the %(language)s files. Choose another language in the Files Panel.")
        self.main_controller.view.show_info_notice(text % {'language': name})

    # EVENT HANDLERS #
    def _on_store_loaded(self, sc):
        if not sc.is_stream():
            self._follow_cursor(None)
            self._opened_paths = None
            self.menu_item.set_sensitive(False)
            self.set_shown(False)
            return
        view = self._ensure_view()
        reloaded = self._opened_paths == sc.opened_paths
        self._notify_language(sc, stream_languages(sc.all_files))
        self._load_view(sc)
        self.menu_item.set_sensitive(True)
        self.set_shown(view.shown if reloaded else panel_shown_at_open(sc))
        self._opened_paths = sc.opened_paths
        self._follow_cursor(sc.cursor)
        self._on_cursor_changed(sc.cursor)

    def _on_files_changed(self, sc):
        """A file joined: show it among the others, its template under it."""
        if self.view is None or not sc.is_stream():
            return
        self._load_view(sc)
        self.set_shown(True)
        self._follow_cursor(sc.cursor)

    def _on_files_filled(self, sc, renumbered):
        """Files waiting for their stats got their units."""
        if self.view is None or not sc.is_stream():
            return
        if renumbered:
            # Files with no units left the list.
            self._load_view(sc)
        else:
            self.view.update_progress(sc.store.segment_counts())
        self.files_selected(self.view.selected_files())
        self._follow_cursor(sc.cursor)
        self._on_cursor_changed(sc.cursor)

    def _load_view(self, sc):
        bundle = sc.bundle()
        self.view.show_bundle_items(bool(bundle and bundle.languages))
        self.view.load(
            [segment.entry for segment in sc.store.segments],
            stream_languages(sc.all_files),
            sc.stream_language,
            sc.all_files.templates,
            sc.store.fileset.unpaired,
        )
        self.view.update_progress(sc.store.segment_counts())

    def _on_store_closed(self, _sc):
        self._follow_cursor(None)
        self._opened_paths = None
        self.menu_item.set_sensitive(False)
        self.set_shown(False)

    def _on_cursor_changed(self, cursor):
        sc = self.store_controller
        if self.view is None or not sc.is_stream() or cursor.index < 0:
            return
        self.view.track(sc.store.segment_of(cursor.index))

    def _on_stats_changed(self, sc):
        if self.view is None or not sc.is_stream() or self._progress_source is not None:
            return

        def update():
            self._progress_source = None
            if sc.is_stream():
                self.view.update_progress(sc.store.segment_counts())
            return False

        self._progress_source = GLib.timeout_add(self.PROGRESS_DELAY_MS, update)

    def _on_menu_toggled(self, item):
        if self._syncing_menu:
            return
        if self.view is None:
            return
        had_focus = self.view.widget.get_focus_child() is not None
        self.set_shown(item.get_active())
        if item.get_active():
            self.view.focus()
        elif had_focus:
            self.focus_units()
