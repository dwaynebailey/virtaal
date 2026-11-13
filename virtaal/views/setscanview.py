#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The window listing what a check or search of every language's files
found."""

from gi.repository import Gtk, Pango

from virtaal.common.pan_app import ngettext
from virtaal.support.libi18n.numbers import format_number

from .baseview import BaseView

(
    COL_CHECK,
    COL_LANGUAGE,
    COL_FILE,
    COL_UNIT,
    COL_SOURCE,
    COL_TARGET,
    COL_TOOLTIP,
    COL_PATH,
    COL_CODE,
    COL_INDEX,
    COL_POSITION,
) = range(11)

KIND_CHECK = 'check'
KIND_SEARCH = 'search'


def _one_line(text):
    return ' '.join(text.split())


def progress_text(done, total):
    # l10n: Progress of a check or search of every language's files
    text = ngettext("%(done)s of %(total)s file", "%(done)s of %(total)s files", total)
    return text % {'done': format_number(done), 'total': format_number(total)}


def summary_text(kind, count, listed, unreadable):
    if kind == KIND_CHECK:
        # l10n: How many problems a check of every language's files found
        text = ngettext("%(count)s problem", "%(count)s problems", count)
    else:
        # l10n: How many units a search of every language's files found
        text = ngettext("%(count)s unit found", "%(count)s units found", count)
    parts = [text % {'count': format_number(count)}]
    if listed < count:
        # l10n: Shown after the number of problems or units found, when there are too many to list
        parts.append(_("Only the first %(count)s are listed.") % {'count': format_number(listed)})
    if unreadable:
        # l10n: Shown after the number of problems or units found
        text = ngettext(
            "%(count)s file could not be read.", "%(count)s files could not be read.", unreadable
        )
        parts.append(text % {'count': format_number(unreadable)})
    return ' '.join(parts)


class SetScanView(BaseView):
    """A non-modal window: what to look for at the top, the results
    below. Activating a result goes to its unit."""

    def __init__(self, controller, kind, check_names, chosen_checks):
        self.controller = controller
        self.kind = kind
        self.check_buttons = {}
        self.dialog = Gtk.Dialog(
            title=self.title(kind),
            transient_for=controller.main_controller.view.main_window,
            modal=False,
            destroy_with_parent=True,
        )
        self.dialog.set_default_size(900, 480)
        self.btn_stop = self.dialog.add_button(_("_Stop"), Gtk.ResponseType.CANCEL)
        self.dialog.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
        self.dialog.connect('response', self._on_response)
        self.dialog.connect('delete-event', self._on_delete)

        content = self.dialog.get_content_area()
        content.set_border_width(6)
        content.set_spacing(6)
        if kind == KIND_CHECK:
            content.pack_start(self._check_choices(check_names, chosen_checks), False, False, 0)
        else:
            content.pack_start(self._search_row(), False, False, 0)
        self.lbl_saved_only = Gtk.Label(label=self.saved_only_text(kind), xalign=0)
        self.lbl_saved_only.get_style_context().add_class('dim-label')
        self.lbl_saved_only.set_line_wrap(True)
        content.pack_start(self.lbl_saved_only, False, False, 0)
        content.pack_start(self._results(), True, True, 0)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.set_no_show_all(True)
        self.lbl_summary = Gtk.Label(xalign=0)
        self.lbl_summary.set_line_wrap(True)
        status.pack_start(self.lbl_summary, True, True, 0)
        status.pack_end(self.progress, False, False, 0)
        content.pack_start(status, False, False, 0)
        content.show_all()
        self.btn_stop.set_sensitive(False)

    @staticmethod
    def title(kind):
        if kind == KIND_CHECK:
            # l10n: Window title
            return _("Check All Languages")
        # l10n: Window title
        return _("Find in All Languages")

    @staticmethod
    def saved_only_text(kind):
        # The scan reads the files from disk, the open ones included.
        if kind == KIND_CHECK:
            # l10n: In Check All Languages: the files are read from disk
            return _("Saved files only: unsaved changes aren’t checked.")
        # l10n: In Find in All Languages: the files are read from disk
        return _("Saved files only: unsaved changes aren’t searched.")

    def _check_choices(self, check_names, chosen):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(8)
        flow.set_homogeneous(False)
        flow.set_column_spacing(6)
        for name, label in check_names:
            button = Gtk.CheckButton(label=label)
            button.set_active(name in chosen)
            button.connect('toggled', self._on_check_toggled)
            self.check_buttons[name] = button
            flow.add(button)
        box.pack_start(flow, True, True, 0)
        # l10n: Button: check every language's files
        self.btn_start = Gtk.Button.new_with_mnemonic(_("_Check"))
        self.btn_start.connect('clicked', self._on_start)
        box.pack_end(self.btn_start, False, False, 0)
        return box

    def _search_row(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ent_search = Gtk.Entry()
        self.ent_search.set_activates_default(False)
        self.ent_search.connect('activate', self._on_start)
        self.ent_search.connect('changed', self._on_search_changed)
        self.ent_search.get_accessible().set_name(_("Search"))
        # l10n: Check box in Find in All Languages: search the source text
        self.chk_source = Gtk.CheckButton(label=_("Source"), active=True)
        # l10n: Check box in Find in All Languages: search the translations
        self.chk_target = Gtk.CheckButton(label=_("Translation"), active=True)
        for button in (self.chk_source, self.chk_target):
            button.connect('toggled', self._on_search_changed)
        self.chk_case = Gtk.CheckButton.new_with_mnemonic(_('_Case sensitive'))
        self.chk_regex = Gtk.CheckButton.new_with_mnemonic(_("_Regular expression"))
        self.btn_start = Gtk.Button(label=_('Search'))
        self.btn_start.connect('clicked', self._on_start)
        box.pack_start(self.ent_search, True, True, 0)
        box.pack_start(self.chk_source, False, False, 0)
        box.pack_start(self.chk_target, False, False, 0)
        box.pack_start(self.chk_case, False, False, 0)
        box.pack_start(self.chk_regex, False, False, 0)
        box.pack_start(self.btn_start, False, False, 0)
        self.btn_start.set_sensitive(False)
        return box

    def _results(self):
        self.liststore = Gtk.ListStore(str, str, str, int, str, str, str, str, str, int, int)
        self.treeview = Gtk.TreeView(model=self.liststore)
        self.treeview.set_tooltip_column(COL_TOOLTIP)
        self.treeview.get_accessible().set_name(self.title(self.kind))
        self.treeview.connect('row-activated', self._on_row_activated)

        def column(title, col, expand=False, numeric=False, width=None, middle=False):
            renderer = Gtk.CellRendererText()
            if numeric:
                renderer.set_property('xalign', 1.0)
            else:
                ellipsize = Pango.EllipsizeMode.MIDDLE if middle else Pango.EllipsizeMode.END
                renderer.set_property('ellipsize', ellipsize)
            tvcol = Gtk.TreeViewColumn(title, renderer, text=col)
            tvcol.set_resizable(True)
            tvcol.set_expand(expand)
            tvcol.set_sort_column_id(col)
            if width:
                tvcol.set_min_width(width)
            self.treeview.append_column(tvcol)

        if self.kind == KIND_CHECK:
            # l10n: Column heading: the quality check a translation fails
            column(_("Check"), COL_CHECK, width=100)
        column(_("Language"), COL_LANGUAGE, width=90)
        column(_("File"), COL_FILE, width=140, middle=True)
        # l10n: Column heading: the unit's number in its file
        column(_("Unit"), COL_UNIT, numeric=True)
        # l10n: Column heading: the source text of a unit
        column(_("Source"), COL_SOURCE, expand=True)
        # l10n: Column heading: the translation of a unit
        column(_("Translation"), COL_TARGET, expand=True)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.treeview)
        return scrolled

    # METHODS #
    def show(self):
        self.dialog.present()
        if self.kind == KIND_SEARCH:
            self.ent_search.grab_focus()

    def chosen_checks(self):
        return [name for name, button in self.check_buttons.items() if button.get_active()]

    def search(self):
        return {
            'text': self.ent_search.get_text(),
            'case': self.chk_case.get_active(),
            'regex': self.chk_regex.get_active(),
            'parts': self._search_parts(),
        }

    def _search_parts(self):
        parts = (('source', self.chk_source), ('target', self.chk_target))
        return [part for part, button in parts if button.get_active()]

    def set_search_text(self, text):
        self.ent_search.set_text(text)

    def started(self, total):
        self.liststore.clear()
        self.lbl_summary.set_text('')
        self.progress.show()
        self.update_progress(0, total)
        self.btn_stop.set_sensitive(True)
        self.btn_start.set_sensitive(False)

    def update_progress(self, done, total):
        self.progress.set_fraction(done / total if total else 1)
        self.progress.set_text(progress_text(done, total))

    def add_rows(self, rows):
        """Add C{rows}: (check label, language name, file name, unit
        number, source, target, message, path, language code, unit
        index, position) each."""
        for row in rows:
            check, language, name, number, source, target, message, *rest = row
            self.liststore.append(
                [check, language, name, number, _one_line(source), _one_line(target), message]
                + rest
            )

    def set_summary(self, summary):
        self.lbl_summary.set_text(summary)

    def finished(self, summary, finished):
        self.set_summary(summary)
        if finished:
            self.progress.hide()
        self.btn_stop.set_sensitive(False)
        self._update_start()

    def close(self):
        self.dialog.hide()

    def destroy(self):
        self.dialog.destroy()

    def _update_start(self):
        if self.btn_stop.get_sensitive():
            return
        if self.kind == KIND_CHECK:
            self.btn_start.set_sensitive(bool(self.chosen_checks()))
        else:
            self.btn_start.set_sensitive(
                bool(self.ent_search.get_text()) and bool(self._search_parts())
            )

    # EVENT HANDLERS #
    def _on_start(self, _widget):
        if self.btn_start.get_sensitive():
            self.controller.start(self.kind)

    def _on_check_toggled(self, _button):
        self._update_start()

    def _on_search_changed(self, _widget):
        self._update_start()

    def _on_row_activated(self, treeview, path, _column):
        row = treeview.get_model()[path]
        self.controller.open_result(row[COL_PATH], row[COL_CODE], row[COL_INDEX], row[COL_POSITION])

    def _on_response(self, _dialog, response):
        if response == Gtk.ResponseType.CANCEL:
            self.controller.stop()
        elif response == Gtk.ResponseType.CLOSE:
            self.controller.close(self.kind)

    def _on_delete(self, _dialog, _event):
        self.controller.close(self.kind)
        return True
