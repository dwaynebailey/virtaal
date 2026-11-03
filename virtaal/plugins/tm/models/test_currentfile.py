#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import weakref
from types import SimpleNamespace

from virtaal.plugins.tm.models.currentfile import TMModel


class _FakeUnit:
    def __init__(self, source, alttrans=None):
        self.source = source
        self._alttrans = alttrans if alttrans is not None else []

    def getalttrans(self):
        return self._alttrans


class _FakeUnitNoAltTrans:
    """No getalttrans attribute at all - some unit types don't have it."""

    def __init__(self, source):
        self.source = source


class _FakeAlt:
    def __init__(self, source, target, xmlelement=None):
        self.source = source
        self.target = target
        if xmlelement is not None:
            self.xmlelement = xmlelement


class _FakeCandidate:
    """A pounit-shaped fake, matching what match.unit2dict() expects."""

    def __init__(self, source, target, quality_pct):
        self.source = source
        self.target = target
        self._quality_pct = quality_pct

    def getnotes(self, origin=None):
        return 'Quality: %d%%' % self._quality_pct

    def getcontext(self):
        return ''


class _FakeMatcher:
    def __init__(self, candidates):
        self._candidates = candidates

    def matches(self, query_str):
        return self._candidates


def _make_model(min_quality=50):
    model = TMModel.__new__(TMModel)
    model.controller = SimpleNamespace(min_quality=min_quality)
    return model


def test_check_alttrans_returns_empty_list_without_getalttrans():
    model = _make_model()
    unit = _FakeUnitNoAltTrans('hello world')

    assert model._check_alttrans(unit) == []


def test_check_alttrans_returns_empty_list_when_no_alternatives():
    model = _make_model()
    unit = _FakeUnit('hello world', alttrans=[])

    assert model._check_alttrans(unit) == []


def test_check_alttrans_filters_out_low_quality_matches():
    model = _make_model(min_quality=50)
    alt = _FakeAlt('completely unrelated text', 'onvverwante teks')
    unit = _FakeUnit('hello world', alttrans=[alt])

    assert model._check_alttrans(unit) == []


def test_check_alttrans_includes_high_quality_matches_with_default_tmsource():
    model = _make_model(min_quality=50)
    alt = _FakeAlt('hello world', 'hallo wêreld')
    unit = _FakeUnit('hello world', alttrans=[alt])

    results = model._check_alttrans(unit)

    assert len(results) == 1
    assert results[0]['source'] == 'hello world'
    assert results[0]['target'] == 'hallo wêreld'
    assert results[0]['tmsource'] == 'This file'
    assert results[0]['quality'] == 100.0


def test_check_alttrans_appends_a_non_lmc_origin_to_tmsource():
    model = _make_model(min_quality=50)
    xmlelement = SimpleNamespace(
        get=lambda key, default='': 'some-tm-name' if key == 'origin' else default
    )
    alt = _FakeAlt('hello world', 'hallo wêreld', xmlelement=xmlelement)
    unit = _FakeUnit('hello world', alttrans=[alt])

    results = model._check_alttrans(unit)

    assert results[0]['tmsource'] == 'This file\nsome-tm-name'


def test_check_alttrans_expands_lmc_origin_metadata():
    class _FakePI:
        def __init__(self, target, text):
            self.target = target
            self.text = text

    pis = [
        _FakePI('contact-name', 'Jane Translator'),
        _FakePI('category', 'legal'),
        _FakePI('original', '/path/to/source.odt'),
    ]
    xmlelement = SimpleNamespace(
        get=lambda key, default='': 'lmc' if key == 'origin' else default,
        xpath=lambda expr: pis,
    )
    alt = _FakeAlt('hello world', 'hallo wêreld', xmlelement=xmlelement)
    unit = _FakeUnit('hello world', alttrans=[alt])
    model = _make_model(min_quality=50)

    results = model._check_alttrans(unit)

    # os.path.splitext() only strips the extension, not the directory.
    assert results[0]['tmsource'] == 'Jane Translator\nlegal\n/path/to/source\nlmc'


def test_check_other_units_excludes_a_100_percent_match_from_the_unit_itself():
    # Showing a unit its own translation back would be redundant noise.
    model = _make_model()
    unit = _FakeUnit('hello world')
    candidate = _FakeCandidate('hello world', 'hallo wêreld', 100)
    candidate.unit_ref = weakref.ref(unit)
    model.matcher = _FakeMatcher([candidate])

    assert model._check_other_units(unit) == []


def test_check_other_units_includes_non_100_percent_matches():
    model = _make_model()
    model.matcher = _FakeMatcher([_FakeCandidate('hello world', 'hallo wêreld', 85)])

    results = model._check_other_units(_FakeUnit('hello world'))

    assert len(results) == 1
    assert results[0]['quality'] == '85'
    assert results[0]['tmsource'] == 'This file'


class _FakeExtendableMatcher:
    def __init__(self, start, stop):
        self.extended_with = []
        self._start = start
        self._stop = stop

    def extendtm(self, unit):
        self.extended_with.append(unit)

    def getstartlength(self, min_similarity, text):
        return self._start

    def getstoplength(self, min_similarity, text):
        return self._stop


def test_on_unit_modified_ignores_an_unmodified_unit():
    model = _make_model()
    model.matcher = _FakeExtendableMatcher(start=0, stop=100)
    model.cache = {'anything': ['cached']}

    model._on_unit_modified(None, _FakeUnit('x'), modified=False)

    assert model.matcher.extended_with == []
    assert model.cache == {'anything': ['cached']}


def test_on_unit_modified_extends_the_matcher_for_a_translated_unit():
    model = _make_model()
    model.matcher = _FakeExtendableMatcher(start=0, stop=100)
    model.cache = {}
    new_unit = SimpleNamespace(source='hello', istranslated=lambda: True)

    model._on_unit_modified(None, new_unit, modified=True)

    assert model.matcher.extended_with == [new_unit]


def test_on_unit_modified_prunes_only_cached_keys_within_the_affected_length_range():
    """A newly (re)translated unit could change TM suggestions for any
    other cached source string of a similar length - only those
    need to be invalidated, not the whole cache."""
    model = _make_model()
    model.matcher = _FakeExtendableMatcher(start=3, stop=6)
    model.cache = {
        'ab': ['too short, unaffected'],
        'abcd': ['within range, must be evicted'],
        'abcdefgh': ['too long, unaffected'],
    }
    new_unit = SimpleNamespace(source='hello', istranslated=lambda: True)

    model._on_unit_modified(None, new_unit, modified=True)

    assert set(model.cache.keys()) == {'ab', 'abcdefgh'}


def test_check_alttrans_falls_back_to_default_tmsource_if_lmc_parsing_raises():
    xmlelement = SimpleNamespace(
        get=lambda key, default='': 'lmc' if key == 'origin' else default,
        xpath=lambda expr: (_ for _ in ()).throw(RuntimeError('malformed XML')),
    )
    alt = _FakeAlt('hello world', 'hallo wêreld', xmlelement=xmlelement)
    unit = _FakeUnit('hello world', alttrans=[alt])
    model = _make_model(min_quality=50)

    results = model._check_alttrans(unit)

    # The lmc-specific enrichment failed, but the plain "\n" + origin
    # append happens unconditionally afterwards regardless.
    assert results[0]['tmsource'] == 'This file\nlmc'


def test_query_does_not_leak_alttrans_between_units_with_the_same_source():
    # Units sharing a source share a cache entry, but each has its own alt-trans.
    model = _make_model(min_quality=50)
    model.cache = {}
    model.matcher = _FakeMatcher([_FakeCandidate('Open file', 'Maak lêer oop', 80)])
    emitted = []
    model.emit = lambda signal, query_str, matches: emitted.append([m['target'] for m in matches])
    unit_a = _FakeUnit('Open', alttrans=[_FakeAlt('Open', 'A')])
    unit_b = _FakeUnit('Open', alttrans=[_FakeAlt('Open', 'B')])

    model.query(None, unit_a)
    model.query(None, unit_b)
    model.query(None, unit_a)

    assert emitted == [
        ['Maak lêer oop', 'A'],
        ['Maak lêer oop', 'B'],
        ['Maak lêer oop', 'A'],
    ]
    assert [m['target'] for m, _ref in model.cache['Open']] == ['Maak lêer oop']


def _po_store(entries):
    from translate.storage import po

    body = ''.join(
        ('msgctxt "%s"\n' % ctx if ctx is not None else '')
        + 'msgid "%s"\nmsgstr "%s"\n\n' % (source, target)
        for ctx, source, target in entries
    )
    return po.pofile(body.encode('utf-8'))


def _model_for(store):
    model = _make_model(min_quality=75)
    model.controller.max_matches = 10
    model.config = {'max_length': 1000}
    model.matcher = None
    emitted = []
    model.emit = lambda signal, query_str, matches: emitted.append(
        [(m['target'], int(m['quality'])) for m in matches]
    )
    storecontroller = SimpleNamespace(get_store=lambda: SimpleNamespace(_trans_store=store))
    model.recreate_matcher(storecontroller)
    return model, emitted


def _offered(model, emitted, unit):
    emitted.clear()
    model.query(None, unit)
    return emitted[0] if emitted else []


def test_query_offers_a_100_percent_match_from_another_unit_with_the_same_source():
    store = _po_store([(None, 'Open', 'Maak oop'), (None, 'Open', '')])
    model, emitted = _model_for(store)

    assert _offered(model, emitted, store.units[1]) == [('Maak oop', 100)]


def test_query_does_not_offer_a_unit_its_own_translation():
    store = _po_store([(None, 'Open', 'Maak oop'), (None, 'Open', '')])
    model, emitted = _model_for(store)

    assert _offered(model, emitted, store.units[0]) == []


def test_query_filters_a_shared_cache_entry_per_unit():
    # Same-source units share a cache entry; the filter is per unit.
    store = _po_store([(None, 'Open', 'Maak oop'), (None, 'Open', '')])
    model, emitted = _model_for(store)
    own, other = store.units

    assert _offered(model, emitted, own) == []
    assert _offered(model, emitted, other) == [('Maak oop', 100)]
    assert _offered(model, emitted, own) == []


def test_query_does_not_offer_a_100_percent_match_with_a_different_msgctxt():
    store = _po_store([('verb', 'Open', 'Maak oop'), ('adjective', 'Open', '')])
    model, emitted = _model_for(store)

    assert _offered(model, emitted, store.units[1]) == []


def test_query_offers_a_100_percent_match_with_the_same_msgctxt():
    store = _po_store([('verb', 'Open', 'Maak oop'), ('verb', 'Open', '')])
    model, emitted = _model_for(store)

    assert _offered(model, emitted, store.units[1]) == [('Maak oop', 100)]


def test_query_offers_a_100_percent_match_when_only_one_unit_has_a_msgctxt():
    store = _po_store(
        [
            ('verb', 'Open', 'Maak oop'),
            (None, 'Open', ''),
            (None, 'Save', 'Stoor'),
            ('menu', 'Save', ''),
        ]
    )
    model, emitted = _model_for(store)

    assert _offered(model, emitted, store.units[1]) == [('Maak oop', 100)]
    assert _offered(model, emitted, store.units[3]) == [('Stoor', 100)]


def test_query_still_offers_below_100_percent_matches():
    store = _po_store(
        [('verb', 'Open the file', 'Maak die lêer oop'), ('noun', 'Open the files', '')]
    )
    model, emitted = _model_for(store)

    assert _offered(model, emitted, store.units[1]) == [('Maak die lêer oop', 93)]


def test_query_offers_a_translation_entered_in_another_unit_after_loading():
    store = _po_store([(None, 'Open', ''), (None, 'Open', '')])
    model, emitted = _model_for(store)
    first, second = store.units
    assert _offered(model, emitted, second) == []

    first.target = 'Maak oop'
    model._on_unit_modified(None, first, modified=True)

    assert _offered(model, emitted, second) == [('Maak oop', 100)]
    assert _offered(model, emitted, first) == []


def test_query_keeps_excluding_own_translation_after_the_controller_coerces_quality():
    # accept_response() turns the cached matches' quality into an int in place.
    store = _po_store([(None, 'Open', 'Maak oop'), (None, 'Open', '')])
    model, emitted = _model_for(store)
    own, other = store.units

    def accept(signal, query_str, matches):
        for m in matches:
            m['quality'] = int(m['quality'])

    model.emit = accept
    model.query(None, other)
    model.emit = lambda signal, query_str, matches: emitted.append(
        [(m['target'], int(m['quality'])) for m in matches]
    )

    assert _offered(model, emitted, own) == []
