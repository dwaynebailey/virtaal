#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""File > Pre-translate This File…: the units still to translate filled
with their best suggestion, from the sources that need nothing sent
over the network (and what online services gave a file fetch), marked
for review, as one undo step."""

import logging
from typing import NamedTuple

from gi.repository import GLib
from translate.misc.multistring import multistring
from translate.storage.workflow import StateEnum

from virtaal.common.pan_app import config_bool, config_int
from virtaal.support import catxliff_compat, unitsnapshot

from .tmcontroller import _rank

TM, MT = 'tm', 'mt'
"""Where a fill came from: a translation memory, or machine translation
(a match with no quality)."""

QUALITIES = (100, 95, 90, 85, 80, 75)
"""The lowest match qualities to choose from."""


class Options(NamedTuple):
    min_quality: int = 100
    """The lowest quality of a translation memory match used."""
    use_mt: bool = False
    """Whether machine translation fetched with the file is used."""
    exact_translated: bool = False
    """Whether an exact match is marked translated, not for review."""


def load_options(config):
    """The options last used, from the TM's plugins.ini section."""
    return Options(
        config_int(config.get('pretranslate_quality', 100), 100, 1, 100, 'pretranslate_quality'),
        config_bool(config.get('pretranslate_mt', False)),
        config_bool(config.get('pretranslate_exact', False)),
    )


def save_options(config, options):
    config['pretranslate_quality'] = options.min_quality
    config['pretranslate_mt'] = options.use_mt
    config['pretranslate_exact'] = options.exact_translated


def _texts(target):
    strings = getattr(target, 'strings', None)
    return [str(text) for text in strings] if strings else [str(target or '')]


def wanted(unit):
    """Whether Pre-translate fills unit: it has no translation in any
    form, can take one and has a source."""
    if unit.istranslated() or any(_texts(unit.target)):
        return False
    return not catxliff_compat.is_locked(unit) and bool(str(unit.source).strip())


def review_state(unit):
    """The state that asks for a review in unit's format: its highest
    below translated (XLIFF's Needs review, PO's fuzzy); None if it has
    none."""
    states = [s for s in (unit.STATE or ()) if StateEnum.EMPTY < s < StateEnum.UNREVIEWED]
    return max(states) if states else None


def fill_state(unit, exact, options):
    """The state for a unit filled with an exact match or not, or None if
    unit can't be marked so (a format without a review state)."""
    if exact and options.exact_translated:
        return StateEnum.UNREVIEWED
    return review_state(unit)


def candidates(tm_controller, unit, answers, options):
    """Every usable match the sources gave for unit, adapted to it as the
    suggestions are, best first. answers holds (model, matches)."""
    accel_markers = tm_controller._accel_markers() if tm_controller.abstract_placeables else ()
    found = []
    for model, matches in answers:
        matches = [dict(match) for match in matches if match.get('target')]
        tm_controller._tidy_matches(model, str(unit.source), matches)
        for match in matches:
            if tm_controller.abstract_placeables:
                match = tm_controller._adapt(match['query_str'], match, accel_markers, unit)
            match = tm_controller._cap_for_codes(unit, match)
            kind = TM if match.get('quality') else MT
            if (kind == MT and options.use_mt) or (
                kind == TM and match['quality'] >= options.min_quality
            ):
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
            form = tm_controller._adapt(query, form, accel_markers)
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


def choose(tm_controller, unit, answers, options):
    """The Fill for unit from the sources' answers, or None."""
    for match in candidates(tm_controller, unit, answers, options):
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

    def count(self, kind):
        return sum(1 for fill in self.fills if fill.kind == kind)


class Pretranslation:
    """Asks every model for each unit still to translate, a unit at a
    time from the main loop, and chooses its fill. on_progress(done,
    total) after each unit; on_done(Result) at the end, unless
    cancelled. Nothing is changed until apply_result()."""

    def __init__(self, tm_controller, models, units, options, on_progress, on_done):
        self.tm_controller = tm_controller
        self.models = list(models)
        self.units = [unit for unit in units if wanted(unit)]
        self.options = options
        self.fills = []
        self.done = 0
        self.cancelled = False
        self._on_progress = on_progress
        self._on_done = on_done
        self._answers = None
        self._idle = None

    @property
    def total(self):
        return len(self.units)

    def start(self):
        self._idle = GLib.idle_add(self._next)

    def cancel(self):
        self.cancelled = True
        if self._idle is not None:
            GLib.source_remove(self._idle)
            self._idle = None

    def _next(self):
        self._idle = None
        if self.cancelled:
            return False
        if self.done >= len(self.units):
            self._on_done(Result(self.fills, len(self.units)))
            return False
        unit = self.units[self.done]
        answers = self._answers = {}
        for model in self.models:
            self._ask(model, unit, answers)
        return False

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
        fill = choose(
            self.tm_controller, unit, [(m, answers[m]) for m in self.models], self.options
        )
        if fill is not None:
            self.fills.append(fill)
        self.done += 1
        self._on_progress(self.done, len(self.units))
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
