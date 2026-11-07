#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Amagama Sources dialog: which of the server's projects suggestions
come from, the weight of each, and the licences to accept (#4565)."""

import unicodedata

from gi.repository import GLib, Gtk, Pango

from virtaal.support.libi18n.numbers import format_percent

from .models.amagama import MAX_WEIGHT, sources_for

COL_USE, COL_TITLE, COL_LICENCE, COL_WEIGHT, COL_NAME, COL_DEFAULT = range(6)


def _compare_titles(model, a, b, _data):
    """Titles in alphabetical order, whatever their case."""
    return GLib.utf8_collate(model[a][COL_TITLE].casefold(), model[b][COL_TITLE].casefold())


def parse_percent(text):
    """The weight a percentage typed in any digits gives, or None."""
    digits = ''.join(str(unicodedata.digit(char)) for char in text if char.isdigit())
    if not digits:
        return None
    return min(int(digits), MAX_WEIGHT * 100) / 100


class AmagamaSourcesDialog:
    """Lists the server's sources for the current languages, each to use
    or leave out and to weight, and applies each change at once."""

    def __init__(self, model, parent=None):
        self.model = model
        self._closed = False

        self.dialog = Gtk.Dialog(
            # l10n: The title of the window listing the projects Amagama has translations from
            title=_('Amagama Sources'),
            transient_for=parent,
            modal=True,
        )
        self.dialog.set_default_size(620, 460)
        self.dialog.add_button(_('_Close'), Gtk.ResponseType.CLOSE)
        self.dialog.connect('response', self._on_response)

        intro = Gtk.Label(
            label=_(
                "Choose the projects Amagama's suggestions come from, and how much to prefer each."
            ),
            wrap=True,
            xalign=0,
        )
        self.status = Gtk.Label(wrap=True, xalign=0)

        self.liststore = Gtk.ListStore(bool, str, str, float, str, float)
        self.liststore.set_sort_func(COL_TITLE, _compare_titles, None)
        self.liststore.set_sort_column_id(COL_TITLE, Gtk.SortType.ASCENDING)
        self.treeview = Gtk.TreeView(model=self.liststore)
        self.treeview.set_search_column(COL_TITLE)
        self.treeview.get_accessible().set_name(self.dialog.get_title())
        self._add_columns()

        self.scrolled = scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.treeview)
        content = self.dialog.get_content_area()
        content.set_border_width(6)
        content.set_spacing(6)
        content.pack_start(intro, False, False, 0)
        content.pack_start(self.status, False, False, 0)
        content.pack_start(scrolled, True, True, 0)
        content.pack_start(self._licence_box(), False, False, 0)

    def _licence_box(self):
        """The field for the licences to accept, and a note on what it
        leaves out."""
        self.licence = Gtk.Entry(
            text=self.model.licence.replace(',', ', '),
            # l10n: Shown in the empty field of licences to accept
            placeholder_text=_('Any license'),
            hexpand=True,
            tooltip_text=_(
                # l10n: The licences field's tooltip. Leave GPL-*, MIT and !AGPL-* as they are.
                'SPDX license identifiers, separated by commas: * stands for any'
                ' characters, ! leaves a license out. For example: GPL-*, MIT or !AGPL-*'
            ),
        )
        self.licence.connect('changed', self._on_licence_changed)
        self.licence.connect('activate', lambda _entry: self.apply_licence())
        self.licence.connect('focus-out-event', self._on_licence_focus_out)
        # l10n: Before a field listing the licences a project's translations must be under
        label = Gtk.Label(label=_('_Licenses:'), use_underline=True, mnemonic_widget=self.licence)
        self.licence_note = Gtk.Label(
            # l10n: Shown under the licences field once licences are listed in it
            label=_('Projects with no known license are left out.'),
            wrap=True,
            xalign=0,
            no_show_all=True,
        )
        self.licence_note.get_style_context().add_class('dim-label')
        grid = Gtk.Grid(column_spacing=6, row_spacing=3)
        grid.attach(label, 0, 0, 1, 1)
        grid.attach(self.licence, 1, 0, 1, 1)
        grid.attach(self.licence_note, 1, 1, 1, 1)
        self._on_licence_changed(self.licence)
        return grid

    def _add_columns(self):
        use = Gtk.CellRendererToggle()
        use.connect('toggled', self._on_toggled)
        # l10n: Column heading: whether suggestions come from this project
        self.treeview.append_column(Gtk.TreeViewColumn(_('Use'), use, active=COL_USE))

        title = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        # l10n: Column heading: a project Amagama has translations from, such as GNOME 49
        column = Gtk.TreeViewColumn(_('Project'), title, text=COL_TITLE, sensitive=COL_USE)
        column.set_expand(True)
        column.set_sort_column_id(COL_TITLE)
        self.treeview.append_column(column)

        licence = Gtk.CellRendererText()
        # l10n: Column heading: the licence of a project's translations, such as GPL-3.0-or-later
        column = Gtk.TreeViewColumn(_('License'), licence, text=COL_LICENCE, sensitive=COL_USE)
        column.set_sort_column_id(COL_LICENCE)
        self.treeview.append_column(column)

        weight = Gtk.CellRendererSpin(
            editable=True,
            xalign=1.0,
            adjustment=Gtk.Adjustment(
                lower=0, upper=MAX_WEIGHT * 100, step_increment=10, page_increment=50
            ),
        )
        weight.connect('editing-started', self._on_weight_editing_started)
        weight.connect('edited', self._on_weight_edited)
        # l10n: Column heading: how much a project's suggestions are preferred, 100% being usual
        column = Gtk.TreeViewColumn(_('Weight'), weight, sensitive=COL_USE, editable=COL_USE)
        column.set_cell_data_func(weight, self._weight_data_func)
        column.set_sort_column_id(COL_WEIGHT)
        self.treeview.append_column(column)

    # METHODS #
    def show(self):
        self.dialog.show_all()
        self.fill(self.model.sources)
        self._set_status(
            '' if self.model.sources is not None else _('Getting the list of projects…')
        )
        self.model.fetch_sources(self._on_fetched, self._on_failed)

    def fill(self, sources):
        """List sources' projects for the model's languages, as chosen."""
        self.liststore.clear()
        for source in sources_for(sources or (), self.model.source_lang, self.model.target_lang):
            name = source['name']
            self.liststore.append(
                [
                    name not in self.model.excluded,
                    source['title'],
                    source['licence'],
                    self.model.weights.get(name, source['weight']),
                    name,
                    source['weight'],
                ]
            )
        self.scrolled.set_visible(len(self.liststore) > 0)

    def _set_status(self, text):
        self.status.set_text(text)
        self.status.set_visible(bool(text))

    def apply(self):
        """Pass the listed choices to the model, keeping those for projects
        not listed."""
        listed = {row[COL_NAME] for row in self.liststore}
        excluded = set(self.model.excluded) - listed
        excluded.update(row[COL_NAME] for row in self.liststore if not row[COL_USE])
        weights = {k: v for k, v in self.model.weights.items() if k not in listed}
        weights.update(
            (row[COL_NAME], row[COL_WEIGHT])
            for row in self.liststore
            if row[COL_WEIGHT] != row[COL_DEFAULT]
        )
        self.model.set_choices(excluded, weights)

    def apply_licence(self):
        self.model.set_licence(self.licence.get_text())

    # EVENT HANDLERS #
    def _on_fetched(self, sources):
        if self._closed:
            return
        self._set_status('')
        self.fill(sources)

    def _on_failed(self):
        if self._closed:
            return
        if len(self.liststore):
            self._set_status('')
        else:
            self._set_status(_('Could not get the list of projects from Amagama.'))

    def _on_licence_changed(self, entry):
        self.licence_note.set_visible(bool(entry.get_text().strip(' ,')))

    def _on_licence_focus_out(self, _entry, _event):
        if not self._closed:
            self.apply_licence()
        return False

    def _on_toggled(self, _renderer, path):
        row = self.liststore[path]
        row[COL_USE] = not row[COL_USE]
        self.apply()

    def _on_weight_edited(self, _renderer, path, text):
        weight = parse_percent(text)
        if weight is None:
            return
        self.liststore[path][COL_WEIGHT] = weight
        self.apply()

    def _weight_data_func(self, _column, renderer, model, itr, _data):
        weight = model.get_value(itr, COL_WEIGHT)
        renderer.set_property('text', format_percent(weight, decimals=0))

    def _on_weight_editing_started(self, _renderer, editable, path):
        # From the number, not the percentage shown.
        editable.set_value(round(self.liststore[path][COL_WEIGHT] * 100))

    def _on_response(self, _dialog, _response):
        self._closed = True
        self.apply_licence()
        self.model.save_config()
        self.dialog.destroy()
