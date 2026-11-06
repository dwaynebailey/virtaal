#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from translate.storage import po

from virtaal.support import checkerspec


def _unit(entry):
    return po.pofile.parsestring(entry.encode('utf-8')).units[0]


@pytest.fixture(params=list(checkerspec.checker_classes()))
def checker(request):
    return checkerspec.checker_classes()[request.param]()


def test_plural_forms_are_checked_against_msgid_plural(checker):
    # #3279: the variable and brackets are in msgid_plural.
    unit = _unit(
        '#, python-format\n'
        'msgid "Assigned to one project"\n'
        'msgid_plural "Assigned to %(num)s projects"\n'
        'msgstr[0] "Tildelt til et projekt"\n'
        'msgstr[1] "Tildelt til %(num)s projekter"\n'
    )

    assert checker.run_filters(unit) == {}


def test_a_plural_form_missing_a_variable_still_fails(checker):
    unit = _unit(
        'msgid "One file"\n'
        'msgid_plural "%(num)s files"\n'
        'msgstr[0] "Een leer"\n'
        'msgstr[1] "Baie leers"\n'
    )

    assert 'printf' in checker.run_filters(unit)


def test_a_plural_with_only_msgstr1_unchanged_is_not_unchanged(checker):
    unit = _unit(
        'msgid "Open file"\nmsgid_plural "Open files"\n'
        'msgstr[0] "Maak leer oop"\nmsgstr[1] "Open files"\n'
    )

    assert 'unchanged' not in checker.run_filters(unit)


def test_a_translation_with_one_form_is_checked_against_msgid_plural(checker):
    unit = _unit('msgid "One file"\nmsgid_plural "%(num)s files"\nmsgstr[0] "%(num)s leers"\n')

    assert checker.run_filters(unit) == {}


def test_a_plural_with_only_its_singular_unchanged_is_not_unchanged(checker):
    # #3328's own example.
    unit = _unit(
        'msgid "%{text} %{files}"\nmsgid_plural "%{text} %{files} files"\n'
        'msgstr[0] "%{text} %{files}"\nmsgstr[1] "%{text} %{files} filer"\n'
    )

    assert 'unchanged' not in checker.run_filters(unit)


def test_a_plural_with_every_form_unchanged_is_unchanged(checker):
    unit = _unit(
        'msgid "Open file"\nmsgid_plural "Open files"\n'
        'msgstr[0] "Open file"\nmsgstr[1] "Open files"\n'
    )

    assert checker.run_filters(unit)['unchanged'] == 'Consider translating'


@pytest.mark.parametrize(
    'target, unchanged',
    [('Open files', True), ('Open file', False), ('Maak leers oop', False)],
)
def test_a_one_form_translation_is_unchanged_against_msgid_plural(checker, target, unchanged):
    unit = _unit(f'msgid "Open file"\nmsgid_plural "Open files"\nmsgstr[0] "{target}"\n')

    assert ('unchanged' in checker.run_filters(unit)) is unchanged
