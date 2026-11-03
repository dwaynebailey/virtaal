#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import weakref

from translate.search import match

from virtaal.common import pan_app
from virtaal.support.match_compat import matcher as _matcher

from .basetmmodel import BaseTMModel


class _UnitMatcher(_matcher):
    """Matcher whose candidates and matches carry a weak reference to the
    unit they came from, so a match can be told apart from the unit being
    translated without an index or keeping closed files' units alive."""

    def usable(self, unit):
        if super().usable(unit):
            self._usable.append(unit)
            return True
        return False

    def extendtm(self, units, store=None, sort=True):
        # Upstream appends one candidate per usable unit, in order.
        self._usable = []
        start = len(self.candidates.units)
        super().extendtm(units, store=store, sort=False)
        for candidate, unit in zip(self.candidates.units[start:], self._usable):
            candidate.unit_ref = weakref.ref(unit)
        self._usable = []
        if sort:
            self.candidates.units.sort(key=match.sourcelen, reverse=self.sort_reverse)

    def buildunits(self, candidates):
        units = super().buildunits(candidates)
        for unit, (_score, candidate) in zip(units, candidates):
            unit.unit_ref = getattr(candidate, 'unit_ref', None)
        return units


def _offered_to(unit, ref):
    """Whether a 100% match from the unit behind ref helps unit: not its
    own translation, and not one for a different context."""
    other = ref() if ref is not None else None
    if other is None:
        return True
    if other is unit:
        return False
    context, other_context = unit.getcontext(), other.getcontext()
    return not (context and other_context and context != other_context)


class TMModel(BaseTMModel):
    """Translation memory model that matches against translated strings from current file"""

    __gtype_name__ = 'CurrentFileTMModel'
    display_name = _('Current File')
    description = _('Translated units from the currently open file')

    default_config = {'max_length': 1000}

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        super().__init__(controller)

        self.matcher = None
        self.internal_name = internal_name
        self.load_config()

        self._signal_tracker.connect(
            self.controller.main_controller.store_controller, 'store-loaded', self.recreate_matcher
        )
        if self.controller.main_controller.store_controller.get_store() is not None:
            self.recreate_matcher(self.controller.main_controller.store_controller)

        self._signal_tracker.connect(
            self.controller.main_controller.store_controller.unit_controller,
            'unit-done',
            self._on_unit_modified,
        )

    # METHODS #
    def recreate_matcher(self, storecontroller):
        store = storecontroller.get_store()._trans_store
        if self.matcher is None:
            options = {
                'max_length': pan_app.config_int(
                    self.config['max_length'],
                    self.default_config['max_length'],
                    1,
                    1000000,
                    'max_length',
                ),
                'max_candidates': self.controller.max_matches,
                'min_similarity': self.controller.min_quality,
            }
            self.matcher = _UnitMatcher(store, **options)
        else:
            self.matcher.extendtm(store.units)
        self.cache = {}

    def query(self, tmcontroller, unit):
        query_str = unit.source
        found = []
        # The cache is cleared when translated units change, so this is safe
        if query_str in self.cache:
            found = self.cache[query_str]
        else:
            found = self._find_matches(unit)
            # Cache even empty results, so we don't need to query again
            self.cache[query_str] = found
            # We don't want to cache alt trans or the 100% filter, since these
            # are different for units with the same source text.

        matches = self._filter_for(unit, found) + self._check_alttrans(unit)
        if matches:
            self.emit('match-found', query_str, matches)

    def _check_other_units(self, unit):
        return self._filter_for(unit, self._find_matches(unit))

    def _find_matches(self, unit):
        """Return (match, unit ref) pairs for unit's source text."""
        found = []

        query_str = unit.source
        if not isinstance(query_str, str):
            query_str = query_str.decode('utf-8')

        for candidate in self.matcher.matches(query_str):
            m = match.unit2dict(candidate)
            # l10n: Try to keep this as short as possible.
            m['tmsource'] = _('This file')
            found.append((m, getattr(candidate, 'unit_ref', None)))
        return found

    @staticmethod
    def _filter_for(unit, found):
        # The controller makes a cached quality an int in place
        return [m for (m, ref) in found if int(m['quality'] or 0) < 100 or _offered_to(unit, ref)]

    def _check_alttrans(self, unit):
        if not hasattr(unit, 'getalttrans'):
            return []
        alttrans = unit.getalttrans()
        if not alttrans:
            return []

        from translate.search.lshtein import LevenshteinComparer

        lcomparer = LevenshteinComparer(max_len=1000)

        results = []
        for alt in alttrans:
            quality = lcomparer.similarity(
                unit.source, alt.source, self.controller.min_quality - 15
            )
            # let's check if it is useful, but be more lenient
            if quality < self.controller.min_quality - 10:
                continue
            tmsource = _('This file')

            xmlelement = getattr(alt, 'xmlelement', None)
            if xmlelement is not None:
                origin = alt.xmlelement.get('origin', '')
                if origin:
                    if origin == "lmc":
                        # Experimental code to test lmc research. Everything
                        # in a try block, just in case.
                        try:
                            import os.path

                            extras = xmlelement.xpath('processing-instruction()')
                            meta = dict((pi.target, pi.text) for pi in extras)
                            tmsource = [
                                meta.get("contact-name", ""),
                                meta.get("category", ""),
                                os.path.splitext(meta.get("original", ""))[0],
                            ]
                            tmsource = "\n".join(filter(None, tmsource))
                        except Exception as e:
                            import logging

                            logging.info(e)

                    tmsource += "\n" + origin

            results.append(
                {
                    'source': alt.source,
                    'target': alt.target,
                    'quality': quality,
                    'tmsource': tmsource,
                }
            )
        return results

    # EVENT HANDLERS #
    def _on_unit_modified(self, widget, new_unit, modified):
        """Add the new translation unit to the TM."""
        if modified and new_unit.istranslated():
            self.matcher.extendtm(new_unit)
            # This new target text might be relevant for other units that are
            # already cached, so let's remove ones with source text of similar
            # length
            min_quality = self.controller.min_quality
            start = self.matcher.getstartlength(min_quality, new_unit.source)
            stop = self.matcher.getstoplength(min_quality, new_unit.source)

            def unaffected(key):
                length = len(key)
                return length < start or length > stop

            self.cache = dict((k, v) for (k, v) in self.cache.items() if unaffected(k))
