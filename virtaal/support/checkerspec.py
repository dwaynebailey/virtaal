#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The quality checkers by project style, and a description of a
checker that another process can build the same checker from."""


def checker_classes():
    """{project style code: checker class}; None is the default."""
    from translate.filters import checks

    from virtaal.support import checks_compat  # noqa: F401 - applies its patch on import
    from virtaal.support.qtchecks import QtChecker

    return {
        # A new checker code also needs a name in ChecksController's
        # _checker_code_to_name.
        None: checks.StandardChecker,
        'openoffice': checks.OpenOfficeChecker,
        'mozilla': checks.MozillaChecker,
        'drupal': checks.DrupalChecker,
        'gnome': checks.GnomeChecker,
        'kde': checks.KdeChecker,
        'qt': QtChecker,
    }


def describe(checker):
    """A dict to build C{checker} again with L{build}, or None for a
    checker that isn't one of L{checker_classes}."""
    from virtaal.support.extrachecks import ExtraChecksFilter

    # None: not wrapped, so not leaving out dismissed checks either.
    extra = None
    base = checker
    if isinstance(checker, ExtraChecksFilter):
        base = checker.checker
        extra = [check.name for check in checker.checks]
    codes = [code for code, cls in checker_classes().items() if type(base) is cls]
    if not codes:
        return None
    return {
        'code': codes[0],
        'language': base.config.targetlanguage,
        'extra': extra,
        'config': str(checker.config.__dict__),
    }


def build(spec):
    """The checker L{describe} described, or None if it comes out
    configured differently."""
    from virtaal.support.extrachecks import EXTRA_CHECKS, ExtraChecksFilter

    checker = checker_classes()[spec['code']]()
    checker.config.updatetargetlanguage(spec['language'])
    if spec['extra'] is not None:
        checks = [cls() for cls in EXTRA_CHECKS if cls.name in spec['extra']]
        checker = ExtraChecksFilter(checker, checks)
    if str(checker.config.__dict__) != spec['config']:
        return None
    return checker
