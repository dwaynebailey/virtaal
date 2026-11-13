#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Local Translation Memory dialog: the files in the local TM, per
language pair, to remove or export."""

import os

from gi.repository import GLib, GObject, Gtk, Pango

from virtaal.common import pan_app
from virtaal.common.pan_app import ngettext
from virtaal.support import tmdb

(
    COL_STORE,
    COL_NAME,
    COL_TOOLTIP,
    COL_EXISTS,
    COL_PAIR,
    COL_UNITS,
    COL_UPDATED,
    COL_TIME,
    COL_KIND,
    COL_PAIR_CODES,
) = range(10)

RESPONSE_REMOVE = 1
RESPONSE_EXPORT = 2


def _gtk_text(msgid):
    """GTK's own translation of msgid, kept out of virtaal.pot."""
    return GLib.dgettext('gtk30', msgid)


def store_name(store):
    """The store's name as the dialog shows it."""
    if store['kind'] == tmdb.STORE_LEGACY:
        return _('Earlier translations (file not recorded)')
    name = os.path.basename(store['path'])
    if store['member']:
        name += ':' + store['member']
    return name


def _kind_name(kind):
    return {tmdb.STORE_SAVED: _('Saved'), tmdb.STORE_IMPORTED: _('Imported')}.get(kind, '')


def _date(timestamp):
    """The day of timestamp as the UI language writes a date."""
    if not timestamp:
        return ''
    import datetime

    from babel.dates import format_date

    from virtaal.support.libi18n.numbers import localise_digits, ui_locale

    locale = ui_locale()
    when = datetime.date.fromtimestamp(timestamp)
    return localise_digits(format_date(when, 'medium', locale=locale), locale)


def _pair_name(pair):
    """The language pair by the languages' names."""
    from virtaal.models.langmodel import LanguageModel

    return '%s → %s' % (
        LanguageModel(pair['source_lang']).name,
        LanguageModel(pair['target_lang']).name,
    )


def glossary_link():
    from virtaal.support import ms_glossary, openmailto

    link = Gtk.LinkButton.new_with_label(
        ms_glossary.DOWNLOAD_URL,
        # l10n: A link in the Local Translation Memory window to the page where
        # Microsoft offers its translations of its software for download, to
        # import with File > Import into Translation Memory
        _("Get Microsoft’s UI strings glossaries"),
    )
    link.connect('activate-link', lambda button: openmailto.open(button.get_uri()) or True)
    return link


class LocalTMDialog:
    """Lists what each file added to the local TM C{model}, per language
    pair, and removes or exports it."""

    def __init__(self, model, parent=None):
        self.model = model
        self.mainview = model.controller.main_controller.view
        self._closed = False

        self.dialog = Gtk.Dialog(
            title=model.display_name,
            transient_for=parent or self.mainview.main_window,
            modal=True,
        )
        self.dialog.set_default_size(820, 400)
        self.btn_remove = self.dialog.add_button(_('_Remove'), RESPONSE_REMOVE)
        self.btn_export = self.dialog.add_button(_('_Export…'), RESPONSE_EXPORT)
        self.dialog.add_button(_('_Close'), Gtk.ResponseType.CLOSE)
        self.dialog.connect('response', self._on_response)

        self.liststore = Gtk.ListStore(
            object, str, str, bool, str, int, str, GObject.TYPE_INT64, str, str
        )
        self.treeview = Gtk.TreeView(model=self.liststore)
        self.treeview.set_tooltip_column(COL_TOOLTIP)
        self.treeview.get_accessible().set_name(model.display_name)
        self._add_columns()
        self.treeview.get_selection().connect('changed', self._on_selection_changed)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.treeview)
        content = self.dialog.get_content_area()
        content.set_border_width(6)
        content.pack_start(scrolled, True, True, 0)
        self.glossary_link = glossary_link()
        self.glossary_link.set_halign(Gtk.Align.START)
        content.pack_start(self.glossary_link, False, False, 0)
        self._update_buttons()

    def _add_columns(self):
        def column(title, col, expand=False, numeric=False, sort_col=None, **attributes):
            renderer = Gtk.CellRendererText()
            if numeric:
                renderer.set_property('xalign', 1.0)
            if expand:
                renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
            tvcol = Gtk.TreeViewColumn(title, renderer, text=col, **attributes)
            tvcol.set_resizable(True)
            tvcol.set_expand(expand)
            tvcol.set_sort_column_id(col if sort_col is None else sort_col)
            self.treeview.append_column(tvcol)
            return tvcol

        column(_('File'), COL_NAME, expand=True, sensitive=COL_EXISTS).set_min_width(280)
        column(_('Languages'), COL_PAIR, sort_col=COL_PAIR_CODES)
        column(_('Translations'), COL_UNITS, numeric=True)
        column(_('Last updated'), COL_UPDATED, sort_col=COL_TIME)
        column(
            _(
                # l10n: Column heading: how a file came into the translation
                # memory, "Saved" (by saving it) or "Imported"
                'Type'
            ),
            COL_KIND,
        )

    # METHODS #
    def show(self):
        self.dialog.show_all()
        self.refresh()

    def refresh(self):
        """Ask the local TM for its stores again."""
        self.model.list_stores(
            self._fill, lambda: self._error(_("Couldn’t read the local translation memory."))
        )

    def _fill(self, stores):
        if self._closed:
            return
        self.liststore.clear()
        for store in stores:
            name = store_name(store)
            exists = store['exists'] is not False
            if not exists:
                name = _(
                    # l10n: A file in the translation memory that is no longer on disk
                    '%(file)s (file not found)'
                ) % {'file': name}
            tooltip = GLib.markup_escape_text(store['path'])
            for pair in store['pairs']:
                updated = pair['updated'] or store['updated'] or 0
                self.liststore.append(
                    [
                        (store, pair),
                        name,
                        tooltip,
                        exists,
                        _pair_name(pair),
                        pair['units'],
                        _date(updated),
                        updated,
                        _kind_name(store['kind']),
                        '%s\t%s' % (pair['source_lang'], pair['target_lang']),
                    ]
                )
        self._update_buttons()

    def selected(self):
        """The selected row's (store, pair), or None."""
        liststore, it = self.treeview.get_selection().get_selected()
        if it is None:
            return None
        return liststore[it][COL_STORE]

    def _update_buttons(self):
        self.btn_remove.set_sensitive(self.selected() is not None)
        self.btn_export.set_sensitive(len(self.liststore) > 0)

    def _error(self, message):
        if self._closed:
            return

        def show():
            # Not from inside the HTTP client's callback: the error dialog
            # runs a main loop of its own.
            self.mainview.show_error_dialog(message=message, parent=self.dialog)
            return False

        GLib.idle_add(show)

    # Remove #
    def confirm_remove(self, store, pair):
        """Whether the user wants the selected file's translations removed."""
        dialog = Gtk.MessageDialog(
            transient_for=self.dialog,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=ngettext(
                # l10n: %(file)s is a file name, or "Earlier translations (file not recorded)"
                "Remove %(count)d translation from “%(file)s”?",
                "Remove %(count)d translations from “%(file)s”?",
                pair['units'],
            )
            % {'count': pair['units'], 'file': store_name(store)},
        )
        dialog.format_secondary_text(
            _('Translations another file also added stay in the translation memory.')
        )
        dialog.add_button(_gtk_text('_Cancel'), Gtk.ResponseType.CANCEL)
        remove = dialog.add_button(_('_Remove'), Gtk.ResponseType.ACCEPT)
        remove.get_style_context().add_class('destructive-action')
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        try:
            return dialog.run() == Gtk.ResponseType.ACCEPT
        finally:
            dialog.destroy()

    def remove_selected(self):
        selected = self.selected()
        if selected is None:
            return
        store, pair = selected
        if not self.confirm_remove(store, pair):
            return
        self.model.remove_store(
            store['path'],
            store['member'],
            pair['source_lang'],
            pair['target_lang'],
            lambda _removed: self.refresh(),
            lambda: self._error(
                _("Couldn’t remove “%(file)s” from the translation memory.")
                % {'file': store_name(store)}
            ),
        )

    # Export #
    def choose_export_file(self, default_name, can_limit):
        """The file the user picks to export to, and whether to export only
        the selected file; None if cancelled."""
        chooser = Gtk.FileChooserDialog(
            title=_('Export Translation Memory'),
            transient_for=self.dialog,
            modal=True,
            action=Gtk.FileChooserAction.SAVE,
        )
        chooser.add_buttons(
            _gtk_text('_Cancel'),
            Gtk.ResponseType.CANCEL,
            _gtk_text('_Save'),
            Gtk.ResponseType.ACCEPT,
        )
        chooser.set_default_response(Gtk.ResponseType.ACCEPT)
        chooser.set_do_overwrite_confirmation(True)
        tmx_filter = Gtk.FileFilter()
        tmx_filter.set_name(_('TMX Translation Memory'))
        tmx_filter.add_pattern('*.tmx')
        chooser.add_filter(tmx_filter)
        only_selected = None
        if can_limit:
            only_selected = Gtk.CheckButton.new_with_mnemonic(_('_Only the selected file'))
            only_selected.set_active(True)
            chooser.set_extra_widget(only_selected)
        last_dir = pan_app.settings.general['lastdir'] or ''
        if os.path.isdir(last_dir):
            chooser.set_current_folder(last_dir)
        chooser.set_current_name(default_name)
        try:
            if chooser.run() != Gtk.ResponseType.ACCEPT:
                return None
            filename = chooser.get_filename()
            limit = only_selected is not None and only_selected.get_active()
        finally:
            chooser.destroy()
        pan_app.settings.general['lastdir'] = os.path.dirname(filename)
        return filename, limit

    def export(self):
        selected = self.selected()
        if selected is None:
            source_lang, target_lang = self.model.source_lang, self.model.target_lang
            default_name = '%s-%s.tmx' % (source_lang, target_lang)
        else:
            store, pair = selected
            source_lang, target_lang = pair['source_lang'], pair['target_lang']
            stem = os.path.splitext(os.path.basename(store['member'] or store['path']))[0]
            default_name = '%s.tmx' % (stem or '%s-%s' % (source_lang, target_lang))
        choice = self.choose_export_file(default_name, selected is not None)
        if choice is None:
            return
        filename, limit = choice
        path = member = None
        if limit:
            path, member = store['path'], store['member']

        def failed():
            self._error(
                _("Couldn’t export the translation memory to “%(file)s”.") % {'file': filename}
            )

        def write(data):
            try:
                with open(filename, 'wb') as f:
                    f.write(data)
            except OSError:
                failed()
                return
            self.mainview.show_info_notice(
                _("Exported the translation memory to “%(file)s”.") % {'file': filename},
                for_file=False,
            )

        self.model.export_tmx(
            source_lang, target_lang, write, failed, path=path, member=member or ''
        )

    # EVENT HANDLERS #
    def _on_selection_changed(self, _selection):
        self._update_buttons()

    def _on_response(self, dialog, response):
        if response == RESPONSE_REMOVE:
            self.remove_selected()
        elif response == RESPONSE_EXPORT:
            self.export()
        else:
            self._closed = True
            dialog.destroy()
