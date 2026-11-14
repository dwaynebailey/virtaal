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


def _choose(unit, matches, kind=TM, **options):
    return pretranslate.choose(_tm(), unit, [(_Model(), matches)], Options(**options), kind)


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


def test_each_round_fills_from_its_own_kind_of_match():
    unit = _po_unit('Open them')
    matches = [
        {'source': 'Open them', 'target': 'Oopmaak'},
        {'source': 'Open it', 'target': 'Maak dit oop', 'quality': 80},
    ]

    tm = _choose(unit, matches, min_quality=75)
    mt = _choose(unit, matches, MT, min_quality=75)

    assert (tm.target, tm.kind) == ('Maak dit oop', TM)
    assert (mt.target, mt.kind, mt.state) == ('Oopmaak', MT, StateEnum.NEEDS_WORK)


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


def _run(models, units, options=None, read=None, tm=None, limits=None):
    progress, results = [], []
    run = pretranslate.Pretranslation(
        tm or _tm(),
        models,
        units,
        options or Options(),
        lambda done, total: progress.append((done, total)),
        results.append,
        read,
        limits,
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
    assert progress == [(0, 2), (1, 2), (2, 2)]


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


def test_a_unit_of_a_file_not_read_yet_is_read_first(idle):
    from virtaal.models.streammodel import PeekUnit

    exact = {'source': 'Open', 'target': 'Maak oop', 'quality': 100}
    real = [_po_unit('Open'), _po_unit('Close', 'Sluit')]
    peeks = [PeekUnit(str(i), 'Open' if i == 0 else 'Close', '', 0) for i in range(2)]

    _run_, _progress, results = _run(
        [_ExactModel([exact])], peeks, read=lambda index, _unit: real[index]
    )
    idle()

    assert [fill.unit for fill in results[0].fills] == [real[0]]


def test_the_review_state_of_a_unit_not_read_yet_is_unknown():
    from virtaal.models.streammodel import PeekUnit

    assert pretranslate.review_state(PeekUnit('1', 'Open', '', 0)) is None


def test_a_model_that_fails_counts_as_no_matches(idle):
    class _Broken:
        def lookup(self, unit, on_done):
            raise RuntimeError('broken')

    _run_, _progress, results = _run([_Broken()], [_po_unit('Open')])
    idle()

    assert results[0].fills == []


# Fetching from free online translation memories first


class _Online(_ExactModel):
    """A free online translation memory that takes a file fetch."""

    display_name = 'Online'
    internal_name = 'online'
    online_service = 'Online'
    billed = machine_translation = False
    BULK_MAX = None

    def bulk_ready(self):
        return True


class _Fetch:
    def __init__(self, model, sources, on_progress, on_done):
        self.model, self.sources = model, sources
        self.total, self.fetched, self.finished = len(sources), 0, None
        self._on_progress, self._on_done = on_progress, on_done

    def answer(self):
        self.fetched = self.total
        self._on_progress(self)
        self.end('done')

    def end(self, finished):
        self.finished = finished
        self._on_done(self)

    def cancel(self):
        if self.finished is None:
            self.end('cancelled')


def _fetching_tm(fetched=()):
    """A TM controller whose file fetches wait for the test."""
    tm = _tm()
    tm.started = []

    def fetch_sources(model, sources, on_progress, on_done, characters=None):
        tm.started.append(_Fetch(model, sources, on_progress, on_done))
        tm.started[-1].characters = characters
        return tm.started[-1]

    tm.unfetched = lambda model, sources: [s for s in map(str, sources) if s not in fetched]
    tm.fetch_sources = fetch_sources
    return tm


@pytest.mark.parametrize(
    'kind, first',
    [
        ({}, True),
        ({'billed': True}, False),
        ({'machine_translation': True}, False),
        ({'BULK_MAX': 0}, False),
        ({'online_service': None}, False),
    ],
)
def test_only_free_online_translation_memories_are_fetched_first(kind, first):
    model = _Online()
    for name, value in kind.items():
        setattr(model, name, value)

    assert pretranslate.fetches_first(model) is first


def test_the_units_to_fill_are_fetched_before_any_is_filled(idle):
    exact = {'source': 'Open', 'target': 'Maak oop', 'quality': 100}
    online = _Online([exact])
    tm = _fetching_tm(fetched={'Close'})
    units = [_po_unit('Open'), _po_unit('Close'), _po_unit('Save', 'Stoor')]

    run, progress, results = _run([online], units, tm=tm)
    idle()
    (fetch,) = tm.started
    assert fetch.sources == ['Open']
    assert run.fetching and results == [] and progress == [(0, 1)]
    fetch.answer()
    idle()

    assert not run.fetching
    assert [fill.unit for fill in results[0].fills] == [units[0]]
    assert results[0].unreachable == ()


def test_nothing_is_fetched_when_every_unit_was(idle):
    tm = _fetching_tm(fetched={'Open'})

    _run_, _progress, results = _run([_Online()], [_po_unit('Open')], tm=tm)
    idle()

    assert tm.started == [] and len(results) == 1


@pytest.mark.parametrize('finished', ['failed', 'refused'])
def test_a_failed_fetch_is_reported_and_the_rest_fills(idle, finished):
    exact = {'source': 'Open', 'target': 'Maak oop', 'quality': 100}
    tm = _fetching_tm()

    _run_, _progress, results = _run([_Online(), _ExactModel([exact])], [_po_unit('Open')], tm=tm)
    tm.started[0].end(finished)
    idle()

    assert [fill.target for fill in results[0].fills] == ['Maak oop']
    assert results[0].unreachable == ('Online',)


def test_cancelling_while_fetching_stops_the_fetch_and_fills_nothing(idle):
    tm = _fetching_tm()

    run, _progress, results = _run([_Online()], [_po_unit('Open')], tm=tm)
    run.cancel()
    idle()

    assert tm.started[0].finished == 'cancelled'
    assert results == []


def test_the_options_are_kept_in_the_config():
    config = {}

    pretranslate.save_options(config, Options(95, True, ('apertium', 'moses')))

    assert pretranslate.load_options(config) == Options(95, True, ('apertium', 'moses'))
    assert pretranslate.load_options({}) == Options(100, False, ())


# Machine translation for the units translation memory left


class _MT:
    """A machine translation service whose fetched suggestions are in
    fetched, {source: [match]}."""

    BULK_MAX = None
    machine_translation = True
    online_service = 'MT'

    def __init__(self, name, billed=False):
        self.internal_name = self.display_name = name
        self.billed = billed
        self.fetched = {}
        self.looked_up = []

    def bulk_ready(self):
        return True

    def bulk_characters(self, sources):
        return sum(len(s) for s in sources)

    def lookup(self, unit, on_done):
        self.looked_up.append(unit.source)
        on_done(self.fetched.get(unit.source, []))


def _answer(fetch):
    """Answer fetch as its machine translation service would."""
    fetch.model.fetched.update(
        {source: [{'source': source, 'target': source[::-1]}] for source in fetch.sources}
    )
    fetch.answer()


OPEN = {'source': 'Open', 'target': 'Maak oop', 'quality': 100}


def test_a_service_not_ticked_is_never_asked(idle):
    paid = _MT('paid', billed=True)
    tm = _fetching_tm()

    _run_, _progress, results = _run([_ExactModel([OPEN]), paid], [_po_unit('Close')], tm=tm)
    idle()

    assert tm.started == [] and paid.looked_up == []
    assert results[0].fills == []


def test_a_ticked_service_is_asked_only_for_the_units_left(idle):
    paid = _MT('paid', billed=True)
    tm = _fetching_tm()
    units = [_po_unit('Open'), _po_unit('Close'), _po_unit('Quit')]

    run, _progress, results = _run(
        [_ExactModel([OPEN]), paid], units, Options(mt_services=('paid',)), tm=tm
    )
    idle()
    (fetch,) = tm.started
    assert fetch.sources == ['Close', 'Quit']
    assert run.mt_sources(paid) == ['Close', 'Quit']
    _answer(fetch)
    idle()

    fills = {str(fill.unit.source): (fill.target, fill.kind) for fill in results[0].fills}
    assert fills == {'Open': ('Maak oop', TM), 'Close': ('esolC', MT), 'Quit': ('tiuQ', MT)}
    assert paid.looked_up == ['Close', 'Quit']


def test_what_was_fetched_before_is_not_asked_again(idle):
    free = _MT('free')
    free.fetched = {'Close': [{'source': 'Close', 'target': 'Sluit'}]}
    tm = _fetching_tm(fetched={'Close'})

    _run_, _progress, results = _run(
        [free], [_po_unit('Close')], Options(mt_services=('free',)), tm=tm
    )
    idle()

    assert tm.started == []
    assert [fill.target for fill in results[0].fills] == ['Sluit']


def test_a_service_is_sent_no_more_than_its_allowance(idle):
    paid = _MT('paid', billed=True)
    tm = _fetching_tm()
    units = [_po_unit('Close'), _po_unit('Quit'), _po_unit('Save')]

    _run([paid], units, Options(mt_services=('paid',)), tm=tm, limits={paid: 9})
    idle()

    (fetch,) = tm.started
    assert fetch.sources == ['Close', 'Quit']
    assert fetch.characters == 9
