#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json

from virtaal.support.httpclient import RESTRequest
from virtaal.views.widgets.settingsdialog import api_key_field

from ..keyedmt import KeyedMTModel
from ..mtplaceables import XmlIds

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


class TMModel(KeyedMTModel):
    """Machine translations from the DeepL API (#4180)."""

    __gtype_name__ = 'DeepLTMModel'
    display_name = _('DeepL')
    description = _('Unreviewed machine translations from DeepL')
    online_service = 'DeepL'
    terms_url = 'https://www.deepl.com/pro-license'
    # Billed per character: off unless turned on.
    PREFETCH_MAX = 2
    BULK_MAX = None
    default_config = {'api_key': '', 'prefetch': 0}
    settings = (api_key_field(),)

    service_name = 'DeepL'
    # Stop for a refused key (403) or used-up quota (456); wait while busy.
    STOP_STATUSES = (403, 456)
    WAIT_STATUSES = (429, 529)
    # Kept in place, and not billed.
    protection = XmlIds()

    # Free keys end in ":fx" and only work on the Free host.
    free_host = 'https://api-free.deepl.com'
    pro_host = 'https://api.deepl.com'

    # INITIALIZERS #
    def _start(self):
        """Fetch the languages the configured key may use, if any."""
        self._sources = {}
        self._targets = {}
        super()._start()

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

    # METHODS #
    def _has_languages(self):
        return bool(self._targets)

    def _set_languages(self, reply):
        sources = {lang['lang'].lower(): lang['lang'] for lang in reply if lang['usable_as_source']}
        targets = {lang['lang'].lower(): lang['lang'] for lang in reply if lang['usable_as_target']}
        self._sources = sources
        self._targets = targets

    def _language_pair(self):
        if self.source_lang == self.target_lang:
            return ()
        source_lang = deepl_code(self.source_lang, self._sources)
        target_lang = deepl_code(self.target_lang, self._targets)
        if source_lang is None or target_lang is None:
            return None
        return source_lang, target_lang

    def _translate_request(self, protected, source_lang, target_lang):
        body = {
            'text': [protected.text],
            'source_lang': source_lang,
            'target_lang': target_lang,
            # Keep the source's capitals and punctuation at the ends.
            'preserve_formatting': True,
        }
        if protected.markup:
            body['tag_handling'] = 'xml'
        return RESTRequest(
            self._url('/v2/translate'),
            '',
            method='POST',
            data=json.dumps(body).encode('utf-8'),
            headers=self._headers('Content-Type: application/json'),
        )

    def _translation(self, reply):
        return reply['translations'][0]['text']

    def _tmsource(self):
        # l10n: Try to keep this as short as possible. Feel free to transliterate.
        return _('DeepL')

    def _stop(self, reason):
        self._sources = {}
        self._targets = {}
        super()._stop(reason)
