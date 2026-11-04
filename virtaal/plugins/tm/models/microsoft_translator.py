#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import logging
import time
from urllib.parse import urlencode

from virtaal.support.httpclient import HTTPClient, RESTRequest
from virtaal.views.widgets.settingsdialog import Field, api_key_field

from .basetmmodel import BaseTMModel
from .remotetm import retry_after

# Microsoft codes to try, in order, before a Virtaal language code itself,
# then before its plain language (en_ZA, then en).
MICROSOFT_CODES = {
    'ckb': ('ku',),
    'fa-af': ('prs',),
    'kok': ('gom',),
    # Microsoft's ku is Central Kurdish; Virtaal's is Kurmanji.
    'ku': ('kmr',),
    'lg': ('lug',),
    'mn': ('mn-Cyrl',),
    'no': ('nb',),
    'ny': ('nya',),
    # Microsoft's pt is Brazil's; Virtaal's is Portugal's.
    'pt': ('pt-PT',),
    'pt-br': ('pt',),
    'rn': ('run',),
    'sr': ('sr-Cyrl',),
    'sr@latin': ('sr-Latn',),
    'tl': ('fil',),
    'tlh': ('tlh-Latn',),
    'zh': ('zh-Hans',),
    'zh-cn': ('zh-Hans',),
    'zh-sg': ('zh-Hans',),
    'zh-hk': ('zh-Hant',),
    'zh-mo': ('zh-Hant',),
    'zh-tw': ('zh-Hant',),
}


def microsoft_code(code, supported):
    """Microsoft's code for Virtaal language code, from supported
    (Microsoft's codes by their lower case), or None."""
    code = code.replace('_', '-').lower()
    language = code.split('-')[0].split('@')[0]
    if '@' in code:
        # A variant Microsoft doesn't list is not the plain language.
        variant = code.split('@', 1)[1]
        candidates = (
            *MICROSOFT_CODES.get(code, ()),
            *MICROSOFT_CODES.get('%s@%s' % (language, variant), ()),
        )
    else:
        candidates = (
            *MICROSOFT_CODES.get(code, ()),
            code,
            *MICROSOFT_CODES.get(language, ()),
            language,
        )
    for candidate in candidates:
        if candidate.lower() in supported:
            return supported[candidate.lower()]
    return None


class TMModel(BaseTMModel):
    """Machine translations from Azure Translator v3 (#3705)."""

    __gtype_name__ = 'MicrosoftTranslatorTMModel'
    # l10n: The name of Microsoft's translation service. See https://www.microsoft.com/translator
    display_name = _('Microsoft Translator')
    description = _('Unreviewed machine translations from Microsoft Translator')
    online_service = 'Microsoft'
    terms_url = (
        'https://learn.microsoft.com/legal/cognitive-services/translator/data-privacy-security'
    )
    # Billed per character: off unless turned on.
    PREFETCH_MAX = 2
    default_config = {'api_key': '', 'region': '', 'prefetch': 0}
    settings = (
        api_key_field(),
        # l10n: The Azure region of the user's Translator resource, such as westeurope. Empty for a global resource.
        Field('region', _('_Region:')),
    )

    # Global: any resource's key works here, with its region if it has one.
    endpoint = 'https://api.cognitive.microsofttranslator.com'

    RETRY_AFTER = 60
    """Seconds to send nothing after a refusal to answer now, without a
    Retry-After."""
    MAX_RETRY_AFTER = 900

    client = None
    _stopped = False
    _retry_at = 0.0
    _fetching = False
    _languages = None
    """Microsoft's codes by their lower case: the same for every key,
    so fetched once."""

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
            self._stop("An API key is needed to use Microsoft Translator")
            return
        if self.client is None:
            self.client = HTTPClient()
        if self._languages:
            self.set_languages_known()
        elif self._fetching:
            # Asked for already: wait for it.
            self.languages_known = False
        else:
            self._fetch_languages()

    def _key(self):
        return self.config['api_key'].strip()

    def _region(self):
        return str(self.config.get('region', '')).strip()

    def _credentials(self):
        return (self.config['api_key'], self.config.get('region', ''))

    def _headers(self, *more):
        # Never logged: HTTPClient logs URLs only.
        headers = ['Ocp-Apim-Subscription-Key: %s' % self._key()]
        if self._region():
            # Needed for a regional or multi-service resource.
            headers.append('Ocp-Apim-Subscription-Region: %s' % self._region())
        return [*headers, *more]

    def _fetch_languages(self):
        """The language list needs no key, so it is sent without one, and
        its reply is kept whatever key is set by then."""
        self.languages_known = False
        self._fetching = True
        request = RESTRequest(
            self.endpoint
            + '/languages?'
            + urlencode({'api-version': '3.0', 'scope': 'translation'}),
            '',
        )

        def done(handler):
            def on_reply(request, value):
                self._fetching = False
                if self._key():
                    handler(request, value)

            return on_reply

        self._send(request, done(self.got_languages), done(self._languages_failed), keyed=False)

    def _send(self, request, on_success, on_error, keyed=True):
        credentials = self._credentials()

        def for_credentials(handler):
            # Replies for a key or region since replaced are dropped.
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

    # METHODS #
    def apply_settings(self):
        self.cache = {}
        self._start()

    def query(self, tmcontroller, unit):
        if self._stopped or not self.hold_until_languages_known(tmcontroller, unit):
            return
        if self._waiting():
            return
        if not self._languages:
            # The language list never came: ask again.
            self._fetch_languages()
            self.hold_until_languages_known(tmcontroller, unit)
            return
        if not self.source_lang or not self.target_lang:
            return
        source_lang = microsoft_code(self.source_lang, self._languages)
        target_lang = microsoft_code(self.target_lang, self._languages)
        if source_lang is None or target_lang is None or source_lang == target_lang:
            logging.debug(
                'Microsoft Translator: language pair not supported: %s => %s',
                self.source_lang,
                self.target_lang,
            )
            return

        query_str = unit.source
        if query_str in self.cache:
            self.emit('match-found', query_str, self.cache[query_str])
            return
        sent = (query_str, self.source_lang, self.target_lang)
        if sent in self._pending:
            return
        params = {'api-version': '3.0', 'from': source_lang, 'to': target_lang, 'textType': 'plain'}
        request = RESTRequest(
            self.endpoint + '/translate?' + urlencode(params),
            '',
            method='POST',
            data=json.dumps([{'Text': query_str}]).encode('utf-8'),
            headers=self._headers('Content-Type: application/json; charset=UTF-8'),
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
            text = json.loads(val)[0]['translations'][0]['text']
        except (ValueError, KeyError, IndexError, TypeError) as e:
            logging.debug('Microsoft Translator: unexpected reply: %s', e)
            return
        if not isinstance(text, str) or not text.strip():
            return
        match = {
            'source': query_str,
            'target': text,
            # l10n: Try to keep this as short as possible. Feel free to transliterate.
            'tmsource': _('Microsoft'),
        }
        self.cache[query_str] = [match]
        self.emit('match-found', query_str, [match])

    def got_languages(self, request, val):
        try:
            languages = {code.lower(): code for code in json.loads(val)['translation']}
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            self._stop("Unexpected language list: %s" % e)
            return
        self._languages = languages
        self.set_languages_known()

    def got_error(self, request, status, query_str):
        if not self._wait_or_stop(request, status):
            # 400, 413, ...: this string only.
            logging.debug('Microsoft Translator: no translation (HTTP %s)', status)

    def _languages_failed(self, request, status):
        if not self._wait_or_stop(request, status):
            self._stop("Language list refused (HTTP %s)" % status)
        self.set_languages_known()

    def _wait_or_stop(self, request, status):
        """Stop for a refused key or region (401) or used-up quota (403);
        wait for a busy server or no reply. Whether it did either."""
        if status in (401, 403):
            self._stop("HTTP %s" % status)
            return True
        if isinstance(status, int) and status not in (408, 429) and status < 500:
            return False
        headers = getattr(request, 'result_headers', None)
        seconds = retry_after(
            headers.getvalue() if headers is not None else b'',
            self.RETRY_AFTER,
            self.MAX_RETRY_AFTER,
        )
        logging.debug(
            'Microsoft Translator: sending nothing for %d s (%s)',
            seconds,
            'HTTP %s' % status if isinstance(status, int) else 'no reply',
        )
        self._retry_at = time.monotonic() + seconds
        return True

    def _waiting(self):
        return time.monotonic() < self._retry_at

    def _stop(self, reason):
        """Send nothing until the key or region changes or Virtaal starts
        again."""
        self._stopped = True
        logging.debug("Stopping all queries for Microsoft Translator. %s", reason)
        self.set_languages_known()
