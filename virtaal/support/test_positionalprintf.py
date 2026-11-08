#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import shutil
import subprocess

import pytest
from translate.filters import checks
from translate.storage import pypo

from virtaal.support.positionalprintf import applies, directives, needs_numbers, numbered

NUMBERED = [
    ('%s of %d', '%d van %s', '%2$d van %1$s'),
    ('%s and %s or %d', '%d of %s en %s', '%3$d of %1$s en %2$s'),
    ('%5.2f and %s', '%s en %5.2f', '%2$s en %1$5.2f'),
    ('%-10s|%lu', '%lu|%-10s', '%2$lu|%1$-10s'),
    ('%d%% of %s', '%s se %d%%', '%2$s se %1$d%%'),
    ('Hello %@, %d', '%d, hallo %@', '%2$d, hallo %1$@'),
    ('%s of %d', '%2$d van %s', '%2$d van %1$s'),
    ('%s of %d', '%1$s van %d', '%1$s van %2$d'),
    ('%s %d %f', '%f %s', '%3$f %1$s'),
]


@pytest.mark.parametrize(('source', 'target', 'expected'), NUMBERED)
def test_numbered(source, target, expected):
    assert needs_numbers(source, target)
    assert numbered(source, target) == expected


@pytest.mark.parametrize(
    ('source', 'target'),
    [
        ('%s of %d', '%s van %d'),  # in order
        ('%s and %s', '%s en %s'),  # the same twice: no order to see
        ('%s', 'x %s'),
        ('%s of %d', '%d'),  # one so far: no order yet
        ('%s of %d', '%2$d van %1$s'),  # numbered already
        ('%1$s of %2$d', '%2$d van %1$s'),
        ('%*d %s', '%s %*d'),  # a width argument
        ('%(a)s %(b)d', '%(b)d %(a)s'),  # Python names
        ('%1 of %2', '%2 van %1'),  # Qt
        ('{0} of {1}', '{1} van {0}'),  # Java
        ('%s of %d', '%d van %s en %x'),  # not in the source
        ('%s of %d', '%3$d van %s'),  # no third argument
        ('%s of %d', '%1$d van %s'),  # the first is %s
        ('100%% of %s', '%s: 100%%'),
        ('', ''),
    ],
)
def test_nothing_to_number(source, target):
    assert not needs_numbers(source, target)
    assert numbered(source, target) is None


def test_directives_leave_out_percent_signs():
    assert [d.spec for d in directives('%d%% of %s, 100%%')] == ['d', 's']


@pytest.mark.parametrize(
    ('fmt', 'flags', 'expected'),
    [
        (None, {'c-format'}, True),
        (None, {'objc-format', 'fuzzy'}, True),
        (None, {'php-format'}, True),
        (None, {'python-format'}, False),
        (None, {'qt-format'}, False),
        (None, {'java-format'}, False),
        (None, {'no-c-format'}, False),
        (None, set(), False),
        ('android', set(), True),
        ('strings', set(), True),
        ('json', set(), False),
        ('fluent', set(), False),
    ],
)
def test_applies(fmt, flags, expected):
    assert applies(fmt, flags) == expected


def _po_unit(source, target, flag='c-format'):
    unit = pypo.pounit(source)
    unit.target = target
    unit.settypecomment(flag)
    return unit


@pytest.mark.parametrize(('source', 'target', 'expected'), NUMBERED[:7])
def test_the_toolkits_printf_check_accepts_the_numbered_target(source, target, expected):
    checker = checks.StandardChecker()

    assert 'printf' not in checker.run_filters(_po_unit(source, expected))


@pytest.mark.skipif(shutil.which('msgfmt') is None, reason='needs msgfmt')
@pytest.mark.parametrize('flag', ['c-format', 'objc-format', 'php-format'])
def test_msgfmt_accepts_a_numbered_translation_of_an_unnumbered_source(tmp_path, flag):
    store = pypo.pofile()
    store.updateheader(add=True, Content_Type='text/plain; charset=UTF-8')
    for source, _target, expected in NUMBERED[:5]:
        if '@' in source and flag != 'objc-format':
            continue
        store.addunit(_po_unit(source, expected, flag))
    path = tmp_path / 'numbered.po'
    path.write_bytes(bytes(store))

    result = subprocess.run(
        ['msgfmt', '-c', '-o', str(tmp_path / 'numbered.mo'), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which('msgfmt') is None, reason='needs msgfmt')
def test_msgfmt_rejects_mixed_numbering(tmp_path):
    store = pypo.pofile()
    store.updateheader(add=True, Content_Type='text/plain; charset=UTF-8')
    store.addunit(_po_unit('%s of %d', '%2$d van %s'))
    path = tmp_path / 'mixed.po'
    path.write_bytes(bytes(store))

    result = subprocess.run(
        ['msgfmt', '-c', '-o', str(tmp_path / 'numbered.mo'), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
