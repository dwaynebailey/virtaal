#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import time

from gi.repository import GLib, GObject
from translate.lang.data import normalize

from virtaal.controllers.basecontroller import BaseController
from virtaal.support.translate_compat import forceunicode


class TMController(BaseController):
    """The logic-filled glue between the TM view and -model."""

    __gtype_name__ = 'TMController'
    __gsignals__ = {'start-query': (GObject.SignalFlags.RUN_FIRST, None, (object,))}

    QUERY_DELAY = 300
    """The delay after a unit is selected (C{Cursor}'s "cursor-changed" event)
        before the TM is queried."""

    PREFETCH_BUSY = 4
    """Preload nothing for a backend with more requests than this
        unanswered, so a preload never crowds out a real query."""

    # INITIALIZERS #
    def __init__(self, main_controller, config=None):
        from virtaal.common import GObjectWrapper

        GObjectWrapper.__init__(self)

        self.config = config if config is not None else {}
        self.main_controller = main_controller
        self.disabled_model_names = ['basetmmodel'] + self.config.get('disabled_models', [])
        self.max_matches = self.config.get('max_matches', 5)
        self.min_quality = self.config.get('min_quality', 75)

        # plug-in signals:
        self._signal_ids = {}
        # tm query delay:
        self._delay_id = None
        from .tmview import TMView

        self.storecursor = None
        self.view = TMView(self, self.max_matches)
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

        lang_controller = getattr(self.main_controller, 'lang_controller', None)
        if lang_controller is not None:
            self._lang_changed_ids = [
                lang_controller.connect(signal, self._on_lang_changed)
                for signal in ('source-lang-changed', 'target-lang-changed')
            ]

    def _load_models(self):
        from virtaal.controllers.plugincontroller import PluginController

        from .models.basetmmodel import BaseTMModel

        self.plugin_controller = PluginController.for_backend(
            self, 'TMModel', 'tm', BaseTMModel, lambda *args: self.disabled_model_names
        )

        self._model_signal_ids = {}

        def on_plugin_enabled(plugin_ctrlr, plugin):
            self._model_signal_ids[plugin] = plugin.connect('match-found', self.accept_response)

        def on_plugin_disabled(plugin_ctrlr, plugin):
            plugin.disconnect(self._model_signal_ids[plugin])

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
        # Perform some sanity checks on matches first
        for match in matches:
            if not isinstance(match.get('quality', 0), int):
                match['quality'] = int(match['quality'] or 0)
            if 'tmsource' not in match or match['tmsource'] is None:
                match['tmsource'] = tmmodel.display_name
            match['query_str'] = query_str

        anything_new = False
        for match in matches:
            curr_targets = [normalize(m['target']) for m in self.matches]
            if normalize(match['target']) not in curr_targets:
                # Let's insert at the end to prioritise existing matches over
                # new ones. We rely on the guarantee of sort stability. This
                # way an existing 100% will be above a new 100%.
                self.matches.append(match)
                anything_new = True
            else:
                norm_match_target = normalize(match['target'])
                prevmatch = [
                    m for m in self.matches if normalize(m['target']) == norm_match_target
                ][0]
                if 'quality' not in prevmatch or not prevmatch['quality']:
                    # Matches without quality are assumed to be less appropriate
                    # (ie. MT matches) than matches with an associated quality.
                    self.matches.remove(prevmatch)
                    self.matches.append(match)
                    anything_new = True
        if not anything_new:
            return
        self.matches.sort(key=lambda x: 'quality' in x and x['quality'] or 0, reverse=True)
        self.matches = self.matches[: self.max_matches]

        # Only call display_matches if necessary:
        if self.matches:
            self.view.display_matches(self.matches)

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
        unit_controller.set_unit_target(target_n, forceunicode(match_data['target']))

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
        self.emit('start-query', self.unit)
        self._prefetch_upcoming()

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
            # Translated units get no suggestions unless asked.
            if unit is None or unit.istranslated():
                continue
            for model, depth in models:
                if depth >= distance and model.requests_in_flight() <= self.PREFETCH_BUSY:
                    model.prefetch(unit)

    def _answers_ready(self, unit):
        """Whether every backend that answers over the network has unit's
        answer, or has it on its way."""
        plugins = getattr(getattr(self, 'plugin_controller', None), 'plugins', {})
        return all(
            model.has_cached(unit.source)
            for model in plugins.values()
            if getattr(model, 'PREFETCH_MAX', 0) > 0
        )

    def update_suggestions(self):
        """Query suggestions for the current unit if they are on and it
        still needs work; translated units only get them on request."""
        if not self.storecursor:
            return
        unit = getattr(self, 'unit', None)
        if self.view.active and unit is not None and not unit.istranslated():
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
        if getattr(self, '_rested', False) and self._answers_ready(self.unit):
            self._arm(0)
        else:
            self._arm(self.QUERY_DELAY)

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

        # Arriving after a rest, not while moving fast.
        now = time.monotonic()
        gap = now - getattr(self, '_arrived_at', float('-inf'))
        self._rested = gap >= self.QUERY_DELAY / 1000
        self._arrived_at = now
        self.view.summoned = False
        return self.update_suggestions()

    def _on_context_selected(self, _modecontroller, _context):
        # The editor moved with the rows around it: query again so the
        # suggestions reappear under it, if they apply to this unit.
        if not self.storecursor:
            return
        unit = self.storecursor.deref()
        if getattr(self.view, 'summoned', False) or (
            self.view.active and unit is not None and not unit.istranslated()
        ):
            self.start_query()

    def _on_lang_changed(self, lang_controller, code):
        # The suggestions on screen are for the previous language pair.
        self.view.clear()
        if not self.storecursor:
            return
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
        self.view.hide()

    def _on_store_loaded(self, storecontroller):
        """Disconnect from the previous store's cursor and connect to the new one."""
        if getattr(self, '_cursor_changed_id', None) and self.storecursor:
            self.storecursor.disconnect(self._cursor_changed_id)
        self.storecursor = storecontroller.cursor
        self._cursor_changed_id = self.storecursor.connect(
            'cursor-changed', self._on_cursor_changed
        )

        def handle_first_unit():
            self._on_cursor_changed(self.storecursor)
            return False

        GLib.idle_add(handle_first_unit)

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
