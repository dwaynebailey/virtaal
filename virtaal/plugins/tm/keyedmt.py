#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

# Lives outside models/ so the plug-in controller doesn't list it as a
# TM backend.

import json
import logging
import time

from virtaal.support.httpclient import HTTPClient

from . import bulkfetch, mtplaceables
from .models.basetmmodel import BaseTMModel
from .models.remotetm import retry_after


class KeyedMTModel(BaseTMModel):
    """Machine translations from an online service used with the user's
    API key: one string per request, cached, nothing sent while the
    service asks to wait, and nothing at all once it refuses the key."""

    __gtype_name__ = None

    machine_translation = True
    service_name = None
    """The service's name in log messages."""
    STOP_STATUSES = ()
    """HTTP statuses that stop all queries: a refused key, used-up quota."""
    WAIT_STATUSES = ()
    """HTTP statuses below 500 that mean wait and ask again."""

    RETRY_AFTER = 60
    """Seconds to send nothing after a refusal to answer now, without a
    Retry-After."""
    MAX_RETRY_AFTER = 900

    protection = None
    """How the source's placeables are marked up for the service, as an
    mtplaceables style; None to send the text as it is."""

    client = None
    _stopped = False
    _retry_at = 0.0

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        self.internal_name = internal_name
        super().__init__(controller)
        self.load_config()
        self._start()

    def _start(self):
        self._pending = set()
        self._retry_at = 0.0
        self._stopped = False
        if not self._key():
            self._stop("An API key is needed to use %s" % self.service_name)
            return
        if self.client is None:
            self.client = HTTPClient()
        self._get_languages()

    def _key(self):
        return self.config['api_key'].strip()

    def _credentials(self):
        """What replies are kept for: those sent with other credentials
        are dropped."""
        return self.config['api_key']

    def _send(self, request, on_success, on_error, keyed=True):
        credentials = self._credentials()

        def for_credentials(handler):
            # Replies for credentials since replaced are dropped.
            def on_reply(request, value):
                if not keyed or credentials == self._credentials():
                    handler(request, value)

            return on_reply

        if self.client.add(request) is False:
            # The client's queue was full.
            on_error(request, None)
            return
        request.connect('http-success', for_credentials(on_success))
        for signal in ('http-client-error', 'http-server-error', 'http-failed'):
            request.connect(signal, for_credentials(on_error))

    # HOOKS #
    def _get_languages(self):
        """Make the supported languages known, once a key is set."""
        self._fetch_languages()

    def _fetch_languages(self):
        """Ask for the supported languages, answered to got_languages()."""
        raise NotImplementedError

    def _has_languages(self):
        """Whether the supported languages arrived."""
        raise NotImplementedError

    def _set_languages(self, reply):
        """Keep the supported languages from the decoded reply; raise
        ValueError, KeyError, TypeError or AttributeError for an unusable
        one."""
        raise NotImplementedError

    def _language_pair(self):
        """The service's (source, target) codes for the current languages;
        None if it doesn't support them, () if there is nothing to
        translate."""
        raise NotImplementedError

    def _batch_request(self, protected, source_lang, target_lang):
        """The RESTRequest asking for the translations of protected, a
        list of mtplaceables.Protected all with markup or all without."""
        raise NotImplementedError

    def _translations(self, reply):
        """The translations in the decoded reply, in the order asked for;
        raise ValueError, KeyError, IndexError or TypeError for an
        unusable one."""
        raise NotImplementedError

    def _translate_request(self, protected, source_lang, target_lang):
        """The RESTRequest asking for the translation of protected."""
        return self._batch_request([protected], source_lang, target_lang)

    def _translation(self, reply):
        return self._translations(reply)[0]

    def _tmsource(self):
        """The suggestion's source, for display."""
        raise NotImplementedError

    # METHODS #
    def apply_settings(self):
        self.cache = {}
        self._start()

    def query(self, tmcontroller, unit):
        pair = self._query_pair(tmcontroller, unit)
        if not pair:
            return

        query_str = unit.source
        protected = self._protect(query_str)
        # Strings that differ only in placeables can share a translation.
        if protected.text in self.cache:
            self._found(query_str, protected, self.cache[protected.text])
            return
        sent = (protected.text, self.source_lang, self.target_lang)
        if sent in self._pending:
            return
        request = self._translate_request(protected, *pair)
        self._pending.add(sent)

        def done(request, value, handler):
            self._pending.discard(sent)
            if sent[1:] == (self.source_lang, self.target_lang):
                handler(request, value, query_str)

        self._send(
            request,
            lambda request, value: done(request, value, self.got_translation),
            lambda request, value: done(request, value, self.got_error),
        )

    def _query_pair(self, tmcontroller, unit):
        """The service's language pair to query unit with, or a false
        value while it can't be queried."""
        if self._stopped or not self.hold_until_languages_known(tmcontroller, unit):
            return None
        if self._waiting():
            return None
        if not self._has_languages():
            # The language list never came: ask again.
            self._fetch_languages()
            self.hold_until_languages_known(tmcontroller, unit)
            return None
        if not self.source_lang or not self.target_lang:
            return None
        pair = self._language_pair()
        if pair is None:
            logging.debug(
                '%s: language pair not supported: %s => %s',
                self.service_name,
                self.source_lang,
                self.target_lang,
            )
        return pair

    def has_cached(self, query_str):
        # Also true where nothing would be sent.
        return self._stopped or self._waiting() or self._protect(query_str).text in self.cache

    def _protect(self, query_str):
        if self.protection is None:
            return mtplaceables.Protected(query_str, (), False)
        return self.protection.protect(query_str)

    def _found(self, query_str, protected, text):
        self.emit('match-found', query_str, [self._match(query_str, protected, text)])

    def _match(self, query_str, protected, text):
        restored = mtplaceables.Restored(text, text)
        if protected.markup:
            restored = self.protection.restore(text, protected)
            if restored.returned != restored.text:
                logging.debug('%s: placeables moved in %r', self.service_name, text)
        return mtplaceables.suggestion(query_str, restored, self._tmsource())

    def requests_in_flight(self):
        return len(self.client.requests) if self.client is not None else 0

    def got_translation(self, request, val, query_str):
        try:
            text = self._translation(json.loads(val))
        except (ValueError, KeyError, IndexError, TypeError) as e:
            logging.debug('%s: unexpected reply: %s', self.service_name, e)
            return
        if not isinstance(text, str) or not text.strip():
            return
        protected = self._protect(query_str)
        self.cache[protected.text] = text
        self._found(query_str, protected, text)

    def got_languages(self, request, val):
        try:
            self._set_languages(json.loads(val))
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            self._stop("Unexpected language list: %s" % e)
            return
        self.set_languages_known()

    def got_error(self, request, status, query_str):
        if not self._wait_or_stop(request, status):
            # 400, 413, ...: this string only.
            logging.debug('%s: no translation (HTTP %s)', self.service_name, status)

    def _languages_failed(self, request, status):
        if not self._wait_or_stop(request, status):
            self._stop("Language list refused (HTTP %s)" % status)
        self.set_languages_known()

    def _wait_or_stop(self, request, status):
        """Stop for a STOP_STATUSES status; wait for a WAIT_STATUSES one, a
        server problem or no reply. Whether it did either."""
        if status in self.STOP_STATUSES:
            self._stop("HTTP %s" % status)
            return True
        if isinstance(status, int) and status not in self.WAIT_STATUSES and status < 500:
            return False
        headers = getattr(request, 'result_headers', None)
        seconds = retry_after(
            headers.getvalue() if headers is not None else b'',
            self.RETRY_AFTER,
            self.MAX_RETRY_AFTER,
        )
        logging.debug(
            '%s: sending nothing for %d s (%s)',
            self.service_name,
            seconds,
            'HTTP %s' % status if isinstance(status, int) else 'no reply',
        )
        self._retry_at = time.monotonic() + seconds
        return True

    def _waiting(self):
        return time.monotonic() < self._retry_at

    # A file's suggestions (#1679), many units per request #
    billed = True
    BULK_BATCH = 50
    """The most units one request asks for."""
    BULK_CHARS = 10000
    """The most characters one request sends, but for a longer unit alone."""

    def bulk_ready(self):
        return not self._stopped and self._has_languages() and bool(self._language_pair())

    def bulk_batches(self, sources):
        # A request's units are all marked up or all plain.
        protected = {source: self._protect(source) for source in sources}
        found = []
        for markup in (False, True):
            group = [s for s in sources if bool(protected[s].markup) == markup]
            found += bulkfetch.batches(
                group, self.BULK_BATCH, self.BULK_CHARS, lambda s: len(protected[s].text)
            )
        return found

    def bulk_characters(self, sources):
        return sum(len(self._protect(source).text) for source in sources)

    def bulk_send(self, batch):
        pair = self._language_pair()
        if not pair:
            return None
        request = self._batch_request([self._protect(source) for source in batch], *pair)
        return request if self.client.add(request) is not False else None

    def bulk_parse(self, batch, reply):
        texts = self._translations(json.loads(reply))
        if len(texts) != len(batch):
            raise ValueError('%d translations for %d units' % (len(texts), len(batch)))
        found = {}
        for source, text in zip(batch, texts, strict=True):
            found[source] = []
            if isinstance(text, str) and text.strip():
                protected = self._protect(source)
                self.cache[protected.text] = text
                found[source] = [self._match(source, protected, text)]
        return found

    def bulk_wait(self, request, status):
        if not self._wait_or_stop(request, status) or self._stopped:
            return None
        return max(0, self._retry_at - time.monotonic())

    def _stop(self, reason):
        """Send nothing until the settings change or Virtaal starts again."""
        self._stopped = True
        logging.debug("Stopping all queries for %s. %s", self.service_name, reason)
        self.set_languages_known()
