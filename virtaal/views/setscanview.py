#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The window listing what a check or search of every language's files
found, or what a search of the translation memory found."""

from gi.repository import Gdk, GObject, Gtk, Pango

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
KIND_CONCORDANCE = 'concordance'

# A translation memory search's rows: the list shows the same markup
# as the preview of the selected row.
COL_MATCH_SOURCE, COL_MATCH_TARGET, COL_FOUND_IN, COL_MATCH_TOOLTIP = range(4)

RESPONSE_INSERT = 1
RESPONSE_GO_TO = 2
MIN_LENGTH = 2


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


class MatchList(Gtk.TreeView):
    """A translation memory search's results: Edit > Copy (Cmd+C on
    macOS, which never reaches the list) emits copy-clipboard."""

    __gsignals__ = {
        'copy-clipboard': (GObject.SignalFlags.RUN_LAST | GObject.SignalFlags.ACTION, None, ()),
    }


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
        if kind == KIND_CONCORDANCE:
            self._match_buttons()
        self.btn_stop = self.dialog.add_button(_("_Stop"), Gtk.ResponseType.CANCEL)
        self.dialog.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
        self.dialog.connect('response', self._on_response)
        self.dialog.connect('delete-event', self._on_delete)

        content = self.dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(6)
        if kind == KIND_CHECK:
            content.pack_start(self._check_choices(check_names, chosen_checks), False, False, 0)
        else:
            content.pack_start(self._search_row(), False, False, 0)
        if kind != KIND_CONCORDANCE:
            self.lbl_saved_only = Gtk.Label(label=self.saved_only_text(kind), xalign=0)
            self.lbl_saved_only.get_style_context().add_class('dim-label')
            self.lbl_saved_only.set_line_wrap(True)
            content.pack_start(self.lbl_saved_only, False, False, 0)
            content.pack_start(self._results(), True, True, 0)
        else:
            content.pack_start(self._match_results(), True, True, 0)
            content.pack_start(self._preview(), False, False, 0)

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
        if kind == KIND_CONCORDANCE:
            # l10n: Window title
            return _("Search Translation Memory")
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
        self.btn_start = Gtk.Button.new_with_mnemonic(_("Ch_eck"))
        self.btn_start.connect('clicked', self._on_start)
        box.pack_end(self.btn_start, False, False, 0)
        return box

    def _search_row(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ent_search = Gtk.Entry()
        self.ent_search.set_activates_default(False)
        self.ent_search.connect('activate', self._on_start)
        self.ent_search.connect('changed', self._on_search_changed)
        # l10n: Placeholder of the search box in Find in All Languages
        self.ent_search.set_placeholder_text(_("Text to find"))
        self.ent_search.get_accessible().set_name(_("Text to find"))
        # l10n: Check box in Find in All Languages: search the source text
        self.chk_source = Gtk.CheckButton.new_with_mnemonic(_("S_ource"))
        self.chk_source.set_active(True)
        # l10n: Check box in Find in All Languages: search the translations
        self.chk_target = Gtk.CheckButton.new_with_mnemonic(_("_Translation"))
        self.chk_target.set_active(True)
        for button in (self.chk_source, self.chk_target):
            button.connect('toggled', self._on_search_changed)
        self.chk_case = Gtk.CheckButton.new_with_mnemonic(_('C_ase sensitive'))
        if self.kind == KIND_CONCORDANCE:
            # l10n: Check box in Search Translation Memory: find only whole words
            self.chk_last = self.chk_whole = Gtk.CheckButton.new_with_mnemonic(_("_Whole words"))
        else:
            self.chk_last = self.chk_regex = Gtk.CheckButton.new_with_mnemonic(
                _("_Regular expression")
            )
        self.btn_start = Gtk.Button.new_with_mnemonic(_('_Find'))
        self.btn_start.connect('clicked', self._on_start)
        box.pack_start(self.ent_search, True, True, 0)
        for widget in (self.chk_source, self.chk_target, self.chk_case, self.chk_last):
            box.pack_start(widget, False, False, 0)
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

    def _match_buttons(self):
        # l10n: Button in Search Translation Memory: puts the translation chosen at the cursor
        self.btn_insert = self.dialog.add_button(_("_Insert"), RESPONSE_INSERT)
        # l10n: Button in Search Translation Memory: moves to the unit of an open file
        self.btn_go_to = self.dialog.add_button(_("_Go to Unit"), RESPONSE_GO_TO)
        self.btn_insert.set_sensitive(False)
        self.btn_go_to.set_sensitive(False)

    def _match_results(self):
        self.liststore = Gtk.ListStore(str, str, str, str)
        self.treeview = MatchList(model=self.liststore)
        self.treeview.connect('copy-clipboard', self._on_match_copy)
        self.treeview.set_tooltip_column(COL_MATCH_TOOLTIP)
        self.treeview.get_accessible().set_name(self.title(self.kind))
        self.treeview.connect('row-activated', self._on_match_activated)
        self.treeview.connect('key-press-event', self._on_match_key)
        self.treeview.get_selection().connect('changed', self._on_match_selected)
        self._markup_column(_("Source"), COL_MATCH_SOURCE)
        self._markup_column(_("Translation"), COL_MATCH_TARGET)
        renderer = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.MIDDLE)
        # l10n: Column heading in Search Translation Memory: the file or source a translation is from
        column = Gtk.TreeViewColumn(_("Found in"), renderer, text=COL_FOUND_IN)
        column.set_resizable(True)
        column.set_min_width(140)
        self.treeview.append_column(column)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.treeview)
        return scrolled

    def _markup_column(self, title, col):
        renderer = Gtk.CellRendererText(
            ellipsize=Pango.EllipsizeMode.END, single_paragraph_mode=True
        )
        column = Gtk.TreeViewColumn(title, renderer, markup=col)
        column.set_resizable(True)
        column.set_expand(True)
        self.treeview.append_column(column)

    def _preview(self):
        """The selected row's whole source and translation, to read and
        select from."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.previews = []
        for name in (_("Source"), _("Translation")):
            view = Gtk.TextView(editable=False, cursor_visible=False)
            view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            view.set_left_margin(4)
            view.set_right_margin(4)
            view.get_accessible().set_name(name)
            frame = Gtk.Frame()
            frame.add(view)
            box.pack_start(frame, False, False, 0)
            self.previews.append(view)
        return box

    # METHODS #
    def show(self):
        self.dialog.present()
        if self.kind != KIND_CHECK:
            self.ent_search.grab_focus()

    def chosen_checks(self):
        return [name for name, button in self.check_buttons.items() if button.get_active()]

    def search(self):
        search = {
            'text': self.ent_search.get_text(),
            'case': self.chk_case.get_active(),
            'parts': self._search_parts(),
        }
        if self.kind == KIND_CONCORDANCE:
            search['whole_words'] = self.chk_whole.get_active()
        else:
            search['regex'] = self.chk_regex.get_active()
        return search

    def _search_parts(self):
        parts = (('source', self.chk_source), ('target', self.chk_target))
        return [part for part, button in parts if button.get_active()]

    def set_search_text(self, text):
        self.ent_search.set_text(text)

    def set_search(self, text, side):
        """Search for text, on side "source" or "target" only, or on the
        sides chosen before for None."""
        self.ent_search.set_text(text)
        if side is not None:
            self.chk_source.set_active(side == 'source')
            self.chk_target.set_active(side == 'target')

    def can_start(self):
        return self.btn_start.get_sensitive()

    def started_search(self):
        """A search of the translation memory has started."""
        self.set_rows([])
        self.lbl_summary.set_text('')
        self.btn_stop.set_sensitive(True)

    def set_rows(self, rows):
        """List C{rows}, (source markup, translation markup, found in,
        tooltip markup) each."""
        self.liststore.clear()
        for row in rows:
            self.liststore.append(list(row))
        self._on_match_selected(self.treeview.get_selection())

    def selected_index(self):
        model, itr = self.treeview.get_selection().get_selected()
        return None if itr is None else model.get_path(itr).get_indices()[0]

    def selected_translation(self):
        """The text selected in the translation preview, or None."""
        buffer = self.previews[1].get_buffer()
        bounds = buffer.get_selection_bounds()
        return buffer.get_text(*bounds, False) if bounds else None

    def update_match_buttons(self):
        index = self.selected_index()
        self.btn_insert.set_sensitive(self.controller.can_insert(index))
        self.btn_go_to.set_sensitive(self.controller.can_go_to(index))

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
            shortest = MIN_LENGTH if self.kind == KIND_CONCORDANCE else 1
            self.btn_start.set_sensitive(
                len(self.ent_search.get_text().strip()) >= shortest and bool(self._search_parts())
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

    def _on_match_selected(self, selection):
        model, itr = selection.get_selected()
        for view, col in zip(self.previews, (COL_MATCH_SOURCE, COL_MATCH_TARGET), strict=True):
            buffer = view.get_buffer()
            buffer.set_text('')
            if itr is not None:
                buffer.insert_markup(buffer.get_start_iter(), model[itr][col], -1)
        self.update_match_buttons()

    def _on_match_activated(self, _treeview, path, _column):
        self.controller.insert(path.get_indices()[0])

    def _on_match_key(self, _treeview, event):
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        primary = Gdk.ModifierType.META_MASK | Gdk.ModifierType.CONTROL_MASK
        if event.keyval in (Gdk.KEY_c, Gdk.KEY_C) and state & primary:
            self.treeview.emit('copy-clipboard')
            return True
        return False

    def _on_match_copy(self, _treeview):
        self.controller.copy(self.selected_index())

    def _on_response(self, _dialog, response):
        if response == Gtk.ResponseType.CANCEL:
            self.controller.stop()
        elif response == Gtk.ResponseType.CLOSE:
            self.controller.close(self.kind)
        elif response == RESPONSE_INSERT:
            self.controller.insert(self.selected_index())
        elif response == RESPONSE_GO_TO:
            self.controller.go_to(self.selected_index())

    def _on_delete(self, _dialog, _event):
        self.controller.close(self.kind)
        return True
