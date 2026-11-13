#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The panel beside the units listing the files open together."""

import os

from gi.repository import Gdk, GLib, Gtk, Pango

from virtaal.common import pan_app
from virtaal.common.pan_app import ngettext
from virtaal.support.libi18n.numbers import format_number, format_percent

from .baseview import BaseView

(
    COL_NAME,
    COL_ICON,
    COL_PERCENT,
    COL_PERCENT_TEXT,
    COL_SEGMENTS,
    COL_WEIGHT,
    COL_TOOLTIP,
    COL_SENSITIVE,
    COL_PATH,
    # (translated, needs work, untranslated), for the % column's tooltip.
    COL_COUNTS,
) = range(10)

# Folders open fully up to this many files; past it, only towards the
# file being edited.
EXPAND_ALL_FILES = 200

# Keys that move the highlight; the units follow once it rests.
MOVE_KEYS = frozenset(
    (
        Gdk.KEY_Up,
        Gdk.KEY_Down,
        Gdk.KEY_Page_Up,
        Gdk.KEY_Page_Down,
        Gdk.KEY_Home,
        Gdk.KEY_End,
        Gdk.KEY_KP_Up,
        Gdk.KEY_KP_Down,
        Gdk.KEY_KP_Page_Up,
        Gdk.KEY_KP_Page_Down,
        Gdk.KEY_KP_Home,
        Gdk.KEY_KP_End,
    )
)


def build_tree(names):
    """Nested folders for the relative paths C{names}, as
    C{{'folders': {name: subtree}, 'files': [(name, number)]}}, numbered
    in the order given."""
    tree = {'folders': {}, 'files': []}
    for number, name in enumerate(names):
        parts = name.replace('\\', '/').split('/')
        node = tree
        for part in parts[:-1]:
            node = node['folders'].setdefault(part, {'folders': {}, 'files': []})
        node['files'].append((parts[-1], number))
    return tree


def scope_ranges(numbers, segments, total):
    """The stream index ranges of the files C{numbers}, or C{None} when
    that is all of them (or none)."""
    numbers = set(numbers)
    if not numbers or len(numbers) >= total:
        return None
    return [(segments[n].offset, segments[n].offset + segments[n].count) for n in sorted(numbers)]


def progress_tooltip(translated, fuzzy, untranslated):
    text = _(
        # l10n: Tooltip of a file's or folder's percentage in the files panel
        "Translated: %(translated)s\nNeeds work: %(fuzzy)s\nUntranslated: %(untranslated)s"
    )
    return text % {
        'translated': format_number(translated),
        'fuzzy': format_number(fuzzy),
        'untranslated': format_number(untranslated),
    }


def language_label(name, count):
    # l10n: A language in the files panel's list, and how many of the open files are in it
    return ngettext("%(language)s (%(count)s file)", "%(language)s (%(count)s files)", count) % {
        'language': name,
        'count': format_number(count),
    }


class FilePanelView(BaseView):
    """A tree of the open files with their progress. Selecting files
    limits the units to theirs."""

    WIDTH = 240
    # How long the highlight rests after an arrow key before the units follow.
    SCOPE_DELAY_MS = 400

    def __init__(self, controller):
        self.controller = controller
        self._building = False
        self._keyed = False
        self._scope_source = None
        self._rows = {}
        self._current = None
        self._languages = []
        self._unpaired = []
        self._unpaired_from = 0
        self._list_closed = False
        self._build_widgets()
        self._place_beside_units()

    def _build_widgets(self):
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.widget.set_border_width(6)

        self.language_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label.new_with_mnemonic(_("Lan_guage:"))
        self.language_combo = Gtk.ComboBox.new_with_model(Gtk.ListStore(str, str))
        cell = Gtk.CellRendererText()
        cell.props.ellipsize = Pango.EllipsizeMode.END
        self.language_combo.pack_start(cell, True)
        self.language_combo.add_attribute(cell, 'text', 1)
        self.language_combo.set_wrap_width(1)
        label.set_mnemonic_widget(self.language_combo)
        self.language_combo.get_accessible().set_name(_("Language"))
        self.language_combo.connect('notify::popup-shown', self._on_language_popup_shown)
        self.language_combo.connect('changed', self._on_language_changed)
        self.language_box.pack_start(label, False, False, 0)
        self.language_box.pack_start(self.language_combo, True, True, 0)
        self.language_box.pack_start(self._all_languages_button(), False, False, 0)
        self.widget.pack_start(self.language_box, False, False, 0)

        self.model = Gtk.TreeStore(str, str, int, str, object, int, str, bool, str, object)
        self.treeview = Gtk.TreeView(model=self.model)
        self.treeview.set_headers_visible(False)
        self.treeview.set_enable_search(True)
        self.treeview.set_search_column(COL_NAME)
        self.treeview.set_has_tooltip(True)
        self.treeview.connect('query-tooltip', self._on_query_tooltip)
        # l10n: The accessible name of the list of open files
        self.treeview.get_accessible().set_name(_("Files"))

        column = Gtk.TreeViewColumn()
        icon = Gtk.CellRendererPixbuf()
        column.pack_start(icon, False)
        column.add_attribute(icon, 'icon-name', COL_ICON)
        column.add_attribute(icon, 'sensitive', COL_SENSITIVE)
        name = Gtk.CellRendererText()
        name.props.ellipsize = Pango.EllipsizeMode.MIDDLE
        column.pack_start(name, True)
        column.add_attribute(name, 'text', COL_NAME)
        column.add_attribute(name, 'weight', COL_WEIGHT)
        column.add_attribute(name, 'sensitive', COL_SENSITIVE)
        column.set_expand(True)
        self.treeview.append_column(column)

        progress = Gtk.CellRendererProgress()
        progress.set_fixed_size(56, -1)
        column = Gtk.TreeViewColumn()
        column.pack_start(progress, False)
        column.add_attribute(progress, 'value', COL_PERCENT)
        column.add_attribute(progress, 'text', COL_PERCENT_TEXT)
        column.add_attribute(progress, 'visible', COL_SENSITIVE)
        self.treeview.append_column(column)
        self.progress_column = column

        self._connect_treeview()

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.add(self.treeview)
        self.widget.pack_start(scrolled, True, True, 0)
        self.widget.set_size_request(self.WIDTH // 2, -1)
        self.widget.show_all()
        self.language_box.hide()

    def _connect_treeview(self):
        selection = self.treeview.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        selection.connect('changed', self._on_selection_changed)
        self.treeview.connect('key-press-event', self._on_key_press)
        self.treeview.connect('row-activated', self._on_row_activated)
        self.treeview.connect('button-press-event', self._on_button_press)
        self.treeview.connect('popup-menu', self._on_popup_menu)

    def _all_languages_button(self):
        """A menu of what can be done with every language's files."""
        menu = Gtk.Menu()
        items = (
            (_("Find in All _Languages…"), 'search'),
            (_("Check All Lan_guages…"), 'check'),
        )
        for label, kind in items:
            item = Gtk.MenuItem.new_with_mnemonic(label)
            item.connect('activate', lambda _item, kind=kind: self.controller.scan_all(kind))
            menu.append(item)
        # A translation bundle's: every language's documents, or a TIPP
        # package for each language.
        self.bundle_items = [Gtk.SeparatorMenuItem()]
        for label, kind in (
            (_("Export Translated Doc_uments…"), 'export_documents'),
            (_("Export as _TIPP…"), 'export_tipp'),
        ):
            item = Gtk.MenuItem.new_with_mnemonic(label)
            item.connect('activate', lambda _item, kind=kind: self.controller.export_all(kind))
            self.bundle_items.append(item)
        for item in self.bundle_items:
            menu.append(item)
        menu.show_all()
        self.show_bundle_items(False)
        button = Gtk.MenuButton(popup=menu)
        button.set_image(Gtk.Image.new_from_icon_name('view-more-symbolic', Gtk.IconSize.BUTTON))
        # l10n: Tooltip of the button beside the language list in the files panel
        button.set_tooltip_text(_("All languages"))
        button.get_accessible().set_name(_("All languages"))
        self.all_languages_button = button
        return button

    def _place_beside_units(self):
        """Put the panel and the units in a Gtk.Paned where the units were."""
        mainview = self.controller.main_controller.view
        vbox = mainview.gui.get_object('vbox_main')
        storeview = mainview.gui.get_object('scrwnd_storeview')
        # The units, with the navigation ribbon beside them if it is there.
        units = storeview
        while units.get_parent() is not vbox:
            units = units.get_parent()
        position = vbox.child_get_property(units, 'position')
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        vbox.remove(units)
        self.paned.pack1(self.widget, False, False)
        self.paned.pack2(units, True, False)
        width = pan_app.settings.general['filepanelwidth']
        self.paned.set_position(
            pan_app.config_int(width, self.WIDTH, self.WIDTH // 2, 32767, 'filepanelwidth')
        )
        self.paned.connect('notify::position', self._on_position_changed)
        vbox.pack_start(self.paned, True, True, 0)
        vbox.reorder_child(self.paned, position)
        # The units are hidden for the welcome screen: so is the panel.
        storeview.connect('notify::visible', lambda w, _p: self.paned.set_visible(w.get_visible()))
        self.paned.set_visible(storeview.get_visible())
        self.widget.hide()

    # METHODS #
    def show(self):
        self.widget.show()

    def hide(self):
        self.widget.hide()

    @property
    def shown(self):
        return self.widget.get_visible()

    def focus(self):
        self.treeview.grab_focus()

    def load(self, entries, languages=(), language=None, templates=(), unpaired=()):
        """Show the files C{entries} (FileEntry objects, as numbered in
        the stream) and the C{languages}, (code, name, count) each, with
        C{language} chosen. C{templates} are shown greyed, outside the
        stream, under the files paired with them if any; so are the
        C{unpaired} files, which have no known template."""
        self._building = True
        try:
            self.model.clear()
            self._rows = {}
            self._current = None
            self._entries = list(entries)
            self._file_count = len(entries)
            paired = {
                os.path.normcase(os.path.abspath(entry.template.template))
                for entry in entries
                if entry.template is not None
            }
            templates = [t for t in templates if os.path.normcase(t.path) not in paired]
            self._unpaired = list(unpaired)
            self._unpaired_from = len(entries) + len(templates)
            shown = list(entries) + templates + self._unpaired
            tree = build_tree([entry.panel_name for entry in shown])
            # A folder holding everything adds nothing: start inside it.
            while not tree['files'] and len(tree['folders']) == 1:
                tree = next(iter(tree['folders'].values()))
            unknown = [entry.language is None for entry in entries]
            self._add_node(None, tree, unknown if len(languages) > 1 else None)
            if len(entries) <= EXPAND_ALL_FILES:
                self.treeview.expand_all()
            self._load_languages(languages, language)
        finally:
            self._building = False

    def _add_node(self, parent, node, unknown):
        numbers = []
        for name in sorted(node['folders'], key=str.lower):
            it = self.model.append(
                parent, [name, 'folder', 0, '', [], Pango.Weight.NORMAL, None, True, None, None]
            )
            below = self._add_node(it, node['folders'][name], unknown)
            self.model[it][COL_SEGMENTS] = below
            numbers.extend(below)
        for name, number in node['files']:
            if number >= self._file_count:
                self.model.append(parent, self._outside_row(name, number))
                continue
            tooltip = None
            if unknown and unknown[number]:
                # A file whose language isn't known joins the language chosen.
                name += ' (?)'
                # l10n: Tooltip of a file in the files panel
                tooltip = _("This file doesn’t say what language it is in.")
            it = self.model.append(
                parent,
                [
                    name,
                    'text-x-generic',
                    0,
                    '',
                    [number],
                    Pango.Weight.NORMAL,
                    tooltip,
                    True,
                    None,
                    None,
                ],
            )
            self._rows[number] = Gtk.TreeRowReference.new(self.model, self.model.get_path(it))
            numbers.append(number)
            entry = self._entries[number]
            if entry.template is not None:
                self.model.append(
                    it,
                    [
                        entry.template_name,
                        'text-x-generic',
                        0,
                        '',
                        [],
                        Pango.Weight.NORMAL,
                        _("This is the source-language file."),
                        False,
                        None,
                        None,
                    ],
                )
        return numbers

    def _outside_row(self, name, number):
        """The greyed row of a file outside the stream: a source-language
        file, or a file whose template isn't known."""
        tooltip = _("This is the source-language file.")
        path = None
        if number >= self._unpaired_from:
            path = self._unpaired[number - self._unpaired_from].path
            tooltip = _(
                # l10n: Tooltip of a greyed file in the files panel, like
                # af.properties, whose English (or other source-language)
                # file Virtaal couldn't find
                "Virtaal can’t tell which file this one translates. "
                "Double-click to choose its source-language file."
            )
        return [name, 'text-x-generic', 0, '', [], Pango.Weight.NORMAL, tooltip, False, path, None]

    def _load_languages(self, languages, language):
        store = self.language_combo.get_model()
        store.clear()
        self._languages = [code for code, _name, _count in languages]
        for code, name, count in languages:
            store.append([code, language_label(name, count)])
        if language in self._languages:
            self.language_combo.set_active(self._languages.index(language))
        self.language_box.set_visible(len(languages) > 1)

    def show_bundle_items(self, shown):
        for item in self.bundle_items:
            item.set_visible(shown)

    def select_language(self, language):
        """Show C{language} as chosen, without acting on it."""
        self._building = True
        try:
            if language in self._languages:
                self.language_combo.set_active(self._languages.index(language))
        finally:
            self._building = False

    def update_progress(self, counts):
        """Show each file's and folder's (translated, fuzzy, total) units;
        a file's are None while it waits for them."""

        def update(it):
            translated = fuzzy = total = 0
            while it is not None:
                row = self.model[it]
                if row[COL_ICON] == 'folder':
                    t, f, n = update(self.model.iter_children(it))
                elif not row[COL_SEGMENTS]:
                    it = self.model.iter_next(it)
                    continue
                elif counts[row[COL_SEGMENTS][0]] is None:
                    row[COL_PERCENT] = 0
                    row[COL_PERCENT_TEXT] = ''
                    row[COL_COUNTS] = None
                    it = self.model.iter_next(it)
                    continue
                else:
                    t, f, n = counts[row[COL_SEGMENTS][0]]
                fraction = t / n if n else 1
                row[COL_PERCENT] = int(fraction * 100)
                row[COL_PERCENT_TEXT] = format_percent(fraction, decimals=0)
                row[COL_COUNTS] = (t, f, n - t - f)
                translated += t
                fuzzy += f
                total += n
                it = self.model.iter_next(it)
            return translated, fuzzy, total

        update(self.model.get_iter_first())

    def track(self, number):
        """Mark the file C{number} the cursor is in, and its folders."""
        if number == self._current:
            return
        self._mark(self._current, Pango.Weight.NORMAL)
        self._current = number
        path = self._mark(number, Pango.Weight.BOLD)
        if path is None:
            return
        if path.get_depth() > 1:
            parent = path.copy()
            parent.up()
            self.treeview.expand_to_path(parent)
        visible = self.treeview.get_visible_range()
        if visible and not (visible[0].compare(path) <= 0 <= visible[1].compare(path)):
            self.treeview.scroll_to_cell(path, None, True, 0.5, 0)

    def _mark(self, number, weight):
        ref = self._rows.get(number)
        if number is None or ref is None or not ref.valid():
            return None
        path = ref.get_path()
        it = self.model.get_iter(path)
        while it is not None:
            self.model[it][COL_WEIGHT] = weight
            it = self.model.iter_parent(it)
        return path

    def selected_files(self):
        model, paths = self.treeview.get_selection().get_selected_rows()
        numbers = set()
        for path in paths:
            numbers.update(model[path][COL_SEGMENTS])
        return numbers

    # EVENT HANDLERS #
    def _on_position_changed(self, paned, _pspec):
        """Remember the width the user gave the panel."""
        if self.widget.get_mapped():
            pan_app.settings.general['filepanelwidth'] = paned.get_position()

    def _on_query_tooltip(self, treeview, x, y, keyboard, tooltip):
        """The row's tooltip, or over the % column, its unit counts."""
        found, x, y, model, path, it = treeview.get_tooltip_context(x, y, keyboard)
        if not found:
            return False
        row = model[it]
        at = None if keyboard else treeview.get_path_at_pos(x, y)
        on_progress = at is not None and at[1] is self.progress_column
        if on_progress and row[COL_SENSITIVE] and row[COL_COUNTS]:
            tooltip.set_text(progress_tooltip(*row[COL_COUNTS]))
            treeview.set_tooltip_cell(tooltip, path, self.progress_column, None)
            return True
        if not row[COL_TOOLTIP]:
            return False
        tooltip.set_text(row[COL_TOOLTIP])
        treeview.set_tooltip_row(tooltip, path)
        return True

    def _on_selection_changed(self, _selection):
        if self._building:
            return
        if not self._keyed:
            self._apply_selection()
            return
        # Holding an arrow key reads no file until it stops.
        self._cancel_scope()
        self._scope_source = GLib.timeout_add(self.SCOPE_DELAY_MS, self._apply_rested)

    def _cancel_scope(self):
        if self._scope_source is not None:
            GLib.source_remove(self._scope_source)
            self._scope_source = None

    def _apply_selection(self):
        self._cancel_scope()
        self.controller.files_selected(self.selected_files())
        return False

    def _apply_rested(self):
        """The units follow a highlight moved with the arrow keys, which
        keep moving it: the panel keeps the focus the unit editor takes."""
        focused = self.treeview.has_focus()
        self._apply_selection()
        if focused:
            # After the editor's own focus grab (Gdk.PRIORITY_REDRAW + 4).
            GLib.idle_add(self._refocus, priority=Gdk.PRIORITY_REDRAW + 5)
        return False

    def _refocus(self):
        self.treeview.grab_focus()
        return False

    def _on_key_press(self, _treeview, event):
        if event.keyval in MOVE_KEYS:
            # The selection changes in the default handler, right after.
            self._keyed = True
            GLib.idle_add(self._end_key)
            return False
        if event.keyval != Gdk.KEY_Escape:
            return False
        selection = self.treeview.get_selection()
        if selection.count_selected_rows():
            selection.unselect_all()
        else:
            self.controller.focus_units()
        return True

    def _apply_pending(self):
        if self._scope_source is not None:
            self._apply_selection()
        return False

    def _end_key(self):
        self._keyed = False
        return False

    def _on_row_activated(self, _treeview, path, _column):
        """Enter applies a highlight the units haven't followed yet.
        Double-click or Enter on a file with no known template asks for
        its template."""
        self._apply_pending()
        unpaired = self.model[path][COL_PATH]
        if unpaired:
            self.controller.pair_file(unpaired)

    def _on_button_press(self, treeview, event):
        if not event.triggers_context_menu():
            if self._scope_source is not None:
                # A click on the highlighted row changes no selection.
                GLib.idle_add(self._apply_pending)
            return False
        found = treeview.get_path_at_pos(int(event.x), int(event.y))
        unpaired = found and self.model[found[0]][COL_PATH]
        if not unpaired:
            return False
        self.pair_menu(unpaired).popup_at_pointer(event)
        return True

    def _on_popup_menu(self, treeview):
        """Shift+F10 or the Menu key on a file with no known template."""
        _model, paths = treeview.get_selection().get_selected_rows()
        unpaired = len(paths) == 1 and self.model[paths[0]][COL_PATH]
        if not unpaired:
            return False
        area = treeview.get_cell_area(paths[0], treeview.get_column(0))
        self.pair_menu(unpaired).popup_at_rect(
            treeview.get_bin_window(), area, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None
        )
        return True

    def pair_menu(self, path):
        """The menu of a file with no known template."""
        menu = Gtk.Menu()
        # l10n: In the files panel's menu for a greyed file like af.properties
        # whose English (or other source-language) file wasn't found
        item = Gtk.MenuItem.new_with_mnemonic(_("_Choose Source-Language File…"))
        item.connect('activate', lambda _item: self.controller.pair_file(path))
        menu.append(item)
        menu.show_all()
        menu.attach_to_widget(self.treeview, None)
        self._pair_menu = menu
        return menu

    def _on_language_popup_shown(self, combo, _param):
        if combo.props.popup_shown:
            return
        self._list_closed = True

        def closed_without_change():
            self._list_closed = False
            return False

        GLib.idle_add(closed_without_change)

    def _on_language_changed(self, combo):
        if self._building:
            return
        chosen = self._list_closed
        self._list_closed = False
        active = combo.get_active()
        if not chosen or active < 0:
            # Arrow keys on the closed list only browse it.
            return
        self.controller.language_chosen(self._languages[active])
