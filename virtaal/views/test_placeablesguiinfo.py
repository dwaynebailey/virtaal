#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest

from virtaal.views import placeablesguiinfo
from virtaal.views.placeablesguiinfo import GPlaceableGUI, UnknownXMLGUI


@pytest.mark.parametrize(
    ('tag', 'code_id', 'labels'),
    [
        ('g', '1', ('<1|', '>')),
        ('g', None, ('<', '>')),
        ('x', '2', ('[2]', None)),
        ('bx', '3', ('((', None)),
        ('ex', '3', ('))', None)),
        ('ph', '4', ('{ph|', '}')),
        ('bpt', '5', ('{bpt|', '}')),
    ],
)
def test_code_marks_label_codes_as_the_editor_does(tag, code_id, labels):
    [mark] = placeablesguiinfo.code_marks([(0, 1, tag, code_id)])

    assert mark[:4] == (0, 1, *labels)


def test_code_marks_colour_codes_as_the_editor_does():
    g, ph = placeablesguiinfo.code_marks([(0, 1, 'g', '1'), (1, 2, 'ph', '2')])

    assert 'background="%s"' % GPlaceableGUI.bg in g[4]
    assert 'background="%s"' % UnknownXMLGUI.bg in ph[4]
