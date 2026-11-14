#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Pre-translate dialog: its options, the progress, and what it
filled."""

import logging

from gi.repository import Gtk

from virtaal.common.pan_app import ngettext
from virtaal.models.streammodel import PeekUnit
from virtaal.support.libi18n.numbers import format_number, format_percent

from . import pretranslate
from .fetchdialog import (
    COL_MODEL,
    COL_STATUS,
    COL_UNITS,
    ServiceList,
    _progress_text,
    finished_status,
    listed,
)

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
    if options.mt_services:
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


def unreachable_note(service):
    # l10n: Pre-translate's result; %(service)s is an online service such as Amagama
    return _("%(service)s couldn't be reached; only the other sources were used.") % {
        'service': service
    }


def machine_translation(models):
    """The machine translation services of models the window lists."""
    return [m for m in models if getattr(m, 'machine_translation', False) and listed(m)]


class PretranslateDialog(ServiceList):
    """Asks how to pre-translate the files open, does it, and says what
    it filled."""

    def __init__(self, tm_controller, parent=None):
        self.tm_controller = tm_controller
        self.main_controller = tm_controller.main_controller
        self.run = None
        self.result = None
        self.options = pretranslate.load_options(tm_controller.config)
        self.all_units = tm_controller.file_units()
        self.units = [u for u in self.all_units if pretranslate.wanted(u)]

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
        self.close_button.hide()
        self.dialog.set_default_response(RESPONSE_RUN)
        self.dialog.connect('response', self._on_response)
        self._init_services(tm_controller, self.dialog)
        self.columns[COL_UNITS].set_visible(False)

        content = self.dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(6)
        self.intro = self._label(self._intro())
        content.pack_start(self.intro, False, False, 0)
        self.options_box = self._options_box()
        content.pack_start(self.options_box, False, False, 0)
        self.phase = Gtk.Label(xalign=0, no_show_all=True)
        content.pack_start(self.phase, False, False, 0)
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

        self._mt_list(box)
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
        if any(map(pretranslate.fetches_first, self._models())):
            online = _(
                # l10n: In the Pre-translate window: it sends the units' text to free online translation memories, such as Amagama, before filling them
                'Free online translation memories, such as Amagama, are asked for these units first.'
            )
            box.pack_start(self._label(online), False, False, 0)
        return box

    def _mt_list(self, box):
        """The machine translation services to tick, if any is on."""
        models = machine_translation(self._models())
        if not models:
            return
        label = Gtk.Label(
            # l10n: In the Pre-translate window, above the list of machine translation services to tick
            label=_('_Machine translation for the units translation memory doesn’t fill:'),
            use_underline=True,
            xalign=0,
            mnemonic_widget=self.treeview,
        )
        box.pack_start(label, False, False, 0)
        scrolled = Gtk.ScrolledWindow(min_content_height=110)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.treeview)
        box.pack_start(scrolled, True, True, 0)
        box.pack_start(self.billed_note, False, False, 0)
        self.fill(models)

    def _models(self):
        return list(self.tm_controller.plugin_controller.plugins.values())

    # The list of machine translation services #
    def _plan(self, model):
        if self.run is not None:
            return self.run.mt_sources(model), True
        return self.tm_controller.unfetched(model, [u.source for u in self.units]), True

    @staticmethod
    def _tickable(asked):
        # Suggestions fetched before are used too.
        return True

    def _ticked_at_first(self, model, asked):
        # A service that charges is ticked anew each time.
        return not model.billed and model.internal_name in self.options.mt_services

    def _update_buttons(self):
        pass

    def _describe_all(self):
        for row in self.liststore:
            if row[COL_MODEL] not in self._checking:
                self._describe(row, row[COL_MODEL])

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
            self.exact_translated.get_active(),
            tuple(model.internal_name for model in self.ticked()),
        )

    def start(self):
        self.options = self.chosen_options()
        free = tuple(m.internal_name for m in self.ticked() if not m.billed)
        pretranslate.save_options(
            self.tm_controller.config, self.options._replace(mt_services=free)
        )
        self.options_box.set_sensitive(False)
        self.run_button.set_sensitive(False)
        self.progress.show()
        self.run = pretranslate.Pretranslation(
            self.tm_controller,
            self._models(),
            self.all_units,
            self.options,
            self._on_progress,
            self._on_done,
            self._read,
            limits={model: self.allowances.get(model) for model in self.ticked()},
            on_phase=self._on_phase,
        )
        self.run.start()

    def _read(self, index, unit):
        """The unit at index, from its file if a stream's file isn't read
        yet."""
        if not isinstance(unit, PeekUnit):
            return unit
        store = self.main_controller.store_controller.get_store()
        try:
            return store.get_unit(index)
        except Exception:
            logging.exception('Pre-translate could not read the file of unit %d', index)
            return None

    def cancel(self):
        if self.run is not None:
            self.run.cancel()
            self.run = None

    def close(self):
        self._closed = True
        self.cancel()
        store_controller = self.main_controller.store_controller
        for signal_id in self._store_ids:
            store_controller.disconnect(signal_id)
        self._store_ids = []
        self.dialog.destroy()

    def _show_summary(self, result):
        self.intro.set_text(summary_heading(result))
        for service in result.unreachable:
            note = self._label(unreachable_note(service))
            self.dialog.get_content_area().pack_start(note, False, False, 0)
            note.show()
        self.options_box.hide()
        self.phase.hide()
        self.progress.hide()
        for row, (text, count) in enumerate(summary_rows(result, self.options)):
            self.summary.attach(Gtk.Label(label=text, xalign=0), 0, row, 1, 1)
            self.summary.attach(Gtk.Label(label=format_number(count), xalign=1), 1, row, 1, 1)
        for child in self.summary.get_children():
            child.show()
        self.summary.show()
        self.cancel_button.hide()
        self.run_button.hide()
        self.close_button.show()
        self.close_button.grab_focus()
        # As small as what is left in it.
        self.dialog.resize(1, 1)

    # EVENT HANDLERS #
    def _on_phase(self):
        run = self.run
        fetching = run.fetching and bool(run.fetches)
        # l10n: In the Pre-translate window, above its progress: online services are being asked for the units' suggestions
        self.phase.set_text(_('Fetching suggestions…') if fetching else '')
        self.phase.set_visible(fetching)
        if run.kind != pretranslate.MT:
            return
        if run.fetching:
            # What the services are sent: the units left.
            self.fetches = {fetch.model: fetch for fetch in run.fetches}
            self._describe_all()
            return
        for model, fetch in self.fetches.items():
            self._row_of(model)[COL_STATUS] = finished_status(fetch.finished)

    def _on_progress(self, done, total):
        run = self.run
        if run is not None and run.kind == pretranslate.MT and run.fetching:
            for model, fetch in self.fetches.items():
                self._row_of(model)[COL_STATUS] = _progress_text(fetch.fetched, fetch.total)
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
