#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import subprocess
import sys

import pytest

from virtaal.support import abstraction
from virtaal.support.abstraction import (
    EMAIL,
    ENTITY,
    FILE,
    NUMBER,
    OPTION,
    TAG_CLOSE,
    TAG_EMPTY,
    TAG_OPEN,
    URL,
    VARIABLE,
    abstract,
    accelerator,
    adapt,
    placeables,
    rebuild,
)

# Text with placeables of every class, nested and repeated tags, entities
# and the two parser bugs worked around. None has an accelerator or "...".
CORPUS = [
    'Delete %(count)s files',
    'Copy %s to %10s',
    'Copy %1 to %2',
    'Hello {0}, you have {1} messages',
    'Printed %d of %i pages',
    'Click <b>here</b>',
    'Click <b>bold <i>both</i></b> now',
    'A <br/> break and <img src="a.png" /> image',
    'Go <a href="https://example.org/3">there</a>',
    'Fish &amp; chips &#169; &lt;tag&gt;',
    'See https://example.org/path?q=1. Then go.',
    'Visit (https://example.org/x) today',
    'Read https://en.wikipedia.org/wiki/A_(b) first',
    'Write to someone@example.org about it',
    'Open /etc/passwd and ~/bin/tool',
    'Version 3.5 of 10, -5 or +3, 1,234.5',
    'Use --help or -h for help',
    '100% done, 5% off and 50% of it',
    'a%%b and %(n)d%%',
    'Two\nlines',
    'Plain text with no placeables',
    '',
]


def _fill(abstract_text, found):
    """The abstract with its markers replaced, in order, by found's text."""
    pieces = iter(found)
    return ''.join(next(pieces).raw if '\ue001' <= c <= '\ue00a' else c for c in abstract_text)


@pytest.mark.parametrize('text', CORPUS)
def test_abstract_restores_by_order(text):
    abstract_text, found = abstract(text)

    assert _fill(abstract_text, found) == text
    assert [text[p.start : p.end] for p in found] == [p.raw for p in found]


@pytest.mark.parametrize('text', CORPUS)
def test_rebuild_against_itself_changes_nothing(text):
    found = placeables(text)

    assert rebuild(text, found, found) == (text, 0, 0, False)
    assert adapt(text, text, text, 100).target == text


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('Delete %(count)s files', 'Delete \ue001 files'),
        ('Copy %1 now', 'Copy \ue001 now'),
        ('Hi {0}', 'Hi \ue001'),
        ('%d pages', '\ue001 pages'),
        ('<b>x</b>', TAG_OPEN + 'x' + TAG_CLOSE),
        ('a<br/>b', 'a' + TAG_EMPTY + 'b'),
        ('a &amp; b', 'a ' + ENTITY + ' b'),
        ('see https://a.org/x', 'see ' + URL),
        ('mail a@b.org', 'mail ' + EMAIL),
        ('open /etc/hosts', 'open ' + FILE),
        ('version 3.5', 'version ' + NUMBER),
        ('use --help', 'use ' + OPTION),
    ],
)
def test_abstract_gives_each_class_its_marker(text, expected):
    assert abstract(text)[0] == expected


def test_markers_are_the_reserved_block():
    assert [VARIABLE, TAG_OPEN, TAG_CLOSE, TAG_EMPTY, ENTITY, URL, EMAIL, FILE, NUMBER, OPTION] == [
        chr(c) for c in range(0xE001, 0xE00B)
    ]


def test_strings_differing_only_in_placeables_share_an_abstract():
    assert abstract('Hi %{name}')[0] == abstract('Hi %s')[0]
    assert abstract('Delete %(count)s files')[0] == abstract('Delete {0} files')[0]
    assert abstract('Click <b>here</b>')[0] == abstract('Click <strong>here</strong>')[0]
    assert abstract('Version 3.5 of 10')[0] == abstract('Version 2.1 of 12')[0]


def test_words_are_not_abstracted():
    # Caps, CamelCase and punctuation are words the translator chooses for.
    assert abstract('Use the GNOME CamelCase API!')[0] == 'Use the GNOME CamelCase API!'


def test_nested_tags_are_paired():
    found = placeables('<b>bold <i>both</i></b>')

    assert [(p.raw, p.pair) for p in found] == [('<b>', 3), ('<i>', 2), ('</i>', 1), ('</b>', 0)]


def test_unbalanced_and_empty_tags_have_no_pair():
    found = placeables('a</b> <br/> <i>open')

    assert [(p.marker, p.pair) for p in found] == [
        (TAG_CLOSE, None),
        (TAG_EMPTY, None),
        (TAG_OPEN, None),
    ]


def test_a_space_flag_before_a_letter_is_text():
    # translate-toolkit parses "% d" in "100% done" as a format.
    assert abstract('100% done')[0] == NUMBER + '% done'
    assert abstract('5% off')[0] == NUMBER + '% off'
    assert [p.raw for p in placeables('a % 5d b')] == ['% 5d']


def test_trailing_punctuation_is_not_part_of_a_url():
    assert [p.raw for p in placeables('See https://a.org/x?q=1.')] == ['https://a.org/x?q=1']
    assert abstract('(https://a.org/x)')[0] == '(' + URL + ')'
    assert [p.raw for p in placeables('https://a.org/A_(b)')] == ['https://a.org/A_(b)']


@pytest.mark.parametrize(
    ('text', 'expected', 'url'),
    [
        # translate-toolkit's URL needs a space, an end or a bracket after
        # a bare host: the full stop made "https:" text and the rest a path.
        ('See https://example.org.', 'See ' + URL + '.', 'https://example.org'),
        ('Is it https://example.org?', 'Is it ' + URL + '?', 'https://example.org'),
        ('Read https://example.org: it helps', 'Read ' + URL + ': it helps', 'https://example.org'),
        ('See www.example.org.', 'See ' + URL + '.', 'www.example.org'),
        ('See https://example.org...', 'See ' + URL + '…', 'https://example.org'),
    ],
)
def test_a_url_ending_a_sentence_is_whole_with_the_punctuation_outside(text, expected, url):
    abstract_text, found = abstract(text)

    assert abstract_text == expected
    assert [(p.marker, p.raw) for p in found] == [(URL, url)]


@pytest.mark.parametrize(
    ('text', 'expected', 'found'),
    [
        ('_Open', 'Open', (0, '_')),
        ('&Open', 'Open', (0, '&')),
        ('~Open', 'Open', (0, '~')),
        ('E&xit', 'Exit', (1, '&')),
        ('Save &As & Exit', 'Save As & Exit', (5, '&')),
        ('_Ölbild', 'Ölbild', (0, '_')),
        ('_1 file', NUMBER + ' file', (0, '_')),
        ('R&D', 'RD', (1, '&')),
    ],
)
def test_one_accelerator_is_removed(text, expected, found):
    assert accelerator(text) == found
    assert abstract(text)[0] == expected


@pytest.mark.parametrize(
    'text',
    ['_Save &As', 'Tom && Jerry', '__init__', 'a & b', 'Fish &amp; chips', '%(file_name)s'],
)
def test_no_accelerator(text):
    assert accelerator(text) is None


def test_an_entity_is_not_an_accelerator():
    assert abstract('Fish &amp;chips')[0] == 'Fish ' + ENTITY + 'chips'
    assert accelerator('&Fish &amp; chips') == (0, '&')


def test_ellipses_are_one_character():
    assert abstract('Wait...')[0] == abstract('Wait…')[0] == 'Wait…'


@pytest.mark.parametrize('text', ['Icon \ue001 here %s', 'Icon \ue0ff here', 'Icon \ue000'])
def test_text_holding_the_reserved_block_stays_raw(text):
    assert abstract(text) == (text, [])
    assert placeables(text) == []
    assert adapt(text, text, text, 100).target == text


def test_rebuild_takes_the_query_placeables():
    result = adapt('Delete %(count)s files', 'Delete {0} files', 'Verwyder {0} lêers', 59)

    assert result.target == 'Verwyder %(count)s lêers'
    assert result.quality == 99
    assert result.raw_quality == 59
    assert (result.rebuilt.rebuilt, result.rebuilt.unresolved) == (1, 0)


def test_rebuild_keeps_the_translators_order():
    assert adapt('Copy %1 to %2', 'Copy %1 to %2', 'Kopieer %2 na %1', 100).target == (
        'Kopieer %2 na %1'
    )
    assert adapt('Copy {a} to {b}', 'Copy {0} to {1}', '{1} na {0}', 60).target == '{b} na {a}'
    assert adapt('Copy %(a)s to %(b)s', 'Copy %s to %d', '%d na %s', 60).target == (
        '%(b)s na %(a)s'
    )


def test_rebuild_keeps_repeats():
    result = adapt('<b>a</b> and <i>b</i>', '<b>a</b> and <b>b</b>', '<b>x</b> en <b>y</b>', 70)

    assert result.target == '<b>x</b> en <i>y</i>'


def test_rebuild_maps_nested_tags():
    result = adapt(
        'Click <b>bold <i>both</i></b>',
        'Click <strong>bold <em>both</em></strong>',
        'Klik <strong>vet <em>albei</em></strong>',
        50,
    )

    assert result.target == 'Klik <b>vet <i>albei</i></b>'
    assert result.rebuilt.rebuilt == 4


def test_a_target_placeable_not_in_the_source_is_unresolved():
    result = adapt('Version 3.5 of 10', 'Version 2.1 of 12', 'Weergawe 2,1 van 12', 82)

    assert result.target == 'Weergawe 2,1 van 10'
    assert (result.rebuilt.rebuilt, result.rebuilt.unresolved) == (1, 1)
    assert result.quality == 100 - 1 - 5


def test_a_source_placeable_missing_from_the_query_is_unresolved():
    result = adapt('Open %s', 'Open %s in %s', 'Maak %s oop in %s', 50)

    assert result.target == 'Maak %s oop in %s'
    assert result.rebuilt.unresolved == 1


def test_extra_query_placeables_are_not_added():
    assert adapt('Open %s in %s', 'Open %s', 'Maak %s oop', 54).target == 'Maak %s oop'


def test_a_placeable_only_one_side_has_costs_more_than_one_edit():
    # On the abstracts a missing placeable is one character: without the
    # penalty, 'usage: ' (88) would outrank the right match (81).
    short = adapt('usage: %s', 'usage: ', 'Verwendung: ', 77)
    right = adapt('usage: %s', 'usage: %s\n\n', 'Verwendung: %s\n\n', 81)

    assert short.quality == round(87.5 - abstraction.UNMATCHED_PENALTY)
    assert right.quality == 81
    assert right.quality > short.quality


def test_an_extra_source_placeable_costs_both_penalties():
    result = adapt('Open %s for %s', 'Open %s for %s in %s', 'Maak %s oop vir %s in %s', 50)

    assert result.rebuilt.unresolved == 1
    assert result.quality == round(
        abstraction.similarity('Open \ue001 for \ue001', 'Open \ue001 for \ue001 in \ue001')
        - abstraction.UNRESOLVED_PENALTY
        - abstraction.UNMATCHED_PENALTY
    )


def test_quality_is_never_below_raw():
    # A worse abstract score after penalties keeps the raw one.
    result = adapt('5 of 6 and 7', '5 of 6 and 7 ok', '8 van 9 en 10 goed', 90)

    assert result.rebuilt.unresolved == 3
    assert result.quality == 90


def test_only_an_identical_source_scores_100():
    assert adapt('Delete %s files', 'Delete %d files', 'Verwyder %d lêers', 80).quality == 99
    assert adapt('Wait...', 'Wait…', 'Wag…', 80).quality == 99
    assert adapt('Wait…', 'Wait…', 'Wag…', 100).quality == 100


def test_the_accelerator_takes_the_query_marker():
    result = adapt('&Open file', '_Open file', 'Maak lêer _oop', 80, accel_markers=('&',))

    assert result.target == 'Maak lêer &oop'
    assert result.rebuilt.accelerator
    assert result.quality == 99


def test_the_accelerator_is_left_without_the_style_marker():
    assert adapt('&Open file', '_Open file', 'Maak lêer _oop', 80).target == 'Maak lêer _oop'
    assert adapt('R&D', 'R_D', 'N_O', 80, accel_markers=('_',)).target == 'N_O', (
        'the query marker is not one of the style markers'
    )


def test_a_target_without_one_accelerator_is_left_for_the_check():
    assert adapt('&Open', '_Open', 'Oop', 80, ('&',)).target == 'Oop'
    assert adapt('&Open', '_Open', '_Maak _oop', 80, ('&',)).target == '_Maak _oop'


def test_the_accelerator_and_placeables_together():
    result = adapt('&Delete %(n)s', '_Delete {0}', '{0} _verwyder', 70, ('&',))

    assert result.target == '%(n)s &verwyder'


def test_import_needs_no_gtk():
    code = 'import sys, virtaal.support.abstraction; print("gi" in sys.modules)'

    assert (
        subprocess.run(
            [sys.executable, '-c', code], capture_output=True, text=True, check=True
        ).stdout.strip()
        == 'False'
    )


def test_the_version_is_two():
    assert abstraction.ABSTRACTION_VERSION == 2


@pytest.mark.parametrize(
    ('text', 'from_source', 'to_source', 'expected'),
    [
        ('Maak %s oop', 'Open %s', 'Open %d', 'Maak %d oop'),
        # In the translation's order.
        (
            '%(count)d lêers in %(dir)s',
            '%(dir)s has %(count)d files',
            '%(folder)s has %(n)d files',
            '%(n)d lêers in %(folder)s',
        ),
        ('Klik <b>hier</b>', 'Click <b>here</b>', 'Click <i>here</i>', 'Klik <i>hier</i>'),
        # Nothing to change.
        ('Maak %s oop', 'Open %s', 'Open %s now', None),
        ('Maak oop', 'Open %s', 'Open %d', None),
        # Not one to one.
        ('Maak %s oop', 'Open %s', 'Open %d of %s', None),
        ('Maak %s oop', 'Open %s', 'Open it', None),
        # A placeable the source doesn't have.
        ('Maak %s en %d oop', 'Open %s', 'Open %d', None),
    ],
)
def test_transfer(text, from_source, to_source, expected):
    assert abstraction.transfer(text, from_source, to_source) == expected


@pytest.mark.parametrize(
    ('query', 'source', 'target', 'expected'),
    [
        ('{{n}} files', '{{count}} files', '{{count}} lêers', '{{n}} lêers'),
        ('{n} files', '{count} files', '{count} lêers', '{n} lêers'),
        ('Hello { $name }', 'Hello { $user }', 'Hallo { $user }', 'Hallo { $name }'),
        ('Hi %{name}', 'Hi %{user}', 'Hallo %{user}', 'Hallo %{name}'),
        ('Open %2$@', 'Open %1$@', 'Maak %1$@ oop', 'Maak %2$@ oop'),
        ('Value {1:N2}', 'Value {0:N2}', 'Waarde {0:N2}', 'Waarde {1:N2}'),
        ('Save as %s', 'Save as {name}', 'Stoor as {name}', 'Stoor as %s'),
    ],
)
def test_a_format_placeholder_adapts_like_printf(query, source, target, expected):
    adapted = adapt(query, source, target, 70)

    assert adapted.target == expected
    assert adapted.rebuilt.unresolved == 0


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('{{count}} notes', [('{{count}}', 0, 9)]),
        ('Hi %1$@', [('%1$@', 3, 7)]),
        ('V {0:N2}', [('{0:N2}', 2, 8)]),
        ('Hi %<n>d', [('%<n>d', 3, 8)]),
    ],
)
def test_a_format_placeholder_is_one_variable(text, expected):
    assert [(p.raw, p.start, p.end) for p in placeables(text)] == expected
    assert {p.marker for p in placeables(text)} == {abstraction.VARIABLE}
