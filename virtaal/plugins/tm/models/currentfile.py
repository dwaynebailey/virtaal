#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import bisect
import collections
import itertools
import time
import weakref
from types import SimpleNamespace

from gi.repository import GLib
from translate.misc.multistring import multistring
from translate.search import match

from virtaal.common import pan_app
from virtaal.support import inlinecodes, textfold
from virtaal.support.match_compat import matcher as _matcher

from .basetmmodel import BaseTMModel


class _UnitMatcher(_matcher):
    """Matcher whose candidates and matches carry a weak reference to the
    unit they came from, so a match can be told apart from the unit being
    translated without an index or keeping closed files' units alive.

    Units with the same source and target share one candidate, which
    lists them all in unit_refs; unit_ref is the first of them."""

    def inittm(self, stores, reverse=False):
        self._shared = {}
        self._by_unit = {}  # id(unit): its candidate
        super().inittm(stores, reverse=reverse)

    def usable(self, unit):
        # Upstream's existingunits keeps only the last target per source,
        # so deduplicate on (source, target) here instead.
        source, target = unit.source, unit.target
        if not (source and target and (self.usefuzzy or not unit.isfuzzy())):
            return False
        if len(source) < 2:
            return False
        key = (source, target)
        candidate = self._shared.get(key)
        if candidate is not None:
            refs = [ref for ref in candidate.unit_refs if ref() is not None]
            if not any(ref() is unit for ref in refs):
                refs.append(weakref.ref(unit))
                self._by_unit[id(unit)] = candidate
            candidate.unit_refs = refs
            candidate.unit_ref = refs[0]
            return False
        # Units of the same extendtm() call share a candidate too.
        owners = self._batch.setdefault(key, [])
        owners.append(unit)
        return len(owners) == 1

    def extendtm(self, units, store=None, sort=True):
        # Upstream appends one candidate per usable unit, in order.
        self._batch = {}
        start = len(self.candidates.units)
        super().extendtm(units, store=store, sort=False)
        new = self.candidates.units[start:]
        for candidate, (key, owners) in zip(new, self._batch.items(), strict=True):
            candidate.unit_key = key
            candidate.unit_refs = [weakref.ref(unit) for unit in owners]
            candidate.unit_ref = candidate.unit_refs[0]
            self._shared[key] = candidate
            for unit in owners:
                self._by_unit[id(unit)] = candidate
        self._batch = {}
        if sort and not self.sort_reverse:
            # What sort() gives, without sorting all of a large file's.
            del self.candidates.units[start:]
            for candidate in new:
                bisect.insort(self.candidates.units, candidate, key=match.sourcelen)
        elif sort:
            self.candidates.units.sort(key=match.sourcelen, reverse=self.sort_reverse)

    def forget(self, unit):
        """Drop unit's candidate, or just unit's share of it if other
        units still have it. Owners that are gone are dropped too."""
        # A unit is in at most one candidate.
        candidate = self._by_unit.pop(id(unit), None)
        if candidate is None:
            return
        refs = candidate.unit_refs
        if not any(ref() is unit for ref in refs):
            return  # The id of a unit that is gone.
        candidate.unit_refs = [ref for ref in refs if ref() is not None and ref() is not unit]
        if candidate.unit_refs:
            candidate.unit_ref = candidate.unit_refs[0]
            return
        units = self.candidates.units
        del units[next(i for i, c in enumerate(units) if c is candidate)]
        del self._shared[candidate.unit_key]

    def buildunits(self, candidates):
        units = super().buildunits(candidates)
        for unit, (_score, candidate) in zip(units, candidates, strict=True):
            unit.unit_ref = getattr(candidate, 'unit_ref', None)
        return units


def _add_markup(match_data, unit):
    """Give match_data the inline markup of unit, an XLIFF or TMX unit
    with inline codes."""
    if unit is None:
        return
    source_markup, target_markup = inlinecodes.unit_markup(unit)
    if source_markup is not None:
        match_data['source_markup'] = source_markup
        match_data['target_markup'] = target_markup


def _code(language):
    return language.replace('-', '_').lower()


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

    # In-process: nothing to hide, and the unit just left can be a match.
    PREFETCH_MAX = 0
    BULK_MAX = 0
    default_config = {'max_length': 1000, 'prefetch': 0}
    LOAD_CHUNK = 500  # Units read per extendtm() call.
    LOAD_BUDGET = 0.02  # Seconds of the main loop each loading step takes.
    _loading = None  # The file being read, if any.
    _stream = None  # (The files opened together, the handler of their file-loaded.)
    _label = None  # What the matches are said to come from.
    matcher = None
    source_lang = None

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        super().__init__(controller)

        self.matcher = None
        self.internal_name = internal_name
        self.load_config()

        store_controller = self.controller.main_controller.store_controller
        self._signal_tracker.connect(store_controller, 'store-loaded', self.recreate_matcher)
        self._signal_tracker.connect(store_controller, 'store-closed', self._on_store_closed)
        if self.controller.main_controller.store_controller.get_store() is not None:
            self.recreate_matcher(self.controller.main_controller.store_controller)

        self._signal_tracker.connect(
            self.controller.main_controller.store_controller.unit_controller,
            'unit-done',
            self._on_unit_modified,
        )

    # METHODS #
    def destroy(self):
        self._on_store_closed()
        super().destroy()

    def set_source_lang(self, language):
        if self.matcher is not None:
            self.matcher.set_language(language)

    def recreate_matcher(self, storecontroller):
        """Read the file's translations from the main loop, a step at a
        time, so that a large file doesn't freeze the window. For files
        opened together, each one's as it is read."""
        self._on_store_closed()
        opened = storecontroller.get_store()
        segments = getattr(opened, 'segments', None)
        # l10n: Try to keep this as short as possible.
        self._label = _('This file')
        if segments is None:
            store = opened._trans_store
            # No store while a folder's files all wait to be paired.
            units = list(store.units) if store is not None else []
        else:
            if len(segments) > 1:
                # l10n: Where a suggestion comes from: the files opened together, as opposed to "This file". Try to keep this as short as possible.
                self._label = _('These files')
            self._stream = (opened, opened.connect('file-loaded', self._on_file_loaded))
            units = [
                unit
                for model in opened.loaded_models()
                if self._in_language(opened, model)
                for unit in model._trans_store.units
            ]
        # A new matcher for each file: an earlier file's translations
        # aren't "This file" ones, and may be in another language.
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
        self.matcher = _UnitMatcher([], lang=self.source_lang, **options)
        self._add_units(units)

    def _add_units(self, units):
        """Read units into the matcher a step at a time, after those
        still being read."""
        self.cache = {}
        if self._loading is not None:
            self._loading.pending.append(units)
            return
        pending = collections.deque([units])

        def queued():
            while pending:
                yield from pending.popleft()

        loading = self._loading = SimpleNamespace(
            units=queued(),
            pending=pending,
            sorted=True,
            query=None,
            source_id=None,
        )
        loading.source_id = GLib.idle_add(self._load_step)

    @staticmethod
    def _in_language(stream, model):
        """Whether a file of the stream is in its target language."""
        expected, found = stream.get_target_language(), model.get_target_language()
        return not (expected and found and _code(expected) != _code(found))

    def _on_file_loaded(self, stream, number, _peek):
        model = stream.segments[number].model
        if self._in_language(stream, model):
            self._add_units(list(model._trans_store.units))

    def _on_store_closed(self, *_args):
        self._stop_loading()
        stream, self._stream = self._stream, None
        if stream is not None:
            stream[0].disconnect(stream[1])

    def _load_step(self):
        """Read units for up to LOAD_BUDGET seconds. @returns: Whether
        there are units left."""
        loading = self._loading
        deadline = time.perf_counter() + self.LOAD_BUDGET
        while True:
            units = list(itertools.islice(loading.units, self.LOAD_CHUNK))
            if not units:
                self._finish_loading()
                return False
            self.matcher.extendtm(units, sort=False)
            loading.sorted = False
            if time.perf_counter() >= deadline:
                return True

    def _finish_loading(self):
        loading, self._loading = self._loading, None
        self._sort_candidates(loading)
        if loading.query is not None:
            tmcontroller, unit = loading.query
            if getattr(tmcontroller, 'current_query', None) == unit.source:
                self.query(tmcontroller, unit)

    def _stop_loading(self, *_args):
        if self._loading is not None:
            GLib.source_remove(self._loading.source_id)
            self._loading = None

    def _sort_candidates(self, loading):
        """Sort the candidates as matches() needs them, if units were
        added without sorting."""
        if not loading.sorted:
            self.matcher.candidates.units.sort(
                key=match.sourcelen, reverse=self.matcher.sort_reverse
            )
            loading.sorted = True

    def query(self, tmcontroller, unit):
        query_str = unit.source
        found = []
        # The cache is cleared when translated units change, so this is safe
        if query_str in self.cache:
            found = self.cache[query_str]
        else:
            found = self._find_matches(unit)
            if self._loading is not None:
                # Matches from part of the file: asked again once it is read.
                self._loading.query = (tmcontroller, unit)
            else:
                # Cache even empty results, so we don't need to query again
                self.cache[query_str] = found
            # We don't want to cache alt trans or the 100% filter, since these
            # are different for units with the same source text.

        matches = (
            self._filter_for(unit, found)
            + self._check_alttrans(unit)
            + self._check_plural_forms(unit, getattr(tmcontroller, 'target_n', 0))
        )
        if matches:
            self.emit('match-found', query_str, matches)

    def lookup(self, unit, on_done):
        if self.matcher is None:
            on_done([])
            return
        on_done(self._filter_for(unit, self._find_matches(unit)) + self._check_alttrans(unit))

    def _check_other_units(self, unit):
        return self._filter_for(unit, self._find_matches(unit))

    def _find_matches(self, unit):
        """Return (match, unit ref) pairs for unit's source text."""
        found = []

        query_str = unit.source
        if not isinstance(query_str, str):
            query_str = query_str.decode('utf-8')

        if self._loading is not None:
            self._sort_candidates(self._loading)
        for candidate in self.matcher.matches(query_str):
            m = match.unit2dict(candidate)
            m['tmsource'] = self._label or _('This file')
            ref = getattr(candidate, 'unit_ref', None)
            _add_markup(m, ref() if ref is not None else None)
            found.append((m, ref))
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

        lcomparer = textfold.comparer(self.source_lang)

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

    def _check_plural_forms(self, unit, target_n):
        """The unit's other filled plural forms, as suggestions for form
        target_n: they are often nearly the same (#874)."""
        hasplural = getattr(unit, 'hasplural', None)
        if hasplural is None or not hasplural():
            return []
        targets = [str(t) for t in unit.target.strings]
        if isinstance(unit.source, multistring):
            sources = [str(s) for s in unit.source.strings]
        else:
            # .qm: one source for every form.
            sources = [str(unit.source)]

        def source_for(n):
            return sources[min(n, len(sources) - 1)]

        lcomparer = textfold.comparer(self.source_lang)
        own = targets[target_n] if target_n < len(targets) else ''
        seen = {own, ''}
        results = []
        for n, target in enumerate(targets):
            if n == target_n or target in seen:
                continue
            seen.add(target)
            # Never 100: it translates another form, not this one.
            quality = lcomparer.similarity(source_for(target_n), source_for(n), 0)
            results.append(
                {
                    'source': source_for(n),
                    'target': target,
                    'quality': min(int(quality), 99),
                    'tmsource': _('This file'),
                }
            )
        return results

    # EVENT HANDLERS #
    def _on_unit_modified(self, widget, new_unit, modified):
        """Replace the unit's previous translation in the TM with the new one."""
        if not modified:
            return
        self.matcher.forget(new_unit)
        if new_unit.istranslated():
            self.matcher.extendtm(new_unit)
        # The old and new target text might be relevant for other units that
        # are already cached, so let's remove ones with source text of similar
        # length
        min_quality = self.controller.min_quality
        start = self.matcher.getstartlength(min_quality, new_unit.source)
        stop = self.matcher.getstoplength(min_quality, new_unit.source)

        def unaffected(key):
            length = len(key)
            return length < start or length > stop

        self.cache = dict((k, v) for (k, v) in self.cache.items() if unaffected(k))
