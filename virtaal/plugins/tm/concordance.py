#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Edit > Search Translation Memory…: a word or phrase in the files open
and the sources of translations that can search for one, listed in a
panel under the units to insert from."""

import datetime
import os

from gi.repository import Gdk, GLib, Gtk

from virtaal.common import pan_app
from virtaal.common.pan_app import ngettext
from virtaal.support import concordance, inlinecodes
from virtaal.support.libi18n.numbers import format_number
from virtaal.views import markup

ACCEL_PATH = '<Virtaal>/Edit/Search Translation Memory'
HIGHLIGHT = 'background="yellow" foreground="black"'
OPEN_FILES_STEP = 2000
"""How many units of the files open are searched each time the main loop
is idle."""


def side_name(parts):
    """The side to search for the parts chosen."""
    if set(parts) == {'source', 'target'}:
        return 'both'
    return parts[0] if parts else 'source'


def origin_name(match, name):
    """Where a match from the source called name is from: the newest file
    it came from, else the source."""
    origins = match.get('origins') or ()
    return os.path.basename(origins[0]) if origins else name


def found_in(match):
    """Where a match is from, as its row shows it, with its count if merged."""
    text = match.get('found_in', '')
    if match.get('count', 1) > 1:
        text += ' ×%s' % format_number(match['count'])
    return text


def tooltip(match):
    """A match's context, every file it came from and its date, as markup."""
    lines = []
    if match.get('context'):
        lines.append(match['context'])
    origins = match.get('origins') or ()
    if origins:
        # l10n: Followed by the files a translation memory entry came from, one a line
        text = ngettext("In %(count)s file:", "In %(count)s files:", len(origins))
        lines.append(text % {'count': format_number(len(origins))})
        lines.extend(origins)
    if match.get('time'):
        lines.append(datetime.date.fromtimestamp(match['time']).isoformat())
    return GLib.markup_escape_text('\n'.join(lines)) if lines else None


def concordance_summary(count, listed, truncated, failed, searching):
    """The status of a search of the translation memory: how many
    matches, whether some were left out, the sources that couldn't be
    searched and those still being searched, by name."""
    # l10n: How many translations a search of the translation memory found
    text = ngettext("%(count)s match", "%(count)s matches", count)
    parts = [text % {'count': format_number(count)}]
    if truncated:
        parts.append(_("Only the first %(count)s are listed.") % {'count': format_number(listed)})
    for name in failed:
        # l10n: %(source)s is a source of translations, such as Local TM
        parts.append(_("%(source)s couldn’t be searched.") % {'source': name})
    for name in searching:
        # l10n: %(source)s is a source of translations, such as Local TM
        parts.append(_("Searching %(source)s…") % {'source': name})
    return ' '.join(parts)


def open_files_name():
    # l10n: In Search Translation Memory: the files being edited, as a source of translations
    return _("Open Files")


def _strings(text):
    strings = getattr(text, 'strings', None)
    return [str(string) for string in strings] if strings else [str(text or '')]


def unit_paths(store, count):
    """The path of the file of each of the count units of store."""
    segments = getattr(store, 'segments', None)
    if segments is None:
        return [store.get_filename() or ''] * count
    paths = []
    for segment in segments:
        paths.extend([segment.path] * segment.count)
    return paths


def unit_match(query, sides, unit, index, path):
    """The match for a translated unit of the files open, at index in
    their units, if the phrase is in it; else None."""
    targets = _strings(unit.target)
    if not any(targets):
        return None
    texts = {'source': _strings(unit.source), 'target': targets}
    if not any(query.maybe(text) for side in sides for text in texts[side] if text):
        return None
    found = concordance.match_hits(query, sides, texts)
    if not found:
        return None
    getcontext = getattr(unit, 'getcontext', None)
    match = {
        'source': texts['source'][0],
        'target': targets[0],
        'context': getcontext() if callable(getcontext) else None,
        'index': index,
        'origins': [path] if path else [],
        'time': 0,
    }
    return concordance.describe(match, found, texts)


def row_markup(match, side):
    """A match's text of side as markup, with its inline codes and the
    phrase highlighted."""
    text = concordance.shown(match, side)
    codes = []
    code_markup = match.get(side + '_markup')
    if code_markup and text == match[side]:
        from virtaal.views import placeablesguiinfo

        codes = placeablesguiinfo.code_marks(inlinecodes.code_spans(code_markup, text))
    codes = [
        *codes,
        *((start, end, None, None, HIGHLIGHT) for start, end in match['hits'].get(side, ())),
    ]
    return markup.markuptext(text, codes=codes)


class ConcordanceController:
    """Runs a search in every source that can search for a phrase, and
    keeps its panel."""

    def __init__(self, tm_controller):
        self.tm_controller = tm_controller
        self.main_controller = tm_controller.main_controller
        self.view = None
        self.matches = []
        self._generation = 0
        self._found = {}
        self._searching = []
        self._failed = []
        self._truncated = False
        self._total = 0
        self._sources = []
        self._scan = None
        mainview = self.main_controller.view
        self.menu_item = mainview.gui.get_object('mnu_search_tm')
        self._activate_id = self.menu_item.connect('activate', self.show)
        self._setup_accelerator(mainview)
        self.view_item = mainview.gui.get_object('mnu_concordance_panel')
        self._toggled_id = self.view_item.connect('toggled', self._on_view_toggled)
        self._syncing_menu = False
        store_controller = self.main_controller.store_controller
        self._store_ids = [
            store_controller.connect('store-loaded', self._on_store_loaded),
            store_controller.connect('store-closed', self._on_store_closed),
        ]
        # Built now: putting it in place moves the units, and the focus
        # with them.
        self._view()
        if store_controller.get_store() is not None:
            self._on_store_loaded(store_controller)

    def _setup_accelerator(self, mainview):
        Gtk.AccelMap.add_entry(ACCEL_PATH, Gdk.KEY_k, Gdk.ModifierType.CONTROL_MASK)
        menu_edit = mainview.gui.get_object('menu_edit')
        if not menu_edit.get_accel_group():
            accel_group = Gtk.AccelGroup()
            mainview.add_accel_group(accel_group)
            menu_edit.set_accel_group(accel_group)
        self.menu_item.set_accel_path(ACCEL_PATH)
        # GtkosxApplication only shows an item's shortcut in the native
        # menu when the item is (re)parented after its accel path is set.
        position = menu_edit.get_children().index(self.menu_item)
        menu_edit.remove(self.menu_item)
        menu_edit.insert(self.menu_item, position)
        mainview.sync_menubar()

    def destroy(self):
        self.stop()
        for signal_id in self._store_ids:
            self.main_controller.store_controller.disconnect(signal_id)
        self.menu_item.disconnect(self._activate_id)
        self.view_item.disconnect(self._toggled_id)
        self._sync_view_item(False)
        for item in (self.menu_item, self.view_item):
            item.set_sensitive(False)
        if self.view is not None:
            self.view.destroy()
            self.view = None

    # METHODS #
    def show(self, *_args):
        """Ctrl+K: search the text selected in the unit, else go to the
        panel's entry; from the panel, go back to the translation."""
        text, side = self._selection()
        view = self._view()
        if view.shown and view.has_focus():
            self.focus_editor()
            return
        if text:
            view.set_search(text, side)
        self.set_shown(True)
        if text and view.can_start():
            self.start()
            view.focus_results()
        else:
            view.focus_entry()

    def set_shown(self, shown):
        view = self._view()
        if shown:
            view.show()
        else:
            if view.has_focus():
                self.focus_editor()
            view.hide()
        pan_app.settings.general['concordancepanel'] = '1' if shown else ''
        self._sync_view_item(shown)

    def _sync_view_item(self, shown):
        self._syncing_menu = True
        try:
            self.view_item.set_active(shown)
        finally:
            self._syncing_menu = False

    def focus_editor(self):
        """Put the focus back in the translation being edited."""
        unit_view = getattr(self.main_controller.unit_controller, 'view', None)
        targets = getattr(unit_view, 'targets', None)
        if targets:
            targets[min(unit_view.focused_target_n or 0, len(targets) - 1)].grab_focus()

    def panel_moved(self):
        """Keep the suggestions above the panel as it grows or shrinks."""
        tm_view = getattr(self.tm_controller, 'view', None)
        if getattr(tm_view, 'isvisible', False):
            tm_view.update_geometry()

    def _view(self):
        if self.view is None:
            from virtaal.views.concordancepanel import ConcordancePanel

            self.view = ConcordancePanel(self)
        return self.view

    def _selection(self):
        """(the text selected in the unit, "source" or "target"), or
        ('', None)."""
        unit_view = getattr(self.main_controller.unit_controller, 'view', None)
        focus = self.main_controller.view.main_window.get_focus()
        if unit_view is None or focus is None:
            return '', None
        for side, boxes in (('target', unit_view.targets), ('source', unit_view.sources)):
            if focus in boxes:
                bounds = focus.get_buffer().get_selection_bounds()
                text = focus.get_text(*bounds).replace('￼', '').strip() if bounds else ''
                return (text, side) if text else ('', None)
        return '', None

    def start(self, _kind=None):
        """Search every source for the phrase in the panel."""
        self.stop()
        search = self.view.search()
        self._query = concordance.Query(search['text'], search['case'], search['whole_words'])
        self._sides = concordance.SIDES[side_name(search['parts'])]
        self._options = {
            'text': search['text'],
            'side': side_name(search['parts']),
            'case': search['case'],
            'whole_words': search['whole_words'],
            'limit': concordance.LIMIT,
        }
        self._found = {}
        self._failed = []
        self._truncated = False
        self._total = 0
        self.matches = []
        self.view.started_search()
        self._sources = list(self._models())
        self._searching = [name for name, _model in self._sources]
        self._search_open_files(self._generation)
        for name, model in self._sources:
            self._search_model(self._generation, name, model)
        self._update()
        if not self._searching:
            self._finish()

    def _models(self):
        """(name, model) of each source of suggestions that can search."""
        plugins = getattr(getattr(self.tm_controller, 'plugin_controller', None), 'plugins', {})
        for model in plugins.values():
            if callable(getattr(model, 'concordance', None)):
                yield getattr(model, 'shortname', None) or model.display_name, model

    def _search_open_files(self, generation):
        """Search the units of the files open, with their unsaved changes,
        a slice each time the main loop is idle. The unit being edited is
        left out."""
        store = self.main_controller.store_controller.get_store()
        if store is None:
            return
        name = open_files_name()
        units = store.get_units()
        paths = unit_paths(store, len(units))
        current = getattr(self.main_controller.unit_controller, 'current_unit', None)
        found = []
        done = [0]
        self._searching.append(name)

        def step():
            start, end = done[0], min(done[0] + OPEN_FILES_STEP, len(units))
            for index in range(start, end):
                if units[index] is current:
                    continue
                match = unit_match(self._query, self._sides, units[index], index, paths[index])
                if match is not None:
                    found.append(match)
            done[0] = end
            if end < len(units):
                return True
            self._scan = None
            ranked = concordance.rank(found)[: concordance.LIMIT]
            self._on_found(generation, name, ranked, len(found), True, False)
            return False

        self._scan = GLib.idle_add(step)

    def _search_model(self, generation, name, model):
        first = getattr(model, 'first', None)
        first = bool(first()) if callable(first) else False

        def on_done(matches, total, complete):
            self._on_found(generation, name, matches, total, complete, first)

        model.concordance(dict(self._options), on_done, lambda: self._on_failed(generation, name))

    def stop(self):
        """Stop every search on its way."""
        self._generation += 1
        if self._scan is not None:
            GLib.source_remove(self._scan)
            self._scan = None
        for _name, model in self._sources:
            cancel = getattr(model, 'cancel_concordance', None)
            if callable(cancel):
                cancel()
        self._sources = []
        if self._searching:
            self._searching = []
            self._finish()

    def can_insert(self, index):
        unit = getattr(self.main_controller.unit_controller, 'current_unit', None)
        return self._match(index) is not None and unit is not None

    def can_go_to(self, index):
        match = self._match(index)
        return match is not None and match.get('index') is not None

    def insert(self, index):
        """Put the translation of the match at index at the cursor in the
        translation."""
        if not self.can_insert(index):
            return
        match = self.matches[index]
        if concordance.shown(match, 'target') != match['target']:
            match = {'target': concordance.shown(match, 'target')}
        self.tm_controller.insert_match(match)

    def go_to(self, index):
        if not self.can_go_to(index):
            return
        self.main_controller.store_controller.cursor.force_index(self.matches[index]['index'])

    def copy(self, index):
        match = self._match(index)
        if match is not None:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(concordance.shown(match, 'target'), -1)

    def _match(self, index):
        if index is None or not 0 <= index < len(self.matches):
            return None
        return self.matches[index]

    # EVENT HANDLERS #
    def _on_store_loaded(self, store_controller):
        self._drop_open_files()
        for item in (self.menu_item, self.view_item):
            item.set_sensitive(True)
        if pan_app.config_bool(pan_app.settings.general.get('concordancepanel', '')):
            self._view().show()
            self._sync_view_item(True)

    def _on_store_closed(self, _store_controller):
        self._drop_open_files()
        for item in (self.menu_item, self.view_item):
            item.set_sensitive(False)

    def _drop_open_files(self):
        """The files open's matches go with them."""
        self.stop()
        if self._found.pop(open_files_name(), None) is not None and self.view is not None:
            self._update()

    def _on_view_toggled(self, item):
        if self._syncing_menu:
            return
        self.set_shown(item.get_active())
        if item.get_active():
            self.view.focus_entry()

    def _on_found(self, generation, name, matches, total, complete, first):
        if generation != self._generation:
            return
        for match in matches:
            match['found_in'] = origin_name(match, name)
            if first:
                match['first'] = True
        self._found[name] = matches
        self._total += total
        self._truncated = self._truncated or not complete or total > len(matches)
        self._done(name)

    def _on_failed(self, generation, name):
        if generation != self._generation:
            return
        self._failed.append(name)
        self._done(name)

    def _done(self, name):
        if name in self._searching:
            self._searching.remove(name)
        self._update()
        if not self._searching:
            self._finish()

    def _update(self):
        found = [match for matches in self._found.values() for match in matches]
        self.matches = concordance.rank(concordance.merge(found))
        rows = [
            (row_markup(m, 'source'), row_markup(m, 'target'), found_in(m), tooltip(m))
            for m in self.matches
        ]
        self.view.set_rows(rows)
        self.view.set_summary(self._summary())

    def _summary(self):
        count = self._total if self._truncated else len(self.matches)
        return concordance_summary(
            count, len(self.matches), self._truncated, self._failed, self._searching
        )

    def _finish(self):
        if self.view is not None:
            self.view.finished(self._summary(), True)


def install(tm_controller):
    """The concordance controller for tm_controller's main window, if it
    has one."""
    gui = getattr(getattr(tm_controller.main_controller, 'view', None), 'gui', None)
    if gui is None or gui.get_object('mnu_search_tm') is None:
        return None
    return ConcordanceController(tm_controller)
