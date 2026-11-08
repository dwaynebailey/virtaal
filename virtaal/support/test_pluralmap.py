#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest

from virtaal.support import pluralmap


@pytest.mark.parametrize(
    'language, cldr_from_gettext, gettext_from_cldr',
    [
        # gettext's three Russian forms are CLDR's one, few and many;
        # "other" (fractions) takes the form most numbers take.
        (
            'ru',
            [(0, False), (1, False), (2, False), (2, True)],
            [(0, False), (1, False), (2, False)],
        ),
        ('af', [(0, False), (1, False)], [(0, False), (1, False)]),
        ('de', [(0, False), (1, False)], [(0, False), (1, False)]),
        # French gettext has no "many" (a million): it is the plural.
        ('fr', [(0, False), (1, False), (1, False)], [(0, False), (2, True)]),
    ],
)
def test_forms_map_by_the_numbers_they_take(language, cldr_from_gettext, gettext_from_cldr):
    gettext, cldr = pluralmap.gettext_layout(language), pluralmap.cldr_layout(language)

    assert pluralmap.form_map(gettext, cldr) == cldr_from_gettext
    assert pluralmap.form_map(cldr, gettext) == gettext_from_cldr


def test_an_unknown_language_has_no_layouts():
    assert pluralmap.gettext_layout('xx') is None
    assert pluralmap.cldr_layout('xx') is None


def test_ordinal_layouts_are_cldr_s_ordinal_forms():
    assert pluralmap.cldr_layout('en', ordinal=True).count == 4


def test_russian_other_is_guessed_from_few():
    gettext, cldr = pluralmap.gettext_layout('ru'), pluralmap.cldr_layout('ru')

    mapped = pluralmap.form_map(gettext, cldr, pluralmap.cldr_guesses('ru'))

    assert mapped == [(0, False), (1, False), (2, False), (1, True)]
    # Only Russian has an entry; Ukrainian keeps the general rule.
    assert pluralmap.cldr_guesses('uk') == {}
