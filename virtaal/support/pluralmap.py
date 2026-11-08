#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Map a plural's forms from one layout to another: gettext's (a PO
file's Plural-Forms) and CLDR's categories (an i18next or Android
file's). Each form is matched by the numbers that take it, so no
per-language table is needed. No GTK here."""

import functools
import warnings
from collections import Counter

SAMPLES = (*range(1000), 10000, 100000, 1000000)
"""Whole numbers a form's meaning is read from."""


class Layout:
    """How a language's plural forms are laid out: C{count} forms, and
    C{form(n)}, the form whole number n takes."""

    def __init__(self, count, form):
        self.count = count
        self.form = form

    @functools.cached_property
    def samples(self):
        """{form: [numbers]} of the sample numbers each form takes."""
        found = {}
        for number in SAMPLES:
            found.setdefault(self.form(number), []).append(number)
        return found


@functools.cache
def gettext_layout(language):
    """The gettext layout of C{language}'s plurals, or None if Virtaal
    knows no plural rule for it."""
    import gettext

    from virtaal.models.langmodel import LanguageModel

    try:
        model = LanguageModel(language)
    except Exception:
        return None
    if not model.nplurals or not model.plural:
        return None
    try:
        rule = gettext.c2py(model.plural)
    except (ValueError, SyntaxError):
        return None
    return Layout(int(model.nplurals), _bounded(rule, int(model.nplurals)))


def _bounded(rule, count):
    def form(number):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            index = rule(number)
        return index if 0 <= index < count else count - 1

    return form


@functools.cache
def cldr_layout(language, ordinal=False):
    """The CLDR layout of C{language}'s plurals (cardinal, or ordinal):
    its categories in CLDR's order. None if Babel doesn't know it."""
    import babel

    from virtaal.support.converters import plural_categories

    categories = plural_categories(language, ordinal)
    if not categories:
        return None
    locale = babel.Locale.parse(language.replace('-', '_'))
    rule = locale.ordinal_form if ordinal else locale.plural_form
    return Layout(len(categories), lambda number: categories.index(rule(number)))


def form_map(source, target):
    """[(source form, guessed)] for each form of the layout C{target}:
    the form of C{source} that the same numbers take. A form whose
    numbers C{source} splits between forms takes the one most of them
    take; a form no whole number takes (Russian "other", for fractions)
    takes C{source}'s form most numbers take. Either is a guess."""
    general = max(source.samples, key=lambda form: len(source.samples[form]))
    found = []
    for form in range(target.count):
        numbers = target.samples.get(form)
        if not numbers:
            found.append((general, True))
            continue
        counts = Counter(source.form(number) for number in numbers)
        best = counts.most_common(1)[0][0]
        found.append((best, len(counts) > 1))
    return found
