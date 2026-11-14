#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""TMDB.concordance: finding a phrase in the local TM."""

import pytest

from virtaal.support import concordance, tmdb

APP = '/files/app.po'


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'tm.db')
    yield tmdb.TMDB(path)
    for connection, _cursor in tmdb.TMDB._tm_dbs.pop(path, {}).values():
        connection.close()


def _unit(source, target, key=None, **fields):
    return {'source': source, 'target': target, 'context': None, 'key': key or source, **fields}


def _add(db, pairs, path=APP, source_lang='en', target_lang='af'):
    db.sync_store(path, [_unit(*pair) for pair in pairs], source_lang, target_lang)


def _found(db, phrase, **options):
    result = db.concordance(phrase, 'en', 'af', **options)
    return [(m['source'], concordance.shown(m, 'target')) for m in result['matches']]


def test_a_phrase_is_found_in_the_sources_ignoring_case(db):
    _add(db, [('Open the file', 'Maak die lêer oop'), ('Close', 'Sluit')])

    assert _found(db, 'THE FILE') == [('Open the file', 'Maak die lêer oop')]


def test_a_phrase_with_other_letters_is_found_ignoring_case(db):
    _add(db, [('Élan', 'Gees'), ('Café crème', 'Koffie'), ('Cafe', 'Kafee')])

    assert _found(db, 'CAFÉ') == [('Café crème', 'Koffie')]
    assert _found(db, 'élan') == [('Élan', 'Gees')]


def test_a_decomposed_entry_is_found_by_a_composed_phrase(db):
    _add(db, [('Le café', 'Die koffie'), ('cafeteria', 'kafeteria')])

    assert _found(db, 'café') == [('Le café', 'Die koffie')]
    assert _found(db, 'cafe') == [('cafeteria', 'kafeteria')]


def test_case_sensitive_finds_the_case_given_only(db):
    _add(db, [('File', 'Lêer'), ('file', 'lêer')])

    assert _found(db, 'File', case=True) == [('File', 'Lêer')]


@pytest.mark.parametrize('phrase', ['100%', 'a_b', 'back\\slash'])
def test_like_wildcards_in_the_phrase_are_plain_text(db, phrase):
    _add(db, [(phrase + ' here', 'x'), ('100 percent, aXb, backXslash', 'y')])

    assert _found(db, phrase) == [(phrase + ' here', 'x')]


def test_accelerators_and_placeables_don_t_hide_a_phrase(db):
    _add(db, [('Open &File', 'Maak &lêer oop'), ('Delete %d files', 'Skrap %d lêers')])

    assert _found(db, 'Open File') == [('Open &File', 'Maak &lêer oop')]
    assert _found(db, '%s files') == [('Delete %d files', 'Skrap %d lêers')]


def test_three_dots_find_an_ellipsis(db):
    _add(db, [('Save…', 'Stoor…'), ('Save as', 'Stoor as')])

    assert _found(db, 'save...') == [('Save…', 'Stoor…')]


def test_a_phrase_with_other_letters_is_found_in_the_translations_and_their_forms(db):
    db.sync_store(
        APP,
        [
            _unit('Close', 'Schließen'),
            _unit(
                'One file',
                'Eine Datei',
                plural_source='%d files',
                plural_targets=['Eine Datei', '%d Dateien schließen'],
            ),
        ],
        'en',
        'af',
    )

    assert sorted(_found(db, 'SCHLIEßEN', side='target')) == [
        ('Close', 'Schließen'),
        ('One file', '%d Dateien schließen'),
    ]


def test_an_entry_without_its_abstract_yet_is_found_past_its_accelerator(db):
    _add(db, [('Open &File', 'Maak &lêer oop'), ('Delete %d files', 'Skrap %d lêers')])
    db.cursor.execute('UPDATE sources SET abstract = NULL')
    db.cursor.execute('UPDATE targets SET abstract = NULL')
    db.connection.commit()

    assert _found(db, 'open file') == [('Open &File', 'Maak &lêer oop')]
    assert _found(db, '%s files') == [('Delete %d files', 'Skrap %d lêers')]


def test_the_translation_side_finds_the_phrase_in_any_plural_form(db):
    db.sync_store(
        APP,
        [
            _unit(
                'One file',
                'Een lêer',
                plural_source='%d files',
                plural_targets=['Een lêer', '%d lêers'],
            ),
            _unit('Close', 'Sluit'),
        ],
        'en',
        'af',
    )

    result = db.concordance('lêers', 'en', 'af', side='target')

    (match,) = result['matches']
    assert concordance.shown(match, 'target') == '%d lêers'
    assert match['hits'] == {'target': [[3, 8]]}
    assert match['plural_targets'] == ['Een lêer', '%d lêers']
    assert _found(db, 'lêers') == []
    assert _found(db, '%s files') == [('One file', '%d lêers')]


def test_both_sides_find_either(db):
    _add(db, [('Save', 'Stoor'), ('Stoop', 'Stoep'), ('Close', 'Sluit')])

    assert sorted(_found(db, 'stoo', side='both')) == [('Save', 'Stoor'), ('Stoop', 'Stoep')]


def test_whole_words_leaves_out_part_of_a_word(db):
    _add(db, [('Profile', 'Profiel'), ('A file', "'n Lêer")])

    assert _found(db, 'file', whole_words=True) == [('A file', "'n Lêer")]


def test_whole_words_rank_first_then_shorter_sources(db):
    _add(db, [('Profile', 'Profiel'), ('Open the file now', 'a'), ('The file', 'b')])

    assert _found(db, 'file') == [
        ('The file', 'b'),
        ('Open the file now', 'a'),
        ('Profile', 'Profiel'),
    ]


def test_the_limit_cuts_the_list_and_the_total_counts_all(db):
    _add(db, [('File %d' % n, 'Lêer %d' % n) for n in range(5)])

    result = db.concordance('file', 'en', 'af', limit=2)

    assert len(result['matches']) == 2
    assert result['total'] == 5
    assert result['complete']


def test_more_candidates_than_are_read_isnt_complete(db, monkeypatch):
    monkeypatch.setattr(tmdb, 'CONCORDANCE_CANDIDATES', 3)
    _add(db, [('File %d' % n, 'Lêer %d' % n) for n in range(5)])

    result = db.concordance('file', 'en', 'af')

    assert len(result['matches']) == 3
    assert not result['complete']


def test_a_match_has_the_paths_of_the_files_it_came_from(db):
    _add(db, [('Open', 'Maak oop')], path='/files/a.po')
    _add(db, [('Open', 'Maak oop')], path='/files/b.po')

    (match,) = db.concordance('open', 'en', 'af')['matches']

    assert sorted(match['origins']) == ['/files/a.po', '/files/b.po']
    assert 'tids' not in match


def test_an_excluded_file_s_entries_are_not_found(db):
    _add(db, [('Open', 'Maak oop')], path='/files/a.po')
    db.set_exclude('/files/a.po', True)

    assert _found(db, 'open') == []


def test_only_the_language_pair_asked_for_is_searched(db):
    _add(db, [('Open', 'Maak oop')])
    _add(db, [('Open', 'Öffnen')], path='/files/de.po', target_lang='de')

    assert _found(db, 'open') == [('Open', 'Maak oop')]


def test_a_phrase_too_short_finds_nothing(db):
    _add(db, [('a', 'x')])

    assert db.concordance('a', 'en', 'af') == {'matches': [], 'total': 0, 'complete': True}


def test_the_search_works_without_the_fulltext_index(db):
    _add(db, [('Open the selected translation file', 'Maak die gekose lêer oop')])
    db.fulltext = False

    assert _found(db, 'selected translation') == [
        ('Open the selected translation file', 'Maak die gekose lêer oop')
    ]


@pytest.mark.parametrize('side', ['source', 'target', 'both'])
@pytest.mark.parametrize('phrase', ['file', 'lêer', 'Open &File'])
def test_the_query_never_scans_the_fulltext_index(db, side, phrase):
    """A MATCH on fulltext as a JOIN ran once per target: 252 s on a
    200,000-unit file."""
    query = concordance.Query(phrase)
    sql, params = tmdb.concordance_sql(query, concordance.SIDES[side], 'en', 'af')
    db.connection.create_function('cf', 1, concordance.cf, deterministic=True)

    plan = db.cursor.execute('EXPLAIN QUERY PLAN ' + sql, (*params, 10)).fetchall()

    details = ' '.join(row[-1] for row in plan)
    assert 'fulltext' not in details
    assert 'VIRTUAL TABLE' not in details
