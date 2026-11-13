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

from .models.amagama import (
    BUILTIN_PREFIX,
    MAX_WEIGHT,
    licence_globs,
    parse_licence,
    sources_for,
)

COL_USE, COL_TITLE, COL_LICENCE, COL_WEIGHT, COL_NAME, COL_DEFAULT = range(6)

ANY, CUSTOM = 'any', 'custom'


def licence_presets():
    """(key, label, tm.ini value) of each licence choice, Custom last."""
    return (
        # l10n: A choice of licences: any at all, with no licence asked for
        (ANY, _('Any license'), ''),
        # l10n: A choice of licences: those of free and open source software, copyleft and permissive
        ('free', _('Free software'), BUILTIN_PREFIX + 'free'),
        # l10n: A choice of licences: those that let translations be used in any software, such as MIT or Apache
        ('permissive', _('Permissive only'), BUILTIN_PREFIX + 'permissive'),
        # l10n: A choice of licences: shows a field to list them
        (CUSTOM, _('Custom…'), None),
    )


def preset_of(licence):
    """The key of the choice tm.ini's licence is: Any for a preset this
    version doesn't know, Custom for globs."""
    if not licence:
        return ANY
    for key, _label, value in licence_presets():
        if value == licence:
            return key
    return ANY if licence.startswith(BUILTIN_PREFIX) else CUSTOM


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
                "Choose the projects Amagama’s suggestions come from, and how much to prefer each."
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
        """The choice of licences to accept, a field for any others, and a
        note on what a choice leaves out."""
        self.licence_combo = Gtk.ComboBoxText()
        for key, label, _globs in licence_presets():
            self.licence_combo.append(key, label)
        self.licence = Gtk.Entry(
            text=licence_globs(self.model.licence).replace(',', ', '),
            # l10n: Shown in the empty field of licences to accept
            placeholder_text=_('Any license'),
            hexpand=True,
            no_show_all=True,
            tooltip_text=_(
                # l10n: The licences field's tooltip. Leave GPL-*, MIT and !AGPL-* as they are.
                'SPDX license identifiers, separated by commas: * stands for any'
                ' characters, ! leaves a license out. For example: GPL-*, MIT or !AGPL-*'
            ),
        )
        self.licence.connect('changed', lambda _entry: self._show_licence_note())
        self.licence.connect('activate', lambda _entry: self.apply_licence())
        self.licence.connect('focus-out-event', self._on_licence_focus_out)
        label = Gtk.Label(
            # l10n: Before a choice of the licences a project's translations must be under
            label=_('_Licenses:'),
            use_underline=True,
            mnemonic_widget=self.licence_combo,
        )
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
        grid.attach(self.licence_combo, 1, 0, 1, 1)
        grid.attach(self.licence, 2, 0, 1, 1)
        grid.attach(self.licence_note, 1, 1, 2, 1)
        self.licence_combo.set_active_id(preset_of(self.model.licence))
        self.licence_combo.connect('changed', self._on_preset_changed)
        self._on_preset_changed(self.licence_combo, apply=False)
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

    def licence_value(self):
        """tm.ini's licence for the choice: the preset's name, or the
        licences typed in."""
        key = self.licence_combo.get_active_id()
        if key == CUSTOM:
            return self.licence.get_text()
        return {k: value for k, _label, value in licence_presets()}[key]

    def apply_licence(self):
        self.model.set_licence(self.licence_value())

    def _custom(self):
        return self.licence_combo.get_active_id() == CUSTOM

    def _show_licence_note(self):
        globs = licence_globs(parse_licence(self.licence_value()))
        self.licence_note.set_visible(bool(globs))

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

    def _on_preset_changed(self, combo, apply=True):
        key = combo.get_active_id()
        custom = key == CUSTOM
        globs = licence_globs(self.licence_value()).replace(',', ', ')
        # The field starts from the last preset, to change it.
        if not custom:
            self.licence.set_text(globs)
        combo.set_tooltip_text(None if custom else globs or None)
        self.licence.set_visible(custom)
        self._show_licence_note()
        if apply:
            self.apply_licence()
        if custom and apply:
            self.licence.grab_focus()
            self.licence.set_position(-1)

    def _on_licence_focus_out(self, _entry, _event):
        if not self._closed and self._custom():
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
        # A preset applied when chosen; one this version doesn't know stays.
        if self._custom():
            self.apply_licence()
        self.model.save_config()
        self.dialog.destroy()
