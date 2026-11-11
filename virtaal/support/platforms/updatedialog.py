#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""File > Update from Server… (#3337): saves the work copy open, brings
in what others changed on the server (update.py) while a dialog waits
with Cancel, asks about conflicts with Submit's dialog, offers a new
download when the strings themselves changed, and says what came in."""

from gi.repository import GLib, Gtk

from virtaal.common.pan_app import ngettext
from virtaal.support.libi18n.numbers import format_number
from virtaal.views.widgets.util import run_dialog

from . import update
from .serversdialog import error_message
from .submitdialog import ConflictDialog, CopyDialogs, ProgressDialog

LOST_SHOWN = 10
"""Changes that can't be kept listed by their source, at most."""


def _counted(text, count):
    return text % {'count': format_number(count)}


def lost_message(sources):
    """What a new download loses: sources are those of the changes it
    can't keep."""
    lines = [
        _counted(
            ngettext(
                # l10n: Followed by the source text of each change, one a line
                '%(count)s change not yet submitted is to a string removed or reworded '
                'on the server, and will be lost:',
                '%(count)s changes not yet submitted are to strings removed or reworded '
                'on the server, and will be lost:',
                len(sources),
            ),
            len(sources),
        )
    ]
    lines += ['  ' + source for source in sources[:LOST_SHOWN]]
    if len(sources) > LOST_SHOWN:
        lines.append('  …')
    return '\n'.join(lines)


def status_message(result):
    if not result.changed:
        # l10n: In the status bar after Update from Server found nothing new
        return _('There are no changes on the server.')
    if not result.taken:
        # l10n: In the status bar after Update from Server found changes on the
        # server that the file already had
        return _('Your copy is already up to date.')
    return _counted(
        ngettext(
            # l10n: In the status bar after Update from Server
            'Updated %(count)s unit from the server.',
            'Updated %(count)s units from the server.',
            result.taken,
        ),
        result.taken,
    )


class UpdateDialogs(CopyDialogs):
    """One Update of the work copies open: with several files open, of
    every work copy among them, as one (update.Batch)."""

    update = None

    def choose(self, conflicts):
        return ConflictDialog(conflicts, self.parent, _('_OK')).run()

    def ask_download(self, copy, plan):
        """Whether to download again, keeping what plan keeps."""
        if plan.lost:
            sources = [str(copy.matched[r].source) for r in plan.lost]
            details = lost_message(sources)
        else:
            details = _('Your changes not yet submitted are kept.')
        return self._ask_download(details)

    def ask_downloads(self, plans):
        """ask_download() for several files at once: plans are (copy,
        Plan)."""
        parts = []
        for copy, plan in plans:
            sources = [str(copy.matched[r].source) for r in plan.lost]
            parts.append(copy.label())
            if sources:
                parts += ['  ' + line for line in lost_message(sources).split('\n')]
        if not any(plan.lost for _copy, plan in plans):
            parts.append(_('Your changes not yet submitted are kept.'))
        return self._ask_download('\n'.join(parts))

    def _ask_download(self, details):
        dialog = Gtk.MessageDialog(
            transient_for=self.parent,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            text=_(
                'The strings to translate changed on the server since you downloaded them. '
                'Download them again?'
            ),
        )
        dialog.format_secondary_text(details)
        dialog.add_button(_('_Cancel'), Gtk.ResponseType.CANCEL)
        # l10n: Downloads a translation from its server again, over the local copy
        dialog.add_button(_('_Download Again'), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        response = run_dialog(dialog)
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def run(self):
        """Saves, then brings in the server's changes. The update.Result
        (a BatchResult for several files), or None if nothing was asked."""
        if self.main_controller.store_controller.is_modified():
            if not self.main_controller.save_file():
                return None
        self.copies = self._attach(self.copies)
        if not self.copies:
            return None
        self.copy = self.copies[0]
        items = self._items(self.copies)
        if items is None:
            return None
        if len(items) == 1:
            result = self.fetch(self.session)
        else:
            result = self.fetch_many(items)
        self.finish(result)
        return result

    def _until_done(self, title, start):
        loop = GLib.MainLoop()
        outcome = []

        def done(result):
            outcome.append(result)
            loop.quit()

        self.progress = ProgressDialog(self.parent, lambda: self.update.cancel(), title)
        self.progress.waiting(title)
        self.progress.show()
        start(done)
        if not outcome:
            loop.run()
        self.progress.destroy()
        return outcome[0]

    def fetch(self, session):
        """Runs the Update with its dialog until it ends."""

        def start(done):
            self.update = update.Update(
                self.copy, self.platform, session, self._on_conflicts, self._on_download, done
            )
            self.update.start()

        return self._until_done(self.copy.location(), start)

    def fetch_many(self, items):
        """Runs the Update of several files with one dialog."""

        def start(done):
            self.update = update.Batch(items, self._on_conflicts, self._on_downloads, done)
            self.update.start()

        return self._until_done(self._location(), start)

    def _asking(self, question):
        """question() with the progress hidden, once the reply that led
        to it is handled."""

        def ask():
            self.progress.dialog.hide()
            question()
            if not self.update.finished:
                self.progress.show()
            return False

        GLib.idle_add(ask)

    def _on_conflicts(self, found, resolve):
        self._asking(lambda: resolve(self.choose(found)))

    def _on_download(self, plan, proceed):
        def ask():
            if (plan.kept or plan.lost or plan.reworded) and not self.ask_download(self.copy, plan):
                proceed(None)
                return
            proceed(self.choose(plan.conflicts) if plan.conflicts else {})

        self._asking(ask)

    def _on_downloads(self, plans, proceed):
        self._asking(lambda: proceed(self.ask_downloads(plans)))

    def _reopen(self, downloaded):
        """The new downloads, opened in place of the old, with the changes
        kept written back into them: downloaded are (old copy, Result)."""
        main_controller = self.main_controller
        store_controller = main_controller.store_controller
        if store_controller.is_stream():
            store_controller.reopen_stream(
                {old.path: result.downloaded.path for old, result in downloaded}
            )
        else:
            store_controller.open_file(downloaded[0][1].downloaded.path)
        main_controller.mode_controller.refresh_mode()
        carried = []
        for _old, result in downloaded:
            copy = store_controller.work_copies.get(result.downloaded.path)
            if copy is None or not self._attach([copy]):
                continue
            if update.carry(copy, result.carried):
                carried.append(copy)
        if carried:
            self._save_changed_units(carried)
        copy = store_controller.work_copy
        self.copy = copy or downloaded[0][1].downloaded

    def finish(self, result):
        """Saves what came in, then tells."""
        results = getattr(result, 'results', [result])
        files = list(zip(self.copies, results, strict=True))
        changed = [c for c, r in files if r.downloaded is None and r.file_changed]
        if changed:
            self._save_changed_units(changed)
        downloaded = [(c, r) for c, r in files if r.downloaded is not None]
        if downloaded:
            self._reopen(downloaded)
        if result.stop == update.FAILED:
            self._tell(error_message(result.error, self.platform), Gtk.MessageType.WARNING)
            return
        if result.stop is None:
            self._told(status_message(result))
