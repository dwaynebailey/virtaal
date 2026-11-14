#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Suggestions fetched for a whole file (#1679): kept in the local TM
file, answering for their service until they expire or the unit is
translated."""

import time
from types import SimpleNamespace

import pytest
from gi.repository import GObject

from virtaal.plugins.tm import fetched as fetched_module
from virtaal.plugins.tm.fetched import FetchedSuggestions, needs_suggestions
from virtaal.plugins.tm.models.basetmmodel import BaseTMModel
from virtaal.plugins.tm.tmcontroller import TMController
from virtaal.support import tmdb

MATCH = {'source': 'Open', 'target': 'Maak oop', 'tmsource': 'Google'}


def _unit(source, translated=False, fuzzy=False):
    return SimpleNamespace(
        source=source,
        istranslated=lambda: translated and not fuzzy,
        isfuzzy=lambda: fuzzy,
    )


@pytest.fixture
def path(tmp_path):
    path = str(tmp_path / 'tm.db')
    yield path
    tmdb.SuggestionCache(path).close()


@pytest.fixture
def fetched(path):
    found = FetchedSuggestions(path=lambda: path)
    yield found
    found.close()


def _stored(path, sources, source_lang='en', target_lang='af'):
    cache = tmdb.SuggestionCache(path)
    return cache.get(source_lang, target_lang, sources)


def test_untranslated_and_fuzzy_units_need_suggestions():
    assert needs_suggestions(_unit('Open'))
    assert needs_suggestions(_unit('Open', translated=True, fuzzy=True))
    assert not needs_suggestions(_unit('Open', translated=True))
    assert not needs_suggestions(_unit('  '))


def test_a_service_is_asked_for_each_source_once_in_file_order(fetched):
    units = [_unit('Open'), _unit('Close'), _unit('Open'), _unit('Quit', translated=True)]
    fetched.load(units, 'en', 'af')

    assert fetched.plan('google_translate', units, 0, 1000) == (['Open', 'Close'], True)
    fetched.keep('google_translate', {'Open': [MATCH]})
    assert fetched.plan('google_translate', units, 0, 1000) == (['Close'], True)
    assert fetched.plan('amagama', units, 0, 1000) == (['Open', 'Close'], True)


WORDY = [_unit('one two'), _unit('three four five'), _unit('six'), _unit('seven eight')]


@pytest.mark.parametrize(
    'start, words, expected',
    [
        # From the current unit on, until the budget is spent.
        (1, 4, (['three four five', 'six'], False)),
        (0, 5, (['one two', 'three four five'], False)),
        # Then round from the top.
        (2, 5, (['six', 'seven eight', 'one two'], False)),
        (2, 8, (['six', 'seven eight', 'one two', 'three four five'], True)),
        # A unit longer than the budget goes on its own.
        (1, 2, (['three four five'], False)),
    ],
)
def test_a_fetch_covers_the_next_units_up_to_the_word_budget(fetched, start, words, expected):
    fetched.load(WORDY, 'en', 'af')

    assert fetched.plan('amagama', WORDY, start, words) == expected


def test_a_translated_unit_failing_a_check_needs_suggestions_too(fetched, path):
    units = [_unit('Open', translated=True), _unit('Close', translated=True), _unit('Quit')]
    fetched.load(units, 'en', 'af')
    fetched.keep('amagama', {'Open': [MATCH], 'Close': []})

    fetched.load(units, 'en', 'af', failing={0})

    assert fetched.matches('amagama', 'Open') == [MATCH]
    assert fetched.matches('amagama', 'Close') is None
    assert fetched.plan('google', units, 0, 100, failing={0}) == (['Open', 'Quit'], True)
    assert fetched.plan('google', units, 0, 100) == (['Quit'], True)


def test_the_word_budget_counts_every_plural_form(fetched):
    from translate.misc.multistring import multistring

    plural = _unit(multistring(['one file', 'many more files']))
    fetched.load([plural], 'en', 'af')

    assert fetched.plan('amagama', [plural, _unit('x')], 0, 5) == (['one file'], False)


def test_kept_suggestions_are_there_on_the_next_open(fetched, path):
    units = [_unit('Open'), _unit('Close')]
    fetched.load(units, 'en', 'af')
    fetched.keep('google_translate', {'Open': [MATCH], 'Close': []})

    again = FetchedSuggestions(path=lambda: path)
    again.load(units, 'en', 'af')

    assert again.matches('google_translate', 'Open') == [MATCH]
    assert again.matches('google_translate', 'Close') == []
    assert again.matches('amagama', 'Open') is None
    again.load(units, 'en', 'de')
    assert again.matches('google_translate', 'Open') is None


def test_opening_a_file_drops_what_its_translated_units_had(fetched, path):
    fetched.load([_unit('Open'), _unit('Close')], 'en', 'af')
    fetched.keep('google_translate', {'Open': [MATCH], 'Close': []})

    fetched.load([_unit('Open', translated=True), _unit('Close')], 'en', 'af')

    assert fetched.matches('google_translate', 'Open') is None
    assert _stored(path, ['Open', 'Close']) == {'google_translate': {'Close': []}}


def test_dropping_services_forgets_their_answers_for_every_pair(fetched, path):
    fetched.load([_unit('Open')], 'en', 'de')
    fetched.keep('amagama', {'Open': []})
    fetched.load([_unit('Open')], 'en', 'af')
    fetched.keep('amagama', {'Open': []})
    fetched.keep('google_translate', {'Open': [MATCH]})
    assert fetched.counts() == {'amagama': 2, 'google_translate': 1}

    fetched.drop(['amagama'])

    assert fetched.counts() == {'google_translate': 1}
    assert fetched.matches('amagama', 'Open') is None
    assert fetched.matches('google_translate', 'Open') == [MATCH]
    assert _stored(path, ['Open']) == {'google_translate': {'Open': [MATCH]}}
    assert _stored(path, ['Open'], target_lang='de') == {}


def test_with_no_usable_tm_file_nothing_is_kept(tmp_path):
    fetched = FetchedSuggestions(path=lambda: str(tmp_path / 'no' / 'such' / 'tm.db'))

    assert fetched.counts() == {}
    fetched.drop(['amagama'])


def test_billed_services_are_told_by_their_backends():
    controller = TMController.__new__(TMController)
    free, paid = (
        SimpleNamespace(internal_name='amagama', billed=False),
        SimpleNamespace(billed=True),
    )
    controller.plugin_controller = SimpleNamespace(
        plugins={'deepl': paid},
        _get_plugin_class=lambda name: {'google_translate': paid}[name],
    )

    billed = controller.billed_services(
        ['amagama', 'deepl', 'google_translate', 'gone'], models=[free]
    )

    # One no longer found is kept unless paid ones are cleared too.
    assert billed == {'deepl', 'google_translate', 'gone'}


def test_a_source_still_to_translate_elsewhere_in_the_file_keeps_them(fetched, path):
    fetched.load([_unit('Open')], 'en', 'af')
    fetched.keep('google_translate', {'Open': [MATCH]})

    fetched.load([_unit('Open', translated=True), _unit('Open')], 'en', 'af')

    assert fetched.matches('google_translate', 'Open') == [MATCH]


def test_translating_a_unit_drops_its_suggestions(fetched, path):
    fetched.load([_unit('Open'), _unit('Close')], 'en', 'af')
    fetched.keep('google_translate', {'Open': [MATCH], 'Close': []})
    fetched.keep('amagama', {'Open': []})

    fetched.forget('Open')

    assert fetched.matches('google_translate', 'Open') is None
    assert fetched.matches('amagama', 'Open') is None
    assert _stored(path, ['Open', 'Close']) == {'google_translate': {'Close': []}}


def test_suggestions_older_than_the_days_kept_go(path, monkeypatch):
    found = FetchedSuggestions(days=30, path=lambda: path)
    found.load([_unit('Open')], 'en', 'af')
    now = time.time()
    monkeypatch.setattr(tmdb.time, 'time', lambda: now - 31 * 86400)
    found.keep('google_translate', {'Open': [MATCH]})
    monkeypatch.setattr(tmdb.time, 'time', lambda: now)

    found.load([_unit('Open')], 'en', 'af')

    assert found.matches('google_translate', 'Open') is None
    found.close()


def test_nothing_is_kept_without_a_file_open(fetched, path):
    fetched.keep('google_translate', {'Open': [MATCH]})
    fetched.forget('Open')

    assert _stored(path, ['Open']) == {}


def test_a_tm_file_that_cannot_be_used_keeps_nothing(tmp_path):
    found = FetchedSuggestions(path=lambda: str(tmp_path / 'missing' / 'tm.db'))

    found.load([_unit('Open')], 'en', 'af')
    found.keep('google_translate', {'Open': [MATCH]})

    # Kept for this session only.
    assert found.matches('google_translate', 'Open') == [MATCH]


def test_the_tm_file_is_the_one_the_local_tm_settings_name(tmp_path, monkeypatch):
    monkeypatch.setattr(fetched_module.pan_app, 'get_config_dir', lambda: str(tmp_path))
    assert fetched_module.tm_file() == str(tmp_path / 'tm.db')

    (tmp_path / 'tm.ini').write_text('[localtm]\ntmdb = other.db\n')

    assert fetched_module.tm_file() == str(tmp_path / 'other.db')


# A backend answers with what was fetched


class _Model(BaseTMModel):
    __gtype_name__ = 'TestFetchedModel'
    PREFETCH_MAX = 0
    BULK_MAX = 10
    default_config = {'prefetch': 0}


def _model(fetched):
    model = _Model.__new__(_Model)
    GObject.GObject.__init__(model)
    model.internal_name = 'google_translate'
    model.controller = SimpleNamespace(fetched=fetched)
    model.queried = []
    model.query = lambda controller, unit: model.queried.append(unit.source)
    model.emitted = []
    model.connect('match-found', lambda m, q, matches: model.emitted.append((q, matches)))
    return model


def test_fetched_suggestions_answer_instead_of_the_service(fetched):
    fetched.load([_unit('Open'), _unit('Close'), _unit('Quit')], 'en', 'af')
    fetched.keep('google_translate', {'Open': [MATCH], 'Close': []})
    model = _model(fetched)

    for source in ('Open', 'Close', 'Quit'):
        model._start_query(None, _unit(source))

    assert model.emitted == [('Open', [MATCH])]
    assert model.emitted[0][1][0] is not fetched.matches('google_translate', 'Open')[0]
    assert model.queried == ['Quit']


def test_pre_translate_looks_up_only_what_was_fetched(fetched):
    fetched.load([_unit('Open'), _unit('Quit')], 'en', 'af')
    fetched.keep('google_translate', {'Open': [MATCH]})
    model = _model(fetched)
    found = []

    for source in ('Open', 'Quit'):
        model.lookup(_unit(source), found.append)

    assert found == [[MATCH], []]
    assert found[0][0] is not fetched.matches('google_translate', 'Open')[0]
    assert model.queried == []


def test_a_model_without_fetched_suggestions_is_queried():
    model = _model(None)

    model._start_query(None, _unit('Open'))

    assert model.queried == ['Open']


# The controller


class _Units:
    # Checked: nothing fails.
    checks = {}

    def __init__(self, units):
        self.units = units

    def get_units(self):
        return self.units


def _controller(fetched, units, index=0):
    controller = TMController.__new__(TMController)
    controller.fetched = fetched
    controller.storecursor = SimpleNamespace(index=index)
    lang = SimpleNamespace(
        source_lang=SimpleNamespace(code='en'), target_lang=SimpleNamespace(code='af')
    )
    controller.main_controller = SimpleNamespace(
        lang_controller=lang,
        store_controller=SimpleNamespace(get_store=lambda: _Units(units)),
    )
    return controller


class _Request:
    def __init__(self):
        self.handlers = {}

    def connect(self, signal, handler):
        self.handlers[signal] = handler


class _BulkModel:
    internal_name = 'google_translate'
    BULK_MAX = None

    def __init__(self):
        self.sent = []

    def bulk_batches(self, sources):
        return [[source] for source in sources]

    def bulk_send(self, batch):
        self.sent.append((batch, _Request()))
        return self.sent[-1][1]

    def bulk_parse(self, batch, reply):
        return {source: [dict(MATCH, source=source)] for source in batch}


def test_a_file_fetch_keeps_what_each_batch_brings(fetched, path):
    units = [_unit('Open'), _unit('Close'), _unit('Quit'), _unit('Done', translated=True)]
    fetched.load(units, 'en', 'af')
    fetched.keep('google_translate', {'Open': []})
    controller = _controller(fetched, units)
    model = _BulkModel()
    progress, done = [], []

    fetch = controller.fetch_for_file(model, progress.append, done.append)
    for _n in range(2):
        model.sent[-1][1].handlers['http-success'](model.sent[-1][1], b'')

    assert [batch for batch, _r in model.sent] == [['Close'], ['Quit']]
    assert progress == [fetch, fetch] and done == [fetch]
    assert _stored(path, ['Close', 'Quit'])['google_translate']['Quit'][0]['source'] == 'Quit'
    assert fetched.matches('google_translate', 'Close')[0]['source'] == 'Close'


def test_a_fetch_can_be_cut_to_a_character_allowance(fetched):
    units = [_unit('Open'), _unit('Close'), _unit('Quit')]
    fetched.load(units, 'en', 'af')
    controller = _controller(fetched, units)
    controller.fetch_words = 1000
    model = _BulkModel()
    model.bulk_characters = lambda sources: sum(len(s) for s in sources)

    assert controller.cap_characters(model, ['Open', 'Close', 'Quit'], 9) == (
        ['Open', 'Close'],
        True,
    )
    assert controller.cap_characters(model, ['Open'], 9) == (['Open'], False)
    controller.fetch_for_file(model, lambda f: None, lambda f: None, characters=4)
    assert [batch for batch, _r in model.sent] == [['Open']]


def test_the_failing_units_are_those_any_check_lists():
    controller = _controller(None, [])
    store = SimpleNamespace(checks={'check-endpunc': [1, 4], 'check-printf': [4, 7]})
    controller.main_controller.store_controller = SimpleNamespace(get_store=lambda: store)

    assert controller.failing_units() == {1, 4, 7}
    store.checks = None
    assert controller.failing_units() == frozenset()


@pytest.mark.parametrize('ceiling, expected', [(None, 1000), (300, 300), (5000, 1000), (0, 0)])
def test_a_backend_may_only_lower_the_word_budget(ceiling, expected):
    controller = _controller(None, [])
    controller.fetch_words = 1000

    assert controller.fetch_budget(SimpleNamespace(BULK_MAX=ceiling)) == expected


def test_a_fetch_plan_starts_at_the_current_unit(fetched):
    fetched.load(WORDY, 'en', 'af')
    controller = _controller(fetched, WORDY, index=2)
    controller.fetch_words = 5
    model = SimpleNamespace(internal_name='amagama', BULK_MAX=None)

    assert controller.fetch_plan(model) == (['six', 'seven eight', 'one two'], False)
    controller.storecursor = SimpleNamespace(index=-1)
    assert controller.fetch_plan(model) == (['one two', 'three four five'], False)
    assert controller.fetch_plan(SimpleNamespace(internal_name='apertium', BULK_MAX=0)) == (
        [],
        False,
    )


def test_the_budget_and_expiry_come_from_tm_ini(tmp_path, monkeypatch):
    from virtaal.common import pan_app

    monkeypatch.setattr(pan_app, 'get_config_dir', lambda: str(tmp_path))
    monkeypatch.setattr('virtaal.plugins.tm.tmcontroller.GLib.idle_add', lambda func: None)
    controller = TMController.__new__(TMController)

    controller._load_fetched()
    assert (controller.fetch_words, controller.fetched.days) == (1000, 7)

    (tmp_path / 'tm.ini').write_text('[suggestions]\nfetch_words = 250\ncache_days = 2\n')
    controller._load_fetched()
    assert (controller.fetch_words, controller.fetched.days) == (250, 2)


def test_turning_a_service_off_stops_its_fetch(fetched):
    units = [_unit('Open'), _unit('Close')]
    fetched.load(units, 'en', 'af')
    controller = _controller(fetched, units)
    controller.fetch_words = 1000
    model, other = _BulkModel(), _BulkModel()
    done = []

    fetch = controller.fetch_for_file(model, lambda f: None, done.append)
    other_fetch = controller.fetch_for_file(other, lambda f: None, done.append)
    controller.stop_fetch(model)
    controller.stop_fetch(object())

    assert fetch.finished == 'cancelled' and other_fetch.finished is None
    assert done == [fetch]
    assert controller._fetches == {other: other_fetch}
    model.sent[-1][1].handlers['http-success'](model.sent[-1][1], b'')
    assert len(model.sent) == 1


def test_confirming_a_translated_unit_drops_its_fetched_suggestions(fetched):
    units = [_unit('Open')]
    fetched.load(units, 'en', 'af')
    fetched.keep('google_translate', {'Open': [MATCH]})
    controller = _controller(fetched, units)

    controller._on_unit_done(None, _unit('Open', translated=True, fuzzy=True), True)
    assert fetched.matches('google_translate', 'Open') == [MATCH]
    controller._on_unit_done(None, _unit('Open', translated=True), True)

    assert fetched.matches('google_translate', 'Open') is None


def test_opening_a_file_loads_its_fetched_suggestions(fetched, path):
    units = [_unit('Open')]
    fetched.load(units, 'en', 'af')
    fetched.keep('google_translate', {'Open': [MATCH]})
    controller = _controller(FetchedSuggestions(path=lambda: path), units)

    controller.load_fetched()

    assert controller.fetched.matches('google_translate', 'Open') == [MATCH]
    controller.fetched.close()


def test_a_unit_with_fetched_suggestions_is_not_preloaded():
    from .test_tmcontroller import _prefetch_controller, _PrefetchModel

    model = _PrefetchModel(2)
    model.fetched_matches = lambda unit: [] if unit.source == 'next' else None
    controller, _asked = _prefetch_controller(model)

    controller._prefetch_upcoming()

    assert model.prefetched == ['after']


def test_a_fetch_walks_on_through_every_file_open_together(tmp_path, fetched):
    from virtaal.models.fileset import FileSet
    from virtaal.models.streammodel import PeekUnit, StreamModel
    from virtaal.models.test_streammodel import _FakeController, _po
    from virtaal.support import statsworker

    _po(tmp_path / 'a.po', [('One', ''), ('Two', 'Twee')])
    _po(tmp_path / 'b.po', [('Three', ''), ('Four', 'Vier'), ('Five six', '')])
    stream = StreamModel(FileSet.from_paths([str(tmp_path)]), _FakeController())
    try:
        units = stream.get_units()
        assert isinstance(units[2], PeekUnit)
        fetched.load(units, 'en', 'af')
        controller = _controller(fetched, units, index=1)
        controller.main_controller.store_controller = SimpleNamespace(get_store=lambda: stream)
        controller.fetch_words = 3
        stream.checks = {'check-simplecaps': [3]}

        assert controller.fetch_plan(SimpleNamespace(internal_name='amagama', BULK_MAX=None)) == (
            ['Three', 'Four'],
            False,
        )
        controller.fetch_words = 10
        assert controller.fetch_plan(SimpleNamespace(internal_name='amagama', BULK_MAX=None)) == (
            ['Three', 'Four', 'Five six', 'One'],
            True,
        )
    finally:
        statsworker.wait()


def test_closing_the_file_stops_the_checks():
    controller = _controller(None, [])
    cancelled = []
    controller._checks = SimpleNamespace(cancel=lambda: cancelled.append(True))
    controller.view = SimpleNamespace(hide=lambda: None)

    controller._on_store_closed(None)

    assert cancelled == [True] and controller._checks is None
