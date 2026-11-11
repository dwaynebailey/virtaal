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
    """One Update of the work copy open."""

    update = None

    def choose(self, conflicts):
        return ConflictDialog(conflicts, self.parent, _('_OK')).run()

    def ask_download(self, copy, plan):
        """Whether to download again, keeping what plan keeps."""
        dialog = Gtk.MessageDialog(
            transient_for=self.parent,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            text=_(
                'The strings to translate changed on the server since you downloaded them. '
                'Download them again?'
            ),
        )
        if plan.lost:
            sources = [str(copy.matched[r].source) for r in plan.lost]
            dialog.format_secondary_text(lost_message(sources))
        else:
            dialog.format_secondary_text(_('Your changes not yet submitted are kept.'))
        dialog.add_button(_('_Cancel'), Gtk.ResponseType.CANCEL)
        # l10n: Downloads a translation from its server again, over the local copy
        dialog.add_button(_('_Download Again'), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        response = run_dialog(dialog)
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def run(self):
        """Saves, then brings in the server's changes. The update.Result,
        or None if nothing was asked."""
        if self.main_controller.store_controller.is_modified():
            if not self.main_controller.save_file():
                return None
        server = self._server()
        if server is None:
            return None
        self.session = server.session()
        result = self.fetch(self.session)
        self.finish(result)
        return result

    def fetch(self, session):
        """Runs the Update with its dialog until it ends."""
        loop = GLib.MainLoop()
        outcome = []

        def done(result):
            outcome.append(result)
            loop.quit()

        self.progress = ProgressDialog(
            self.parent, lambda: self.update.cancel(), self.copy.location()
        )
        self.progress.waiting(self.copy.location())
        self.update = update.Update(
            self.copy, self.platform, session, self._on_conflicts, self._on_download, done
        )
        self.progress.show()
        self.update.start()
        if not outcome:
            loop.run()
        self.progress.destroy()
        return outcome[0]

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

    def _reopen(self, result):
        """The new download, opened in place of the old, with the
        changes kept written back into it."""
        main_controller = self.main_controller
        store_controller = main_controller.store_controller
        store_controller.open_file(result.downloaded.path)
        main_controller.mode_controller.refresh_mode()
        copy = store_controller.work_copy
        if copy is not None and update.carry(copy, result.carried):
            store_controller.set_modified(True)
            main_controller.save_file()
        self.copy = copy or result.downloaded

    def finish(self, result):
        """Saves what came in, then tells."""
        if result.downloaded is not None:
            self._reopen(result)
        elif result.file_changed:
            self._save_changed_units()
        if result.stop == update.FAILED:
            self._tell(error_message(result.error, self.platform), Gtk.MessageType.WARNING)
            return
        if result.stop is None:
            self._told(status_message(result))
