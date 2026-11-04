#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A TM provider that can query the web service for the Apertium software for
Machine Translation.

https://wiki.apertium.org/wiki/Apertium_web_service

Note: the old api.apertium.org/json service (with its appId query parameter)
has been retired; this now targets the APY instance Apertium itself hosts,
which needs no appId.

The language pairs at start come from the cached asset manifest, else
the saved copy of the last live list, else a live request (#4678). With
a list from either copy, queries run at once and the live list is
fetched with the first query, replacing it.
"""

import json
import logging
import os
from urllib.parse import urlencode

import pycountry

from virtaal.common import pan_app
from virtaal.support.httpclient import HTTPClient, RESTRequest

from .basetmmodel import BaseTMModel, unescape_html_entities

# APY codes that pycountry's ISO 639-3 code doesn't give.
APY_LANGUAGES = {
    'bs': 'hbs_BS',
    'hr': 'hbs_HR',
    'ms': 'zlm',
    'sr': 'hbs_SR',
}


# The last pair list Apertium returned, in the config dir.
PAIRS_FILENAME = 'apertium-pairs.json'


def _config_dir():
    return pan_app.get_config_dir()


def read_pairs(section):
    """[(source, target), ...] from a {'pairs': [[source, target], ...]}
    section, as the asset manifest and the saved copy hold them, or
    None if it is of any other shape or has no pairs."""
    pairs = section.get('pairs') if isinstance(section, dict) else None
    if not isinstance(pairs, list) or not pairs:
        return None
    if not all(
        isinstance(pair, list) and len(pair) == 2 and all(isinstance(c, str) and c for c in pair)
        for pair in pairs
    ):
        return None
    return [tuple(pair) for pair in pairs]


def _manifest_pairs(config_dir):
    from virtaal.support.asset_manifest import AssetManifest

    return read_pairs(AssetManifest(config_dir=config_dir).section('apertium'))


def _saved_pairs(config_dir):
    try:
        with open(os.path.join(config_dir, PAIRS_FILENAME), encoding='utf-8') as f:
            return read_pairs(json.load(f))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logging.debug("Apertium: no usable saved language list (%s)", exc)
        return None


def _save_pairs(config_dir, pairs):
    path = os.path.join(config_dir, PAIRS_FILENAME)
    try:
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump({'pairs': sorted(map(list, set(pairs)))}, f)
        os.replace(path + '.tmp', path)
    except OSError as exc:
        logging.debug("Apertium: could not save the language list (%s)", exc)


def apy_codes(code):
    """APY's possible codes for a Virtaal language code, most specific
    first: ca@valencia gives cat_valencia then cat, pt_BR gives
    por_BR then por. APY lists ISO 639-3 codes (#4664)."""
    base, _, variant = code.replace('@', '_').partition('_')
    language = APY_LANGUAGES.get(base)
    if language is None:
        found = pycountry.languages.get(alpha_2=base) if len(base) == 2 else None
        language = found.alpha_3 if found else base
    codes = [code]
    if variant:
        codes.append(language + '_' + variant)
    codes.append(language)
    codes.append(language.split('_')[0])
    return list(dict.fromkeys(codes))


class TMModel(BaseTMModel):
    """This is the translation memory model."""

    __gtype_name__ = 'ApertiumTMModel'
    display_name = _('Apertium')
    description = _('Unreviewed machine translations from Apertium')
    online_service = 'Apertium'

    url = "https://www.apertium.org/apy"

    # A volunteer-run server: at most one ahead, and only if turned on.
    PREFETCH_MAX = 1
    default_config = {'prefetch': 0}

    # A start list from a copy waits for the first query to fetch the
    # live one; then the live one is in flight until it answers.
    _refresh_due = False
    _refreshing = False

    # INITIALISERS #
    def __init__(self, internal_name, controller):
        self.internal_name = internal_name
        self.language_pairs = []
        self.languages_known = False
        self.load_config()

        self.client = HTTPClient()
        self.url_getpairs = "%(url)s/listPairs" % {"url": self.url}
        self.url_translate = "%(url)s/translate" % {"url": self.url}
        config_dir = _config_dir()
        pairs = _manifest_pairs(config_dir) or _saved_pairs(config_dir)
        if pairs:
            self.language_pairs = pairs
            self.languages_known = True
            self._refresh_due = True
        else:
            self._request_language_pairs()

        super().__init__(controller)

    def _request_language_pairs(self):
        self._refresh_due = False
        self._refreshing = True
        langreq = RESTRequest(self.url_getpairs, '')
        self.client.add(langreq)
        langreq.connect('http-success', lambda langreq, response: self.got_language_pairs(response))
        for signal in ('http-client-error', 'http-server-error'):
            langreq.connect(signal, lambda langreq, status: self._language_pairs_failed())

    def _language_pairs_failed(self):
        self._refreshing = False
        self.set_languages_known()

    # METHODS #
    def query(self, tmcontroller, unit):
        """Send the query to the web service. The response is handled by means
        of a call-back because it happens asynchronously."""
        if not self.hold_until_languages_known(tmcontroller, unit):
            return

        pair = self._apy_pair()
        if pair is None:
            if self._refresh_due or self._refreshing:
                # Apertium may have added the pair since the copy the
                # start list came from: wait for the live list.
                self.languages_known = False
                self.hold_until_languages_known(tmcontroller, unit)
                if self._refresh_due:
                    self._request_language_pairs()
            return

        query_str = unit.source
        if query_str in self.cache:
            self.emit('match-found', query_str, self.cache[query_str])
        else:
            values = {
                'q': query_str,
                'langpair': "%s|%s" % pair,
                'markUnknown': "no",
                'format': 'html',
            }
            req = RESTRequest(self.url_translate + "?" + urlencode(values), '')
            self.client.add(req)
            req.connect(
                'http-success', lambda req, response: self.got_translation(response, query_str)
            )
        if self._refresh_due:
            self._request_language_pairs()

    def requests_in_flight(self):
        return len(self.client.requests)

    def _apy_pair(self):
        """The most specific listed APY pair for the current languages, or
        None."""
        for source in apy_codes(self.source_lang or ''):
            for target in apy_codes(self.target_lang or ''):
                if (source, target) in self.language_pairs:
                    return (source, target)
        return None

    def got_language_pairs(self, val):
        """Handle the response from the web service to set up language pairs."""
        try:
            data = json.loads(val)
            if data['responseStatus'] != 200:
                logging.debug("Failed to get languages:\n%s", (data['responseDetails']))
                return
            pairs = [
                (pair['sourceLanguage'], pair['targetLanguage']) for pair in data['responseData']
            ]
            if not all(isinstance(c, str) for pair in pairs for c in pair):
                raise TypeError('a language code is not a string')
        except (ValueError, KeyError, TypeError) as exc:
            # Not JSON, or of another shape (#4438).
            logging.warning("Ignoring an Apertium language list it can't read: %s", exc)
        else:
            # An empty list is more likely a server fault than Apertium
            # dropping every pair: keep the one from the start.
            if pairs:
                self.language_pairs = pairs
                _save_pairs(_config_dir(), pairs)
        finally:
            self._refreshing = False
            self.set_languages_known()

    def got_translation(self, val, query_str):
        """Handle the response from the web service now that it came in."""
        try:
            data = json.loads(val)
            if data['responseStatus'] != 200:
                logging.debug("Failed to translate '%s':\n%s", query_str, data['responseDetails'])
                return
            target = data['responseData']['translatedText']
            if not isinstance(target, str):
                raise TypeError('translatedText is %s' % type(target).__name__)
        except (ValueError, KeyError, TypeError) as exc:
            # Not JSON, or of another shape (#4438).
            logging.warning("Ignoring an Apertium translation it can't read: %s", exc)
            return
        target = unescape_html_entities(target)
        if target.endswith("\n") and not query_str.endswith("\n"):
            target = target[:-1]  # chop of \n
        match = {
            'source': query_str,
            'target': target,
            # l10n: Try to keep this as short as possible. Feel free to transliterate in CJK languages for optimal vertical display.
            'tmsource': _('Apertium'),
        }
        self.cache[query_str] = [match]

        self.emit('match-found', query_str, [match])
