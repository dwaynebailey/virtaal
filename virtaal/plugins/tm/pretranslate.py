#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""File > Pre-translate This File…: the units still to translate filled
with their best suggestion, marked for review, as one undo step. Free
online translation memories are asked for those units first, in a file
fetch, and the machine translation services ticked for those left."""

import logging
from typing import NamedTuple

from gi.repository import GLib
from translate.misc.multistring import multistring
from translate.storage.workflow import StateEnum

from virtaal.common.pan_app import config_bool, config_int
from virtaal.support import catxliff_compat, unitsnapshot

from .bulkfetch import FAILED, REFUSED
from .tmcontroller import _rank

TM, MT = 'tm', 'mt'
"""Where a fill came from: a translation memory, or machine translation
(a match with no quality)."""

QUALITIES = (100, 95, 90, 85, 80, 75)
"""The lowest match qualities to choose from."""


class Options(NamedTuple):
    min_quality: int = 100
    """The lowest quality of a translation memory match used."""
    exact_translated: bool = False
    """Whether an exact match is marked translated, not for review."""
    mt_services: tuple = ()
    """The machine translation services (internal names) asked for the
    units translation memory leaves."""


def load_options(config):
    """The options last used, from the TM's plugins.ini section."""
    services = str(config.get('pretranslate_mt_services', ''))
    return Options(
        config_int(config.get('pretranslate_quality', 100), 100, 1, 100, 'pretranslate_quality'),
        config_bool(config.get('pretranslate_exact', False)),
        tuple(name for name in services.split(',') if name),
    )


def save_options(config, options):
    """Keep options; the dialog leaves paid services out of mt_services."""
    config['pretranslate_quality'] = options.min_quality
    config['pretranslate_exact'] = options.exact_translated
    config['pretranslate_mt_services'] = ','.join(options.mt_services)


def _texts(target):
    strings = getattr(target, 'strings', None)
    return [str(text) for text in strings] if strings else [str(target or '')]


def wanted(unit):
    """Whether Pre-translate fills unit: it has no translation in any
    form, can take one and has a source."""
    if unit.istranslated() or any(_texts(unit.target)):
        return False
    return not catxliff_compat.is_locked(unit) and bool(str(unit.source).strip())


def fetches_first(model):
    """Whether Pre-translate fetches model's suggestions for the units
    first: a free online translation memory that takes a file fetch."""
    return (
        getattr(model, 'online_service', None) is not None
        and not getattr(model, 'billed', False)
        and not getattr(model, 'machine_translation', False)
        and getattr(model, 'BULK_MAX', 0) != 0
        and model.bulk_ready()
    )


def review_state(unit):
    """The state that asks for a review in unit's format: its highest
    below translated (XLIFF's Needs review, PO's fuzzy); None if it has
    none, or isn't read yet."""
    states = [
        s
        for s in (unit.STATE or ())
        if isinstance(s, int) and StateEnum.EMPTY < s < StateEnum.UNREVIEWED
    ]
    return max(states) if states else None


def fill_state(unit, exact, options):
    """The state for a unit filled with an exact match or not, or None if
    unit can't be marked so (a format without a review state)."""
    if exact and options.exact_translated:
        return StateEnum.UNREVIEWED
    return review_state(unit)


def candidates(tm_controller, unit, answers, options, kind=TM):
    """Every usable match of kind the sources gave for unit, adapted to it
    as the suggestions are, best first. answers holds (model, matches)."""
    accel_markers = tm_controller._accel_markers() if tm_controller.abstract_placeables else ()
    found = []
    for model, matches in answers:
        matches = [dict(match) for match in matches if match.get('target')]
        tm_controller._tidy_matches(model, str(unit.source), matches)
        for match in matches:
            if tm_controller.abstract_placeables:
                match = tm_controller._adapt(
                    match['query_str'], match, accel_markers, unit, tm_controller._source_language()
                )
            match = tm_controller._cap_for_codes(unit, match)
            if (TM if match.get('quality') else MT) != kind:
                continue
            if kind == MT or match['quality'] >= options.min_quality:
                found.append(dict(match, pretranslate_kind=kind))
    return sorted(found, key=_rank, reverse=True)


def plural_texts(tm_controller, unit, match):
    """The forms of a stored plural match for plural unit, each form read
    as the unit's own; None unless every form maps exactly."""
    forms = match.get('plural_targets')
    count = tm_controller._unit_nplurals(unit)
    if not count or not isinstance(forms, list) or not isinstance(match.get('plural_source'), str):
        return None
    sources = getattr(unit.source, 'strings', None) or [unit.source]
    query = str(sources[min(1, len(sources) - 1)])
    accel_markers = tm_controller._accel_markers() if tm_controller.abstract_placeables else ()
    texts = [str(match['target'])]
    for target_n in range(1, count):
        found = tm_controller._stored_form(unit, match, len(forms), target_n)
        if found is None or found[1] or not 0 < found[0] < len(forms) or not forms[found[0]]:
            return None
        form = {'source': match['plural_source'], 'target': forms[found[0]], 'quality': 100}
        if tm_controller.abstract_placeables:
            form = tm_controller._adapt(
                query, form, accel_markers, lang=tm_controller._source_language()
            )
        texts.append(str(form['target']))
    return texts


def fill_target(tm_controller, unit, match):
    """What filling unit with match sets its target to: a list of trees
    (unit's own inline codes kept) or text, or None if it can't."""
    if unit.hasplural():
        texts = plural_texts(tm_controller, unit, match)
        return multistring(texts) if texts else None
    rich = tm_controller._rich_target(unit, match)
    if rich is not None:
        return [rich]
    return tm_controller._plain_target(unit, match) or None


class Fill(NamedTuple):
    unit: object
    target: object
    state: object
    kind: str


def choose(tm_controller, unit, answers, options, kind=TM):
    """The Fill for unit from the sources' answers of kind, or None."""
    for match in candidates(tm_controller, unit, answers, options, kind):
        target = fill_target(tm_controller, unit, match)
        if target is None:
            continue
        kind = match['pretranslate_kind']
        state = fill_state(unit, kind == TM and match['quality'] >= 100, options)
        if state is None:
            continue
        return Fill(unit, target, state, kind)
    return None


def apply(fill):
    """Fill fill.unit: (before, after) unitsnapshot.Snapshot()s."""
    unit = fill.unit
    before = unitsnapshot.take(unit)
    if isinstance(fill.target, list):
        unit.rich_target = fill.target
    else:
        unit.target = fill.target
    if unit.STATE and unit.get_state_n() != fill.state:
        unit.set_state_n(fill.state)
    return before, unitsnapshot.take(unit)


class Result(NamedTuple):
    fills: list
    wanted: int
    """How many units were still to translate."""
    unreachable: tuple = ()
    """The services whose file fetch failed."""

    def count(self, kind):
        return sum(1 for fill in self.fills if fill.kind == kind)


class Pretranslation:
    """Fills the units still to translate in two rounds: translation
    memory, then the machine translation services ticked for the units
    it left. Each round first fetches its units' suggestions from the
    services that take a file fetch (the free online translation
    memories, then the machine translation services), then asks its
    models for each unit, a unit at a time from the main loop, and
    chooses its fill. on_progress(done, total) after each batch fetched
    and each unit; on_phase() when a fetch or fill starts; on_done(Result)
    at the end, unless cancelled. Nothing is changed until
    apply_result(). read(index, unit) gives the unit at index of units as
    read from its file, for a stream's file not read yet. limits holds
    the characters a machine translation service may be sent."""

    def __init__(
        self,
        tm_controller,
        models,
        units,
        options,
        on_progress,
        on_done,
        read=None,
        limits=None,
        on_phase=None,
    ):
        self.tm_controller = tm_controller
        models = list(models)
        self.tm_models = [m for m in models if not getattr(m, 'machine_translation', False)]
        self.mt_models = [
            m
            for m in models
            if getattr(m, 'machine_translation', False)
            and getattr(m, 'internal_name', None) in options.mt_services
        ]
        self.units = [(index, unit) for index, unit in enumerate(units) if wanted(unit)]
        self._read = read
        self.options = options
        self.limits = dict(limits or {})
        self.fills = []
        self.cancelled = False
        self._on_progress = on_progress
        self._on_done = on_done
        self._on_phase = on_phase or (lambda: None)
        self._answers = None
        self._idle = None
        self._unreachable = []
        self._read_units = []
        self.kind = TM
        self.round = []
        self.models = []
        self.done = 0
        self.fetches = []
        self.fetching = self._starting = False
        self.mt_units = None
        """The units left for machine translation, once known."""

    @property
    def total(self):
        return len(self.units)

    def start(self):
        self._start_round(TM, self.units, self.tm_models, filter(fetches_first, self.tm_models))

    def cancel(self):
        self.cancelled = True
        for fetch in self.fetches:
            fetch.cancel()
        if self._idle is not None:
            GLib.source_remove(self._idle)
            self._idle = None

    def mt_sources(self, model):
        """What model would be asked for: the sources of the units left
        for machine translation, or of every unit until they are known,
        that it has no suggestions fetched for."""
        units = self.units if self.mt_units is None else self.mt_units
        return self.tm_controller.unfetched(model, [unit.source for _index, unit in units])

    def _start_round(self, kind, units, models, fetch_from):
        self.kind, self.round, self.models, self.done = kind, units, models, 0
        # A fetch can end as it starts: filling waits until all are.
        self.fetching = self._starting = True
        self.fetches = []
        for model in fetch_from:
            self._fetch(model, [unit.source for _index, unit in units])
        self._starting = False
        self._on_phase()
        self._fetch_progress()
        self._after_fetches()

    def _fetch(self, model, sources):
        sources = self.tm_controller.unfetched(model, sources)
        limit = self.limits.get(model)
        if limit is not None:
            sources, _capped = self.tm_controller.cap_characters(model, sources, limit)
        if sources:
            self.fetches.append(
                self.tm_controller.fetch_sources(
                    model, sources, self._on_fetched, self._on_fetch_done, limit
                )
            )

    def _fetch_progress(self):
        if self.fetches:
            self._on_progress(
                sum(f.fetched for f in self.fetches), sum(f.total for f in self.fetches)
            )

    def _on_fetched(self, _fetch):
        if not self.cancelled:
            self._fetch_progress()

    def _on_fetch_done(self, fetch):
        if fetch.finished in (FAILED, REFUSED):
            logging.info('Pre-translate: %s could not be reached', fetch.model)
            self._unreachable.append(fetch.model)
        self._after_fetches()

    def _after_fetches(self):
        """Fill, once every fetch has ended."""
        if self.cancelled or self._starting or not self.fetching:
            return
        if any(fetch.finished is None for fetch in self.fetches):
            return
        self.fetching = False
        self._on_phase()
        self._on_progress(0, len(self.round))
        self._idle = GLib.idle_add(self._next)

    def _next(self):
        self._idle = None
        if self.cancelled:
            return False
        if self.done >= len(self.round):
            self._end_round()
            return False
        index, unit = self.round[self.done]
        unit = self._unit(index, unit)
        if unit is None:
            self.done += 1
            self._on_progress(self.done, len(self.round))
            self._idle = GLib.idle_add(self._next)
            return False
        if self.kind == TM:
            self._read_units.append((index, unit))
        answers = self._answers = {}
        for model in self.models:
            self._ask(model, unit, answers)
        if not self.models:
            self._answered(unit, answers)
        return False

    def _end_round(self):
        if self.kind == TM and self.mt_models:
            filled = {id(fill.unit) for fill in self.fills}
            self.mt_units = [(i, u) for i, u in self._read_units if id(u) not in filled]
            if self.mt_units:
                ready = [m for m in self.mt_models if m.BULK_MAX != 0 and m.bulk_ready()]
                self._start_round(MT, self.mt_units, self.mt_models, ready)
                return
        self._on_done(Result(self.fills, len(self.units), self.unreachable()))

    def unreachable(self):
        """The names of the services whose fetch failed."""
        return tuple(m.display_name or m.internal_name for m in self._unreachable)

    def _unit(self, index, unit):
        """unit, read from its file if it wasn't; None if that one isn't
        wanted after all."""
        if self._read is not None:
            unit = self._read(index, unit)
        return unit if unit is not None and wanted(unit) else None

    def _ask(self, model, unit, answers):
        def on_done(matches):
            if self.cancelled or answers is not self._answers or model in answers:
                return
            answers[model] = matches or []
            if len(answers) == len(self.models):
                self._answered(unit, answers)

        try:
            model.lookup(unit, on_done)
        except Exception:
            logging.exception('Pre-translate: %s could not look up a unit', model)
            on_done([])

    def _answered(self, unit, answers):
        found = [(m, answers[m]) for m in self.models]
        fill = choose(self.tm_controller, unit, found, self.options, self.kind)
        if fill is not None:
            self.fills.append(fill)
        self.done += 1
        self._on_progress(self.done, len(self.round))
        # Not from inside a model's answer: the next unit's may come at once.
        self._idle = GLib.idle_add(self._next)


def apply_result(main_controller, result):
    """Fill the units of result, as one undo step."""
    changes = []
    for fill in result.fills:
        if wanted(fill.unit):
            changes.append((fill.unit, *apply(fill)))
    if not changes:
        return
    # Applied again, the undo step is the same change.
    for unit, before, _after in changes:
        unitsnapshot.restore(unit, before)
    main_controller.undo_controller.change_units(changes)
