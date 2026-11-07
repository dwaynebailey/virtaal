#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import re
from html import entities as htmlentitydefs

from gi.repository import GObject

from virtaal.common import SignalTracker
from virtaal.common.configurable import Configurable
from virtaal.models.basemodel import BaseModel


# http://effbot.org/zone/re-sub.htm#unescape-html
def unescape_html_entities(text):
    def fixup(m):
        text = m.group(0)
        entity = htmlentitydefs.entitydefs.get(text[1:-1])
        if not entity:
            if text[:2] == "&#":
                try:
                    return chr(int(text[2:-1]))
                except ValueError:
                    pass
        else:
            return entity
        return text

    return re.sub(r"&(#[0-9]+|\w+);", fixup, text)


class BaseTMModel(BaseModel, Configurable):
    """The base interface to be implemented by all TM backend models."""

    __gtype_name__ = None
    __gsignals__ = {
        'match-found': (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (
                GObject.TYPE_STRING,
                GObject.TYPE_PYOBJECT,
            ),
        )
    }

    CONFIG_FILENAME = "tm.ini"

    description = ""
    """A description of the backend. Will be displayed to users."""
    display_name = None
    """The backend's name, suitable for display."""

    configure_func = None
    """A function that starts the configuration, if available."""
    online_service = None
    """The outside service, by name, this backend sends the text being
    translated to; None if it sends none (#1301)."""
    terms_url = None
    """That service's terms of service, if it has any."""

    settings = ()
    """The settingsdialog.Field()s Configure… edits, if any (#1519)."""

    languages_known = True
    """False while a model is still fetching the languages it supports."""

    # Each backend declares its own PREFETCH_MAX, the most units ahead it
    # may be queried for (0-2; 0 for none), and a "prefetch" default in
    # default_config: the user's choice, up to that. It also declares its
    # BULK_MAX, the most source words one fetch for a whole file may ask
    # it for (#1679): 0 for none, None for as many as the fetch budget.

    # INITIALIZERS #
    def __init__(self, controller):
        """Initialise the model and connects it to the appropriate events.

        Only call this from child classes once the object was successfully
        created and want to be connected to signals."""
        super().__init__()
        # Some backends load their config before this.
        if not hasattr(self, 'config'):
            self.config = {}
        self.controller = controller
        self._signal_tracker = SignalTracker()
        self._signal_tracker.connect(self.controller, 'start-query', self._start_query)

        # static suggestion cache for slow TM queries
        self.cache = {}

        self.source_lang = None
        self.target_lang = None
        self.checker = None
        lang_controller = self.controller.main_controller.lang_controller
        checks_controller = self.controller.main_controller.checks_controller
        self._set_source_lang(None, lang_controller.source_lang.code)
        self._set_target_lang(None, lang_controller.target_lang.code)
        self._set_checker(None, checks_controller.code)
        self._signal_tracker.connect(lang_controller, 'source-lang-changed', self._set_source_lang)
        self._signal_tracker.connect(lang_controller, 'target-lang-changed', self._set_target_lang)
        self._signal_tracker.connect(checks_controller, 'checker-set', self._set_checker)
        if self.settings:
            self.configure_func = self.configure

    # METHODS #
    def destroy(self):
        self.save_config()
        self._signal_tracker.disconnect_all()

    def configure(self, parent):
        from virtaal.views.widgets.settingsdialog import SettingsDialog

        values = SettingsDialog(parent, self.display_name, self.settings, self.config).run()
        if values is not None:
            self.update_settings(values)

    def update_settings(self, values):
        """Save values, from the settings dialog, and use them now."""
        if all(str(self.config.get(key, '')) == value for key, value in values.items()):
            return
        self.config.update(values)
        self.save_config()
        self.apply_settings()

    def apply_settings(self):
        """Models with settings override this to use changed config."""
        pass

    def query(self, tmcontroller, unit):
        """Attempt to give suggestions applicable to query_str.

        All TM backends must implement this method, check for
        suggested translations for unit, emit "match-found" on success."""
        pass

    def _start_query(self, tmcontroller, unit):
        """Answer with the suggestions fetched for the file, if any,
        rather than ask again (#1679)."""
        matches = self.fetched_matches(unit)
        if matches is None:
            self.query(tmcontroller, unit)
        elif matches:
            self.emit('match-found', unit.source, [dict(match) for match in matches])

    def match_weight(self, match):
        """How much match is trusted, its quality multiplied by it to
        order suggestions: 1 unless the service weights its sources."""
        return 1.0

    def fetched_matches(self, unit):
        """The matches fetched for unit with the whole file (maybe none),
        or None if it wasn't."""
        fetched = getattr(self.controller, 'fetched', None)
        if fetched is None:
            return None
        return fetched.matches(getattr(self, 'internal_name', None), unit.source)

    def prefetch_depth(self):
        """How many units ahead to look up: the "prefetch" setting, within
        0 and PREFETCH_MAX."""
        ceiling = getattr(self, 'PREFETCH_MAX', 0)
        default = self.default_config.get('prefetch', 0)
        value = self.config.get('prefetch', default)
        try:
            depth = int(value)
        except (TypeError, ValueError):
            logging.debug('%s: prefetch %r is not a number; using %s', self, value, default)
            depth = int(default)
        if not 0 <= depth <= ceiling:
            logging.debug('%s: prefetch %r is outside 0-%d', self, value, ceiling)
        return max(0, min(depth, ceiling))

    def prefetch(self, unit):
        """Look unit up ahead of its arrival, so its answer is cached."""
        if self.languages_known and not self.has_cached(unit.source):
            self.query(self.controller, unit)

    def has_cached(self, query_str):
        """Whether a query for query_str needs no wait."""
        return query_str in self.cache

    def requests_in_flight(self):
        """How many of this model's requests are still unanswered."""
        return 0

    def hold_until_languages_known(self, tmcontroller, unit):
        """True if the model knows its languages. Otherwise keep this query
        to run once they are known, and return False (#4677)."""
        if self.languages_known:
            return True
        self._held_query = (tmcontroller, unit)
        return False

    def set_languages_known(self):
        """Mark the languages as known and run the query held until then."""
        self.languages_known = True
        held, self._held_query = getattr(self, '_held_query', None), None
        if held is not None:
            self.query(*held)

    # Fetching a whole file's suggestions (#1679), see bulkfetch.py #
    billed = False
    """Whether the service bills by the character sent."""

    BULK_WAIT_STATUSES = (429, 503)
    """HTTP statuses that make a file fetch wait and ask again."""

    def bulk_ready(self):
        """Whether the service can be asked for a file's suggestions now:
        it knows these languages and nothing stops it."""
        return False

    def bulk_batches(self, sources):
        """sources in the batches each request asks for: one each, unless
        the service takes more."""
        return [[source] for source in sources]

    def bulk_allowance(self, on_done, on_error):
        """Ask how many more characters the service will take on this
        account: on_done(count), on_done(None) for no known limit, or
        on_error() if it won't say."""
        on_done(None)

    def bulk_characters(self, sources):
        """How many characters asking for sources sends."""
        return sum(len(source) for source in sources)

    def bulk_send(self, batch):
        """The request asking for one batch, or None if it can't be sent."""
        raise NotImplementedError

    def bulk_parse(self, batch, reply):
        """{source: [match, ...]} from the reply to batch's request, with an
        empty list for a source without a suggestion; raise ValueError,
        KeyError, IndexError, TypeError or AttributeError for an unusable
        reply."""
        raise NotImplementedError

    def bulk_wait(self, request, status):
        """Seconds to wait before asking again after the HTTP status (None
        for no reply), or None to stop."""
        if status not in self.BULK_WAIT_STATUSES:
            return None
        from .remotetm import retry_after

        return retry_after(request.result_headers.getvalue(), 60, 900)

    def set_source_lang(self, language):
        """models override this to implement their own
        source-lang-changed event handlers"""
        pass

    def set_target_lang(self, language):
        """models override this to implement their own
        target-lang-changed event handlers"""
        pass

    def set_checker(self, checker):
        """models override this to implement their own
        checker-set event handlers"""
        pass

    def _set_source_lang(self, controller, language):
        """private method for baseline handling of source language
        change events"""
        if language != self.source_lang:
            self.source_lang = language
            self.cache = {}
            self.set_source_lang(language)

    def _set_target_lang(self, controller, language):
        """private method for baseline handling of target language change events"""
        if language != self.target_lang:
            self.target_lang = language
            self.cache = {}
            self.set_target_lang(language)

    def _set_checker(self, controller, checker):
        """private method for handling of project style change events"""
        if checker != self.checker:
            self.checker = checker
            # Matches can depend on the style sent with the query.
            self.cache = {}
            self.set_checker(checker)
