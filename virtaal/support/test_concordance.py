#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest

from virtaal.support import abstraction, concordance, textfold
from virtaal.support.concordance import Query


def _spans(query, text):
    hit = query.find(text)
    return None if hit is None else [text[start:end] for start, end in hit.spans]


@pytest.mark.parametrize(
    'text',
    ['Open &File', 'Save %s...', '<b>Hi</b> there', 'Open F&ile', 'Visit https://x.org.', 'xy'],
)
def test_abstract_offsets_cover_the_abstract(text):
    found, offsets = abstraction.abstract_offsets(text)

    assert found == abstraction.abstract(text)[0]
    assert len(offsets) == len(found)
    assert offsets[0][0] == 0
    assert offsets[-1][1] == len(text)


def test_every_hit_is_found_ignoring_case():
    assert _spans(Query('file'), 'File, file and FILE') == ['File', 'file', 'FILE']


def test_case_sensitive_finds_the_case_given_only():
    assert _spans(Query('File', case=True), 'file File') == ['File']


def test_non_ascii_letters_fold_too():
    assert _spans(Query('ÉCOLE'), "L'école") == ['école']
    assert _spans(Query('straße'), 'STRASSE') == ['STRASSE']


def test_composed_and_decomposed_letters_match():
    assert _spans(Query('café'), 'Le café noir') == ['café']
    assert _spans(Query('café'), 'Café') == ['Café']


def test_a_hit_never_ends_inside_a_letter():
    assert Query('cafe').find('café') is None
    assert Query('cafe').find('café') is None


def test_a_mark_alone_is_found_in_its_letter():
    assert _spans(Query('́'), 'café') == ['é']


def test_whole_words_leaves_out_part_of_a_word():
    query = Query('file', whole_words=True)

    assert _spans(query, 'files and file') == ['file']
    assert query.find('profile') is None


def test_a_hit_says_whether_it_is_whole_words_and_in_its_case():
    assert Query('file').find('Profile') == concordance.Hit(((3, 7),), False, True)
    assert Query('file').find('A File') == concordance.Hit(((2, 6),), True, False)


def test_an_accelerator_doesn_t_hide_a_phrase():
    assert _spans(Query('Open File'), 'Open &File now') == ['Open &File']
    assert _spans(Query('Open File'), 'Open F_ile') == ['Open F_ile']


def test_a_placeable_matches_another_of_its_kind():
    assert _spans(Query('%s files'), 'Delete %d files') == ['%d files']
    assert _spans(Query('{name} files'), 'Delete %(count)s files') == ['%(count)s files']


def test_an_ellipsis_matches_three_dots():
    assert _spans(Query('Save…'), 'Save...') == ['Save...']


def test_maybe_is_false_only_where_find_finds_nothing():
    texts = ['Open &File', 'Delete %d files', 'café', 'nothing here', 'Café']
    for phrase in ('open file', '%s files', 'café', '́', 'here'):
        query = Query(phrase)
        for text in texts:
            if query.find(text) is not None:
                assert query.maybe(text), (phrase, text)
    assert not Query('open file').maybe('nothing here')


def test_like_patterns_escape_their_wildcards():
    assert concordance.like_escape('100%_a\\b') == '100\\%\\_a\\\\b'
    assert Query('Save %s now%').loose_like() == '%Save % now\\%%'


def test_match_hits_takes_the_first_form_holding_the_phrase():
    found = concordance.match_hits(
        Query('lêers'), ('target',), {'target': ['Een lêer', '%d lêers']}
    )

    assert found['target'][:2] == ('%d lêers', 1)


def test_the_other_side_shows_the_form_going_with_the_one_found():
    texts = {'source': ['One file', '%d files'], 'target': ['Een lêer', '%d lêers']}
    query = Query('%s files')

    match = concordance.describe(
        {'source': 'One file', 'target': 'Een lêer'},
        concordance.match_hits(query, ('source',), texts),
        texts,
    )

    assert (concordance.shown(match, 'source'), concordance.shown(match, 'target')) == (
        '%d files',
        '%d lêers',
    )


def _match(source, target, **fields):
    return {'source': source, 'target': target, **fields}


def test_identical_pairs_merge_with_their_count_and_origins():
    merged = concordance.merge(
        [
            _match('Open', 'Maak oop', origins=['a.po'], time=1),
            _match('Open', 'Open', origins=['b.po'], time=2),
            _match('Open', 'Maak oop', origins=['c.po', 'a.po'], time=3, whole=True),
        ]
    )

    assert [(m['target'], m['count'], m['origins'], m['time']) for m in merged] == [
        ('Maak oop', 2, ['a.po', 'c.po'], 3),
        ('Open', 1, ['b.po'], 2),
    ]
    assert merged[0]['whole']


def test_a_merged_pair_keeps_what_only_a_later_one_has():
    (merged,) = concordance.merge(
        [_match('Open', 'Maak oop', origins=[]), _match('Open', 'Maak oop', index=4, first=True)]
    )

    assert merged['index'] == 4
    assert merged['first']


def test_ranking_puts_whole_words_case_and_short_sources_first():
    matches = [
        _match('Open the profile', 'x', whole=False, exact=True),
        _match('Open the File', 'x', whole=True, exact=False),
        _match('Open the file now', 'x', whole=True, exact=True),
        _match('The file', 'x', whole=True, exact=True),
        _match('The file', 'y', whole=True, exact=True, count=3),
        _match('The file', 'z', whole=True, exact=True, time=5),
    ]

    ranked = [(m['source'], m['target']) for m in concordance.rank(matches)]

    assert ranked == [
        ('The file', 'y'),
        ('The file', 'z'),
        ('The file', 'x'),
        ('Open the file now', 'x'),
        ('Open the File', 'x'),
        ('Open the profile', 'x'),
    ]


def test_a_source_listed_first_ranks_above_better_hits():
    first = _match('Open the profile', 'x', first=True)
    other = _match('File', 'x', whole=True, exact=True)

    assert concordance.rank([other, first]) == [first, other]


def test_cf_folds_compatibility_forms_and_case():
    assert concordance.cf('ﬁle Straße') == 'file strasse'
    assert concordance.cf(None) is None


@pytest.mark.parametrize(
    ('phrase', 'text', 'lang', 'spans'),
    [
        ('KAYIT', 'Yeni kayıt ekle', 'tr', ['kayıt']),
        ('iptal', 'İPTAL ET', 'tr', ['İPTAL']),
        ('KAYIT', 'Yeni kayıt ekle', 'en', []),
        ('info', 'INFO', 'tr', []),
        ('ΑΡΧΕΙΟ', 'Νέο αρχείο', 'el', ['αρχείο']),
        ('αρχείο', 'ΝΕΟ ΑΡΧΕΙΟ', 'el', ['ΑΡΧΕΙΟ']),
        ('αρχειο', 'Νέο αρχείο', 'el', []),
        ('ΑΡΧΕΙΟ', 'Νέο αρχείο', 'en', []),
        ('OK', 'Press ＯＫ', 'en', ['ＯＫ']),
    ],
)
def test_a_phrase_is_found_as_the_tm_folds_its_language(phrase, text, lang, spans):
    query = Query(phrase, langs={'source': lang})
    found = concordance.match_hits(query, ('source',), {'source': [text]})

    hit = found.get('source', (None, None, None))[2]
    assert [text[a:b] for a, b in (hit.spans if hit else ())] == spans


def test_a_side_is_folded_in_its_own_language():
    query = Query('INFO', langs={'source': 'en', 'target': 'tr'})
    texts = {'source': ['More info'], 'target': ['Daha fazla info']}

    assert set(concordance.match_hits(query, ('source', 'target'), texts)) == {'source'}


@pytest.mark.parametrize(
    ('phrase', 'text', 'lang'),
    [('KAYIT', 'kayıt', 'tr'), ('ΑΡΧΕΙΟ', 'αρχείο', 'el'), ('STRASSE', 'Straße', 'de')],
)
def test_maybe_lets_through_what_find_finds(phrase, text, lang):
    query = Query(phrase, langs={'source': lang})

    assert query.find(text, lang) and query.maybe(text)


@pytest.mark.parametrize(
    ('phrase', 'langs', 'plain'),
    [
        ('file', {}, True),
        ('Open file', {'source': 'tr'}, False),
        ('Open', {'source': 'tr'}, True),
        ('STRASSE', {}, False),
        ('café', {}, False),
    ],
)
def test_is_plain(phrase, langs, plain):
    assert Query(phrase, langs=langs).is_plain() == plain


@pytest.mark.parametrize(
    ('stored', 'phrase', 'lang'),
    [
        ('Open file', 'OPEN FILE', 'en'),
        ('Straße', 'STRASSE', 'de'),
        ('café', 'CAFÉ', 'fr'),
        ('Kayıt', 'KAYIT', 'tr'),
        ('Αρχείο', 'ΑΡΧΕΙΟ', 'el'),
    ],
)
def test_concordance_finds_what_the_tm_matches_past_case(stored, phrase, lang):
    assert textfold.case_only_difference(stored, phrase, lang)
    assert Query(phrase, langs={'source': lang}).find(stored, lang) is not None
