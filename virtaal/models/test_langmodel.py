#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from translate.lang.data import languages as toolkit_langs

from virtaal.models import langmodel
from virtaal.models.langmodel import LanguageModel
from virtaal.support.libi18n import plurals


def test_luganda_comes_from_the_plural_table():
    assert 'lg' not in toolkit_langs

    lang = LanguageModel('lg')

    assert (lang.code, lang.nplurals, lang.plural) == ('lg', 2, '(n != 1)')
    # ISO 639's name, as gettext_lang names translate-toolkit's.
    assert LanguageModel.languages['lg'][0] == 'Ganda'


def test_translate_toolkits_entry_wins_over_the_table(monkeypatch):
    monkeypatch.setitem(plurals.PLURAL_RULES, 'de', (3, 'n % 3'))

    assert langmodel.known_languages()['de'] == toolkit_langs['de']


def test_a_table_language_iso_639_lacks_is_named_by_its_code(monkeypatch):
    monkeypatch.setitem(plurals.PLURAL_RULES, 'qqx', (1, '0'))

    assert langmodel.known_languages()['qqx'] == ('qqx', 1, '0')
