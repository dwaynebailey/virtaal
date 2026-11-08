#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Quality checks beyond the toolkit's, e.g. from an external library.

An extra check is a class with:

- ``name``: its failure name, as for a toolkit check.
- ``category``: a ``translate.filters.decorators.Category``.
- ``applies_to(store)``: whether it checks this toolkit store.
- ``available()``: whether it can run here, e.g. its library loads.
- ``check(unit)``: the failure message for unit, or None.
"""

from virtaal.support import dismissedchecks
from virtaal.support.gettextpo import MsgfmtCheck
from virtaal.support.phrase_compat import PhraseTagCheck

EXTRA_CHECKS = [MsgfmtCheck, PhraseTagCheck]


def checks_for(store):
    """Instances of the extra checks that apply to store and can run."""
    return [cls() for cls in EXTRA_CHECKS if cls.applies_to(store) and cls.available()]


class ExtraChecksFilter:
    """A toolkit checker that also runs extra checks, and leaves out the
    checks dismissed on a unit (#3276). Delegates everything else to
    the checker."""

    def __init__(self, checker, checks):
        self.checker = checker
        self.checks = checks
        # statsdb caches failures per checker config.
        checker.config.extra_checks = sorted(check.name for check in checks)

    def __getattr__(self, name):
        return getattr(self.checker, name)

    def run_filters(self, unit, categorised=False, with_dismissed=False):
        """unit's failures; with_dismissed: also the dismissed ones."""
        failures = self.checker.run_filters(unit, categorised)
        for check in self.checks:
            message = check.check(unit)
            if not message:
                continue
            if categorised:
                failures[check.name] = {'message': message, 'category': check.category}
            else:
                failures[check.name] = message
        if with_dismissed:
            return failures
        return dismissedchecks.filter_failures(unit, failures)


def with_extra_checks(checker, store):
    """checker, wrapped to also run the extra checks for store, if any,
    and leave out dismissed checks."""
    return ExtraChecksFilter(checker, checks_for(store) if store is not None else [])
