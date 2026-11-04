#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import logging
import time

from virtaal.support.httpclient import HTTPClient, RESTRequest
from virtaal.views.widgets.settingsdialog import api_key_field

from .basetmmodel import BaseTMModel
from .remotetm import retry_after

# DeepL codes to try, in order, before a Virtaal language code itself and
# then its plain language (en_ZA, then en). DeepL translates into
# English, Portuguese and Chinese only by variant.
_LATIN_AMERICA = 'ar bo cl co cr cu do ec gt hn mx ni pa pe pr py sv us uy ve'
DEEPL_CODES = {
    'en': ('en-US',),
    **{'en-' + region: ('en-GB',) for region in ('au', 'ie', 'in', 'nz', 'za')},
    'no': ('nb',),
    'pt': ('pt-PT',),
    'zh': ('zh-Hans',),
    'zh-cn': ('zh-Hans',),
    'zh-sg': ('zh-Hans',),
    'zh-hk': ('zh-Hant',),
    'zh-mo': ('zh-Hant',),
    'zh-tw': ('zh-Hant',),
    **{'es-' + region: ('es-419',) for region in _LATIN_AMERICA.split()},
}


def deepl_code(code, supported):
    """DeepL's code for Virtaal language code, from supported (DeepL's
    codes by their lower case), or None."""
    code = code.replace('_', '-').lower()
    if '@' in code:
        # A variant DeepL doesn't list is not the plain language.
        candidates = DEEPL_CODES.get(code, ())
    else:
        language = code.split('-')[0]
        candidates = (
            *DEEPL_CODES.get(code, ()),
            code,
            language,
            *DEEPL_CODES.get(language, ()),
        )
    for candidate in candidates:
        if candidate.lower() in supported:
            return supported[candidate.lower()]
    return None


class TMModel(BaseTMModel):
    """Machine translations from the DeepL API (#4180)."""

    __gtype_name__ = 'DeepLTMModel'
    display_name = _('DeepL')
    description = _('Unreviewed machine translations from DeepL')
    online_service = 'DeepL'
    terms_url = 'https://www.deepl.com/pro-license'
    # Billed per character: off unless turned on.
    PREFETCH_MAX = 2
    default_config = {'api_key': '', 'prefetch': 0}
    settings = (api_key_field(),)

    # Free keys end in ":fx" and only work on the Free host.
    free_host = 'https://api-free.deepl.com'
    pro_host = 'https://api.deepl.com'

    RETRY_AFTER = 60
    """Seconds to send nothing after a refusal to answer now, without a
    Retry-After."""
    MAX_RETRY_AFTER = 900

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
        """Fetch the languages the configured key may use, if any."""
        self._sources = {}
        self._targets = {}
        self._pending = set()
        self._retry_at = 0.0
        self._stopped = False
        if not self._key():
            self._stop("An API key is needed to use DeepL")
            return
        if self.client is None:
            self.client = HTTPClient()
        self._fetch_languages()

    def _key(self):
        return self.config['api_key'].strip()

    def _url(self, path):
        host = self.free_host if self._key().endswith(':fx') else self.pro_host
        return host + path

    def _headers(self, *more):
        # Never logged: HTTPClient logs URLs only.
        return ['Authorization: DeepL-Auth-Key %s' % self._key(), *more]

    def _fetch_languages(self):
        self.languages_known = False
        request = RESTRequest(
            self._url('/v3/languages?resource=translate_text'), '', headers=self._headers()
        )
        self._send(request, self.got_languages, self._languages_failed)

    def _send(self, request, on_success, on_error):
        key = self.config['api_key']

        def for_key(handler):
            # Replies for a key since replaced are dropped.
            def on_reply(request, value):
                if key == self.config['api_key']:
                    handler(request, value)

            return on_reply

        if self.client.add(request) is False:
            # The client's queue was full.
            on_error(request, None)
            return
        request.connect('http-success', for_key(on_success))
        for signal in ('http-client-error', 'http-server-error', 'http-failed'):
            request.connect(signal, for_key(on_error))

    # METHODS #
    def apply_settings(self):
        self.cache = {}
        self._start()

    def query(self, tmcontroller, unit):
        if self._stopped or not self.hold_until_languages_known(tmcontroller, unit):
            return
        if self._waiting():
            return
        if not self._targets:
            # The language list never came: ask again.
            self._fetch_languages()
            self.hold_until_languages_known(tmcontroller, unit)
            return
        if not self.source_lang or not self.target_lang or self.source_lang == self.target_lang:
            return
        source_lang = deepl_code(self.source_lang, self._sources)
        target_lang = deepl_code(self.target_lang, self._targets)
        if source_lang is None or target_lang is None:
            logging.debug(
                'DeepL: language pair not supported: %s => %s', self.source_lang, self.target_lang
            )
            return

        query_str = unit.source
        if query_str in self.cache:
            self.emit('match-found', query_str, self.cache[query_str])
            return
        sent = (query_str, self.source_lang, self.target_lang)
        if sent in self._pending:
            return
        body = {
            'text': [query_str],
            'source_lang': source_lang,
            'target_lang': target_lang,
            # Keep the source's capitals and punctuation at the ends.
            'preserve_formatting': True,
        }
        request = RESTRequest(
            self._url('/v2/translate'),
            '',
            method='POST',
            data=json.dumps(body).encode('utf-8'),
            headers=self._headers('Content-Type: application/json'),
        )
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

    def has_cached(self, query_str):
        # Also true where nothing would be sent.
        return self._stopped or self._waiting() or query_str in self.cache

    def requests_in_flight(self):
        return len(self.client.requests) if self.client is not None else 0

    def got_translation(self, request, val, query_str):
        try:
            text = json.loads(val)['translations'][0]['text']
        except (ValueError, KeyError, IndexError, TypeError) as e:
            logging.debug('DeepL: unexpected reply: %s', e)
            return
        if not isinstance(text, str) or not text.strip():
            return
        match = {
            'source': query_str,
            'target': text,
            # l10n: Try to keep this as short as possible. Feel free to transliterate.
            'tmsource': _('DeepL'),
        }
        self.cache[query_str] = [match]
        self.emit('match-found', query_str, [match])

    def got_languages(self, request, val):
        try:
            languages = json.loads(val)
            sources = {
                lang['lang'].lower(): lang['lang'] for lang in languages if lang['usable_as_source']
            }
            targets = {
                lang['lang'].lower(): lang['lang'] for lang in languages if lang['usable_as_target']
            }
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            self._stop("Unexpected language list: %s" % e)
            return
        self._sources = sources
        self._targets = targets
        self.set_languages_known()

    def got_error(self, request, status, query_str):
        if not self._wait_or_stop(request, status):
            # 400, 413, ...: this string only.
            logging.debug('DeepL: no translation (HTTP %s)', status)

    def _languages_failed(self, request, status):
        if not self._wait_or_stop(request, status):
            self._stop("Language list refused (HTTP %s)" % status)
        self.set_languages_known()

    def _wait_or_stop(self, request, status):
        """Stop for a refused key (403) or used-up quota (456); wait for a
        busy server or no reply. Whether it did either."""
        if status in (403, 456):
            self._stop("HTTP %s" % status)
            return True
        if isinstance(status, int) and status not in (429, 529) and status < 500:
            return False
        headers = getattr(request, 'result_headers', None)
        seconds = retry_after(
            headers.getvalue() if headers is not None else b'',
            self.RETRY_AFTER,
            self.MAX_RETRY_AFTER,
        )
        logging.debug(
            'DeepL: sending nothing for %d s (%s)',
            seconds,
            'HTTP %s' % status if isinstance(status, int) else 'no reply',
        )
        self._retry_at = time.monotonic() + seconds
        return True

    def _waiting(self):
        return time.monotonic() < self._retry_at

    def _stop(self, reason):
        """Send nothing until the key changes or Virtaal starts again."""
        self._stopped = True
        self._sources = {}
        self._targets = {}
        logging.debug("Stopping all queries for DeepL. %s", reason)
        self.set_languages_known()
