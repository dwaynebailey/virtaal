#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from gi.repository import GLib

from virtaal.common import GObjectWrapper

from .basecontroller import BaseController


def stream_languages(all_files):
    """(code, name, count) for each language of C{all_files}, most files
    first. Files of no known language are left out."""
    from virtaal.models.langmodel import LanguageModel

    found = [(code, n) for code, n in all_files.languages().items() if code]
    rows = [(code, LanguageModel(code).name, n) for code, n in found]
    return sorted(rows, key=lambda row: (-row[2], row[1].lower()))


def panel_shown_at_open(store_controller):
    """Open for a folder; for several files, available but closed."""
    return store_controller.opened_folder() is not None


class FilePanelController(BaseController):
    """The files panel, shown when several files or a folder are open."""

    # How long after the last change to update the files' progress.
    PROGRESS_DELAY_MS = 300

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
        languages = stream_languages(sc.all_files)
        reloaded = self._opened_paths == sc.opened_paths
        self._notify_language(sc, languages)
        view.load(
            [segment.entry for segment in sc.store.segments],
            languages,
            sc.stream_language,
            sc.all_files.templates,
        )
        view.update_progress(sc.store.segment_counts())
        self.menu_item.set_sensitive(True)
        self.set_shown(view.shown if reloaded else panel_shown_at_open(sc))
        self._opened_paths = sc.opened_paths
        self._follow_cursor(sc.cursor)
        self._on_cursor_changed(sc.cursor)

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
