#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The suggestions fetched from online services for the open file
(#1679), kept in the local TM file apart from the user's translations.
A service's fetched suggestions for a unit answer for it, so it isn't
asked again, until they expire or the unit is translated."""

import logging
import os
from sqlite3 import dbapi2

from virtaal.common import pan_app
from virtaal.support import sdlxliff_compat, statsdb

DAYS = 7
"""How many days fetched suggestions are kept: tm.ini's [suggestions]
cache_days."""

WORDS = 1000
"""How many source words one fetch asks a service for, about an hour's
work: tm.ini's [suggestions] fetch_words."""


def tm_file():
    """The local TM's database, as its settings name it."""
    config_dir = pan_app.get_config_dir()
    config = pan_app.load_config(os.path.join(config_dir, 'tm.ini'), 'localtm')
    return os.path.join(config_dir, os.path.expanduser(config.get('tmdb', 'tm.db')))


def needs_suggestions(unit):
    """Whether a file fetch asks for unit: one still to translate or
    needing work, that can take a suggestion."""
    return (
        not unit.istranslated()
        and not sdlxliff_compat.is_locked(unit)
        and bool(str(unit.source).strip())
    )


class FetchedSuggestions:
    """The open file's fetched suggestions, by service and source."""

    def __init__(self, days=DAYS, path=tm_file):
        self.days = days
        self._path = path
        self._db = None
        self._pair = None
        self.by_service = {}

    def _cache(self):
        """The local TM file's suggestion cache, or None if it can't be
        used now."""
        if self._db is None:
            from virtaal.support import tmdb

            try:
                self._db = tmdb.SuggestionCache(self._path())
            except (dbapi2.Error, OSError) as e:
                logging.debug("Can't use the local TM for fetched suggestions: %s", e)
                return None
        return self._db

    def _run(self, action, *args, default=None):
        cache = self._cache()
        if cache is None:
            return default
        try:
            return getattr(cache, action)(*args)
        except dbapi2.Error as e:
            logging.debug("Fetched suggestions: can't %s: %s", action, e)
            return default

    def expire(self):
        self._run('expire', self.days)

    def load(self, units, source_lang, target_lang):
        """Drop what expired, and what units translated now had; then
        keep the rest of units' for this language pair at hand."""
        self._pair = (source_lang, target_lang)
        self.by_service = {}
        if not source_lang or not target_lang:
            return
        self.expire()
        wanted = {str(unit.source) for unit in units if needs_suggestions(unit)}
        done = {str(unit.source) for unit in units if unit.istranslated()} - wanted
        self._run('forget', source_lang, target_lang, done)
        self.by_service = self._run('get', source_lang, target_lang, wanted, default={})

    def clear(self):
        self._pair = None
        self.by_service = {}

    def matches(self, service, source):
        """service's fetched matches for source (a list, maybe empty), or
        None if there are none."""
        return self.by_service.get(service, {}).get(str(source))

    def plan(self, service, units, start, words):
        """(sources, whole) for asking service: the sources of units it
        still needs to be asked for, each once, from units[start] on (and
        then from the top), until they come to about words source words;
        whole if those are all it needs."""
        have = self.by_service.get(service, {})
        sources, total = {}, 0
        for unit in units[start:] + units[:start]:
            source = str(unit.source)
            if source in sources or source in have or not needs_suggestions(unit):
                continue
            count = statsdb.wordsinunit(unit)[0]
            if sources and total + count > words:
                return list(sources), False
            sources[source] = None
            total += count
        return list(sources), True

    def keep(self, service, found):
        """Keep found, {source: [match, ...]}, as service's answers."""
        if self._pair is None or not found:
            return
        self.by_service.setdefault(service, {}).update(found)
        self._run('put', service, *self._pair, found)

    def forget(self, source):
        """Drop every service's answer for source, translated now."""
        source = str(source)
        if self._pair is None or not any(source in found for found in self.by_service.values()):
            return
        for found in self.by_service.values():
            found.pop(source, None)
        self._run('forget', *self._pair, [source])

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
