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

and may have:

- ``toolkit_unit(unit)``: what the toolkit's checks see of unit, for
  text they would misread, such as Phrase's tags.
- ``for_store(store)``: a classmethod making the instance for store,
  for a check that keeps something of it.
- ``steer(unit)``: in place of ``check()``, (names, {name: message}):
  the failures to leave out on unit, such as checks a translation
  server says to ignore there, and those it adds (a message of None
  adds none).
- ``config_name``: the name that stands for it in the checker's config,
  which the stats cache keys checks by: what it checks with, such as a
  digest of a server's flags.
- ``in_child = False``: it can't be built again in a child process
  (checkerspec.describe() gives None, so the files are checked here).

A plug-in may add its check to EXTRA_CHECKS while it is on.
"""

from virtaal.support import dismissedchecks
from virtaal.support.gettextpo import MsgfmtCheck
from virtaal.support.phrase_compat import PhraseTagCheck

EXTRA_CHECKS = [MsgfmtCheck, PhraseTagCheck]


def checks_for(store):
    """Instances of the extra checks that apply to store and can run."""
    found = []
    for cls in EXTRA_CHECKS:
        if not (cls.applies_to(store) and cls.available()):
            continue
        make = getattr(cls, 'for_store', None)
        check = make(store) if make is not None else cls()
        if check is not None:
            found.append(check)
    return found


class ExtraChecksFilter:
    """A toolkit checker that also runs extra checks, and leaves out the
    checks dismissed on a unit (#3276). Delegates everything else to
    the checker."""

    def __init__(self, checker, checks):
        self.checker = checker
        self.checks = checks
        # statsdb caches failures per checker config.
        checker.config.extra_checks = sorted(
            getattr(check, 'config_name', check.name) for check in checks
        )

    def __getattr__(self, name):
        return getattr(self.checker, name)

    def run_filters(self, unit, categorised=False, with_dismissed=False):
        """unit's failures; with_dismissed: also the dismissed ones."""
        checked = unit
        for check in self.checks:
            checked = getattr(check, 'toolkit_unit', lambda u: u)(checked)
        failures = self.checker.run_filters(checked, categorised)
        for check in self.checks:
            steer = getattr(check, 'steer', None)
            if steer is not None:
                ignored, found = steer(unit)
            else:
                ignored, found = (), {check.name: check.check(unit)}
            for name in ignored:
                failures.pop(name, None)
            for name, message in found.items():
                if not message:
                    continue
                if categorised:
                    failures[name] = {'message': message, 'category': check.category}
                else:
                    failures[name] = message
        if with_dismissed:
            return failures
        return dismissedchecks.filter_failures(unit, failures)


def with_extra_checks(checker, store):
    """checker, wrapped to also run the extra checks for store, if any,
    and leave out dismissed checks."""
    return ExtraChecksFilter(checker, checks_for(store) if store is not None else [])
