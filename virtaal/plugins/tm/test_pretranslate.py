#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from types import SimpleNamespace

import pytest
from gi.repository import GLib
from translate.misc.multistring import multistring
from translate.storage import po, xliff
from translate.storage.workflow import StateEnum

from virtaal.plugins.tm import pretranslate
from virtaal.plugins.tm.pretranslate import MT, TM, Options
from virtaal.plugins.tm.tmcontroller import TMController

_XLIFF = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
<file original="a" source-language="en" target-language="af" datatype="plaintext"><body>
<trans-unit id="1"><source>%s</source></trans-unit>
</body></file></xliff>"""


def _xliff_unit(source):
    return xliff.xlifffile.parsestring((_XLIFF % source).encode('utf-8')).units[0]


def _po_unit(source, target=''):
    store = po.pofile()
    unit = store.addsourceunit(source)
    unit.target = target
    return unit


def _tm(nplurals=2):
    controller = TMController.__new__(TMController)
    controller.abstract_placeables = True
    controller.min_quality = 75
    controller.main_controller = SimpleNamespace(
        checks_controller=None,
        lang_controller=SimpleNamespace(get_target_nplurals=lambda: nplurals),
    )
    return controller


class _Model:
    display_name = 'A TM'

    def __init__(self, matches=(), later=False):
        self.matches = list(matches)
        self.later = later
        self.waiting = []

    def lookup(self, unit, on_done):
        if self.later:
            self.waiting.append(lambda: on_done(self.matches))
        else:
            on_done(self.matches)


class _ExactModel(_Model):
    def lookup(self, unit, on_done):
        on_done([m for m in self.matches if m['source'] == unit.source])


def _choose(unit, matches, **options):
    return pretranslate.choose(_tm(), unit, [(_Model(), matches)], Options(**options))


# Which units are filled


def test_only_units_with_no_translation_are_wanted():
    fuzzy = _po_unit('Open', 'Oop')
    fuzzy.markfuzzy()

    assert pretranslate.wanted(_po_unit('Open'))
    assert not pretranslate.wanted(_po_unit('Open', 'Maak oop'))
    assert not pretranslate.wanted(fuzzy)
    assert not pretranslate.wanted(_po_unit(' '))


def test_a_locked_unit_is_not_wanted():
    unit = _po_unit('Open')
    unit.read_only = True

    assert not pretranslate.wanted(unit)


def test_a_plural_with_one_form_filled_is_not_wanted():
    unit = _po_unit(multistring(['%d file', '%d files']))
    unit.target = multistring(['', '%d lêers'])

    assert not pretranslate.wanted(unit)


def test_the_review_state_is_the_formats_own():
    assert pretranslate.review_state(_po_unit('Open')) == StateEnum.NEEDS_WORK
    assert pretranslate.review_state(_xliff_unit('Open')) == StateEnum.NEEDS_REVIEW


# Choosing the match


def test_an_exact_match_fills_the_unit_for_review():
    fill = _choose(_po_unit('Open'), [{'source': 'Open', 'target': 'Maak oop', 'quality': 100}])

    assert (fill.target, fill.state, fill.kind) == ('Maak oop', StateEnum.NEEDS_WORK, TM)


def test_an_exact_match_can_be_marked_translated():
    match = {'source': 'Open', 'target': 'Maak oop', 'quality': 100}

    fill = _choose(_po_unit('Open'), [match], exact_translated=True)

    assert fill.state == StateEnum.UNREVIEWED


def test_a_fuzzy_match_is_for_review_even_with_exact_ones_translated():
    match = {'source': 'Open it', 'target': 'Maak dit oop', 'quality': 90}

    fill = _choose(_po_unit('Open them'), [match], min_quality=90, exact_translated=True)

    assert fill.state == StateEnum.NEEDS_WORK


def test_a_match_below_the_lowest_quality_is_left_out():
    match = {'source': 'Open it', 'target': 'Maak dit oop', 'quality': 90}

    assert _choose(_po_unit('Open them'), [match]) is None


def test_machine_translation_only_fills_when_asked():
    unit = _po_unit('Open')
    machine = {'source': 'Open', 'target': 'Oopmaak'}

    assert _choose(unit, [machine]) is None
    assert _choose(unit, [machine], use_mt=True).kind == MT


def test_a_translation_memory_match_beats_machine_translation():
    unit = _po_unit('Open them')
    matches = [
        {'source': 'Open them', 'target': 'Oopmaak'},
        {'source': 'Open it', 'target': 'Maak dit oop', 'quality': 80},
    ]

    fill = _choose(unit, matches, min_quality=75, use_mt=True)

    assert (fill.target, fill.kind) == ('Maak dit oop', TM)


def test_the_best_match_is_used():
    matches = [
        {'source': 'Opens', 'target': 'Oop', 'quality': 96},
        {'source': 'Open', 'target': 'Maak oop', 'quality': 100},
    ]

    assert _choose(_po_unit('Open'), matches, min_quality=95).target == 'Maak oop'


def test_a_match_gets_the_units_placeables():
    match = {'source': 'Delete 3 files', 'target': 'Skrap 3 lêers', 'quality': 100}

    fill = _choose(_po_unit('Delete 5 files'), [match])

    assert fill.target == 'Skrap 5 lêers'


def test_a_match_keeps_the_units_own_inline_codes():
    unit = _xliff_unit('Click <g id="1">here</g>')
    match = {
        'source': 'Click here',
        'target': 'Klik hier',
        'quality': 100,
        'source_markup': 'Click <g id="7">here</g>',
        'target_markup': 'Klik <g id="7">hier</g>',
    }

    fill = _choose(unit, [match])
    pretranslate.apply(fill)

    assert '<g id="1">hier</g>' in bytes(unit._store).decode()
    assert unit.get_state_n() == StateEnum.NEEDS_REVIEW


PLURAL_MATCH = {
    'source': 'Delete %d file',
    'target': 'Skrap %d lêer',
    'quality': 100,
    'plural_source': 'Delete %d files',
    'plural_targets': ['Skrap %d lêer', 'Skrap %d lêers'],
}


def test_a_plural_is_filled_form_by_form():
    unit = _po_unit(multistring(['Delete %d file', 'Delete %d files']))

    fill = _choose(unit, [PLURAL_MATCH])
    pretranslate.apply(fill)

    assert unit.target.strings == ['Skrap %d lêer', 'Skrap %d lêers']
    assert unit.isfuzzy()


@pytest.mark.parametrize(
    'match',
    [
        {k: v for k, v in PLURAL_MATCH.items() if not k.startswith('plural')},
        {**PLURAL_MATCH, 'plural_targets': ['Skrap %d lêer', '']},
        {**PLURAL_MATCH, 'plural_targets': ['Skrap %d lêer']},
    ],
)
def test_a_plural_without_every_form_is_not_filled(match):
    unit = _po_unit(multistring(['Delete %d file', 'Delete %d files']))

    assert _choose(unit, [match]) is None


# Going through the units


@pytest.fixture
def idle(monkeypatch):
    calls = []

    def add(func, *args, **kwargs):
        calls.append(lambda: func(*args))
        return len(calls)

    monkeypatch.setattr(GLib, 'idle_add', add)
    monkeypatch.setattr(GLib, 'source_remove', lambda _id: None)

    def run():
        while calls:
            calls.pop(0)()

    return run


def _run(models, units, options=None):
    progress, results = [], []
    run = pretranslate.Pretranslation(
        _tm(),
        models,
        units,
        options or Options(),
        lambda done, total: progress.append((done, total)),
        results.append,
    )
    run.start()
    return run, progress, results


def test_every_unit_still_to_translate_is_asked_for(idle):
    exact = {'source': 'Open', 'target': 'Maak oop', 'quality': 100}
    units = [_po_unit('Open'), _po_unit('Open', 'Oop'), _po_unit('Close')]

    _run_, progress, results = _run([_ExactModel([exact])], units)
    idle()

    (result,) = results
    assert result.wanted == 2
    assert [fill.unit for fill in result.fills] == [units[0]]
    assert progress == [(1, 2), (2, 2)]


def test_a_unit_waits_for_every_model(idle):
    later = _Model([{'source': 'Open', 'target': 'Maak oop', 'quality': 100}], later=True)

    _run_, _progress, results = _run([_Model(), later], [_po_unit('Open')])
    idle()
    assert results == []
    later.waiting.pop()()
    idle()

    assert [fill.target for fill in results[0].fills] == ['Maak oop']


def test_a_cancelled_run_ends_without_a_result(idle):
    later = _Model([{'source': 'Open', 'target': 'Maak oop', 'quality': 100}], later=True)

    run, _progress, results = _run([later], [_po_unit('Open')])
    idle()
    run.cancel()
    later.waiting.pop()()
    idle()

    assert results == []


def test_a_model_that_fails_counts_as_no_matches(idle):
    class _Broken:
        def lookup(self, unit, on_done):
            raise RuntimeError('broken')

    _run_, _progress, results = _run([_Broken()], [_po_unit('Open')])
    idle()

    assert results[0].fills == []


def test_the_options_are_kept_in_the_config():
    config = {}

    pretranslate.save_options(config, Options(95, True, True))

    assert pretranslate.load_options(config) == Options(95, True, True)
    assert pretranslate.load_options({}) == Options(100, False, False)
