#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""translate-toolkit's checks compare every plural form of a translation
with the singular source, so msgstr[1] "%(num)s projekter" for msgid_plural
"%(num)s projects" reported an added variable and brackets (#3279). Here
msgstr[0] is checked against msgid and the other forms against
msgid_plural, as msgfmt does; a translation with one form stands for
every number, so it is checked against msgid_plural.

A plural is only unchanged when every form is: a singular left the same
as msgid next to a translated plural is not flagged (#3328)."""

from translate.filters.checks import FilterFailure, TranslationChecker
from translate.lang import data

_run_test = TranslationChecker.run_test


def _sources(unit, forms):
    sources = [data.normalize(str(s)) or "" for s in unit.source.strings]
    if not sources:
        return None
    if len(forms) == 1:
        return [sources[-1]]
    return [sources[min(n, len(sources) - 1)] for n in range(len(forms))]


def run_test(self, test, unit):
    if not self.hasplural:
        return _run_test(self, test, unit)
    forms = unit.target.strings
    sources = _sources(unit, forms)
    if sources is None:
        return _run_test(self, test, unit)
    messages = []
    failed = 0
    for source, form in zip(sources, forms, strict=True):
        try:
            if not test(source, str(form)):
                failed += 1
        except FilterFailure as e:
            failed += 1
            messages += [m for m in e.messages if m not in messages]
    if getattr(test, '__name__', None) == 'unchanged' and failed < len(forms):
        return True
    result = not failed
    if not result and messages:
        raise FilterFailure(messages)
    return result


TranslationChecker.run_test = run_test
