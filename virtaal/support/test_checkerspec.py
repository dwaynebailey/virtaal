#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from translate.filters import checks

from virtaal.support import checkerspec, extrachecks


@pytest.mark.parametrize('code', list(checkerspec.checker_classes()))
def test_a_described_checker_is_built_again_the_same(code):
    checker = checkerspec.checker_classes()[code]()
    checker.config.updatetargetlanguage('af')

    built = checkerspec.build(checkerspec.describe(checker))

    assert type(built) is type(checker)
    assert str(built.config.__dict__) == str(checker.config.__dict__)


class _Extra:
    name = 'extra'

    def check(self, unit):
        return None


def test_extra_checks_are_built_again(monkeypatch):
    monkeypatch.setattr(extrachecks, 'EXTRA_CHECKS', [_Extra])
    checker = extrachecks.ExtraChecksFilter(checks.StandardChecker(), [_Extra()])

    spec = checkerspec.describe(checker)
    built = checkerspec.build(spec)

    assert spec['extra'] == ['extra']
    assert isinstance(built, extrachecks.ExtraChecksFilter)
    assert [type(check) for check in built.checks] == [_Extra]
    assert str(built.config.__dict__) == str(checker.config.__dict__)


def test_a_checker_of_another_class_is_not_described():
    class Custom(checks.StandardChecker):
        pass

    assert checkerspec.describe(Custom()) is None


def test_a_checker_that_comes_out_differently_is_not_built():
    spec = checkerspec.describe(checks.StandardChecker())
    spec['config'] = spec['config'].replace("'targetlanguage': None", "'targetlanguage': 'af'")

    assert checkerspec.build(spec) is None


def test_a_wrapped_checker_without_extra_checks_is_built_wrapped(monkeypatch):
    # So another process leaves out dismissed checks too (#3276).
    monkeypatch.setattr(extrachecks, 'EXTRA_CHECKS', [])
    checker = extrachecks.ExtraChecksFilter(checks.StandardChecker(), [])

    built = checkerspec.build(checkerspec.describe(checker))

    assert isinstance(built, extrachecks.ExtraChecksFilter)
    assert str(built.config.__dict__) == str(checker.config.__dict__)
