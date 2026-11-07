#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Fetch Suggestions dialog (#1679): asks the ticked online services
for the suggestions the open file's units still need, for use without a
connection later."""

from gi.repository import GLib, Gtk, Pango

from virtaal.common.pan_app import ngettext
from virtaal.support.libi18n.numbers import format_number

from .bulkfetch import CANCELLED, DONE

COL_MODEL, COL_USE, COL_AVAILABLE, COL_NAME, COL_UNITS, COL_CHARS, COL_STATUS = range(7)

RESPONSE_FETCH, RESPONSE_STOP = 1, 2


def listed(model):
    """Whether the dialog lists model: a backend it may ask, or an online
    service it may not."""
    return model.BULK_MAX != 0 or model.online_service is not None


def _name(model):
    return model.display_name or model.internal_name


class FetchSuggestionsDialog:
    """Lists the services with how much of the file each may be asked
    for, and fetches from the ticked ones, each at its own pace."""

    def __init__(self, controller, models, parent=None):
        self.controller = controller
        self.fetches = {}
        # What is left of each service's allowance (None: no limit), the
        # services still being asked, and those that wouldn't say.
        self.allowances = {}
        self._checking = set()
        self._refused = set()
        # The status each fetch ended with.
        self._results = {}
        self._closed = False

        self.dialog = Gtk.Dialog(
            # l10n: The title of the window that gets suggestions for every unit of the file at once
            title=_('Fetch Suggestions'),
            transient_for=parent,
            modal=True,
        )
        self.dialog.set_default_size(560, 320)
        self.dialog.add_button(_('_Close'), Gtk.ResponseType.CLOSE)
        # l10n: Button: stops asking the services for suggestions
        self.stop_button = self.dialog.add_button(_('_Stop'), RESPONSE_STOP)
        # l10n: Button: asks the ticked services for suggestions for the file
        self.fetch_button = self.dialog.add_button(_('_Fetch'), RESPONSE_FETCH)
        self.dialog.set_default_response(RESPONSE_FETCH)
        self.dialog.connect('response', self._on_response)

        intro = Gtk.Label(
            label=_(
                'Get suggestions now for about the next hour of work, from this unit on: '
                'units still to translate, needing work or failing a quality check. They '
                'are kept to use without a connection.'
            ),
            wrap=True,
            xalign=0,
        )
        self.liststore = Gtk.ListStore(object, bool, bool, str, str, str, str)
        self.treeview = Gtk.TreeView(model=self.liststore)
        self.treeview.get_accessible().set_name(self.dialog.get_title())
        self._add_columns()
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.treeview)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.billed_note = Gtk.Label(
            # l10n: Under the list of services; the characters column shows only for those that charge
            label=_('Services that charge by the character show how many would be sent.'),
            wrap=True,
            xalign=0,
        )

        content = self.dialog.get_content_area()
        content.set_border_width(6)
        content.set_spacing(6)
        content.pack_start(intro, False, False, 0)
        content.pack_start(scrolled, True, True, 0)
        content.pack_start(self.billed_note, False, False, 0)
        content.pack_start(self.progress, False, False, 0)
        self.fill(models)

    def _add_columns(self):
        use = Gtk.CellRendererToggle()
        use.connect('toggled', self._on_toggled)
        # l10n: Column heading: whether to ask this service
        column = Gtk.TreeViewColumn(
            _('Ask'), use, active=COL_USE, activatable=COL_AVAILABLE, sensitive=COL_AVAILABLE
        )
        self.treeview.append_column(column)

        name = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        # l10n: Column heading: a source of suggestions, such as Amagama or Google Translate
        column = Gtk.TreeViewColumn(_('Service'), name, text=COL_NAME, sensitive=COL_AVAILABLE)
        column.set_expand(True)
        self.treeview.append_column(column)

        for heading, col, xalign in (
            # l10n: Column heading: how much of the file a service is asked for: "Whole file" or "Next hour (about 40 units)"
            (_('Covers'), COL_UNITS, 0.0),
            # l10n: Column heading: how many characters are sent to a service that charges by the character
            (_('Characters'), COL_CHARS, 1.0),
        ):
            renderer = Gtk.CellRendererText(xalign=xalign)
            self.treeview.append_column(
                Gtk.TreeViewColumn(heading, renderer, text=col, sensitive=COL_AVAILABLE)
            )

        status = Gtk.CellRendererText()
        # l10n: Column heading: how the fetch from a service is going
        self.treeview.append_column(
            Gtk.TreeViewColumn(_('Status'), status, text=COL_STATUS, sensitive=COL_AVAILABLE)
        )

    # METHODS #
    def show(self):
        self.dialog.show_all()
        self._update_progress()
        self._update_buttons()

    def fill(self, models):
        self.liststore.clear()
        for model in sorted(filter(listed, models), key=lambda m: _name(m).casefold()):
            row = [model, False, False, _name(model), '', '', '']
            self._describe(row, model, default=True)
            self.liststore.append(row)
        self.billed_note.set_no_show_all(True)
        self.billed_note.set_visible(any(row[COL_MODEL].billed for row in self.liststore))
        for row in self.liststore:
            if row[COL_AVAILABLE]:
                self._check_allowance(row[COL_MODEL], default=True)

    def _check_allowance(self, model, default=False):
        """Ask model what is left of its allowance, and show it."""
        self._checking.add(model)
        self._describe(self._row_of(model), model)

        def answer(left):
            self._checking.discard(model)
            self.allowances[model] = left
            if not self._closed:
                self._describe(self._row_of(model), model, default)

        def refused():
            self._checking.discard(model)
            self._refused.add(model)
            if not self._closed:
                self._describe(self._row_of(model), model)

        model.bulk_allowance(answer, refused)

    def _describe(self, row, model, default=False):
        """Fill row in for model and what the file still needs from it."""
        if self._unavailable(model):
            row[COL_USE] = row[COL_AVAILABLE] = False
            row[COL_UNITS] = row[COL_CHARS] = ''
            # l10n: Status: this service can't be asked for a whole file's suggestions
            row[COL_STATUS] = _('Unavailable')
            return
        if model in self._checking:
            row[COL_USE] = row[COL_AVAILABLE] = False
            row[COL_UNITS] = row[COL_CHARS] = ''
            # l10n: Status: asking a service how many characters are left of the account's allowance
            row[COL_STATUS] = _('Checking the allowance…')
            return
        asked, whole = self.controller.fetch_plan(model)
        left = self.allowances.get(model)
        capped = False
        if left is not None:
            asked, capped = self.controller.cap_characters(model, asked, left)
        row[COL_AVAILABLE] = bool(asked)
        if default:
            # Asking a service that charges is the user's choice.
            row[COL_USE] = bool(asked) and not model.billed
        elif not asked:
            row[COL_USE] = False
        row[COL_UNITS] = self._covers(asked, whole and not capped)
        row[COL_CHARS] = _characters(model, asked, left)
        row[COL_STATUS] = self._results.get(model) or _plan_status(asked, capped)

    def _unavailable(self, model):
        return (
            not self.controller.fetch_budget(model)
            or not model.bulk_ready()
            or model in self._refused
        )

    def _covers(self, asked, whole):
        if whole and self.controller.is_stream():
            # l10n: Covers: every unit still needing suggestions, in all the files open together
            return _('All files')
        if whole:
            # l10n: Covers: every unit of the file still needing suggestions
            return _('Whole file')
        return ngettext(
            # l10n: Covers: the units about an hour of translating takes, from the current one
            'Next hour (about %(count)s unit)',
            'Next hour (about %(count)s units)',
            len(asked),
        ) % {'count': format_number(len(asked))}

    def ticked(self):
        return [row[COL_MODEL] for row in self.liststore if row[COL_USE] and row[COL_AVAILABLE]]

    def fetch(self):
        """Ask every ticked service at once, each a batch at a time."""
        self.fetches = {}
        for model in self.ticked():
            self._results.pop(model, None)
            self.fetches[model] = self.controller.fetch_for_file(
                model, self._on_progress, self._on_done, self.allowances.get(model)
            )
        self._update_progress()
        self._update_buttons()

    def stop(self):
        for fetch in list(self.fetches.values()):
            fetch.cancel()

    def _running(self):
        return [fetch for fetch in self.fetches.values() if fetch.finished is None]

    def _row_of(self, model):
        return next(row for row in self.liststore if row[COL_MODEL] is model)

    def _update_progress(self):
        fetches = list(self.fetches.values())
        total = sum(fetch.total for fetch in fetches)
        done = sum(fetch.fetched for fetch in fetches)
        self.progress.set_visible(bool(fetches))
        self.progress.set_fraction(done / total if total else 1.0)
        self.progress.set_text(_progress_text(done, total))

    def _update_buttons(self):
        running = bool(self._running())
        self.fetch_button.set_sensitive(not running and bool(self.ticked()))
        self.stop_button.set_visible(running)
        self.treeview.set_sensitive(not running)

    # EVENT HANDLERS #
    def _on_toggled(self, _renderer, path):
        row = self.liststore[path]
        row[COL_USE] = not row[COL_USE]
        self._update_buttons()

    def _on_progress(self, fetch):
        self._row_of(fetch.model)[COL_STATUS] = _progress_text(fetch.fetched, fetch.total)
        self._update_progress()

    def _on_done(self, fetch):
        if fetch.finished == DONE:
            # l10n: Status: every unit asked for was answered
            result = _('Fetched')
        elif fetch.finished == CANCELLED:
            # l10n: Status: the user stopped the fetch
            result = _('Stopped')
        else:
            # l10n: Status: the service gave an error or no answer, so the fetch stopped
            result = _('Failed')
        self._results[fetch.model] = result
        self._row_of(fetch.model)[COL_STATUS] = result
        if not self._running():
            # Run once every fetch started is under way.
            GLib.idle_add(self._all_done)

    def _all_done(self):
        if self._running() or self.dialog.get_window() is None:
            return False
        for row in self.liststore:
            model = row[COL_MODEL]
            if self.allowances.get(model) is not None and model in self.fetches:
                # Less is left now.
                self._check_allowance(model)
            else:
                self._describe(row, model)
        self._update_progress()
        self._update_buttons()
        return False

    def _on_response(self, _dialog, response):
        if response == RESPONSE_FETCH:
            self.fetch()
            return
        self.stop()
        if response == RESPONSE_STOP:
            return
        self._closed = True
        self.dialog.destroy()


def _progress_text(done, total):
    # l10n: How many of the units asked for were answered so far, as in "120 of 500"
    return _('%(done)s of %(total)s') % {'done': format_number(done), 'total': format_number(total)}


def _characters(model, asked, left):
    """The Characters column: what asking model for asked sends, and what
    is left of its allowance, if it has one."""
    if not model.billed or (not asked and left is None):
        return ''
    count = format_number(model.bulk_characters(asked))
    if left is None:
        return count
    # l10n: Characters column: characters to send, and what is left of the account's allowance, as in "1,200 of 3,500 left"
    return _('%(count)s of %(left)s left') % {'count': count, 'left': format_number(left)}


def _plan_status(asked, capped):
    if capped:
        # l10n: Status: fewer units are asked for, so as not to go past what is left of the account's character allowance
        return _('Cut to the allowance left')
    if not asked:
        # l10n: Status: the service already gave suggestions for every unit needing them
        return _('Nothing to fetch')
    return ''
