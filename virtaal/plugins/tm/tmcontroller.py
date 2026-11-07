#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import os
import time

from gi.repository import GLib, GObject
from translate.lang.data import normalize
from translate.search.lshtein import LevenshteinComparer

from virtaal.common import pan_app
from virtaal.controllers.basecontroller import BaseController
from virtaal.support import abstraction, inlinecodes, sdlxliff_compat
from virtaal.support.httpclient import speculative
from virtaal.support.translate_compat import forceunicode


def _config_bool(value):
    return str(value).strip().lower() not in ('false', 'no', 'off', '0')


def _form_texts(unit):
    """unit's target, one string per plural form."""
    target = getattr(unit, 'target', None)
    if not target:
        return []
    strings = getattr(target, 'strings', None)
    return [str(text) for text in strings] if strings else [str(target)]


def _plain_texts(unit, match):
    """(query, source, target) of a match with inline codes for unit, of
    a format with codes, without the codes' native code; else None."""
    if unit is None or not match.get('source_markup') or not match.get('target_markup'):
        return None
    query = inlinecodes.plain_source(unit)
    source = inlinecodes.plain_text(match['source_markup'])
    target = inlinecodes.plain_text(match['target_markup'])
    if None in (query, source, target):
        return None
    return query, source, target


def _fetched(model, unit):
    """Whether model has suggestions fetched with the file for unit."""
    fetched_matches = getattr(model, 'fetched_matches', None)
    return fetched_matches is not None and fetched_matches(unit) is not None


class TMController(BaseController):
    """The logic-filled glue between the TM view and -model."""

    __gtype_name__ = 'TMController'
    __gsignals__ = {'start-query': (GObject.SignalFlags.RUN_FIRST, None, (object,))}

    QUERY_DELAY = 300
    """The delay after a unit is selected (C{Cursor}'s "cursor-changed" event)
        before the TM is queried."""

    TAP_GRACE = 150
    """After a navigation key is let go, how long another tap may follow
        before the TM is queried."""

    # A key move this soon after the last, with no key let go between,
    # is a held key's repeat. From the desktop's key repeat timing.
    burst_threshold = QUERY_DELAY
    tap_grace = TAP_GRACE

    PREFETCH_BUSY = 4
    """Preload nothing for a backend with more requests than this
        unanswered, so a preload never crowds out a real query."""

    abstract_placeables = True
    """Whether TM matches are scored without their placeables and get the
        current unit's own: plugins.ini's [tm] abstract_placeables."""

    show_translated = False
    """Whether translated units get suggestions unasked too: View >
        Suggestions for Translated Units, plugins.ini's [tm] show_translated."""

    fetched = None
    """The open file's FetchedSuggestions (#1679)."""
    fetch_words = 1000
    """How many source words one fetch asks a service for."""
    # Replaced, never changed in place: the running file fetch of each model.
    _fetches = {}

    # INITIALIZERS #
    def __init__(self, main_controller, config=None):
        from virtaal.common import GObjectWrapper

        GObjectWrapper.__init__(self)

        self.config = config if config is not None else {}
        self.main_controller = main_controller
        self.disabled_model_names = ['basetmmodel'] + self.config.get('disabled_models', [])
        self.max_matches = self.config.get('max_matches', 5)
        self.min_quality = self.config.get('min_quality', 75)
        self.abstract_placeables = _config_bool(self.config.get('abstract_placeables', True))
        self.show_translated = _config_bool(self.config.get('show_translated', False))

        # plug-in signals:
        self._signal_ids = {}
        # tm query delay:
        self._delay_id = None
        from .tmview import TMView

        self.storecursor = None
        self.view = TMView(self, self.max_matches)
        self._load_timing()
        self._load_fetched()
        self._load_models()

        self._connect_plugin()

    def _connect_plugin(self):
        self._store_loaded_id = self.main_controller.store_controller.connect(
            'store-loaded', self._on_store_loaded
        )
        self._store_closed_id = self.main_controller.store_controller.connect(
            'store-closed', self._on_store_closed
        )
        if self.main_controller.store_controller.get_store() is not None:
            self._on_store_loaded(self.main_controller.store_controller)
            self.view._should_show_tmwindow = True

        if self.main_controller.mode_controller is not None:
            self._mode_selected_id = self.main_controller.mode_controller.connect(
                'mode-selected', self._on_mode_selected
            )
            self._context_selected_id = self.main_controller.mode_controller.connect(
                'context-selected', self._on_context_selected
            )

        # How a unit was reached decides when its suggestions show.
        window = getattr(getattr(self.main_controller, 'view', None), 'main_window', None)
        if window is not None:
            self._release_ids = [
                window.connect(signal, self._on_release)
                for signal in ('key-release-event', 'button-release-event')
            ]

        unit_controller = getattr(self.main_controller, 'unit_controller', None)
        if unit_controller is not None:
            self._unit_done_id = unit_controller.connect('unit-done', self._on_unit_done)

        lang_controller = getattr(self.main_controller, 'lang_controller', None)
        if lang_controller is not None:
            self._lang_changed_ids = [
                lang_controller.connect(signal, self._on_lang_changed)
                for signal in ('source-lang-changed', 'target-lang-changed')
            ]

    def _load_timing(self):
        """Read the burst threshold (from the desktop's key repeat timing)
        and tap grace, either set in tm.ini's [suggestions]."""
        from virtaal.support import keyrepeat

        config_file = os.path.join(pan_app.get_config_dir(), 'tm.ini')
        config = pan_app.load_config(config_file, 'suggestions')
        timing = keyrepeat.repeat_timing()
        burst = sum(timing) if timing else self.QUERY_DELAY
        self.burst_threshold = pan_app.config_int(
            config.get('burst_threshold', burst), burst, 0, 5000, 'burst_threshold'
        )
        self.tap_grace = pan_app.config_int(
            config.get('tap_grace', self.TAP_GRACE), self.TAP_GRACE, 0, 5000, 'tap_grace'
        )
        logging.debug(
            'TM: key repeat %s; bursts within %d ms, tap grace %d ms',
            timing,
            self.burst_threshold,
            self.tap_grace,
        )

    def _load_fetched(self):
        """The suggestions fetched for files (#1679): tm.ini's [suggestions]
        fetch_words source words a fetch, kept cache_days days."""
        from .fetched import DAYS, WORDS, FetchedSuggestions

        config = pan_app.load_config(
            os.path.join(pan_app.get_config_dir(), 'tm.ini'), 'suggestions'
        )
        days = pan_app.config_int(config.get('cache_days', DAYS), DAYS, 1, 3650, 'cache_days')
        self.fetch_words = pan_app.config_int(
            config.get('fetch_words', WORDS), WORDS, 1, 1000000, 'fetch_words'
        )
        self.fetched = FetchedSuggestions(days)
        # What expired goes at start, without holding it up.
        GLib.idle_add(self.fetched.expire)

    def _load_models(self):
        from virtaal.controllers.plugincontroller import PluginController

        from .models.basetmmodel import BaseTMModel

        self.plugin_controller = PluginController.for_backend(
            self, 'TMModel', 'tm', BaseTMModel, lambda *args: self.disabled_model_names
        )

        self._model_signal_ids = {}

        def on_plugin_enabled(plugin_ctrlr, plugin):
            self._model_signal_ids[plugin] = plugin.connect('match-found', self.accept_response)
            if hasattr(plugin, 'import_file'):
                self.view.add_import_menu_item(plugin)
            if hasattr(plugin, 'set_exclude'):
                self.view.add_exclude_menu_item(plugin)

        def on_plugin_disabled(plugin_ctrlr, plugin):
            self.stop_fetch(plugin)
            plugin.disconnect(self._model_signal_ids[plugin])
            if hasattr(plugin, 'import_file'):
                self.view.remove_import_menu_item()
            if hasattr(plugin, 'set_exclude'):
                self.view.remove_exclude_menu_item()

        self._signal_ids['plugin-enabled'] = self.plugin_controller.connect(
            'plugin-enabled', on_plugin_enabled
        )
        self._signal_ids['plugin-disabled'] = self.plugin_controller.connect(
            'plugin-disabled', on_plugin_disabled
        )

        self.plugin_controller.load_plugins()

    # METHODS #
    def accept_response(self, tmmodel, query_str, matches):
        """Accept a query-response from the model.
        (This method is used as Model-Controller communications)"""
        if not self.storecursor:
            # File closed since the query was started
            return
        query_str = forceunicode(query_str)
        if query_str != self.current_query or not matches:
            return
        self._tidy_matches(tmmodel, query_str, matches)
        unit = getattr(self, '_queried_unit', None)
        accel_markers = self._accel_markers() if self.abstract_placeables else ()
        matches = [self._for_form(unit, match, accel_markers) for match in matches]
        matches = [self._cap_for_codes(unit, match) for match in matches]

        anything_new = False
        for match in matches:
            anything_new = self._add_match(match) or anything_new
        if not anything_new:
            return
        self.matches.sort(key=lambda x: 'quality' in x and x['quality'] or 0, reverse=True)
        self.matches = self.matches[: self.max_matches]

        # Only call display_matches if necessary:
        if self.matches:
            self.view.display_matches(self.matches)

    @staticmethod
    def _tidy_matches(tmmodel, query_str, matches):
        """Give every match an int quality, a TM source and the query."""
        for match in matches:
            if not isinstance(match.get('quality', 0), int):
                match['quality'] = int(match['quality'] or 0)
            if 'tmsource' not in match or match['tmsource'] is None:
                match['tmsource'] = tmmodel.display_name
            match['query_str'] = query_str

    def _add_match(self, match):
        """Add match unless one with its target is already there, which
        it replaces if that one has no quality. True if added."""
        norm_match_target = normalize(match['target'])
        prevmatch = next(
            (m for m in self.matches if normalize(m['target']) == norm_match_target), None
        )
        if prevmatch is None:
            # Let's insert at the end to prioritise existing matches over
            # new ones. We rely on the guarantee of sort stability. This
            # way an existing 100% will be above a new 100%.
            self.matches.append(match)
            return True
        if 'quality' not in prevmatch or not prevmatch['quality']:
            # Matches without quality are assumed to be less appropriate
            # (ie. MT matches) than matches with an associated quality.
            self.matches.remove(prevmatch)
            self.matches.append(match)
            return True
        return False

    def _for_form(self, unit, match, accel_markers):
        """match adapted to unit, for the plural form being edited: for a
        form after the first, the match's own form n scored on the source
        plurals, if it has as many forms as the file and scores well
        enough; otherwise its target scored on the source (#1392)."""
        query_str = match['query_str']
        adapted = match
        if self.abstract_placeables:
            adapted = self._adapt(query_str, match, accel_markers, unit)
        form = self._plural_form(unit, match)
        if form is None:
            return adapted
        if self.abstract_placeables:
            form = self._adapt(form['query_str'], form, accel_markers)
        if form['quality'] < getattr(self, 'min_quality', 75):
            return adapted
        return form

    def _plural_form(self, unit, match):
        """match's form target_n with its source plural, scored on unit's,
        or None if it has no such form."""
        target_n = getattr(self, 'target_n', 0)
        forms, source = match.get('plural_targets'), match.get('plural_source')
        if not (target_n and match.get('quality') and isinstance(source, str)):
            return None
        if not isinstance(forms, list) or len(forms) != self._nplurals() or target_n >= len(forms):
            return None
        target = forms[target_n]
        if unit is None or not unit.hasplural() or not (isinstance(target, str) and target):
            return None
        sources = getattr(unit.source, 'strings', None) or [unit.source]
        query = str(sources[min(1, len(sources) - 1)])
        quality = int(LevenshteinComparer(1000).similarity(query, source, 0))
        form = {k: v for k, v in match.items() if k not in ('source_markup', 'target_markup')}
        form.update(
            source=source,
            target=target,
            quality=quality,
            raw_quality=quality,
            query_str=query,
        )
        return form

    def _nplurals(self):
        main_controller = getattr(self, 'main_controller', None)
        lang_controller = getattr(main_controller, 'lang_controller', None)
        return lang_controller.get_target_nplurals() if lang_controller is not None else None

    def existing_target(self):
        """The edited form's translation as it was when the unit was
        reached, or '' if it had none: what suggestions are compared with."""
        forms = getattr(self, '_existing_targets', ())
        target_n = getattr(self, 'target_n', 0) or 0
        return forms[target_n] if target_n < len(forms) else ''

    def _accel_markers(self):
        checks_controller = getattr(
            getattr(self, 'main_controller', None), 'checks_controller', None
        )
        checker = checks_controller.get_checker() if checks_controller is not None else None
        return tuple(getattr(getattr(checker, 'config', None), 'accelmarkers', None) or ())

    @staticmethod
    def _adapt(query_str, match, accel_markers, unit=None):
        """match, or a copy with its target rebuilt with the current unit's
        placeables and accelerator, and its quality rescored. Machine
        translations (no quality) are left alone. A match with inline
        codes for unit, with codes too, is scored on their texts without
        the codes' native code, as the local TM scores it."""
        if not match.get('quality') or match.get('source') is None or not match.get('target'):
            return match
        source, target = forceunicode(match['source']), forceunicode(match['target'])
        # The local TM scored it on the abstracts already.
        raw_quality = match.get('raw_quality', match['quality'])
        adapted = abstraction.adapt(query_str, source, target, raw_quality, accel_markers)
        plain = _plain_texts(unit, match)
        if plain is not None:
            quality = abstraction.adapt(*plain, raw_quality, accel_markers).quality
            adapted = adapted._replace(quality=quality)
        if adapted.target == target and adapted.quality == match['quality']:
            return match
        logging.debug(
            'TM: %r scored %d, adapted %d: %r',
            target,
            match['quality'],
            adapted.quality,
            adapted.target,
        )
        match = dict(match, quality=adapted.quality, raw_quality=adapted.raw_quality)
        if adapted.target != target:
            match['target'] = adapted.target
            match['tm_target'] = target
        return match

    @staticmethod
    def _cap_for_codes(unit, match):
        """match, or a copy scored 99 if it is a 100 whose inline codes
        differ from unit's."""
        if int(match.get('quality') or 0) < 100 or not inlinecodes.codes_differ(
            match.get('source_markup'), unit
        ):
            return match
        return dict(match, quality=99)

    def destroy(self):
        # Destroy the view
        self.view.hide()
        self.view.destroy()

        # Disconnect signals
        self.main_controller.store_controller.disconnect(self._store_loaded_id)
        if getattr(self, '_cursor_changed_id', None):
            self.main_controller.store_controller.cursor.disconnect(self._cursor_changed_id)
        if getattr(self, '_mode_selected_id', None):
            self.main_controller.mode_controller.disconnect(self._mode_selected_id)
        if getattr(self, '_context_selected_id', None):
            self.main_controller.mode_controller.disconnect(self._context_selected_id)
        if getattr(self, '_target_focused_id', None):
            self.main_controller.unit_controller.view.disconnect(self._target_focused_id)
        for signal_id in getattr(self, '_lang_changed_ids', []):
            self.main_controller.lang_controller.disconnect(signal_id)
        if getattr(self, '_unit_done_id', None):
            self.main_controller.unit_controller.disconnect(self._unit_done_id)
        if self.fetched is not None:
            self.fetched.close()
        for signal_id in getattr(self, '_release_ids', []):
            self.main_controller.view.main_window.disconnect(signal_id)
        if getattr(self, '_popup_toggled_id', None):
            self.main_controller.unit_controller.view.disconnect(self._popup_toggled_id)

        self.plugin_controller.shutdown()

    def select_match(self, match_data):
        """Handle a match-selection event.
        (This method is used as View-Controller communications)"""
        unit_controller = self.main_controller.unit_controller
        target_n = unit_controller.view.focused_target_n
        textbox = unit_controller.view.targets[target_n]
        self.main_controller.undo_controller.push_current_text(textbox)
        target = self._rich_target(getattr(unit_controller, 'current_unit', None), match_data)
        if target is None:
            target = forceunicode(match_data['target'])
        unit_controller.set_unit_target(target_n, target)

    @staticmethod
    def _rich_target(unit, match_data):
        """The match's target with unit's own inline codes, or None if
        either has none or they don't map."""
        source_markup = match_data.get('source_markup')
        target_markup = match_data.get('target_markup')
        if not (source_markup and target_markup) or unit is None or unit.hasplural():
            return None
        sources = unit.rich_source
        if len(sources) != 1:
            return None
        return inlinecodes.rebuild(source_markup, target_markup, sources[0])

    def insert_match(self, match_data):
        """Insert the match's text at the cursor in the focused target,
        keeping the rest of the target and its placeables, with the unit's
        own inline codes if they map and the target has none of them."""
        unit_view = self.main_controller.unit_controller.view
        target_n = unit_view.focused_target_n
        if target_n is None:
            return
        textbox = unit_view.targets[target_n]
        unit = getattr(self.main_controller.unit_controller, 'current_unit', None)
        target = self._rich_target(unit, match_data)
        if target is not None and not inlinecodes.shares_codes(target, textbox.elem):
            textbox.insert_elem(target)
        else:
            textbox.insert_text(forceunicode(match_data['target']))
        textbox.grab_focus()

    def send_tm_query(self, unit=None):
        """Send a new query to the TM engine.
        (This method is used as Controller-Model communications)"""
        if unit is not None:
            self.unit = unit

        self.current_query = self.unit.source
        # The unit and plural form the suggestions are for.
        self._queried_unit = self.unit
        unit_view = getattr(self, 'unit_view', None)
        self.target_n = getattr(unit_view, 'focused_target_n', None) or 0
        self.matches = []
        self.view.clear()
        if getattr(self, '_arrival_pending', False):
            self._arrival_pending = False
            logging.debug(
                'TM: query %d ms after a %s arrival',
                (time.monotonic() - self._arrived_at) * 1000,
                self._arrival,
            )
        self.emit('start-query', self.unit)
        self._prefetch_upcoming()

    # Suggestions for a whole file (#1679) #
    def file_units(self):
        """The open file's units, or [] with none open: with several files
        open, every file's, one after the other."""
        store = self.main_controller.store_controller.get_store()
        return store.get_units() if store is not None else []

    def failing_units(self):
        """The indices of the units failing the current checker's quality
        checks, as far as they are known."""
        store = self.main_controller.store_controller.get_store()
        checks = getattr(store, 'checks', None) or {}
        return frozenset(index for indices in checks.values() for index in indices)

    def is_stream(self):
        """Whether several files are open as one."""
        store_controller = self.main_controller.store_controller
        return bool(getattr(store_controller, 'is_stream', lambda: False)())

    def load_fetched(self):
        """Keep the open file's fetched suggestions at hand, for the
        current languages."""
        if self.fetched is None:
            return
        lang_controller = self.main_controller.lang_controller
        self.fetched.load(
            self.file_units(),
            lang_controller.source_lang.code,
            lang_controller.target_lang.code,
            self.failing_units(),
        )

    def fetch_budget(self, model):
        """How many source words model may be asked for at once: the fetch
        budget, or the model's BULK_MAX if lower; 0 for none."""
        ceiling = model.BULK_MAX
        return self.fetch_words if ceiling is None else min(self.fetch_words, ceiling)

    def fetch_plan(self, model):
        """(sources, whole): what a fetch from model asks for, from the
        current unit on, and whether that is all the file needs."""
        cursor = getattr(self, 'storecursor', None)
        start = cursor.index if cursor and cursor.index >= 0 else 0
        words = self.fetch_budget(model)
        if not words:
            return [], False
        return self.fetched.plan(
            model.internal_name, self.file_units(), start, words, self.failing_units()
        )

    @staticmethod
    def cap_characters(model, sources, characters):
        """(the first of sources that come to at most characters characters
        sent to model, whether any were left out)."""
        kept, total = [], 0
        for source in sources:
            total += model.bulk_characters([source])
            if total > characters:
                return kept, True
            kept.append(source)
        return kept, False

    def fetch_for_file(self, model, on_progress, on_done, characters=None):
        """Start asking model for its fetch_plan()'s sources, those that
        come to at most characters characters if given;
        on_progress(fetch) after each batch, on_done(fetch) at the end.
        Returns the BulkFetch."""
        from .bulkfetch import BulkFetch

        service = model.internal_name

        def found(fetch, batch):
            self.fetched.keep(service, batch)
            on_progress(fetch)

        def done(fetch):
            if self._fetches.get(model) is fetch:
                self._fetches = {m: f for m, f in self._fetches.items() if m is not model}
            on_done(fetch)

        sources, _whole = self.fetch_plan(model)
        if characters is not None:
            sources, _capped = self.cap_characters(model, sources, characters)
        fetch = BulkFetch(model, sources, found, done)
        self._fetches = {**self._fetches, model: fetch}
        fetch.start()
        return fetch

    def stop_fetch(self, model):
        """Stop model's file fetch, if one is running: it was turned off."""
        fetch = self._fetches.get(model)
        if fetch is not None:
            fetch.cancel()

    def _prefetching_models(self):
        plugins = getattr(getattr(self, 'plugin_controller', None), 'plugins', {})
        return [(model, model.prefetch_depth()) for model in plugins.values()]

    def _prefetch_upcoming(self):
        """Look up the units the next moves land on, as far ahead as each
        backend may."""
        cursor = getattr(self, 'storecursor', None)
        models = [(m, depth) for (m, depth) in self._prefetching_models() if depth > 0]
        if not cursor or not models:
            return
        upcoming = cursor.upcoming(max(depth for _m, depth in models))
        for distance, index in enumerate(upcoming, 1):
            unit = cursor.model[index]
            if unit is None or not self._shows_unasked(unit):
                continue
            for model, depth in models:
                if _fetched(model, unit):
                    continue
                if depth >= distance and model.requests_in_flight() <= self.PREFETCH_BUSY:
                    with speculative():
                        model.prefetch(unit)

    def _answers_ready(self, unit):
        """Whether every backend that answers over the network has unit's
        answer, or has it on its way."""
        plugins = getattr(getattr(self, 'plugin_controller', None), 'plugins', {})
        return all(
            model.has_cached(unit.source) or _fetched(model, unit)
            for model in plugins.values()
            if getattr(model, 'PREFETCH_MAX', 0) > 0
        )

    def _shows_unasked(self, unit):
        """Whether unit gets suggestions unasked: one still to translate,
        or any with show_translated on. A locked Trados segment can't take
        one."""
        if sdlxliff_compat.is_locked(unit):
            return False
        return self.show_translated or not unit.istranslated()

    def set_show_translated(self, show):
        """Turn suggestions for translated units on or off (#1396)."""
        self.show_translated = show
        self.config['show_translated'] = str(show)
        unit = getattr(self, 'unit', None)
        if unit is not None and unit.istranslated() and not self.view.summoned:
            self.update_suggestions()

    def update_suggestions(self):
        """Query suggestions for the current unit if they are on and it
        gets them unasked; otherwise it only gets them on request."""
        if not self.storecursor:
            return
        unit = getattr(self, 'unit', None)
        if self.view.active and unit is not None and self._shows_unasked(unit):
            return self.start_query()
        if getattr(self, '_delay_id', None):
            GLib.source_remove(self._delay_id)
            self._delay_id = None
        self.view.hide()

    def summon(self):
        """Show suggestions for the current unit once, whether or not they
        are on and the unit is translated."""
        if not self.storecursor:
            return
        self.unit = self.storecursor.deref()
        if self.unit is None:
            return
        self.view.summoned = True
        if not self.view.isvisible:
            self.start_query()

    def start_query(self):
        """Start a TM query after C{self.QUERY_DELAY} milliseconds."""
        if not self.storecursor:
            return

        if not hasattr(self, 'unit'):
            self.unit = self.storecursor.deref()

        self.unit_view = self.main_controller.unit_controller.view
        if getattr(self, '_target_focused_id', None) and getattr(self, 'unit_view', None):
            self.unit_view.disconnect(self._target_focused_id)
        self._target_focused_id = self.unit_view.connect('target-focused', self._on_target_focused)
        if getattr(self, '_popup_toggled_id', None):
            self.unit_view.disconnect(self._popup_toggled_id)
        self._popup_toggled_id = self.unit_view.connect(
            'editor-popup-toggled', self._on_editor_popup_toggled
        )
        self.view.hide()

        def start_query():
            self._delay_id = None
            self.send_tm_query()
            return False

        if self._delay_id:
            GLib.source_remove(self._delay_id)
        self._start_query = start_query
        kind = self._arrival if getattr(self, '_arrival_pending', False) else None
        # The wait now, and the wait from when a key or button is let go.
        wait, self._release_wait = self._query_waits(kind)
        self._arm(wait)

    def _query_waits(self, kind):
        """The wait before querying for a unit reached as kind, and the
        wait from when a key or button is let go (None: no wait)."""
        waits = {
            'commit': (0, None),
            # A tap can't be told from the start of a held key until it is
            # let go, or would have repeated.
            'step': (self.burst_threshold, self.tap_grace),
            'burst': (self.QUERY_DELAY, self.QUERY_DELAY),
            'click': (self.QUERY_DELAY, 0),
            'jump': (self.QUERY_DELAY, 0),
        }
        if kind in waits:
            return waits[kind]
        if kind is None and getattr(self, '_rested', False) and self._answers_ready(self.unit):
            return 0, None
        return self.QUERY_DELAY, None

    def _arm(self, wait):
        if wait:
            self._delay_id = GLib.timeout_add(wait, self._start_query)
        else:
            # Idle, not now: the unit just left must reach the "This file"
            # TM first.
            self._delay_id = GLib.idle_add(self._start_query)

    # EVENT HANDLERS #
    def _on_cursor_changed(self, cursor):
        self.storecursor = cursor
        if cursor is None:
            # this can happen if we close a big file before it finished loading
            return

        self.unit = cursor.deref()

        if self.unit is None:
            return
        self._existing_targets = _form_texts(self.unit)

        # Arriving after a rest, not while moving fast.
        now = time.monotonic()
        gap = now - getattr(self, '_arrived_at', float('-inf'))
        self._rested = gap >= self.QUERY_DELAY / 1000
        self._arrived_at = now
        main_controller = getattr(self, 'main_controller', None)
        kind = getattr(getattr(main_controller, 'store_controller', None), 'arrival', None)
        if kind in ('step', 'commit'):
            last = getattr(self, '_key_moved_at', None)
            if (
                last is not None
                and not getattr(self, '_released', True)
                and (now - last) * 1000 < self.burst_threshold
            ):
                kind = 'burst'
            self._key_moved_at = now
            self._released = False
        else:
            self._key_moved_at = None
        self._arrival, self._arrival_pending = kind, True
        logging.debug('TM: %s arrival, %.0f ms after the last', kind, gap * 1000)
        self.view.summoned = False
        return self.update_suggestions()

    def _on_context_selected(self, _modecontroller, _context):
        # The editor moved with the rows around it: query again so the
        # suggestions reappear under it, if they apply to this unit.
        if not self.storecursor:
            return
        unit = self.storecursor.deref()
        if getattr(self.view, 'summoned', False) or (
            self.view.active and unit is not None and self._shows_unasked(unit)
        ):
            self.start_query()

    def _on_lang_changed(self, lang_controller, code):
        # The suggestions on screen are for the previous language pair.
        self.view.clear()
        if not self.storecursor:
            return
        self.load_fetched()
        if self.view.summoned:
            self.start_query()
        else:
            self.update_suggestions()

    def _on_mode_selected(self, modecontroller, mode):
        self.view.update_geometry()

    def _on_store_closed(self, storecontroller):
        if hasattr(self, '_cursor_changed_id') and self.storecursor:
            self.storecursor.disconnect(self._cursor_changed_id)
        self.storecursor = None
        self._cursor_changed_id = 0
        self._arrival_pending, self._release_wait = False, None
        if self.fetched is not None:
            self.fetched.clear()
        self.view.hide()

    def _on_store_loaded(self, storecontroller):
        """Disconnect from the previous store's cursor and connect to the new one."""
        if getattr(self, '_cursor_changed_id', None) and self.storecursor:
            self.storecursor.disconnect(self._cursor_changed_id)
        self.storecursor = storecontroller.cursor
        self._cursor_changed_id = self.storecursor.connect(
            'cursor-changed', self._on_cursor_changed
        )
        self.load_fetched()

        def handle_first_unit():
            self._on_cursor_changed(self.storecursor)
            return False

        GLib.idle_add(handle_first_unit)

    def _on_unit_done(self, _unit_controller, unit, _modified):
        # A translated unit's fetched suggestions have served their turn.
        if unit is not None and unit.istranslated() and self.fetched is not None:
            self.fetched.forget(unit.source)

    def _on_release(self, _widget, event):
        self._released = True
        wait = getattr(self, '_release_wait', None)
        if wait is not None and getattr(self, '_delay_id', None):
            logging.debug(
                'TM: let go (keyval %s); query in %d ms', getattr(event, 'keyval', '-'), wait
            )
            self._release_wait = None
            GLib.source_remove(self._delay_id)
            self._arm(wait)
        return False

    def _on_editor_popup_toggled(self, unit_view, showing):
        if showing:
            self.view.suspend()
        else:
            self.view.resume()

    def _on_target_focused(self, unitcontroller, target_n):
        self.view.update_geometry()
        # A plural unit's other forms are suggestions for this one (#874).
        storecursor = getattr(self, 'storecursor', None)
        unit = storecursor.deref() if storecursor else None
        if unit is None or not unit.hasplural() or target_n == getattr(self, 'target_n', 0):
            return
        # Not istranslated(): typing in one form already makes it true.
        if self.view.summoned or (
            self.view.active and getattr(self, '_queried_unit', None) is unit
        ):
            self.start_query()
