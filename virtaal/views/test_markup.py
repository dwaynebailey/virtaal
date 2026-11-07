#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Tests for markuptext(), the function behind escape/XML/diff highlighting
in translation text - previously untested despite being used by
tmwidgets.py, storetreemodel.py, storecellrenderer.py and
label_expander.py. Uses the live current_theme colours for assertions
(rather than hardcoding hex codes) so these don't go stale if the theme's
palette changes."""

import pytest
from gi.repository import Gtk, Pango

from virtaal.support import invisiblechars
from virtaal.views import markup, theme
from virtaal.views.markup import markuptext
from virtaal.views.theme import current_theme


def test_markuptext_empty_returns_empty_string():
    assert markuptext("") == ""
    assert markuptext(None) == ""


def test_markuptext_escapes_ampersand_and_lt():
    assert markuptext("a & b < c") == "a &amp; b &lt; c"


def test_markuptext_marks_up_embedded_newline():
    result = markuptext("hello\nworld")
    subtle = current_theme['subtle_fg']
    assert result == 'hello<span foreground="%s">¶\n</span>world' % subtle


def test_markuptext_strips_trailing_newline_but_keeps_pilcrow():
    # A trailing newline's pilcrow is shown, but the newline itself isn't -
    # otherwise the markup would render an extra blank line.
    result = markuptext("trailing newline\n")
    subtle = current_theme['subtle_fg']
    assert result == 'trailing newline<span foreground="%s">¶</span>' % subtle
    assert not result.endswith("\n</span>")


def test_markuptext_markupescapes_false_leaves_newline_alone():
    result = markuptext("hello\nworld", markupescapes=False)
    assert result == "hello\nworld"
    assert "<span" not in result


def test_markuptext_marks_up_unusual_spaces():
    # Flagged: 2+ consecutive spaces anywhere, or 1+ leading/trailing.
    # Not flagged: a single space between words (the "double" here has two
    # spaces before it and after "lead" is a single one - not flagged).
    result = markuptext("  lead double  trail  ")
    assert result.count('<span underline="error" foreground="grey">') == 3
    # the actual space characters are preserved inside the markup, not lost
    assert (
        result.replace('<span underline="error" foreground="grey">', "").replace("</span>", "")
        == "  lead double  trail  "
    )


def test_markuptext_marks_up_leading_and_trailing_tab():
    span = '<span underline="error" foreground="grey">%s</span>'
    assert markuptext("\tleading") == span % "\t" + "leading"
    assert markuptext("trailing\t") == "trailing" + span % "\t"


def test_markuptext_marks_up_nbsp_leading_trailing_and_inline():
    # NBSP is unusual wherever it appears - unlike a regular space, a single
    # inline one (the classic number+unit case, e.g. "10 km") is also
    # flagged, not just doubled/leading/trailing occurrences.
    nbsp = "\u00a0"  # NO-BREAK SPACE
    span = '<span underline="error" foreground="grey">%s</span>'
    assert markuptext(nbsp + "leading") == span % nbsp + "leading"
    assert markuptext("trailing" + nbsp) == "trailing" + span % nbsp
    assert markuptext("10" + nbsp + "km") == "10" + span % nbsp + "km"


@pytest.mark.parametrize(
    "char",
    [
        "\u1680",  # OGHAM SPACE MARK
        "\u2002",  # EN SPACE
        "\u2003",  # EM SPACE
        "\u2007",  # FIGURE SPACE
        "\u2008",  # PUNCTUATION SPACE
        "\u2009",  # THIN SPACE
        "\u200a",  # HAIR SPACE
        "\u200b",  # ZERO WIDTH SPACE
        "\u202f",  # NARROW NO-BREAK SPACE
        "\u205f",  # MEDIUM MATHEMATICAL SPACE
        "\u3000",  # IDEOGRAPHIC SPACE
    ],
)
def test_markuptext_marks_up_unicode_space_separators(char):
    # Mirrors the case list in devsupport/testfiles/whitespace.po - a single
    # occurrence of any of these mid-word is unusual and should be flagged.
    span = '<span underline="error" foreground="grey">%s</span>'
    assert markuptext("a" + char + "b") == "a" + span % char + "b"


def test_markuptext_fancyspaces_false_leaves_spaces_alone():
    result = markuptext("  lead double  trail  ", fancyspaces=False)
    assert result == "  lead double  trail  "
    assert "<span" not in result


def test_markuptext_uses_diff_markup_when_diff_text_differs():
    result = markuptext("hello world", diff_text="hello")
    insert_bg = current_theme['diff_insert_bg']
    assert insert_bg in result
    assert " world</span>" in result


def test_markuptext_ignores_diff_text_when_equal_to_text():
    # diff_text is only used when it differs from text - otherwise this is
    # a no-op plain escape, not a (pointless) self-diff.
    result = markuptext("same & same", diff_text="same & same")
    assert result == "same &amp; same"
    assert "<span" not in result


def test_pango_diff_highlights_set_a_readable_text_colour():
    # A selected TM row has white text; without its own colour a
    # highlighted word was white on pale yellow.
    result = markup.pango_diff('save before closing', 'save before exiting')

    assert "background='%s'" % theme.current_theme['diff_replace_bg'] in result
    assert "foreground='%s'" % theme.readable_fg(theme.current_theme['diff_replace_bg']) in result


def test_readable_fg_picks_black_on_light_and_white_on_dark():
    assert theme.readable_fg('#ffff70') == '#000'
    assert theme.readable_fg('#4a4a00') == '#fff'


@pytest.mark.parametrize(
    ('a', 'b', 'expected'),
    [
        (
            'Set the %(date)s',
            'Set the %(time)s',
            [(0, 'Set the '), (-1, '%(date)s'), (1, '%(time)s')],
        ),
        (
            'Click <b>here</b>',
            'Click <strong>here</strong>',
            [
                (0, 'Click '),
                (-1, '<b>'),
                (1, '<strong>'),
                (0, 'here'),
                (-1, '</b>'),
                (1, '</strong>'),
            ],
        ),
        (
            'Version 3.5 now',
            'Version 3.6 now',
            [(0, 'Version '), (-1, '3.5'), (1, '3.6'), (0, ' now')],
        ),
    ],
)
def test_diff_treats_a_placeable_as_one_unit(a, b, expected):
    assert markup._diff(a, b) == expected


def test_pango_diff_highlights_the_whole_placeable():
    result = markup.pango_diff('Set the %(date)s', 'Set the %(time)s')

    assert result.startswith('Set the <span ')
    assert result.endswith('>%(time)s</span>')


def test_diff_still_splits_words_and_keeps_equal_placeables():
    assert markup._diff('Delete %s files', 'Remove %s files') == [
        (-1, 'Delet'),
        (1, 'Remov'),
        (0, 'e %s files'),
    ]


def test_diff_of_text_holding_the_token_block_is_plain():
    a, b = 'x \U000f0000 %(date)s', 'x \U000f0000 %(time)s'

    assert ''.join(text for op, text in markup._diff(a, b) if op >= 0) == b


@pytest.mark.parametrize(
    ('ops', 'expected'),
    [
        ([(0, 'a & '), (1, 'b')], 'a &amp; [INS:b]'),
        ([(-1, 'x'), (0, 'y')], '[DEL:x]y'),
        ([(0, 'y'), (-1, 'x')], 'y[DEL:x]'),
        # Only the last of two deletions in a row is shown.
        ([(-1, 'a'), (-1, 'b'), (0, 'c')], '[DEL:b]c'),
        ([(-1, 'Save'), (1, 'save')], '[CASE:save]'),
        ([(-1, 'Untitled'), (1, 'titled')], '[CASE:titled]'),
        ([(-1, 'XYZabc'), (1, 'abc')], '[ADD:abc]'),
        ([(-1, 'cat'), (1, 'dog')], '[ADD:dog]'),
        ([(-1, '%(count)s'), (1, 'x')], '[DEL:%(count)s][ADD:x]'),
        ([(-1, 'abcdefgh'), (1, 'x')], '[ADD:x]'),
        ([(1, 'new'), (-1, 'old')], '[INS:new][DEL:old]'),
    ],
)
def test_pango_diff_spans_for_each_kind_of_change(monkeypatch, ops, expected):
    monkeypatch.setattr(markup, '_diff', lambda a, b: ops)
    monkeypatch.setattr(
        markup,
        '_diff_pango_templates',
        {
            'insert_attr': 'INS',
            'delete_attr': 'DEL',
            'replace_attr_add': 'ADD',
            'replace_attr_add_case': 'CASE',
        },
    )
    monkeypatch.setattr(markup, '_pango_spans', lambda attr, text: '[%s:%s]' % (attr, text))

    assert markup.pango_diff('a', 'b') == expected


# Inline codes, as a TM match brings them.

_G = (5, 9, '<1|', '>', 'G')


def test_markuptext_labels_and_marks_a_code():
    assert markuptext('Klik hier & gaan', codes=[_G]) == (
        'Klik <span G>&lt;1|</span><span G>hier</span><span G>></span> &amp; gaan'
    )


def test_markuptext_puts_codes_with_no_text_between_characters():
    codes = [(1, 1, '[2]', None, 'X'), (2, 2, None, None, 'Y'), (3, 3, '))', None, 'E')]

    assert markuptext('abc', codes=codes) == 'a<span X>[2]</span>bc<span E>))</span>'


def test_markuptext_marks_nested_codes():
    codes = [(0, 3, '<1|', '>', 'A'), (1, 1, '[2]', None, 'B')]

    assert markuptext('abc', codes=codes) == (
        '<span A>&lt;1|</span><span A>a</span><span B>[2]</span><span A>bc</span><span A>></span>'
    )


def test_markuptext_counts_codes_past_a_diffs_deleted_text(monkeypatch):
    monkeypatch.setattr(markup, '_diff', lambda a, b: [(0, 'Klik '), (-1, 'daar '), (0, 'hier')])

    marked = markuptext('Klik hier', diff_text='Klik daar hier', codes=[_G])

    assert marked.startswith('Klik <span strikethrough=')
    assert marked.endswith('daar </span><span G>&lt;1|</span><span G>hier</span><span G>></span>')


def test_markuptext_of_only_codes():
    assert markuptext('', codes=[(0, 0, '[1]', None, 'X')]) == '<span X>[1]</span>'


# Zero-width characters get the editor's labels (invisiblechars.MARKERS).

LABELLED = [
    char
    for char, marker in invisiblechars.MARKERS.items()
    if marker.zero_width and char != '\u200b'
]


@pytest.mark.parametrize('char', LABELLED)
def test_markuptext_labels_a_zero_width_character_as_the_editor_does(char):
    label = '<span size="smaller" foreground="%s">\u2066%s\u2069</span>' % (
        current_theme['subtle_fg'],
        invisiblechars.MARKERS[char].label,
    )

    assert markuptext('a' + char + 'b') == 'a' + label + char + 'b'


def test_markuptext_labels_each_zero_width_character_in_a_run():
    assert markuptext('a\u200f\u200eb').count('\u2066') == 2


def test_markuptext_labels_nothing_else_and_only_with_fancyspaces():
    assert markuptext('a, b; 12 c') == 'a, b; 12 c'
    assert markuptext('a\u200fb', fancyspaces=False) == 'a\u200fb'


def _layout(text, markup_text=None):
    # As the unit list lays out a row.
    context = Gtk.TreeView().create_pango_context()
    context.set_base_dir(Pango.Direction.RTL)
    layout = Pango.Layout(context)
    layout.set_auto_dir(False)
    layout.set_width(2000 * Pango.SCALE)
    if markup_text is None:
        layout.set_text(text, -1)
    else:
        layout.set_markup(markup_text, -1)
    return layout


def _visual_order(layout):
    """The layout's characters left to right, without the labels and the
    zero-width characters."""
    text = layout.get_text()
    positions = []
    index = 0
    in_label = False
    for char in text:
        in_label = (in_label or char == '\u2066') and char != '\u2069'
        if not in_label and char not in invisiblechars.MARKERS and char != '\u2069':
            positions.append((layout.index_to_pos(index).x, char))
        index += len(char.encode('utf-8'))
    return [char for _x, char in sorted(positions)]


@pytest.mark.parametrize(
    'text', ['abc \u200f-1 שלום', 'שלום \u200e12, 34 עולם', 'א\u202bב 5\u202c ג']
)
def test_a_label_doesnt_change_the_bidi_order_of_right_to_left_text(text):
    labelled = _layout(text, markuptext(text))

    assert labelled.get_text() != text
    assert _visual_order(labelled) == _visual_order(_layout(text))


def test_a_label_doesnt_change_a_rows_height():
    plain = _layout('abc def')
    labelled = _layout('abc\u200fdef', markuptext('abc\u200fdef'))

    assert labelled.get_pixel_size()[1] == plain.get_pixel_size()[1]
