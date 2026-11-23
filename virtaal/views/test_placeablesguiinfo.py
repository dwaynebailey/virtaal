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


@pytest.mark.parametrize('dark', [False, True])
def test_inline_code_colours_follow_the_theme(dark):
    from gi.repository import Gtk

    from virtaal.views import theme

    fg = '#eeeeec' if dark else '#000000'
    try:
        theme.set_inverse() if dark else theme.set_default()
        placeablesguiinfo.update_style(Gtk.TextView())
        for gui in (
            placeablesguiinfo.BxGUI,
            placeablesguiinfo.ExGUI,
            GPlaceableGUI,
            placeablesguiinfo.XPlaceableGUI,
            UnknownXMLGUI,
        ):
            assert theme.has_good_contrast(gui.bg, fg), gui
    finally:
        theme.set_default()
        placeablesguiinfo.update_style(Gtk.TextView())
