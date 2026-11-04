#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
from urllib.parse import urlencode

from virtaal.support.httpclient import RESTRequest
from virtaal.views.widgets.settingsdialog import Field, api_key_field

from ..keyedmt import KeyedMTModel

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


class TMModel(KeyedMTModel):
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

    service_name = 'Microsoft Translator'
    # Stop for a refused key or region (401) or used-up quota (403); wait
    # while busy.
    STOP_STATUSES = (401, 403)
    WAIT_STATUSES = (408, 429)

    # Global: any resource's key works here, with its region if it has one.
    endpoint = 'https://api.cognitive.microsofttranslator.com'

    _fetching = False
    _languages = None
    """Microsoft's codes by their lower case: the same for every key,
    so fetched once."""

    # INITIALIZERS #
    def _get_languages(self):
        if self._languages:
            self.set_languages_known()
        elif self._fetching:
            # Asked for already: wait for it.
            self.languages_known = False
        else:
            self._fetch_languages()

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

    # METHODS #
    def _has_languages(self):
        return bool(self._languages)

    def _set_languages(self, reply):
        self._languages = {code.lower(): code for code in reply['translation']}

    def _language_pair(self):
        source_lang = microsoft_code(self.source_lang, self._languages)
        target_lang = microsoft_code(self.target_lang, self._languages)
        if source_lang is None or target_lang is None or source_lang == target_lang:
            return None
        return source_lang, target_lang

    def _translate_request(self, query_str, source_lang, target_lang):
        params = {'api-version': '3.0', 'from': source_lang, 'to': target_lang, 'textType': 'plain'}
        return RESTRequest(
            self.endpoint + '/translate?' + urlencode(params),
            '',
            method='POST',
            data=json.dumps([{'Text': query_str}]).encode('utf-8'),
            headers=self._headers('Content-Type: application/json; charset=UTF-8'),
        )

    def _translation(self, reply):
        return reply[0]['translations'][0]['text']

    def _tmsource(self):
        # l10n: Try to keep this as short as possible. Feel free to transliterate.
        return _('Microsoft')
