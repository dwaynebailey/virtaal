#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import re

from virtaal.support import abstraction

from . import remotetm
from .basetmmodel import BaseTMModel


def v2_url(url):
    """The API v2 base of an amaGama API v1 URL (.../api/v1/), or None."""
    found = re.fullmatch(r'(.*/api/)v1/?', url)
    return found.group(1) + 'v2' if found else None


class TMModel(remotetm.TMModel):
    """This is the translation memory model."""

    __gtype_name__ = 'AmagamaTMModel'
    display_name = _('Amagama')
    description = _('Previous translations for Free and Open Source Software')
    # l10n: Try to keep this as short as possible.
    shortname = _('Amagama')
    online_service = 'amaGama'

    PREFETCH_MAX = 2
    default_config = {
        "url": "https://amagama-live.translatehouse.org/api/v1/",
        "prefetch": 2,
    }

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
        key = (self.v2_base, self.source_lang)
        if key not in self._v1_only:
            logging.info('%s: no API v2 for %s (HTTP %d), using v1', self, self.source_lang, status)
            TMModel._v1_only = self._v1_only | {key}
        # Each request sent before the refusal is asked again.
        if self._pending.get(query_str) is request:
            self._pending = {k: v for k, v in self._pending.items() if k != query_str}
        self._request(query_str)

    def _handle_v2_matches(self, widget, query_str, reply):
        """Score the v2 matches, which the server scored on their abstracts,
        on query_str and pass them on as v1 matches."""
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
        self._handle_matches(widget, query_str, scored)

    def push_store(self, store_controller):
        pass

    def upload_store(self, store_controller):
        pass
