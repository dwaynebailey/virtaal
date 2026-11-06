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

    def forget(self, unit):
        pass

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


def _edit(model, unit, target):
    unit.target = target
    model._on_unit_modified(None, unit, modified=True)


def _reopen(model, store):
    model.recreate_matcher(SimpleNamespace(get_store=lambda: SimpleNamespace(_trans_store=store)))


def test_query_offers_only_the_latest_version_of_an_edited_unit():
    # #1266: each interim edit stayed in the matcher.
    store = _po_store([(None, 'Open', ''), (None, 'Open', '')])
    model, emitted = _model_for(store)
    edited, other = store.units

    _edit(model, edited, 'Maak oop')
    _edit(model, edited, 'Open maak')

    assert _offered(model, emitted, other) == [('Open maak', 100)]


def test_query_does_not_offer_a_unit_its_old_versions():
    store = _po_store([(None, 'Open the file', 'Maak die lêer oop')])
    model, emitted = _model_for(store)
    unit = store.units[0]

    _edit(model, unit, 'Open die lêer')

    assert _offered(model, emitted, _po_store([(None, 'Open the files', '')]).units[0]) == [
        ('Open die lêer', 93)
    ]
    assert _offered(model, emitted, unit) == []


def test_query_offers_a_reverted_version_once():
    store = _po_store([(None, 'Open', 'Maak oop'), (None, 'Open', '')])
    model, emitted = _model_for(store)
    edited, other = store.units

    _edit(model, edited, 'Open maak')
    _edit(model, edited, 'Maak oop')

    assert _offered(model, emitted, other) == [('Maak oop', 100)]


def test_query_drops_the_version_of_a_unit_whose_translation_is_removed():
    store = _po_store([(None, 'Open', 'Maak oop'), (None, 'Open', '')])
    model, emitted = _model_for(store)
    edited, other = store.units

    _edit(model, edited, '')

    assert _offered(model, emitted, other) == []


def test_editing_one_of_two_units_with_the_same_translation_keeps_the_other():
    store = _po_store([(None, 'Open', 'Maak oop'), (None, 'Open', 'Maak oop'), (None, 'Open', '')])
    model, emitted = _model_for(store)
    edited, kept, empty = store.units

    _edit(model, edited, 'Open maak')

    assert sorted(_offered(model, emitted, empty)) == [('Maak oop', 100), ('Open maak', 100)]
    # What's left of the shared candidate is now kept's own translation.
    assert _offered(model, emitted, kept) == [('Open maak', 100)]
    assert _offered(model, emitted, edited) == [('Maak oop', 100)]


def test_reopening_a_file_after_edits_offers_only_the_saved_version():
    store = _po_store([(None, 'Open', ''), (None, 'Open', '')])
    model, emitted = _model_for(store)
    _edit(model, store.units[0], 'Maak oop')
    _edit(model, store.units[0], 'Open maak')
    del store

    reopened = _po_store([(None, 'Open', 'Open maak'), (None, 'Open', '')])
    _reopen(model, reopened)
    _edit(model, reopened.units[0], 'Maak oop')

    assert _offered(model, emitted, reopened.units[1]) == [('Maak oop', 100)]


def test_reopening_keeps_translations_from_a_previous_file():
    # Deliberate since 04056a02: the matcher remembers earlier files.
    model, emitted = _model_for(_po_store([(None, 'Save', 'Stoor')]))

    reopened = _po_store([(None, 'Save', '')])
    _reopen(model, reopened)

    assert _offered(model, emitted, reopened.units[0]) == [('Stoor', 100)]


# Other plural forms (#874) #


def _plural_unit(targets, sources=('%d file', '%d files')):
    from translate.storage import po

    body = 'msgid ""\nmsgstr "Plural-Forms: nplurals=%d; plural=n;\\n"\n\n' % len(targets)
    body += 'msgid "%s"\nmsgid_plural "%s"\n' % sources
    body += ''.join('msgstr[%d] "%s"\n' % (i, t) for i, t in enumerate(targets))
    return po.pofile(body.encode('utf-8')).units[1]


def _forms(model, unit, target_n):
    return [
        (m['source'], m['target'], m['quality'], m['tmsource'])
        for m in model._check_plural_forms(unit, target_n)
    ]


def test_check_plural_forms_offers_an_earlier_form_for_a_later_one():
    unit = _plural_unit(['%d lêer', ''])

    assert _forms(_make_model(), unit, 1) == [('%d file', '%d lêer', 87, 'This file')]


def test_check_plural_forms_offers_a_later_form_for_an_earlier_one():
    unit = _plural_unit(['', '%d lêers'])

    assert _forms(_make_model(), unit, 0) == [('%d files', '%d lêers', 87, 'This file')]


def test_check_plural_forms_scores_forms_with_the_same_source_below_100():
    # Forms 1 and 2 both translate msgid_plural.
    unit = _plural_unit(['%d soubor', '%d soubory', ''])

    assert [(t, q) for _s, t, q, _src in _forms(_make_model(), unit, 2)] == [
        ('%d soubor', 87),
        ('%d soubory', 99),
    ]


def test_check_plural_forms_skips_empty_forms_and_the_forms_own_text():
    unit = _plural_unit(['%d soubor', '', '%d soubor'])

    assert _forms(_make_model(), unit, 2) == []
    assert _forms(_make_model(), unit, 1) == [('%d file', '%d soubor', 87, 'This file')]


def test_check_plural_forms_offers_nothing_for_a_singular_unit():
    unit = _po_store([(None, 'Open', 'Maak oop')]).units[0]

    assert _forms(_make_model(), unit, 0) == []


def test_check_plural_forms_offers_nothing_when_no_other_form_is_filled():
    assert _forms(_make_model(), _plural_unit(['', '']), 1) == []


def test_query_offers_the_other_forms_of_the_form_being_edited():
    model, emitted = _model_for(_po_store([(None, 'Open', 'Maak oop')]))
    unit = _plural_unit(['%d lêer', ''])

    emitted.clear()
    model.query(SimpleNamespace(target_n=1), unit)

    assert emitted == [[('%d lêer', 87)]]


class _CountingList(list):
    iterations = 0

    def __iter__(self):
        _CountingList.iterations += 1
        return super().__iter__()


def test_editing_a_unit_finds_its_candidate_without_a_scan():
    # In a large file, a scan of every candidate per edit is slow.
    store = _po_store([(None, 'Source %d' % n, 'Bron %d' % n) for n in range(500)])
    store.addunit(store.UnitClass('Source 7'))
    model, emitted = _model_for(store)
    _edit(model, store.units[-1], 'Bron 7')
    model.matcher.candidates.units = _CountingList(model.matcher.candidates.units)
    _CountingList.iterations = 0

    _edit(model, store.units[7], 'Nuwe bron')

    assert _CountingList.iterations == 0
    assert ('Nuwe bron', 100) in _offered(model, emitted, store.units[-1])
