#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Edit > Search Translation Memory…: a word or phrase in the sources of
translations that can search for one, listed in a window to insert
from."""

import datetime
import os

from gi.repository import Gdk, GLib, Gtk

from virtaal.common.pan_app import ngettext
from virtaal.support import concordance, inlinecodes
from virtaal.support.libi18n.numbers import format_number
from virtaal.views import markup

ACCEL_PATH = '<Virtaal>/Edit/Search Translation Memory'
HIGHLIGHT = 'background="yellow" foreground="black"'


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
    keeps its window."""

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
        mainview = self.main_controller.view
        self.menu_item = mainview.gui.get_object('mnu_search_tm')
        self._activate_id = self.menu_item.connect('activate', self.show)
        self._setup_accelerator(mainview)
        self.menu_item.set_sensitive(True)

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
        self.menu_item.disconnect(self._activate_id)
        self.menu_item.set_sensitive(False)
        if self.view is not None:
            self.view.destroy()
            self.view = None

    # METHODS #
    def show(self, *_args):
        view = self._view()
        if view.dialog.is_active():
            # macOS: the menu's shortcut reaches the main window's menu.
            view.show()
            return
        text, side = self._selection()
        if text:
            view.set_search(text, side)
        view.show()
        if text and view.can_start():
            self.start()

    def _view(self):
        if self.view is None:
            from virtaal.views.setscanview import KIND_CONCORDANCE, SetScanView

            self.view = SetScanView(self, KIND_CONCORDANCE, (), ())
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
        """Search every source for the phrase in the window."""
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

    def _search_model(self, generation, name, model):
        first = getattr(model, 'first', None)
        first = bool(first()) if callable(first) else False

        def on_done(matches, total, complete):
            self._on_found(generation, name, matches, total, complete, first)

        model.concordance(dict(self._options), on_done, lambda: self._on_failed(generation, name))

    def stop(self):
        """Stop every search on its way."""
        self._generation += 1
        for _name, model in self._sources:
            cancel = getattr(model, 'cancel_concordance', None)
            if callable(cancel):
                cancel()
        self._sources = []
        if self._searching:
            self._searching = []
            self._finish()

    def close(self, _kind=None):
        self.stop()
        if self.view is not None:
            self.view.close()

    def can_insert(self, index):
        unit = getattr(self.main_controller.unit_controller, 'current_unit', None)
        return self._match(index) is not None and unit is not None

    def can_go_to(self, index):
        match = self._match(index)
        return match is not None and match.get('index') is not None

    def insert(self, index):
        """Put the translation of the match at index, or the part of it
        selected in the preview, at the cursor in the translation."""
        if not self.can_insert(index):
            return
        match = self.matches[index]
        selected = self.view.selected_translation()
        if selected:
            data = {'target': selected}
        elif concordance.shown(match, 'target') != match['target']:
            data = {'target': concordance.shown(match, 'target')}
        else:
            data = match
        self.tm_controller.insert_match(data)
        self.main_controller.view.main_window.present()

    def go_to(self, index):
        if not self.can_go_to(index):
            return
        self.main_controller.store_controller.cursor.force_index(self.matches[index]['index'])
        self.main_controller.view.main_window.present()

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
        from virtaal.views.setscanview import concordance_summary

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
