#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""File > Submit to Server… (#3337): saves the work copy open, asks to
send its changed units, sends them (submit.py) with their progress and
Cancel, asks about conflicts, and tells what happened: in the status
bar and the title ("Submitted") when all went, in a message when some
didn't."""

from gi.repository import GLib, Gtk, Pango

from virtaal.common.pan_app import ngettext
from virtaal.support.libi18n.numbers import format_number
from virtaal.views.widgets.util import run_dialog

from . import HTTP, PlatformError, submit
from .serversdialog import error_message

CHOICES = (submit.KEEP_MINE, submit.TAKE_THEIRS, submit.SKIP)


def _forms(forms):
    return '\n'.join(forms)


def _resume_time(seconds):
    return GLib.DateTime.new_now_local().add_seconds(seconds).format('%H:%M')


def _counted(text, count):
    """text, from ngettext(), with count."""
    return text % {'count': format_number(count)}


def _part_sent(result):
    text = ngettext(
        # l10n: After a Submit to a translation server that sent only some of the changed units
        'Submitted %(sent)s of %(total)s changed unit.',
        'Submitted %(sent)s of %(total)s changed units.',
        result.total,
    )
    return text % {'sent': format_number(result.sent), 'total': format_number(result.total)}


def result_message(result, copy, platform):
    """What to tell the user about a Submit that didn't send everything,
    or None if it did."""
    if result.stop is None and not result.left:
        return None
    lines = [_part_sent(result)]
    if result.stop == submit.LOCKED:
        lines.append(_('The translation is locked on the server.'))
    elif result.stop == submit.BUDGET and result.reset_in is not None:
        lines.append(
            # l10n: A translation server allows so many requests an hour; %(time)s is when it allows more, such as 14:05
            _("The server's request limit is used up until %(time)s.")
            % {'time': _resume_time(result.reset_in)}
        )
    elif result.stop == submit.BUDGET:
        lines.append(error_message(PlatformError(HTTP, 429), platform))
    elif result.stop == submit.FAILED:
        lines.append(error_message(result.error, platform))
    if result.failures:
        lines.append(
            _counted(
                ngettext(
                    # l10n: Followed by the units, one a line, each with the server's reason
                    '%(count)s unit was refused by the server:',
                    '%(count)s units were refused by the server:',
                    len(result.failures),
                ),
                len(result.failures),
            )
        )
        for r, reason in sorted(result.failures.items()):
            lines.append('  %s: %s' % (str(copy.matched[r].source), reason))
    if result.skipped:
        lines.append(
            _counted(
                ngettext(
                    '%(count)s unit changed on the server was left as it is.',
                    '%(count)s units changed on the server were left as they are.',
                    result.skipped,
                ),
                result.skipped,
            )
        )
    if result.left:
        lines.append(_('Submit again to send the rest.'))
    return '\n'.join(lines)


def status_message(result):
    if not result.left:
        return _counted(
            # l10n: In the status bar after a Submit to a translation server
            ngettext('Submitted %(count)s unit.', 'Submitted %(count)s units.', result.sent),
            result.sent,
        )
    return _part_sent(result)


class ProgressDialog:
    """ "Submitting 12 of 340…" with Cancel."""

    def __init__(self, parent, on_cancel, title=''):
        self.on_cancel = on_cancel
        self.dialog = Gtk.Dialog(transient_for=parent, modal=True, deletable=False, title=title)
        self.dialog.set_default_size(360, -1)
        self.cancel_button = self.dialog.add_button(_('_Cancel'), Gtk.ResponseType.CANCEL)
        self.dialog.connect('response', self._on_response)
        self.label = Gtk.Label(xalign=0)
        self.bar = Gtk.ProgressBar()
        content = self.dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(6)
        content.pack_start(self.label, False, False, 0)
        content.pack_start(self.bar, False, False, 0)
        content.show_all()

    def _on_response(self, _dialog, response):
        if response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.DELETE_EVENT):
            self.cancel_button.set_sensitive(False)
            self.on_cancel()

    def update(self, number, total):
        self.label.set_text(
            # l10n: While sending changed units to a translation server
            _('Submitting %(number)s of %(total)s…')
            % {'number': format_number(number), 'total': format_number(total)}
        )
        self.bar.set_fraction((number - 1) / total if total else 0)

    def show(self):
        self.dialog.show()
        self.dialog.present()

    def destroy(self):
        self.dialog.destroy()


class ConflictDialog:
    """The units changed on the server since the download, with their
    text there and here; for each, Keep mine, Take theirs or Skip."""

    def __init__(self, conflicts, parent=None):
        self.conflicts = conflicts
        self.dialog = Gtk.Dialog(transient_for=parent, modal=True)
        # l10n: The title of the dialog listing units changed both in Virtaal and on the translation server
        self.dialog.set_title(_('Changed on the Server'))
        self.dialog.set_default_size(560, 420)
        self.dialog.add_button(_('_Cancel'), Gtk.ResponseType.CANCEL)
        self.dialog.add_button(_('_Submit'), Gtk.ResponseType.OK)
        self.dialog.set_default_response(Gtk.ResponseType.OK)
        intro = Gtk.Label(wrap=True, xalign=0, max_width_chars=60)
        intro.set_text(
            _counted(
                ngettext(
                    'Someone changed %(count)s unit on the server since you downloaded it.',
                    'Someone changed %(count)s units on the server since you downloaded them.',
                    len(conflicts),
                ),
                len(conflicts),
            )
        )
        grid = Gtk.Grid(row_spacing=4, column_spacing=12, border_width=6)
        self.buttons = {}
        row = 0
        for conflict in conflicts:
            row = self._add(grid, row, conflict)
        scrolled = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(grid)
        content = self.dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(6)
        content.pack_start(intro, False, False, 0)
        content.pack_start(scrolled, True, True, 0)
        content.show_all()

    def _add(self, grid, row, conflict):
        source = Gtk.Label(xalign=0, wrap=True, selectable=True, hexpand=True)
        source.set_markup('<b>%s</b>' % GLib.markup_escape_text(conflict.source))
        grid.attach(source, 0, row, 2, 1)
        labels = {
            submit.KEEP_MINE: (_('Keep mine'), _forms(conflict.mine)),
            submit.TAKE_THEIRS: (_('Take theirs'), _forms(conflict.theirs)),
            # l10n: Leave a unit changed on the server for a later Submit
            submit.SKIP: (_('Skip'), ''),
        }
        group = None
        buttons = {}
        for offset, choice in enumerate(CHOICES, 1):
            label, text = labels[choice]
            button = Gtk.RadioButton.new_with_label_from_widget(group, label)
            group = group or button
            buttons[choice] = button
            grid.attach(button, 0, row + offset, 1, 1)
            shown = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
            shown.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            grid.attach(shown, 1, row + offset, 1, 1)
        buttons[submit.SKIP].set_active(True)
        self.buttons[conflict.index] = buttons
        return row + len(CHOICES) + 1

    def choose(self, index, choice):
        self.buttons[index][choice].set_active(True)

    def decisions(self):
        return {
            index: next(c for c, b in buttons.items() if b.get_active())
            for index, buttons in self.buttons.items()
        }

    def run(self):
        """The decisions, or None if cancelled."""
        response = run_dialog(self.dialog)
        decisions = self.decisions() if response == Gtk.ResponseType.OK else None
        self.dialog.destroy()
        return decisions


class SubmitDialogs:
    """One Submit of the work copy open, from the registry's menu item."""

    def __init__(self, registry, copy, parent=None):
        self.registry = registry
        self.copy = copy
        self.parent = parent
        self.main_controller = registry.main_controller
        self.platform = registry.platform_of(copy)
        self.submission = None

    def _tell(self, text, kind=Gtk.MessageType.INFO):
        dialog = Gtk.MessageDialog(
            transient_for=self.parent,
            modal=True,
            message_type=kind,
            buttons=Gtk.ButtonsType.CLOSE,
            text=text,
        )
        run_dialog(dialog)
        dialog.destroy()

    def confirm(self, count):
        dialog = Gtk.MessageDialog(
            transient_for=self.parent,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            text=ngettext(
                # l10n: %(platform)s is the kind of translation server, such as Weblate
                'Submit %(count)s changed unit to %(platform)s?',
                'Submit %(count)s changed units to %(platform)s?',
                count,
            )
            % {'count': format_number(count), 'platform': self.copy.platform_name()},
        )
        dialog.format_secondary_text(self.copy.location())
        dialog.add_button(_('_Cancel'), Gtk.ResponseType.CANCEL)
        dialog.add_button(_('_Submit'), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        response = run_dialog(dialog)
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def choose(self, conflicts):
        return ConflictDialog(conflicts, self.parent).run()

    def _server(self):
        servers = self.registry.servers()
        return servers.find(self.platform, self.copy.record['server'])

    def run(self):
        """Saves, asks, sends. The submit.Result, or None if nothing
        was sent."""
        store_controller = self.main_controller.store_controller
        if store_controller.is_modified() and not self.main_controller.save_file():
            return None
        if not self.copy.pending():
            self._tell(_('There are no changes to submit.'))
            return None
        server = self._server()
        if server is None:
            self._tell(
                # l10n: Submit can't find the server a file was opened from; %(server)s is its address
                _('%(server)s is not one of your translation servers.')
                % {'server': self.copy.record['server']},
                Gtk.MessageType.ERROR,
            )
            return None
        if not self.confirm(len(self.copy.pending())):
            return None
        result = self.send(server.session())
        self.finish(result)
        return result

    def send(self, session):
        """Runs the Submit with its progress dialog until it ends."""
        loop = GLib.MainLoop()
        outcome = []

        def done(result):
            outcome.append(result)
            loop.quit()

        def conflicts(found, resolve):
            def ask():
                progress.dialog.hide()
                decisions = self.choose(found)
                progress.show()
                if decisions is None:
                    self.submission.cancel()
                resolve(decisions or {})
                return False

            GLib.idle_add(ask)

        progress = ProgressDialog(
            self.parent, lambda: self.submission.cancel(), self.copy.location()
        )
        self.submission = submit.Submission(
            self.copy, self.platform, session, progress.update, conflicts, done
        )
        self.progress = progress
        progress.show()
        self.submission.start()
        if not outcome:
            loop.run()
        progress.destroy()
        return outcome[0]

    def _show_changed_units(self):
        """The units' new text in the list, and in the editor without
        finishing its unit (as recoverycontroller does)."""
        self.main_controller.store_controller.view._treeview.queue_draw()
        unit_controller = self.main_controller.unit_controller
        unit = unit_controller.current_unit
        if unit is not None:
            unit_controller.current_unit = None
            unit_controller.view.unit = None
            unit_controller.load_unit(unit)

    def finish(self, result):
        """Saves what the server changed in the file, then tells."""
        store_controller = self.main_controller.store_controller
        if result.file_changed:
            self._show_changed_units()
            store_controller.set_modified(True)
            self.main_controller.save_file()
        view = self.main_controller.view
        view.set_statusbar_message(status_message(result))
        view.refresh_title()
        self.registry.sync_submit()
        message = result_message(result, self.copy, self.platform)
        if message:
            kind = Gtk.MessageType.INFO if result.stop is None else Gtk.MessageType.WARNING
            self._tell(message, kind)
