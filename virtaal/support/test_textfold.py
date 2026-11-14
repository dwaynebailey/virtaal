#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest

from virtaal.support import textfold


@pytest.mark.parametrize(
    ('text', 'lang', 'folded'),
    [
        ('OPEN FILE', 'en', 'open file'),
        ('Straße', 'de', 'strasse'),
        ('STRASSE', 'de', 'strasse'),
        ('CAFÉ', 'fr', 'café'),
        ('café', 'fr', 'café'),
        ('ΤΕΛΟΣ', 'el', 'τελοσ'),
        ('ＯＰＥＮ １２', 'ja', 'open 12'),
        ('ǅungla', 'hr', 'džungla'),
        ('ﬁle', 'en', 'file'),
        ('KAYIT İPTAL', 'tr', 'kayıt iptal'),
        ('KAYIT İPTAL', 'az', 'kayıt iptal'),
        ('QIRIM', 'crh', 'qırım'),
        ('KAYIT', 'en', 'kayit'),
        ('QAZAQ TILI', 'kk_Latn', 'qazaq tılı'),
        ('INFO', 'kk', 'info'),
        ('INFO', 'az_Cyrl', 'info'),
        ('KAYIT', None, 'kayit'),
    ],
)
def test_fold(text, lang, folded):
    assert textfold.fold(text, lang) == folded


@pytest.mark.parametrize(
    ('lang', 'rule'),
    [
        ('tr', textfold.TURKIC_I),
        ('tr_TR', textfold.TURKIC_I),
        ('az', textfold.TURKIC_I),
        ('az-Cyrl', None),
        ('crh', textfold.TURKIC_I),
        ('gag', textfold.TURKIC_I),
        ('kk', None),
        ('kk_Latn', textfold.TURKIC_I),
        ('tt@latin', textfold.TURKIC_I),
        ('el', textfold.GREEK_CAPITALS),
        ('grc', textfold.GREEK_CAPITALS),
        (['el', 'en'], textfold.GREEK_CAPITALS),
        ('en', None),
        ('', None),
        (None, None),
    ],
)
def test_rule(lang, rule):
    assert textfold.rule(lang) == rule


@pytest.mark.parametrize(
    ('a', 'b', 'lang', 'same'),
    [
        ('Αρχείο', 'ΑΡΧΕΙΟ', 'el', True),
        ('ΑΡΧΕΙΟ', 'Αρχείο', 'el', True),
        ('Αρχείο', 'αρχειο', 'el', False),
        ('Αρχείο', 'ΑΡΧΕΙΟ', 'en', False),
        ('café', 'cafe', 'fr', False),
        ('CAFÉ', 'CAFE', 'fr', False),
        ('Open file', 'OPEN FILE', 'en', True),
        ('Open file', 'Open file', 'en', False),
        ('İptal', 'iptal', 'tr', True),
        ('Iptal', 'iptal', 'tr', False),
    ],
)
def test_case_only_difference(a, b, lang, same):
    assert textfold.case_only_difference(a, b, lang) == same


# The Local TM scored these 39%, 39%, 39%, 71% and 89% (#4798).
@pytest.mark.parametrize(
    ('stored', 'source', 'lang'),
    [
        ('Open file', 'OPEN FILE', 'en'),
        ('Straße', 'STRASSE', 'de'),
        ('café', 'CAFÉ', 'fr'),
        ('Straße', 'Strasse', 'de'),
        ('Open file', 'open file', 'en'),
        ('Kayıt', 'KAYIT', 'tr'),
        ('Αρχείο', 'ΑΡΧΕΙΟ', 'el'),
        ('ＯＫ', 'OK', 'ja'),
    ],
)
def test_a_difference_in_case_only_costs_the_penalty(stored, source, lang):
    assert textfold.comparer(lang).similarity(source, stored) == 100 - textfold.CASE_PENALTY


@pytest.mark.parametrize(
    ('a', 'b'),
    [('Open file', 'Open files'), ('café', 'cafe'), ('Open %s', 'Open %s now'), ('ab', 'xy')],
)
def test_texts_with_no_case_difference_score_as_before(a, b):
    comparer = textfold.Comparer('en')

    assert comparer.similarity(a, b, 0) == comparer.levenshtein.similarity(a, b, 0)


def test_only_identical_texts_score_100():
    comparer = textfold.comparer()

    assert comparer.similarity('Open file', 'Open file') == 100
    assert comparer.similarity('café', 'café') == 100
    assert comparer.similarity('Open file', 'OPEN FILE') < 100
    assert comparer.similarity('', '') == 0


def test_a_case_difference_in_a_fuzzy_match_costs_the_penalty():
    comparer = textfold.comparer('en')

    assert comparer.similarity('Open files', 'OPEN FILE', 0) == pytest.approx(87)


def test_a_case_difference_in_a_long_text_costs_less_than_the_penalty():
    text = 'Open the file ' * 20

    assert 97 < textfold.comparer('en').similarity(text, 'o' + text[1:]) < 100


def test_a_score_below_the_stop_stays_below_it():
    assert textfold.comparer('en').similarity('OPEN FILE', 'Close all', 75) < 75


@pytest.mark.parametrize(
    ('text', 'loose'),
    [('ΑΡΧΕΊΟ', 'αρχειο'), ('İptal', 'iptal'), ('kayıt', 'kayit'), ('Straße', 'strasse')],
)
def test_loose_folds_for_every_language(text, loose):
    assert textfold.loose(text) == loose


@pytest.mark.parametrize(
    ('word', 'lang', 'forms'),
    [
        ('STRASSE', 'de', ['STRASSE', 'strasse', 'Strasse', 'straße', 'Straße']),
        ('Straße', 'de', ['Straße', 'straße', 'STRASSE']),
        ('KAYIT', 'tr', ['KAYIT', 'kayıt', 'Kayıt']),
        ('iptal', 'tr', ['iptal', 'İPTAL', 'İptal']),
        ('αρχείο', 'el', ['αρχείο', 'ΑΡΧΕΊΟ', 'Αρχείο', 'ΑΡΧΕΙΟ']),
        ('ＯＫ', 'ja', ['ＯＫ', 'ｏｋ', 'Ｏｋ', 'ok', 'OK', 'Ok']),
    ],
)
def test_case_forms(word, lang, forms):
    assert textfold.case_forms(word, lang) == forms
