#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The panel beside the units listing the files open together."""

import os

from gi.repository import GLib, Gtk, Pango

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
) = range(9)

# Folders open fully up to this many files; past it, only towards the
# file being edited.
EXPAND_ALL_FILES = 200


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

    def __init__(self, controller):
        self.controller = controller
        self._building = False
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
        label = Gtk.Label(label=_("Language:"))
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

        self.model = Gtk.TreeStore(str, str, int, str, object, int, str, bool, str)
        self.treeview = Gtk.TreeView(model=self.model)
        self.treeview.set_headers_visible(False)
        self.treeview.set_enable_search(True)
        self.treeview.set_search_column(COL_NAME)
        self.treeview.set_tooltip_column(COL_TOOLTIP)
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

        selection = self.treeview.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        selection.connect('changed', self._on_selection_changed)
        self.treeview.connect('key-press-event', self._on_key_press)
        self.treeview.connect('row-activated', self._on_row_activated)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.add(self.treeview)
        self.widget.pack_start(scrolled, True, True, 0)
        self.widget.set_size_request(self.WIDTH // 2, -1)
        self.widget.show_all()
        self.language_box.hide()

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
        menu.show_all()
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
        self.paned.set_position(self.WIDTH)
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
            tree = build_tree([entry.display_name for entry in shown])
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
                parent, [name, 'folder', 0, '', [], Pango.Weight.NORMAL, None, True, None]
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
                tooltip = _("This file doesn't say what language it is in.")
            it = self.model.append(
                parent,
                [name, 'text-x-generic', 0, '', [number], Pango.Weight.NORMAL, tooltip, True, None],
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
                "Virtaal can't tell which file this one translates. "
                "Double-click to choose its source-language file."
            )
        return [name, 'text-x-generic', 0, '', [], Pango.Weight.NORMAL, tooltip, False, path]

    def _load_languages(self, languages, language):
        store = self.language_combo.get_model()
        store.clear()
        self._languages = [code for code, _name, _count in languages]
        for code, name, count in languages:
            store.append([code, language_label(name, count)])
        if language in self._languages:
            self.language_combo.set_active(self._languages.index(language))
        self.language_box.set_visible(len(languages) > 1)

    def select_language(self, language):
        """Show C{language} as chosen, without acting on it."""
        self._building = True
        try:
            if language in self._languages:
                self.language_combo.set_active(self._languages.index(language))
        finally:
            self._building = False

    def update_progress(self, counts):
        """Show each file's and folder's (translated, fuzzy, total) units."""

        def update(it):
            translated = total = 0
            while it is not None:
                row = self.model[it]
                if row[COL_ICON] == 'folder':
                    t, n = update(self.model.iter_children(it))
                elif not row[COL_SEGMENTS]:
                    it = self.model.iter_next(it)
                    continue
                else:
                    t, _fuzzy, n = counts[row[COL_SEGMENTS][0]]
                fraction = t / n if n else 1
                row[COL_PERCENT] = int(fraction * 100)
                row[COL_PERCENT_TEXT] = format_percent(fraction, decimals=0)
                translated += t
                total += n
                it = self.model.iter_next(it)
            return translated, total

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
    def _on_selection_changed(self, _selection):
        if self._building:
            return
        self.controller.files_selected(self.selected_files())

    def _on_key_press(self, _treeview, event):
        from gi.repository import Gdk

        if event.keyval != Gdk.KEY_Escape:
            return False
        selection = self.treeview.get_selection()
        if selection.count_selected_rows():
            selection.unselect_all()
        else:
            self.controller.focus_units()
        return True

    def _on_row_activated(self, _treeview, path, _column):
        """Double-click or Enter on a file with no known template asks
        for its template."""
        unpaired = self.model[path][COL_PATH]
        if unpaired:
            self.controller.pair_file(unpaired)

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
