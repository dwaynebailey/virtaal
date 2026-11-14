#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Pre-translate dialog: its options, the progress, and what it
filled."""

from gi.repository import Gtk

from virtaal.common.pan_app import ngettext
from virtaal.support.libi18n.numbers import format_number, format_percent

from . import pretranslate
from .fetchdialog import _progress_text

RESPONSE_RUN = 1


def qualities(lowest):
    """The lowest match qualities offered: none below what suggestions
    are found at."""
    return [q for q in pretranslate.QUALITIES if q >= lowest] or [100]


def state_name(main_controller, state):
    """The state list's name for state, a StateEnum step."""
    names = main_controller.unit_controller.get_unit_state_names()
    return names.get(state, '')


def summary_rows(result, options):
    """(label, count) for each line of what was filled."""
    rows = [
        # l10n: Pre-translate's result: how many units were filled from translation memories
        (_('From translation memory:'), result.count(pretranslate.TM)),
    ]
    if options.use_mt:
        # l10n: Pre-translate's result: how many units were filled with machine translation
        rows.append((_('From machine translation:'), result.count(pretranslate.MT)))
    # l10n: Pre-translate's result: how many units it found no suggestion for
    rows.append((_('Still to translate:'), result.wanted - len(result.fills)))
    return rows


def summary_heading(result):
    count = len(result.fills)
    if not count:
        # l10n: Pre-translate's result when it filled nothing
        return _('No suggestion was good enough to fill a unit.')
    # l10n: Pre-translate's result
    return ngettext('Filled %(count)s unit.', 'Filled %(count)s units.', count) % {
        'count': format_number(count)
    }


class PretranslateDialog:
    """Asks how to pre-translate the files open, does it, and says what
    it filled."""

    def __init__(self, tm_controller, parent=None):
        self.tm_controller = tm_controller
        self.main_controller = tm_controller.main_controller
        self.run = None
        self.result = None
        self.options = pretranslate.load_options(tm_controller.config)
        self.units = [u for u in tm_controller.file_units() if pretranslate.wanted(u)]

        self.dialog = Gtk.Dialog(
            # l10n: The title of the window that fills the units still to translate with suggestions
            title=_('Pre-translate'),
            transient_for=parent,
            modal=True,
        )
        self.cancel_button = self.dialog.add_button(_('_Cancel'), Gtk.ResponseType.CANCEL)
        # l10n: Button: fills the units still to translate with their best suggestions
        self.run_button = self.dialog.add_button(_('_Pre-translate'), RESPONSE_RUN)
        self.close_button = self.dialog.add_button(_('_Close'), Gtk.ResponseType.CLOSE)
        self.close_button.set_no_show_all(True)
        self.dialog.set_default_response(RESPONSE_RUN)
        self.dialog.connect('response', self._on_response)

        content = self.dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(6)
        self.intro = self._label(self._intro())
        content.pack_start(self.intro, False, False, 0)
        self.options_box = self._options_box()
        content.pack_start(self.options_box, False, False, 0)
        self.progress = Gtk.ProgressBar(show_text=True, no_show_all=True)
        content.pack_start(self.progress, False, False, 0)
        self.summary = Gtk.Grid(column_spacing=12, row_spacing=6, no_show_all=True)
        content.pack_start(self.summary, False, False, 0)
        self.run_button.set_sensitive(bool(self.units))

        store_controller = self.main_controller.store_controller
        self._store_ids = [
            store_controller.connect(signal, self._on_store_changed)
            for signal in ('store-loaded', 'store-closed')
        ]

    @staticmethod
    def _label(text):
        return Gtk.Label(label=text, wrap=True, xalign=0, max_width_chars=60)

    def _intro(self):
        if not self.units:
            # l10n: In the Pre-translate window: nothing to fill
            return _('Every unit has a translation already.')
        return _(
            # l10n: The first lines of the Pre-translate window
            'Fill the units still to translate with their best suggestion. '
            'Undo takes them all back at once.'
        )

    def _options_box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_sensitive(bool(self.units))
        row = Gtk.Box(spacing=12)
        # l10n: Before a list of percentages: the lowest quality of a translation memory match used
        quality_label = Gtk.Label(label=_('_Use matches of at least:'), use_underline=True)
        self.quality = Gtk.ComboBoxText()
        offered = qualities(getattr(self.tm_controller, 'min_quality', 75))
        for quality in offered:
            self.quality.append(str(quality), format_percent(quality / 100, 0))
        chosen = self.options.min_quality
        self.quality.set_active_id(str(chosen if chosen in offered else offered[0]))
        quality_label.set_mnemonic_widget(self.quality)
        row.pack_start(quality_label, False, False, 0)
        row.pack_start(self.quality, False, False, 0)
        box.pack_start(row, False, False, 0)

        self.use_mt = Gtk.CheckButton(
            # l10n: Check box in the Pre-translate window
            label=_('Also use _machine translation fetched for these units'),
            use_underline=True,
            active=self.options.use_mt,
        )
        box.pack_start(self.use_mt, False, False, 0)
        self.exact_translated = Gtk.CheckButton(
            # l10n: Check box in the Pre-translate window: an exact match is marked Translated, not for review
            label=_('Mark _exact matches as translated'),
            use_underline=True,
            active=self.options.exact_translated,
        )
        box.pack_start(self.exact_translated, False, False, 0)

        state = self._review_state_name()
        if state:
            # l10n: In the Pre-translate window; %(state)s is a state such as Needs review
            note = _('The units filled are marked “%(state)s”, for you to review.')
            box.pack_start(self._label(note % {'state': state}), False, False, 0)
        online = _(
            # l10n: In the Pre-translate window: online services are asked nothing; "Fetch Suggestions for This File" is in the File menu
            'Online services fill only units whose suggestions were fetched '
            'with Fetch Suggestions for This File.'
        )
        box.pack_start(self._label(online), False, False, 0)
        return box

    def _review_state_name(self):
        for unit in self.units:
            state = pretranslate.review_state(unit)
            if state is not None:
                return state_name(self.main_controller, unit.get_state_id(state))
        return ''

    # METHODS #
    def show(self):
        self.dialog.show_all()

    def chosen_options(self):
        return pretranslate.Options(
            int(self.quality.get_active_id()),
            self.use_mt.get_active(),
            self.exact_translated.get_active(),
        )

    def start(self):
        self.options = self.chosen_options()
        pretranslate.save_options(self.tm_controller.config, self.options)
        self.options_box.set_sensitive(False)
        self.run_button.set_sensitive(False)
        self.progress.show()
        models = self.tm_controller.plugin_controller.plugins.values()
        self.run = pretranslate.Pretranslation(
            self.tm_controller,
            models,
            self.units,
            self.options,
            self._on_progress,
            self._on_done,
        )
        self._on_progress(0, self.run.total)
        self.run.start()

    def cancel(self):
        if self.run is not None:
            self.run.cancel()
            self.run = None

    def close(self):
        self.cancel()
        store_controller = self.main_controller.store_controller
        for signal_id in self._store_ids:
            store_controller.disconnect(signal_id)
        self._store_ids = []
        self.dialog.destroy()

    def _show_summary(self, result):
        self.intro.set_text(summary_heading(result))
        self.options_box.hide()
        self.progress.hide()
        for row, (text, count) in enumerate(summary_rows(result, self.options)):
            self.summary.attach(Gtk.Label(label=text, xalign=0), 0, row, 1, 1)
            self.summary.attach(Gtk.Label(label=format_number(count), xalign=1), 1, row, 1, 1)
        self.summary.show_all()
        self.cancel_button.hide()
        self.run_button.hide()
        self.close_button.show()
        self.close_button.grab_focus()

    # EVENT HANDLERS #
    def _on_progress(self, done, total):
        self.progress.set_fraction(done / total if total else 1.0)
        self.progress.set_text(_progress_text(done, total))

    def _on_done(self, result):
        self.run = None
        self.result = result
        pretranslate.apply_result(self.main_controller, result)
        self._show_summary(result)

    def _on_response(self, _dialog, response):
        if response == RESPONSE_RUN:
            self.start()
            return
        self.close()

    def _on_store_changed(self, *_args):
        # Its units are no longer the ones open.
        self.close()
