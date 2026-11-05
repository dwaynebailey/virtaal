#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Placeables marked up for machine translation and restored from the
reply, with what the service lost added and what it repeated dropped."""

import pytest

from virtaal.plugins.tm.mtplaceables import Restored, Spans, XmlIds, suggestion

XML = XmlIds()
SPANS = Spans('<span class="notranslate">', '</span>')
ALWAYS = Spans('<span translate="no">', '</span>', always=True)
WRAPPED = Spans('<span class="notranslate">', '</span>', ('<div>', '</div>'))


def _round_trip(style, text, reply=None):
    protected = style.protect(text)
    return protected, style.restore(protected.text if reply is None else reply, protected)


# What goes out.


def test_each_placeable_is_an_empty_xml_element():
    protected = XML.protect('Delete %(count)s of %d files')

    assert protected.text == 'Delete <x id="0"/> of <x id="1"/> files'
    assert protected.markup


def test_a_tag_pair_holds_what_it_marks():
    protected = XML.protect('Click <b>here</b> or <a href="x">there</a>')

    assert protected.text == 'Click <g id="0">here</g> or <g id="2">there</g>'


def test_tags_that_cross_are_each_empty_elements():
    assert XML.protect('<b><i>x</b></i>').text == '<x id="0"/><x id="1"/>x<x id="2"/><x id="3"/>'


def test_an_unpaired_tag_is_an_empty_element():
    assert XML.protect('Line<br>break').text == 'Line<x id="0"/>break'


def test_the_text_around_placeables_is_escaped():
    assert XML.protect('Tom & Jerry > %s').text == 'Tom &amp; Jerry &gt; <x id="0"/>'


def test_text_without_placeables_goes_as_it_is():
    protected = XML.protect('Tom & Jerry <-')

    assert (protected.text, protected.markup) == ('Tom & Jerry <-', False)
    assert XML.restore('Tom &amp; Jerry', protected) == Restored(
        'Tom &amp; Jerry', 'Tom &amp; Jerry'
    )


def test_a_service_reading_all_text_as_html_gets_it_escaped():
    protected = ALWAYS.protect('Tom & Jerry <-')

    assert (protected.text, protected.markup) == ('Tom &amp; Jerry &lt;-', True)
    assert ALWAYS.restore('Tom &amp; Jerry &lt;-', protected).text == 'Tom & Jerry <-'


def test_each_placeable_is_in_an_element_left_untranslated():
    protected = SPANS.protect('Click <b>here</b> for %s')

    assert protected.text == (
        'Click <span class="notranslate">&lt;b&gt;</span>here'
        '<span class="notranslate">&lt;/b&gt;</span> for <span class="notranslate">%s</span>'
    )


def test_the_whole_can_be_one_element():
    assert WRAPPED.protect('Delete %s').text == (
        '<div>Delete <span class="notranslate">%s</span></div>'
    )


# What comes back.


@pytest.mark.parametrize('style', [XML, SPANS, ALWAYS, WRAPPED])
@pytest.mark.parametrize(
    'text',
    [
        'Delete %(count)s files',
        'Click <b>here</b> or <a href="x">there</a> & go',
        'Tom &amp; Jerry',
        'See https://example.org/x. Or mail a@example.org',
        '%1 of %1 and %2',
    ],
)
def test_an_untouched_reply_restores_the_source(style, text):
    _protected, restored = _round_trip(style, text)

    assert restored == Restored(text, text)


def test_placeables_the_service_moved_stay_where_it_put_them():
    _protected, restored = _round_trip(XML, 'Copy %1 to %2', 'Kopieer <x id="1"/> na <x id="0"/>')

    assert restored == Restored('Kopieer %2 na %1', 'Kopieer %2 na %1')


def test_a_tag_pair_comes_back_round_what_the_service_put_in_it():
    _protected, restored = _round_trip(XML, 'Click <b>here</b> now', 'Klik <g id="0">nou hier</g>')

    assert restored.text == restored.returned == 'Klik <b>nou hier</b>'


def test_a_lost_placeable_is_added_at_the_end():
    _protected, restored = _round_trip(XML, 'Delete %(count)s files', 'Verwyder lêers')

    assert restored == Restored('Verwyder lêers %(count)s', 'Verwyder lêers')


def test_a_lost_tag_pair_is_added_at_the_end():
    _protected, restored = _round_trip(XML, 'Click <b>here</b>', 'Klik hier')

    assert restored == Restored('Klik hier <b></b>', 'Klik hier')


def test_a_repeated_placeable_is_dropped():
    # DeepL repeats a tag when the words around it move apart.
    _protected, restored = _round_trip(XML, 'Delete %s now', 'Verwyder <x id="0"/> nou <x id="0"/>')

    assert restored == Restored('Verwyder %s nou ', 'Verwyder %s nou %s')


def test_an_unknown_or_out_of_range_tag_is_dropped():
    _protected, restored = _round_trip(
        XML, 'Delete %s', 'Verwyder <x id="0"/><x id="7"/><b>!</b></g>'
    )

    assert restored == Restored('Verwyder %s!', 'Verwyder %s!')


def test_an_empty_element_written_out_in_full_counts_once():
    _protected, restored = _round_trip(XML, 'Delete %s', 'Verwyder <x id="0"></x>')

    assert restored.text == restored.returned == 'Verwyder %s'


def test_an_element_found_by_its_text_whatever_its_attributes():
    _protected, restored = _round_trip(
        SPANS, 'Delete %s files', "Verwyder <span class='notranslate' lang=en> %s </span> lêers"
    )

    assert restored.text == restored.returned == 'Verwyder %s lêers'


def test_a_placeable_back_without_its_element_is_not_added_again():
    _protected, restored = _round_trip(SPANS, 'Delete %s files', 'Verwyder %s lêers')

    assert restored == Restored('Verwyder %s lêers', 'Verwyder %s lêers')


def test_repeats_of_one_text_restore_in_order():
    _protected, restored = _round_trip(
        SPANS,
        '%1 or %1',
        '<span class="notranslate">%1</span> of <span class="notranslate">%1</span>',
    )

    assert restored == Restored('%1 of %1', '%1 of %1')


def test_an_element_round_other_text_keeps_that_text():
    _protected, restored = _round_trip(
        SPANS, 'Delete %s', 'Verwyder <span class="notranslate">dit</span> %s'
    )

    assert restored.text == 'Verwyder dit %s'


def test_a_reply_without_the_wrapping_element_is_still_read():
    _protected, restored = _round_trip(
        WRAPPED, 'Delete %s', 'Verwyder <span class="notranslate">%s</span>'
    )

    assert restored.text == 'Verwyder %s'


def test_a_string_holding_a_marker_character_goes_as_it_is():
    protected = XML.protect('Icon  %s')

    assert (protected.text, protected.markup) == ('Icon  %s', False)


# The suggestion.


def test_a_restored_suggestion_shows_what_came_back_only_if_it_differs():
    assert suggestion('Delete %s', Restored('Verwyder %s', 'Verwyder %s'), 'MT') == {
        'source': 'Delete %s',
        'target': 'Verwyder %s',
        'tmsource': 'MT',
    }
    flagged = suggestion('Delete %s', Restored('Verwyder %s', 'Verwyder'), 'MT')
    assert flagged['tm_target'] == 'Verwyder'
