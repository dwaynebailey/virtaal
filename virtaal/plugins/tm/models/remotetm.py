#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import email.utils
import logging
import time
from datetime import timezone

from virtaal.views.widgets.settingsdialog import PORT, Field

from .basetmmodel import BaseTMModel


class TMModel(BaseTMModel):
    """TM back-end that allows Virtaal to connect to a remote TM server."""

    __gtype_name__ = 'RemoteTMModel'
    display_name = _('Remote Server')
    description = _('A translation memory server')
    # l10n: Try to keep this as short as possible.
    shortname = _('Remote TM')

    PREFETCH_MAX = 2
    default_config = {
        "host": "localhost",
        "port": "55555",
        "prefetch": 2,
    }
    settings = (
        # l10n: The name or IP address of the translation memory server
        Field('host', _('_Host:'), required=True),
        Field('port', _('_Port:'), PORT),
    )

    RETRY_AFTER = 60
    """Seconds to send nothing after "too many requests" without a Retry-After."""
    MAX_RETRY_AFTER = 900

    # Replaced, never changed in place: the requests sent and not yet
    # answered, by query string, and when sending may resume.
    _pending = {}
    _retry_at = 0.0
    # Counts the times the server learnt new units.
    _generation = 0

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        super().__init__(controller)
        self.internal_name = internal_name
        self.load_config()
        self._connect()

    def _connect(self):
        url = "http://%s:%s/tmserver" % (self.config["host"], self.config["port"])

        from virtaal.support import tmclient

        self.tmclient = tmclient.TMClient(url)
        self.tmclient.set_virtaal_useragent()

    # METHODS #
    def apply_settings(self):
        # Answers still due from the previous server are dropped.
        self.cache = {}
        self._pending = {}
        self._retry_at = 0.0
        self._generation += 1
        self._connect()

    def query(self, tmcontroller, unit):
        # We don't want to do lookups in case of misconfigurations like en->en
        if self.source_lang == self.target_lang:
            return

        query_str = unit.source
        if query_str in self.cache:
            _cached = self.cache[query_str]
            if _cached:
                # Only emit if we actually have something to offer
                self.emit('match-found', query_str, _cached)
        else:
            self._request(query_str)

    def prefetch(self, unit):
        if not self.has_cached(unit.source):
            self._request(unit.source)

    def has_cached(self, query_str):
        # Also true where nothing would be sent.
        return (
            self.source_lang == self.target_lang
            or query_str in self.cache
            or self._sent(query_str)
            or time.monotonic() < self._retry_at
        )

    def requests_in_flight(self):
        return len(getattr(self.tmclient, 'requests', ()))

    def _key(self):
        return (self.source_lang, self.target_lang, self.checker, self._generation)

    def _sent(self, query_str):
        """Whether query_str's answer for these languages is on its way."""
        request = self._pending.get(query_str)
        return request is not None and request.tm_key == self._key()

    def _request(self, query_str):
        # A string already sent shows when its answer lands.
        if self._sent(query_str) or time.monotonic() < self._retry_at:
            return
        self._track(self._send(query_str), query_str)

    def _params(self):
        params = {}
        if self.checker:
            params['style'] = self.checker
        return params

    def _send(self, query_str):
        """The request for query_str, or None if the client's queue was full."""
        return self.tmclient.translate_unit(
            query_str, self.source_lang, self.target_lang, self._handle_matches, self._params()
        )

    def _track(self, request, query_str):
        """Keep query_str's request pending until it is answered."""
        if request is None:
            return
        request.tm_key = self._key()
        self._pending = {**self._pending, query_str: request}
        for signal in (
            'http-success',
            'http-redirect',
            'http-client-error',
            'http-server-error',
            'http-failed',
        ):
            request.connect(signal, self._on_request_done, query_str)

    def _on_request_done(self, request, result, query_str):
        if self._pending.get(query_str) is request:
            self._pending = {k: v for k, v in self._pending.items() if k != query_str}
        if result not in (429, 503):
            return
        headers = getattr(request, 'result_headers', None)
        seconds = retry_after(
            headers.getvalue() if headers is not None else b'',
            self.RETRY_AFTER if result == 429 else None,
            self.MAX_RETRY_AFTER,
        )
        if seconds:
            logging.debug('%s: sending nothing for %d s (HTTP %d)', self, seconds, result)
            self._retry_at = time.monotonic() + seconds

    def _handle_matches(self, widget, query_str, matches):
        """Handle the matches when returned from self.tmclient."""
        if getattr(widget, 'tm_key', self._key()) != self._key():
            # Asked for before a language or style change, or a save.
            return
        # A reply of another shape than a list of matches is no matches (#4437).
        if not isinstance(matches, list):
            matches = []
        matches = [m for m in matches if isinstance(m, dict) and 'source' in m and 'target' in m]
        self.cache[query_str] = matches or None  # None instead of empty list
        if matches:
            for match in matches:
                match['tmsource'] = self.shortname
            self.emit('match-found', query_str, matches)

    def push_store(self, store_controller):
        """Add units in store to TM database on save."""
        for filename, units in store_controller.store.file_parts():
            units = [unit2dict(unit) for unit in units if unit.istranslated()]
            self.tmclient.add_store(filename, units, self.source_lang, self.target_lang)
        self.cache = {}
        self._generation += 1

    def upload_store(self, store_controller):
        """Upload store to TM server."""
        self.tmclient.upload_store(
            store_controller.store._trans_store, self.source_lang, self.target_lang
        )
        self.cache = {}
        self._generation += 1


def unit2dict(unit):
    return {"source": unit.source, "target": unit.target, "context": unit.getcontext()}


def retry_after(headers, default, maximum):
    """Seconds to wait from the last Retry-After in raw response headers,
    from 1 to maximum, or default."""
    value = None
    for line in headers.decode('latin-1').splitlines():
        name, _sep, rest = line.partition(':')
        if name.strip().lower() == 'retry-after':
            value = rest.strip()
    if value is None:
        return default
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return default
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = when.timestamp() - time.time()
    return max(1, min(seconds, maximum))
