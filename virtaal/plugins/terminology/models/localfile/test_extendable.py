#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

import pytest

from virtaal.plugins.terminology.models.localfile import extendable
from virtaal.plugins.terminology.models.localfile.extendable import is_extendable

MILI = os.path.join('devsupport', 'testfiles', 'trados', 'kaur2k-termconv', 'mili_1.xml')


@pytest.mark.parametrize('name', ['terms.po', 'terms.tbx', 'terms.csv'])
def test_a_written_format_is_extendable_before_it_exists(tmp_path, name):
    assert is_extendable(str(tmp_path / name))


@pytest.mark.parametrize(
    'filename',
    [
        MILI,
        os.path.join('devsupport', 'testfiles', 'workflow.qm'),
        'termbase.sdltb',
        'memory.sdltm',
        'glossary.unknown',
        '',
    ],
)
def test_a_read_only_format_is_not_extendable(filename):
    assert not is_extendable(filename)


def test_a_file_that_cant_be_written_to_is_not_extendable(tmp_path, monkeypatch):
    terms = tmp_path / 'terms.po'
    terms.write_text('', encoding='utf-8')
    # os.access, not chmod: the suite also runs as root.
    monkeypatch.setattr(extendable.os, 'access', lambda path, mode: False)

    assert not is_extendable(str(terms))
