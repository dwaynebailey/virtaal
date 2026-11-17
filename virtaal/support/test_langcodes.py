# -*- coding: utf-8 -*-
#
# Copyright 2026 Zuza Software Foundation
#
# This file is part of Virtaal.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

from virtaal.support.langcodes import candidate_pairs, fallback_codes


def test_fallback_codes():
    assert fallback_codes('af') == ['af']
    assert fallback_codes('af_ZA') == ['af_ZA', 'af']
    assert fallback_codes('pt-BR') == ['pt_BR', 'pt']
    assert fallback_codes('sr@latin') == ['sr@latin']
    assert fallback_codes('sr_RS@latin') == ['sr_RS@latin', 'sr@latin']
    assert fallback_codes('zh_TW') == ['zh_TW']
    assert fallback_codes('') == []
    assert fallback_codes(None) == []


def test_candidate_pairs_order():
    # Source fallbacks are exhausted before the target region is dropped
    assert candidate_pairs('en_US', 'pt_BR') == [
        ('en_US', 'pt_BR'), ('en', 'pt_BR'),
        ('en_US', 'pt'), ('en', 'pt'),
    ]
    assert candidate_pairs('en', 'af') == [('en', 'af')]


def test_candidate_pairs_supported():
    supported = (['en'], ['af', 'pt_BR', 'sr@latin'])
    assert candidate_pairs('en_US', 'af_ZA', supported) == [('en', 'af')]
    # The server's spelling is used
    assert candidate_pairs('en', 'pt-br', supported) == [('en', 'pt_BR')]
    assert candidate_pairs('en', 'sr_RS@latin', supported) == [('en', 'sr@latin')]
    # Nothing supported: nothing to query
    assert candidate_pairs('en', 'xx_YY', supported) == []
