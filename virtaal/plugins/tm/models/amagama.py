#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import logging
import os
import re

from virtaal.common import pan_app
from virtaal.support import abstraction

from . import remotetm
from .basetmmodel import BaseTMModel

SOURCES_CACHE = 'amagama-sources.json'
"""The server's last list of sources, in the state directory."""

MAX_WEIGHT = 10
"""The highest weight amaGama takes."""

LONG_EXCLUDE = 2000
"""An exclude= longer than this is sent as only= if that is shorter: a
long URL can be refused."""


def v2_url(url):
    """The API v2 base of an amaGama API v1 URL (.../api/v1/), or None."""
    found = re.fullmatch(r'(.*/api/)v1/?', url)
    return found.group(1) + 'v2' if found else None


def _lang(code):
    """A language code as amaGama writes it: pt_br."""
    return (code or '').replace('-', '_').lower()


def _weight(value, default=1.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return min(max(float(value), 0.0), MAX_WEIGHT)


def parse_sources(reply):
    """The sources in an amaGama /sources/ reply, or None if it has none.
    Each has its name, title, licence, the server's weight and its
    languages."""
    if not isinstance(reply, dict) or not isinstance(reply.get('sources'), list):
        return None
    sources = []
    for source in reply['sources']:
        if not isinstance(source, dict):
            continue
        name = source.get('name')
        if not isinstance(name, str) or not name or ',' in name:
            continue
        title = source.get('displayTitle') or source.get('title')
        licence = source.get('licence')
        targets = source.get('targetLanguages')
        sources.append(
            {
                'name': name,
                'title': title if isinstance(title, str) else name,
                'licence': licence if isinstance(licence, str) else '',
                'weight': _weight(source.get('weight')),
                'sourceLanguage': _lang(source.get('sourceLanguage')),
                'targetLanguages': [_lang(t) for t in targets if isinstance(t, str)]
                if isinstance(targets, list)
                else [],
            }
        )
    return sources


def sources_for(sources, source_lang, target_lang):
    """The sources with translations from source_lang into target_lang, or
    into its base language (pt for pt_BR); else all of source_lang's."""
    source_lang, target_lang = _lang(source_lang), _lang(target_lang)
    ours = [s for s in sources if s['sourceLanguage'] in (source_lang, '')]
    for target in (target_lang, target_lang.partition('_')[0]):
        paired = [s for s in ours if target in s['targetLanguages']]
        if paired:
            return paired
    return ours


def _glob(name):
    """A glob matching only name."""
    return re.sub(r'([*?[])', r'[\1]', name)


def _globs(chosen, names):
    """Globs matching the chosen names: one per family (gnome-*) whose
    known names are all chosen, else each name."""
    families = {}
    for name in names:
        prefix, dash, _rest = name.partition('-')
        if dash:
            families.setdefault(prefix, []).append(name)
    globs, covered = [], set()
    for prefix, members in sorted(families.items()):
        if len(members) > 1 and chosen.issuperset(members):
            globs.append(_glob(prefix) + '-*')
            covered.update(members)
    return globs + [_glob(name) for name in sorted(chosen - covered)]


def source_params(excluded, weights, sources=()):
    """amaGama's exclude= (or only=) and weight= for the sources left out
    and the weights chosen; a weight that is the server's own isn't sent."""
    names = {s['name'] for s in sources}
    defaults = {s['name']: s['weight'] for s in sources}
    excluded = {name for name in excluded if ',' not in name}
    params = {}
    if names and excluded.issuperset(names):
        params['exclude'] = '*'
    elif excluded:
        params['exclude'] = ','.join(_globs(excluded, names))
        if names and len(params['exclude']) > LONG_EXCLUDE:
            only = ','.join(_globs(names - excluded, names))
            if len(only) < len(params['exclude']):
                params = {'only': only}
    weighted = [
        '%s:%g' % (_glob(name), weight)
        for name, weight in sorted(weights.items())
        if name not in excluded and ',' not in name and weight != defaults.get(name, 1.0)
    ]
    if weighted:
        params['weight'] = ','.join(weighted)
    return params


def source_weight(names, weights, sources=()):
    """The weight amaGama orders a suggestion from the sources names by:
    the best of theirs, each the chosen weight or else the server's;
    1 for none."""
    defaults = {s['name']: s['weight'] for s in sources}
    found = [weights.get(n, defaults.get(n, 1.0)) for n in names if isinstance(n, str)]
    return max(found, default=1.0)


def parse_licence(text):
    """amaGama's licence= for the SPDX identifier globs in text, separated
    by commas or spaces; '' for any licence."""
    return ','.join(re.split(r'[\s,]+', str(text).strip())).strip(',')


# Reserved for the built-in licence choices, as for built-in web look-ups
# (lookup/models/weblookup.py): tm.ini keeps a choice's name, and its
# globs always come from the code.
BUILTIN_PREFIX = 'virtaal-'

# SPDX identifier globs for the licence choices, as amaGama's licence=
# matches them: a glob matches a whole identifier, whatever its case.
PERMISSIVE = (
    '0BSD',
    'Apache-*',
    'BSD-*',
    'BSL-1.0',
    'CC-BY-[0-9]*',
    'CC0-1.0',
    'ISC',
    'MIT',
    'MIT-*',
    'PSF-2.0',
    'Unlicense',
    'X11',
    'Zlib',
)
COPYLEFT = (
    'AGPL-*',
    'CC-BY-SA-[0-9]*',
    'CDDL-*',
    'EPL-*',
    'EUPL-*',
    # The GFDL's free variants, not those with invariant sections.
    'GFDL-[0-9].[0-9]-or-later',
    'GFDL-*-no-invariants-*',
    'GPL-*',
    'LGPL-*',
    'MPL-*',
    'OSL-*',
)
LICENCE_PRESETS = {
    BUILTIN_PREFIX + 'free': PERMISSIVE + COPYLEFT,
    BUILTIN_PREFIX + 'permissive': PERMISSIVE,
}


def stored_licence(text):
    """tm.ini's licence for text: a built-in choice's name as it is, else
    the globs it lists, or the name of the choice with those globs."""
    text = str(text).strip()
    if text.startswith(BUILTIN_PREFIX):
        return text
    licence = parse_licence(text)
    globs = {glob.casefold() for glob in licence.split(',') if glob}
    for name, preset in LICENCE_PRESETS.items():
        if globs == {glob.casefold() for glob in preset}:
            return name
    return licence


def licence_globs(licence):
    """amaGama's licence= for tm.ini's licence: a built-in choice's globs
    as this version has them, '' for one it doesn't know."""
    if not licence.startswith(BUILTIN_PREFIX):
        return licence
    return ','.join(LICENCE_PRESETS.get(licence, ()))


def parse_choices(config):
    """The (excluded names, {name: weight}) in config's exclude and weights."""
    excluded = {name.strip() for name in str(config.get('exclude', '')).split(',')}
    weights = {}
    for item in str(config.get('weights', '')).split(','):
        name, colon, value = item.strip().rpartition(':')
        try:
            weight = float(value)
        except ValueError:
            continue
        if colon and name and 0 <= weight <= MAX_WEIGHT:
            weights[name] = weight
    return excluded - {''}, weights


class TMModel(remotetm.TMModel):
    """This is the translation memory model."""

    __gtype_name__ = 'AmagamaTMModel'
    display_name = _('Amagama')
    description = _('Previous translations for Free and Open Source Software')
    # l10n: Try to keep this as short as possible.
    shortname = _('Amagama')
    online_service = 'amaGama'

    PREFETCH_MAX = 2
    BULK_MAX = None
    default_config = {
        "url": "https://amagama-live.translatehouse.org/api/v1/",
        "prefetch": 2,
        "exclude": "",
        "weights": "",
        "licence": "",
    }

    # The server's sources, if known, and the ones left out and weighted.
    sources = None
    excluded = frozenset()
    weights = {}
    licence = ''

    # (API v2 URL, source language) the server refused this session: it has
    # no v2 (404), or its abstracts are another version's (409).
    _v1_only = set()
    v2_base = None

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        # Do not use super() here, as remotetm.TMModel does a bit more than we
        # want in this case.
        BaseTMModel.__init__(self, controller)
        self.internal_name = internal_name
        self.load_config()
        url = self.config["url"]

        from virtaal.support import tmclient

        self.tmclient = tmclient.TMClient(url)
        self.tmclient.set_virtaal_useragent()
        self.v2_base = v2_url(url)
        excluded, self.weights = parse_choices(self.config)
        self.excluded = frozenset(excluded)
        self.licence = stored_licence(self.config.get('licence', ''))
        # Globs a preset now has its name for, as saved before.
        self.config['licence'] = self.licence
        if self.licence.startswith(BUILTIN_PREFIX) and not licence_globs(self.licence):
            logging.debug('amaGama: unknown licence choice %s, so any licence', self.licence)
        self.sources = self._cached_sources()
        self.configure_func = self.configure

    def configure(self, parent):
        from ..amagamadialog import AmagamaSourcesDialog

        AmagamaSourcesDialog(self, parent).show()

    # The server's sources (#4565) #
    def _cache_path(self):
        return os.path.join(pan_app.get_state_dir(), SOURCES_CACHE)

    def _cached_sources(self):
        """The sources last fetched from this server, or None."""
        try:
            with open(self._cache_path(), encoding='utf-8') as cache:
                cached = json.load(cache)
        except (OSError, ValueError):
            return None
        if not isinstance(cached, dict) or cached.get('url') != self.config['url']:
            return None
        return parse_sources(cached)

    def _cache_sources(self, sources):
        try:
            with open(self._cache_path(), 'w', encoding='utf-8') as cache:
                json.dump({'url': self.config['url'], 'sources': sources}, cache)
        except OSError as error:
            logging.debug('%s: could not keep the list of sources: %s', self, error)

    def fetch_sources(self, on_done, on_error):
        """Ask the server for its sources, titled in the UI language; then
        on_done(sources), or on_error() if it gave none."""
        request = self.tmclient.get_sources(pan_app.ui_language or 'en')
        if request is None:
            on_error()
            return

        def done(_request, reply):
            try:
                sources = parse_sources(json.loads(reply))
            except ValueError:
                sources = None
            if sources is None:
                on_error()
                return
            self.sources = sources
            self._cache_sources(sources)
            on_done(sources)

        request.connect('http-success', done)
        for signal in ('http-redirect', 'http-client-error', 'http-server-error', 'http-failed'):
            request.connect(signal, lambda *_args: on_error())

    def set_choices(self, excluded, weights):
        """Leave out the excluded sources and weight the others, from the
        next lookup on."""
        excluded = frozenset(excluded)
        if excluded == self.excluded and weights == self.weights:
            return
        self.excluded, self.weights = excluded, dict(weights)
        self.config['exclude'] = ','.join(sorted(excluded))
        self.config['weights'] = ','.join(
            '%s:%g' % (name, weight) for name, weight in sorted(weights.items())
        )
        self._drop_answers()

    def set_licence(self, text):
        """Keep only sources under the licences text lists, or the
        built-in choice it names, from the next lookup on; with none
        listed, any licence."""
        licence = stored_licence(text)
        if licence == self.licence:
            return
        self.licence = self.config['licence'] = licence
        self._drop_answers()

    def _drop_answers(self):
        # Answers chosen by the earlier choices are dropped.
        self.cache = {}
        self._pending = {}
        self._generation += 1

    def match_weight(self, match):
        """match's weight, as amaGama orders it by (#4565)."""
        names = match.get('sources')
        if not isinstance(names, list):
            return 1.0
        return source_weight(names, self.weights, self.sources or ())

    def _params(self):
        params = super()._params()
        params.update(source_params(self.excluded, self.weights, self.sources or ()))
        licence = licence_globs(self.licence)
        if licence:
            params['licence'] = licence
        return params

    def _uses_v2(self):
        return (
            self.v2_base is not None
            and getattr(self.controller, 'abstract_placeables', True)
            and (self.v2_base, self.source_lang) not in self._v1_only
        )

    def _send(self, query_str):
        """Send the abstract of query_str to API v2, or query_str to v1 when
        the server has no v2 for this source language."""
        if not self._uses_v2():
            return super()._send(query_str)
        request = self.tmclient.translate_abstract(
            self.v2_base,
            query_str,
            abstraction.abstract(query_str)[0],
            abstraction.ABSTRACTION_VERSION,
            self.source_lang,
            self.target_lang,
            self._handle_v2_matches,
            self._params(),
        )
        if request is not None:
            request.connect('http-client-error', self._on_v2_refused, query_str)
        return request

    def _on_v2_refused(self, request, status, query_str):
        if status not in (404, 409):
            return
        if getattr(request, 'tm_key', None) != self._key():
            return
        self._use_v1(status)
        # Each request sent before the refusal is asked again.
        if self._pending.get(query_str) is request:
            self._pending = {k: v for k, v in self._pending.items() if k != query_str}
        self._request(query_str)

    def _use_v1(self, status):
        """Use API v1 for this source language from now on."""
        key = (self.v2_base, self.source_lang)
        if key not in self._v1_only:
            logging.info('%s: no API v2 for %s (HTTP %d), using v1', self, self.source_lang, status)
            TMModel._v1_only = self._v1_only | {key}

    def _handle_v2_matches(self, widget, query_str, reply):
        """Score the v2 matches, which the server scored on their abstracts,
        on query_str and pass them on as v1 matches."""
        self._handle_matches(widget, query_str, self._scored(query_str, reply))

    def _scored(self, query_str, reply):
        """The v2 reply's matches as v1 matches, scored on query_str."""
        matches = []
        if isinstance(reply, dict) and isinstance(reply.get('matches'), list):
            matches = reply['matches']
        min_quality = getattr(self.controller, 'min_quality', 0)
        scored = []
        for match in matches:
            if not (
                isinstance(match, dict)
                and isinstance(match.get('source'), str)
                and isinstance(match.get('target'), str)
            ):
                continue
            raw_quality = abstraction.similarity(query_str, match['source'])
            adapted = abstraction.adapt(query_str, match['source'], match['target'], raw_quality)
            if adapted.quality < min_quality:
                continue
            scored.append(dict(match, quality=adapted.quality, raw_quality=adapted.raw_quality))
        scored.sort(key=lambda match: match['quality'], reverse=True)
        return scored

    # A file's suggestions (#1679), a unit per request #
    def bulk_send(self, batch):
        if not self._uses_v2():
            return super().bulk_send(batch)
        (query_str,) = batch
        request = self.tmclient.translate_abstract(
            self.v2_base,
            query_str,
            abstraction.abstract(query_str)[0],
            abstraction.ABSTRACTION_VERSION,
            self.source_lang,
            self.target_lang,
            None,
            self._params(),
        )
        if request is not None:
            request.amagama_v2 = True
        return request

    def bulk_parse(self, batch, reply):
        (query_str,) = batch
        reply = json.loads(reply)
        if isinstance(reply, dict):
            # API v2's
            return {query_str: self._usable(self._scored(query_str, reply))}
        return {query_str: self._usable(reply)}

    def bulk_wait(self, request, status):
        if status in (404, 409) and getattr(request, 'amagama_v2', False):
            # Asked again through API v1.
            self._use_v1(status)
            return 0
        return super().bulk_wait(request, status)

    def push_store(self, store_controller):
        pass

    def upload_store(self, store_controller):
        pass
