#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import logging
from urllib.parse import quote_plus

import pycurl

from virtaal.support.httpclient import HTTPClient, RESTRequest
from virtaal.views.widgets.settingsdialog import api_key_field

from .. import bulkfetch
from ..mtplaceables import Spans, suggestion
from .basetmmodel import BaseTMModel

# Some codes are weird or can be reused for others
code_translation = {
    'fl': 'tl',  # Filipino -> Tagalog
    'he': 'iw',  # Weird code Google uses for Hebrew
    'nb': 'no',  # Google maps no (Norwegian) to its Norwegian (Bokmål) (nb) translator
}

virtaal_referrer = "https://virtaal.translatehouse.org/"


class TMModel(BaseTMModel):
    """This is a Google Translate translation memory model.

    The plugin uses the U{Google AJAX Languages API<http://code.google.com/apis/ajaxlanguage/>}
    to query Google's machine translation services.  The implementation makes use of the
    U{RESTful<http://code.google.com/apis/ajaxlanguage/documentation/#fonje>} interface for
    Non-JavaScript environments.
    """

    __gtype_name__ = 'GoogleTranslateTMModel'
    # l10n: The name of Google Translate in your language (translated in most languages). See http://translate.google.com/
    display_name = _('Google Translate')
    description = _("Unreviewed machine translations from Google’s translation service")
    online_service = 'Google'
    billed = True
    machine_translation = True
    terms_url = 'https://cloud.google.com/terms'
    # Billed per character: off unless turned on.
    PREFETCH_MAX = 2
    BULK_MAX = None
    default_config = {'api_key': '', 'prefetch': 0}
    settings = (api_key_field(),)

    # The API reads the text as HTML.
    protection = Spans('<span translate="no">', '</span>', always=True)

    translate_url = "https://www.googleapis.com/language/translate/v2?key=%(key)s&q=%(message)s&source=%(from)s&target=%(to)s"
    batch_url = "https://www.googleapis.com/language/translate/v2?key=%(key)s"
    languages_url = "https://www.googleapis.com/language/translate/v2/languages?key=%(key)s"

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        self.internal_name = internal_name
        super().__init__(controller)
        self.load_config()
        self._start()

    def _start(self):
        """Fetch the languages the configured key may use, if any."""
        if not self.config['api_key']:
            self._disable_all("An API key is needed to use the Google Translate plugin")
            return
        if getattr(self, 'client', None) is None:
            self.client = HTTPClient()
        self._languages = set()
        self.languages_known = False
        key = self.config['api_key']
        langreq = RESTRequest(self.languages_url % {'key': key}, '')
        self.client.add(langreq)
        langreq.connect('http-success', self._for_key(key, self.got_languages))
        for signal in ('http-client-error', 'http-server-error'):
            langreq.connect(signal, self._for_key(key, lambda status: self.got_error(status, None)))

    def _for_key(self, key, handler):
        """A signal handler calling handler(value), unless the key changed since."""

        def on_reply(request, value):
            if key == self.config['api_key']:
                handler(value)

        return on_reply

    # METHODS #
    def apply_settings(self):
        self.cache = {}
        self._start()

    def query(self, tmcontroller, unit):
        if not self.hold_until_languages_known(tmcontroller, unit):
            return

        query_str = unit.source
        protected = self.protection.protect(query_str)
        # Google's Terms of Service says the whole URL must be less than "2K"
        # characters. A cut translation would not be of this string.
        if len(protected.text) > 2000 - len(self.translate_url):
            logging.debug('Too long for Google Translate: %d characters', len(protected.text))
            return
        pair = self._pair()
        if pair is None:
            logging.debug(
                'language pair not supported: %s => %s', self.source_lang, self.target_lang
            )
            return
        source_lang, target_lang = pair

        if query_str in self.cache:
            self.emit('match-found', query_str, self.cache[query_str])
        else:
            real_url = self.translate_url % {
                'key': self.config['api_key'],
                'message': quote_plus(protected.text.encode('utf-8')),
                'from': source_lang,
                'to': target_lang,
            }

            req = RESTRequest(real_url, '')
            self.client.add(req)
            # Google's Terms of Service says we need a proper HTTP referrer
            req.curl.setopt(pycurl.REFERER, virtaal_referrer)
            key = self.config['api_key']
            req.connect(
                'http-success',
                self._for_key(key, lambda response: self.got_translation(response, query_str)),
            )
            for signal in ('http-client-error', 'http-server-error'):
                req.connect(
                    signal, self._for_key(key, lambda response: self.got_error(response, query_str))
                )

    def _pair(self):
        """Google's codes for the current languages, or None if it doesn't
        support them."""
        source_lang = code_translation.get(self.source_lang, self.source_lang).replace('_', '-')
        target_lang = code_translation.get(self.target_lang, self.target_lang).replace('_', '-')
        if source_lang not in self._languages or target_lang not in self._languages:
            return None
        return source_lang, target_lang

    def requests_in_flight(self):
        client = getattr(self, 'client', None)
        return len(client.requests) if client is not None else 0

    def got_translation(self, val, query_str):
        """Handle the response from the web service now that it came in."""
        # In December 2011 version 1 of the API was deprecated, and we had to
        # release code to handle the eminent disappearance of the API. Although
        # version 2 is now supported, the code is a bit more careful (as most
        # code probably should be) and in case of error we make the list of
        # supported languages empty so that no unnecessary network activity is
        # performed if we can't communicate with the available API any more.
        try:
            data = json.loads(val)
            # We try to access the members to validate that the dictionary is
            # formed in the way we expect.
            data['data']
            data['data']['translations']
            text = data['data']['translations'][0]['translatedText']
        except Exception as e:
            self._disable_all("Error with json response: %s" % e)
            return

        match = self._match(query_str, text)
        self.cache[query_str] = [match]
        self.emit('match-found', query_str, [match])

    def _match(self, query_str, text):
        protected = self.protection.protect(query_str)
        restored = self.protection.restore(text, protected)
        if restored.returned != restored.text:
            logging.debug('Google Translate: placeables moved in %r', text)
        # l10n: Try to keep this as short as possible. Feel free to transliterate.
        return suggestion(query_str, restored, _('Google'))

    # A file's suggestions (#1679), many units per request #
    BULK_BATCH = 128
    """The most units one request asks for: Google's limit."""
    BULK_CHARS = 5000
    """The most characters one request sends, but for a longer unit alone:
    what Google recommends."""
    BULK_WAIT_STATUSES = (429, 500, 503)

    def bulk_ready(self):
        return bool(self.config['api_key']) and self._pair() is not None

    def bulk_batches(self, sources):
        return bulkfetch.batches(
            sources,
            self.BULK_BATCH,
            self.BULK_CHARS,
            lambda s: len(self.protection.protect(s).text),
        )

    def bulk_characters(self, sources):
        return sum(len(self.protection.protect(source).text) for source in sources)

    def bulk_send(self, batch):
        pair = self._pair()
        if pair is None:
            return None
        body = {
            'q': [self.protection.protect(source).text for source in batch],
            'source': pair[0],
            'target': pair[1],
            'format': 'html',
        }
        req = RESTRequest(
            self.batch_url % {'key': quote_plus(self.config['api_key'])},
            '',
            method='POST',
            data=json.dumps(body).encode('utf-8'),
            headers=['Content-Type: application/json; charset=utf-8'],
        )
        req.curl.setopt(pycurl.REFERER, virtaal_referrer)
        return req if self.client.add(req) is not False else None

    def bulk_parse(self, batch, reply):
        translations = json.loads(reply)['data']['translations']
        if len(translations) != len(batch):
            raise ValueError('%d translations for %d units' % (len(translations), len(batch)))
        found = {}
        for source, translation in zip(batch, translations, strict=True):
            text = translation['translatedText']
            found[source] = [self._match(source, text)] if isinstance(text, str) and text else []
            if found[source]:
                self.cache[source] = found[source]
        return found

    def bulk_wait(self, request, status):
        if status in (400, 401, 403):
            # A refused key or used-up quota.
            self._disable_all("Got an error response: %s" % status)
        return super().bulk_wait(request, status)

    def got_languages(self, val):
        """Handle the response from the web service to set up language pairs."""
        try:
            data = json.loads(val)
            data['data']
            languages = data['data']['languages']
        except Exception as e:
            self._disable_all("Error with json response: %s" % e)
            return
        self._languages = set([lang['language'] for lang in languages])
        self.set_languages_known()

    def got_error(self, val, query_str):
        self._disable_all("Got an error response: %s" % val)

    def _disable_all(self, reason):
        self._languages = set()
        logging.debug("Stopping all queries for Google Translate. %s" % reason)
        self.set_languages_known()
