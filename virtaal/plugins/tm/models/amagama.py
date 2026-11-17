# -*- coding: utf-8 -*-
#
# Copyright 2010 Zuza Software Foundation
# Copyright 2016 F Wolff
#
# This file is part of Virtaal.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.
from __future__ import absolute_import, print_function, unicode_literals

import logging

from virtaal.support import langcodes

from . import remotetm
from .basetmmodel import BaseTMModel


class TMModel(remotetm.TMModel):
    """This is the translation memory model."""

    __gtype_name__ = 'AmagamaTMModel'
    display_name = _('Amagama')
    description = _('Previous translations for Free and Open Source Software')
    #l10n: Try to keep this as short as possible.
    shortname = _('Amagama')

    default_config = {
            "url": "https://amagama-live.translatehouse.org/api/v1/",
    }
    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        # Do not use super() here, as remotetm.TMModel does a bit more than we
        # want in this case.
        BaseTMModel.__init__(self, controller)
        self.internal_name = internal_name
        self.load_config()
        url = self.config["url"]

        self._server_langs = None
        """(source_codes, target_codes) if the server listed them, else None."""
        self._pairs = None
        """Language code pairs still worth trying, best first; None means
        they need to be worked out again."""
        self._pair_confirmed = False

        from virtaal.support import tmclient
        self.tmclient = tmclient.TMClient(url)
        self.tmclient.set_virtaal_useragent()
        self.tmclient.get_languages(self._got_languages, self._no_languages)


    # METHODS #
    def query(self, tmcontroller, unit):
        # We don't want to do lookups in case of misconfigurations like en->en
        if self.source_lang == self.target_lang:
            return

        query_str = unit.source
        if query_str in self.cache:
            _cached = self.cache[query_str]
            if _cached:
                self.emit('match-found', query_str, _cached)
            return

        pairs = self._get_pairs()
        if not pairs:
            return
        self._send_query(query_str, pairs[0])

    def _send_query(self, query_str, pair):
        params = {
            'min_similarity': self.controller.min_quality,
            'max_candidates': self.controller.max_matches,
        }
        if self.checker:
            params['style'] = self.checker

        def on_success(widget, query_str, matches):
            self._confirm_pair(pair)
            self._handle_matches(widget, query_str, matches)

        def on_error(widget, query_str, status):
            self._handle_error(query_str, pair, status)

        self.tmclient.translate_unit(query_str, pair[0], pair[1], on_success, params, on_error)

    def _get_pairs(self):
        if self._pairs is None:
            self._pair_confirmed = False
            self._pairs = langcodes.candidate_pairs(
                    self.source_lang, self.target_lang, self._server_langs)
            if not self._pairs:
                logging.debug("Amagama has no data for %s -> %s",
                        self.source_lang, self.target_lang)
        return self._pairs

    def _confirm_pair(self, pair):
        """The server answered for C{pair}, so stop considering fallbacks."""
        if self._pairs and self._pairs[0] == pair:
            self._pairs = [pair]
            self._pair_confirmed = True

    def _handle_error(self, query_str, pair, status):
        # Amagama answers 404 for a language it doesn't have. Move on to the
        # next, less specific language pair and retry this query. Other errors
        # might be temporary, so they don't change the pair.
        if status != 404 or self._pair_confirmed:
            return
        if not self._pairs or self._pairs[0] != pair:
            # Another response already moved us past this pair
            return
        self._pairs.pop(0)
        if self._pairs:
            logging.debug("Amagama doesn't know %s -> %s, trying %s -> %s",
                    pair[0], pair[1], self._pairs[0][0], self._pairs[0][1])
            self._send_query(query_str, self._pairs[0])
        else:
            logging.debug("Amagama has no data for %s -> %s",
                    self.source_lang, self.target_lang)

    def _got_languages(self, widget, data):
        try:
            sources = data['sourceLanguages']
            targets = data['targetLanguages']
            if not sources or not targets:
                raise ValueError("empty language list")
            supported = (set(sources), set(targets))
        except (KeyError, TypeError, ValueError) as e:
            logging.debug("Ignoring Amagama language list: %s", e)
            return
        self._server_langs = supported
        self._pairs = None

    def _no_languages(self, widget, status):
        # Older servers don't list languages; fall back on 404 responses.
        logging.debug("Amagama language list unavailable (%s)", status)

    def set_source_lang(self, language):
        self._pairs = None

    def set_target_lang(self, language):
        self._pairs = None

    def push_store(self, store_controller):
        pass

    def upload_store(self, store_controller):
        pass
