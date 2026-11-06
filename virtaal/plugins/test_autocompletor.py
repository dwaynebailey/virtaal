#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import re
from types import SimpleNamespace

import pytest
from gi.repository import GLib, Gtk
from translate.misc.multistring import multistring
from translate.storage.placeables import StringElem, general
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.plugins.autocompletor import AutoCompletor, NameForms, Plugin
from virtaal.views.widgets.textbox import TextBox


def _ac(comp_len=AutoCompletor.DEFAULT_COMPLETION_LENGTH):
    return AutoCompletor(main_controller=None, comp_len=comp_len)


class _Fake:
    """A plain, hashable stand-in for a widget - unlike SimpleNamespace,
    whose own __eq__ makes it unusable as a dict key (autocompletor
    keys _textbox_insert_ids by the widget itself)."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# isusable() #


def test_isusable_rejects_a_word_at_the_length_boundary():
    ac = _ac(comp_len=4)
    assert ac.isusable('123456') is False  # len 6 == comp_len + 2


def test_isusable_accepts_a_word_just_over_the_boundary():
    ac = _ac(comp_len=4)
    assert ac.isusable('1234567') is True  # len 7 > comp_len + 2


# add_words() / _update_word_list() #


def test_add_words_filters_out_words_too_short_to_be_usable():
    ac = _ac(comp_len=4)
    ac.add_words(['short', 'alongenoughword'])

    assert 'short' not in ac._word_list
    assert 'alongenoughword' in ac._word_list


def test_add_words_orders_the_word_list_by_frequency_descending():
    ac = _ac(comp_len=1)
    ac.add_words(['rare', 'common', 'common', 'common', 'medium', 'medium'])

    assert ac._word_list == ['common', 'medium', 'rare']


def test_add_words_with_update_false_defers_rebuilding_the_word_list():
    ac = _ac(comp_len=1)
    ac.add_words(['alongword'], update=False)

    assert 'alongword' in ac._word_freq
    assert ac._word_list == []


def test_add_words_accumulates_frequency_across_calls():
    ac = _ac(comp_len=1)
    ac.add_words(['word'])
    ac.add_words(['word'])

    assert ac._word_freq['word'] == 2


# set_unit() - the per-unit candidate pool #


def _unit(source, target=''):
    return SimpleNamespace(source=source, target=target)


def _parsing_ac(*parsers, comp_len=1):
    main_controller = SimpleNamespace(placeables_controller=SimpleNamespace(parsers=list(parsers)))
    return AutoCompletor(main_controller=main_controller, comp_len=comp_len)


def _term_parser(term, *translations):
    """A placeables parser that marks every occurrence of term as a
    TerminologyPlaceable with the given translations, the way the
    terminology plugin's matchers do."""

    def parse(text):
        if term not in text:
            return None
        before, _sep, after = text.partition(term)
        placeable = TerminologyPlaceable([term])
        placeable.translations = list(translations)
        return [StringElem(before), placeable, StringElem(after)]

    return parse


def _native_number_parse(text):
    """Marks runs of non-ASCII digits as NumberPlaceables, which
    NumberPlaceable's own 0-9 pattern doesn't."""
    parts = re.split(r'((?:(?![0-9])\d)+)', text)
    if len(parts) == 1:
        return None
    return [
        general.NumberPlaceable([p]) if i % 2 else StringElem(p) for i, p in enumerate(parts) if p
    ]


def test_set_unit_collects_words_from_the_source_and_the_target():
    ac = _ac(comp_len=1)

    ac.set_unit(_unit('Open the file', 'Maak die lêer oop'))

    assert 'file' in ac._word_list
    assert 'lêer' in ac._word_list


@pytest.mark.parametrize('word', ['করুন', 'हिन्दी', 'می‌خواهم'])
def test_set_unit_keeps_vowel_signs_viramas_and_zwnj_inside_words(word):
    ac = _ac(comp_len=1)

    ac.set_unit(_unit('Do it', word + '। ' + word))

    assert ac._word_list == [word]


def test_set_unit_keeps_the_usable_word_rule_for_unit_words():
    ac = _ac(comp_len=4)

    ac.set_unit(_unit('short', 'alongenoughword'))

    assert ac._word_list == ['alongenoughword']


def test_set_unit_replaces_the_previous_units_words():
    ac = _ac(comp_len=1)
    ac.set_unit(_unit('Helsinki', 'Helsinki'))

    ac.set_unit(_unit('Hello', 'Hallo'))

    assert ac.autocomplete('Hel') == ('Hello', 'lo')
    assert 'Helsinki' not in ac._word_list


def test_set_unit_collects_every_plural_form_skipping_empty_ones():
    ac = _ac(comp_len=1)

    ac.set_unit(_unit(multistring(['one file', 'many files']), multistring(['', 'baie lêers'])))

    assert {'file', 'files', 'lêers'} <= set(ac._word_list)


def test_set_unit_with_no_unit_clears_every_candidate():
    ac = _parsing_ac(general.UrlPlaceable.parse)
    ac.set_unit(_unit('See http://example.com/', 'Sien http://example.com/'))

    ac.set_unit(None)

    assert ac._word_list == []
    assert ac._fast_candidates == []


def test_set_unit_has_no_fast_candidates_without_a_placeables_controller():
    ac = _ac(comp_len=1)  # main_controller=None

    ac.set_unit(_unit('See http://example.com/'))  # must not raise

    assert ac._fast_candidates == []
    assert 'example' in ac._word_list


def test_set_unit_has_no_fast_candidates_with_no_parsers():
    ac = _parsing_ac()

    ac.set_unit(_unit('See http://example.com/'))

    assert ac._fast_candidates == []


def test_set_unit_offers_the_sources_placeables_as_fast_candidates():
    ac = _parsing_ac(*general.parsers)

    ac.set_unit(_unit('Open <b>%(name)s</b> at http://example.com/ in 2026'))

    assert ac._fast_candidates == [
        '<b>',
        '%(name)s',
        '</b>',
        'http://example.com/',
        '2026',
    ]


def test_set_unit_leaves_out_punctuation_and_too_short_placeables():
    ac = _parsing_ac(*general.parsers)

    ac.set_unit(_unit('Delete files… 4'))

    assert ac._fast_candidates == []


def test_set_unit_keeps_two_character_placeables_that_start_with_a_sigil():
    ac = _parsing_ac(*general.parsers)

    ac.set_unit(_unit('Delete %s files… 42'))

    assert ac._fast_candidates == ['%s', '42']


def test_set_unit_adds_variables_the_parsers_leave_out_in_source_order():
    ac = _parsing_ac(*general.parsers)

    ac.set_unit(_unit('{user} sent %1$s to ${dir} and $HOME in {{ count }}, %{n} {0}'))

    assert ac._fast_candidates == [
        '{user}',
        '%1$s',
        '${dir}',
        '$HOME',
        '{{ count }}',
        '%{n}',
        '{0}',
    ]


def _languages_ac(target, source='en'):
    main_controller = SimpleNamespace(
        placeables_controller=SimpleNamespace(parsers=list(general.parsers)),
        lang_controller=SimpleNamespace(
            source_lang=SimpleNamespace(code=source), target_lang=SimpleNamespace(code=target)
        ),
    )
    return AutoCompletor(main_controller=main_controller, comp_len=1)


@pytest.mark.parametrize(
    'target, expected',
    [
        ('af', ['1\xa0234,5', '2026', '0,25']),
        ('de', ['1.234,5', '2026', '0,25']),
        ('hi', ['1,234.5', '2026', '0.25']),
        ('bn_IN', ['১,২৩৪.৫', '২০২৬', '০.২৫']),
        ('ar_EG', ['١٬٢٣٤٫٥', '٢٠٢٦', '٠٫٢٥']),
    ],
)
def test_set_unit_offers_source_numbers_in_the_target_languages_form(target, expected):
    ac = _languages_ac(target)

    ac.set_unit(_unit('Copy 1,234.5 files in 2026 at 0.25'))

    assert ac._fast_candidates == expected


@pytest.mark.parametrize(
    'source',
    [
        'Version 1.2.3',
        'Version v2.50',
        'On 2026-11-05',
        'At 12:30',
        'Bug #4321',
        'Port 08080',
        'Built for x86_64',
        'Path /usr/lib/1234',
        'Item 1234-5',
        'Code A1234',
    ],
)
def test_set_unit_leaves_versions_codes_dates_and_ids_as_they_are(source):
    ac = _languages_ac('bn_IN')

    ac.set_unit(_unit(source))

    assert all(not re.search('[০-৯]', c) for c in ac._fast_candidates), ac._fast_candidates


def test_set_unit_offers_numbers_as_they_are_without_languages():
    ac = _parsing_ac(general.NumberPlaceable.parse)

    ac.set_unit(_unit('Copy 1,234.5 files'))

    assert ac._fast_candidates == ['1,234.5']


def test_autocomplete_fast_offers_a_native_digit_number_for_a_latin_digit():
    ac = _languages_ac('bn_IN')
    ac.set_unit(_unit('Copy 1,234.5 files'))

    assert ac.autocomplete_fast('1') == ('১,২৩৪.৫', ',২৩৪.৫')


def test_set_unit_offers_term_translations_not_the_term_itself():
    ac = _parsing_ac(_term_parser('Cancel', 'Kanselleer', 'Staak'))

    ac.set_unit(_unit('Cancel the download'))

    assert sorted(ac._fast_candidates) == ['Kanselleer', 'Staak']


def test_set_unit_collects_fast_candidates_from_every_plural_source_form():
    ac = _parsing_ac(general.NumberPlaceable.parse)

    ac.set_unit(_unit(multistring(['1000 file', '2000 files'])))

    assert ac._fast_candidates == ['1000', '2000']


# autocomplete_fast() #


def test_autocomplete_fast_completes_a_term_translation_at_a_short_prefix():
    ac = _parsing_ac(_term_parser('Cancel', 'Kanselleer'))
    ac.set_unit(_unit('Cancel'))

    assert ac.autocomplete_fast('Ka') == ('Kanselleer', 'nselleer')


def test_autocomplete_fast_completes_a_placeable_that_starts_with_punctuation():
    ac = _parsing_ac(general.XMLTagPlaceable.parse)
    ac.set_unit(_unit('Press </b> now'))

    assert ac.autocomplete_fast('Druk nou </') == ('</b>', 'b>')


def test_autocomplete_fast_completes_a_multi_word_term_translation():
    ac = _parsing_ac(_term_parser('Close', 'Maak toe'))
    ac.set_unit(_unit('Close'))

    assert ac.autocomplete_fast('Maak t') == ('Maak toe', 'oe')


def test_autocomplete_fast_needs_the_fast_completion_length():
    ac = _parsing_ac(_term_parser('Cancel', 'Kanselleer'))
    ac.set_unit(_unit('Cancel'))

    assert ac.autocomplete_fast('K') == (None, '')


@pytest.mark.parametrize(
    'source, typed, expected',
    [
        ('Delete %s now', 'Vee %', ('%s', 's')),
        ('Hello $name', 'Hallo $', ('$name', 'name')),
        ('Hello {name}', 'Hallo {', ('{name}', 'name}')),
        ('Press <b>', 'Druk <', ('<b>', 'b>')),
        ('Tom &amp; Jerry', 'Tom &', ('&amp;', 'amp;')),
        ('In 2026', 'In 2', ('2026', '026')),
    ],
)
def test_autocomplete_fast_completes_a_sigil_from_one_character(source, typed, expected):
    ac = _parsing_ac(*general.parsers)
    ac.set_unit(_unit(source))

    assert ac.autocomplete_fast(typed) == expected


def test_autocomplete_fast_offers_placeables_missing_from_the_target_first():
    ac = _parsing_ac(*general.parsers)
    ac.set_unit(_unit('Copy %s to %d of %u'))

    assert ac.autocomplete_fast('%', '%') == ('%s', 's')
    assert ac.autocomplete_fast('Kopieer %s na %', 'Kopieer %s na %') == ('%d', 'd')
    assert ac.autocomplete_fast('%d %s %', '%d %s %') == ('%u', 'u')


def test_autocomplete_fast_offers_a_repeated_placeable_until_the_target_has_each():
    ac = _parsing_ac(*general.parsers)
    ac.set_unit(_unit('%s and %s or %d'))

    assert ac.autocomplete_fast('%s %', '%s %') == ('%s', 's')
    assert ac.autocomplete_fast('%s %s %', '%s %s %') == ('%d', 'd')


def test_autocomplete_fast_still_offers_placeables_already_in_the_target():
    ac = _parsing_ac(*general.parsers)
    ac.set_unit(_unit('Delete %s'))

    assert ac.autocomplete_fast('%s %', '%s %') == ('%s', 's')


@pytest.mark.parametrize(
    'source, typed, expected',
    [
        ('In ১২৩৪', 'In 1', ('১২৩৪', '২৩৪')),
        ('In 2026', 'In ২০', ('2026', '26')),
        ('In ٢٠٢٦', 'In ۲', ('٢٠٢٦', '٠٢٦')),
    ],
)
def test_autocomplete_fast_matches_digits_by_value(source, typed, expected):
    ac = _parsing_ac(general.NumberPlaceable.parse, _native_number_parse)
    ac.set_unit(_unit(source))

    assert ac.autocomplete_fast(typed) == expected


def test_autocomplete_fast_matches_a_no_break_space_with_a_space():
    ac = _parsing_ac(_term_parser('Close', 'Maak\xa0toe'))
    ac.set_unit(_unit('Close'))

    assert ac.autocomplete_fast('Maak t') == ('Maak\xa0toe', 'oe')


def test_autocomplete_fast_counts_a_placeable_typed_in_other_digits_as_present():
    ac = _parsing_ac(general.NumberPlaceable.parse, _native_number_parse)
    ac.set_unit(_unit('২০২৬ or 2027'))

    assert ac.autocomplete_fast('2026 or 2', '2026 or 2') == ('2027', '027')


@pytest.mark.parametrize(
    'typed, expected',
    [
        ('Lees die ka', ('kanselleer', 'nselleer')),
        ('Ka', ('Kanselleer', 'nselleer')),
        ('Klaar. ka', ('Kanselleer', 'nselleer')),
    ],
)
def test_autocomplete_fast_completes_a_term_translation_in_the_typed_case(typed, expected):
    ac = _parsing_ac(_term_parser('cancel', 'Kanselleer'))
    ac.set_unit(_unit('Then cancel'))

    assert ac.autocomplete_fast(typed) == expected


def test_autocomplete_fast_completes_a_placeable_in_its_own_case():
    ac = _parsing_ac(general.XMLTagPlaceable.parse, general.CamelCasePlaceable.parse)
    ac.set_unit(_unit('Press <b>GitHub</b>'))

    assert ac.autocomplete_fast('Druk <B') == ('<b>', '>')
    assert ac.autocomplete_fast('Druk git') == ('GitHub', 'Hub')


def test_autocomplete_fast_does_not_complete_from_the_middle_of_a_word():
    ac = _parsing_ac(_term_parser('Cancel', 'Kanselleer'))
    ac.set_unit(_unit('Cancel'))

    assert ac.autocomplete_fast('aKa') == (None, '')


def test_autocomplete_fast_does_not_offer_a_completed_candidate():
    ac = _parsing_ac(general.NumberPlaceable.parse)
    ac.set_unit(_unit('In 2026'))

    assert ac.autocomplete_fast('In 2026') == (None, '')


# autocomplete() #


def test_autocomplete_finds_the_first_matching_prefix_by_frequency():
    ac = _ac(comp_len=1)
    ac.add_words(['helsinki', 'helsinki', 'hello'])  # helsinki more frequent

    word, suffix = ac.autocomplete('hel')

    assert word == 'helsinki'
    assert suffix == 'sinki'


def test_autocomplete_returns_none_and_empty_suffix_for_no_match():
    ac = _ac(comp_len=1)
    ac.add_words(['hello'])

    word, suffix = ac.autocomplete('xyz')

    assert word is None
    assert suffix == ''


def test_autocomplete_offers_a_long_word_typed_elsewhere_in_the_target():
    ac = _ac(comp_len=4)
    ac.set_unit(_unit('Open it', ''))

    assert ac.autocomplete('Hels', 'Ek woon in Helsinki en ') == ('Helsinki', 'inki')


def test_autocomplete_prefers_the_units_words_to_typed_ones():
    ac = _ac(comp_len=1)
    ac.set_unit(_unit('Helsinki', ''))

    assert ac.autocomplete('Hel', 'Hello ') == ('Helsinki', 'sinki')


def test_autocomplete_keeps_the_usable_word_rule_for_typed_words():
    ac = _ac(comp_len=4)

    assert ac.autocomplete('Hel', 'Hello ') == (None, '')


@pytest.mark.parametrize(
    'source, typed, at_start, expected',
    [
        # Capitalised only at the start of a sentence: the typed case wins.
        ('Download the files. Then quit.', 'down', False, ('download', 'load')),
        ('Download the files.', 'Down', False, ('Download', 'load')),
        ('download the files', 'down', True, ('Download', 'load')),
        ('Download the files.', 'DOWN', False, ('DOWNLOAD', 'LOAD')),
        # A name keeps its own case.
        ('Visit Helsinki today', 'hel', False, ('Helsinki', 'sinki')),
        ('Open LibreOffice', 'libre', False, ('LibreOffice', 'Office')),
        ('Open the WORKSPACE', 'work', True, ('WORKSPACE', 'SPACE')),
    ],
)
def test_autocomplete_ignores_case(source, typed, at_start, expected):
    ac = _ac(comp_len=1)
    ac.set_unit(_unit(source))

    assert ac.autocomplete(typed, at_start=at_start) == expected


def test_autocomplete_keeps_the_case_of_a_name_typed_in_the_target():
    ac = _ac(comp_len=4)
    ac.set_unit(_unit('Open it', ''))

    assert ac.autocomplete('hels', 'Ek woon in Helsinki en ') == ('Helsinki', 'inki')


@pytest.mark.parametrize(
    'before, source, expected',
    [
        ('', 'Open it', True),
        ('', 'open it', False),
        ('Klaar. ', 'open it', True),
        ('Klaar! ', 'open it', True),
        ('समाप्त। ', 'open it', True),
        ('Klaar, ', 'Open it', False),
    ],
)
def test_at_start(before, source, expected):
    ac = _ac()
    ac.set_unit(_unit(source))

    assert ac.at_start(before) is expected


# clear_words() #


def test_clear_words_resets_frequency_and_list():
    ac = _ac(comp_len=1)
    ac.add_words(['hello'])

    ac.clear_words()

    assert ac._word_list == []
    assert ac._word_freq['hello'] == 0


# remove_words() - normal (in-sync) case; the desync/exception case
# fixed in #3637 is covered separately below.


def test_remove_words_removes_a_word_present_in_both_structures():
    ac = _ac(comp_len=1)
    ac.add_words(['hello', 'world'])

    ac.remove_words('hello')

    assert 'hello' not in ac._word_list
    assert 'world' in ac._word_list


def test_remove_words_accepts_a_list_of_words():
    ac = _ac(comp_len=1)
    ac.add_words(['hello', 'world', 'kept'])

    ac.remove_words(['hello', 'world'])

    assert ac._word_list == ['kept']


def test_remove_words_tolerates_a_word_freq_entry_never_synced_to_word_list():
    """add_words(update=False) - used internally by set_unit()'s
    per-string loop - populates _word_freq without rebuilding _word_list,
    so the two can legitimately disagree about a given word. Removing
    such a word used to raise an uncaught ValueError from
    _word_list.remove() (only KeyError was ever caught)."""
    ac = _ac(comp_len=1)
    ac.add_words(['itisalongword'], update=False)
    assert 'itisalongword' in ac._word_freq
    assert 'itisalongword' not in ac._word_list

    ac.remove_words('itisalongword')  # must not raise

    assert 'itisalongword' not in ac._word_freq


def test_remove_words_tolerates_the_same_desync_for_a_list_of_words():
    ac = _ac(comp_len=1)
    ac.add_words(['itisalongword'], update=False)

    ac.remove_words(['itisalongword', 'nosuchword'])  # must not raise


# widget registration #


def _fake_widget():
    """A TextBox type tag (for isinstance checks in add_widget()/
    remove_widget()) with its connect()/disconnect() overridden -
    avoids constructing a real, GTK-widget-backed TextBox."""
    calls = []
    widget = TextBox.__new__(TextBox)
    widget.connect = lambda signal, handler: calls.append(('connect', signal)) or 'sigid'
    widget.disconnect = lambda sig_id: calls.append(('disconnect', sig_id))
    return widget, calls


def test_add_widget_ignores_an_already_registered_widget():
    ac = _ac()
    widget = object()
    ac.widgets.add(widget)

    ac.add_widget(widget)  # must not raise despite not being a TextBox


def test_add_widget_rejects_an_unsupported_widget_type():
    ac = _ac()

    with pytest.raises(ValueError):
        ac.add_widget(object())


def test_add_widget_dispatches_a_textbox_to_add_text_box(monkeypatch):
    ac = _ac()
    added = []
    monkeypatch.setattr(ac, '_add_text_box', added.append)
    textbox = TextBox.__new__(TextBox)  # a type tag only - _add_text_box is stubbed

    ac.add_widget(textbox)

    assert added == [textbox]


def test_add_text_box_connects_and_registers_the_widget():
    ac = _ac()
    widget, calls = _fake_widget()

    ac._add_text_box(widget)

    assert calls == [('connect', 'text-inserted')]
    assert widget in ac.widgets
    assert ac._textbox_insert_ids[widget] == 'sigid'


def test_remove_widget_ignores_a_widget_that_was_never_added():
    ac = _ac()

    ac.remove_widget(object())  # must not raise


def test_remove_widget_disconnects_a_registered_textbox():
    ac = _ac()
    widget, calls = _fake_widget()
    ac._add_text_box(widget)

    ac.remove_widget(widget)

    assert ('disconnect', 'sigid') in calls
    assert widget not in ac.widgets


def test_remove_textbox_does_nothing_before_any_widget_was_ever_added():
    ac = _ac()

    ac._remove_textbox(object())  # must not raise


def test_clear_widgets_removes_every_registered_widget():
    ac = _ac()
    w1, _ = _fake_widget()
    w2, _ = _fake_widget()
    ac._add_text_box(w1)
    ac._add_text_box(w2)

    ac.clear_widgets()

    assert ac.widgets == set()


# _on_insert_text() - the actual autocomplete-while-typing logic #


def _insert_text_box(prefix, suffix, buffer_text=None):
    """A fake textbox whose get_text() mirrors TextBox's own
    get_text(start, end)/get_text(start) shapes, backed by a real
    Gtk.TextBuffer for the final selection check."""
    buf = Gtk.TextBuffer()
    buf.set_text(buffer_text if buffer_text is not None else (prefix + suffix))
    calls = []
    textbox = _Fake(
        get_text=lambda start=0, end=None: prefix if end is not None else suffix,
        buffer=buf,
        handler_block=lambda hid: calls.append(('block', hid)),
        handler_unblock=lambda hid: calls.append(('unblock', hid)),
    )
    return textbox, calls


def test_on_insert_text_ignores_a_non_string_insertion():
    ac = _ac(comp_len=1)

    ac._on_insert_text(SimpleNamespace(), text=object(), offset=0, elem=None)  # must not raise


def test_on_insert_text_ignores_a_separator_character():
    ac = _ac(comp_len=1)

    ac._on_insert_text(SimpleNamespace(), text=' ', offset=0, elem=None)  # must not raise


def test_on_insert_text_ignores_a_multi_character_insertion():
    ac = _ac(comp_len=1)

    ac._on_insert_text(SimpleNamespace(), text='word', offset=0, elem=None)  # a paste, not typing


def test_on_insert_text_does_not_suggest_in_the_middle_of_a_word(monkeypatch):
    ac = _ac(comp_len=1)
    ac.add_words(['helsinki'])
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda *a, **k: pytest.fail('must not suggest mid-word'),
    )
    textbox, _ = _insert_text_box(prefix='hel', suffix='sinki')

    ac._on_insert_text(textbox, text='l', offset=2, elem=None)


def test_on_insert_text_does_not_suggest_a_word_shorter_than_comp_len(monkeypatch):
    ac = _ac(comp_len=10)
    ac.add_words(['helsinki'])
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda *a, **k: pytest.fail('must not suggest a too-short word'),
    )
    textbox, _ = _insert_text_box(prefix='hel', suffix='')

    ac._on_insert_text(textbox, text='l', offset=2, elem=None)


def test_on_insert_text_does_not_suggest_when_the_word_is_already_complete(monkeypatch):
    ac = _ac(comp_len=1)
    ac.add_words(['hello'])
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda *a, **k: pytest.fail('must not suggest an exact match'),
    )
    textbox, _ = _insert_text_box(prefix='hell', suffix='')

    ac._on_insert_text(textbox, text='o', offset=4, elem=None)


def test_on_insert_text_does_not_suggest_without_a_matching_word(monkeypatch):
    ac = _ac(comp_len=1)
    ac.add_words(['xyzxyzxyz'])
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda *a, **k: pytest.fail('must not suggest without a match'),
    )
    textbox, _ = _insert_text_box(prefix='hel', suffix='')

    ac._on_insert_text(textbox, text='l', offset=2, elem=None)


def test_on_insert_text_schedules_and_applies_a_deferred_suggestion(monkeypatch):
    ac = _ac(comp_len=1)
    ac.add_words(['helsinki'])
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda f, priority=None: scheduled.append((f, priority)),
    )
    textbox, calls = _insert_text_box(prefix='he', suffix='', buffer_text='helsinki')
    ac._textbox_insert_ids = {textbox: 'sigid'}

    ac._on_insert_text(textbox, text='l', offset=2, elem=None)

    assert len(scheduled) == 1
    func, priority = scheduled[0]
    assert priority == GLib.PRIORITY_HIGH

    assert func() is False  # runs the deferred suggestion for real
    assert textbox.suggestion == {'text': 'sinki', 'offset': 3}
    assert calls == [('block', 'sigid'), ('unblock', 'sigid')]
    start, end = textbox.buffer.get_selection_bounds()
    assert (start.get_offset(), end.get_offset()) == (3, 8)


def test_on_insert_text_suggests_a_term_translation_below_comp_len(monkeypatch):
    ac = _parsing_ac(_term_parser('Cancel', 'Kanselleer'), comp_len=4)
    ac.set_unit(_unit('Cancel'))
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda f, priority=None: scheduled.append(f),
    )
    textbox, _ = _insert_text_box(prefix='K', suffix='', buffer_text='Kanselleer')
    ac._textbox_insert_ids = {textbox: 'sigid'}

    ac._on_insert_text(textbox, text='a', offset=1, elem=None)

    scheduled[0]()
    assert textbox.suggestion == {'text': 'nselleer', 'offset': 2}


def test_on_insert_text_suggests_a_placeable_after_a_separator_character(monkeypatch):
    ac = _parsing_ac(general.XMLTagPlaceable.parse)
    ac.set_unit(_unit('Press </b> now'))
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda f, priority=None: scheduled.append(f),
    )
    textbox, _ = _insert_text_box(prefix='<', suffix='', buffer_text='</b>')
    ac._textbox_insert_ids = {textbox: 'sigid'}

    ac._on_insert_text(textbox, text='/', offset=1, elem=None)

    scheduled[0]()
    assert textbox.suggestion == {'text': 'b>', 'offset': 2}


def test_on_insert_text_suggests_the_missing_placeable_after_one_sigil(monkeypatch):
    ac = _parsing_ac(*general.parsers)
    ac.set_unit(_unit('Copy %s to %d'))
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda f, priority=None: scheduled.append(f),
    )
    textbox, _ = _insert_text_box(prefix='%', suffix=' %s', buffer_text='%d %s')
    ac._textbox_insert_ids = {textbox: 'sigid'}

    ac._on_insert_text(textbox, text='%', offset=0, elem=None)

    scheduled[0]()
    assert textbox.suggestion == {'text': 'd', 'offset': 1}


def test_on_insert_text_suggests_replacing_digits_typed_in_another_script(monkeypatch):
    ac = _parsing_ac(_native_number_parse)
    ac.set_unit(_unit('In ১২৩৪'))
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda f, priority=None: scheduled.append(f),
    )
    textbox, _ = _insert_text_box(prefix='', suffix='', buffer_text='1২৩৪')
    ac._textbox_insert_ids = {textbox: 'sigid'}

    ac._on_insert_text(textbox, text='1', offset=0, elem=None)

    scheduled[0]()
    assert textbox.suggestion == {'text': '২৩৪', 'offset': 1, 'replace': '১'}


def test_on_insert_text_suggests_a_word_typed_earlier_in_the_target(monkeypatch):
    ac = _ac(comp_len=4)
    ac.set_unit(_unit('Hello', ''))
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda f, priority=None: scheduled.append(f),
    )
    textbox, _ = _insert_text_box(
        prefix='die vertaling en vert', suffix='', buffer_text='die vertaling en vertaling'
    )
    ac._textbox_insert_ids = {textbox: 'sigid'}

    ac._on_insert_text(textbox, text='a', offset=21, elem=None)

    scheduled[0]()
    assert textbox.suggestion['text'] == 'ling'


def test_on_insert_text_does_not_complete_the_typed_word_to_itself(monkeypatch):
    ac = _ac(comp_len=4)
    ac.set_unit(_unit('Hello', ''))
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda *a, **k: pytest.fail('must not suggest the word being typed'),
    )
    textbox, _ = _insert_text_box(prefix='Vertalin', suffix='')

    ac._on_insert_text(textbox, text='g', offset=8, elem=None)


def test_on_insert_text_suggests_replacing_the_typed_case_of_a_name(monkeypatch):
    ac = _ac(comp_len=1)
    ac.set_unit(_unit('Visit Helsinki', ''))
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda f, priority=None: scheduled.append(f),
    )
    textbox, _ = _insert_text_box(prefix='Besoek he', suffix='', buffer_text='Besoek helsinki')
    ac._textbox_insert_ids = {textbox: 'sigid'}

    ac._on_insert_text(textbox, text='l', offset=9, elem=None)

    scheduled[0]()
    assert textbox.suggestion == {'text': 'sinki', 'offset': 10, 'replace': 'Hel'}


def test_on_insert_text_suggests_a_capital_at_the_start_of_a_sentence(monkeypatch):
    ac = _ac(comp_len=1)
    ac.set_unit(_unit('Open it. Download the files.', ''))
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.autocompletor.GLib.idle_add',
        lambda f, priority=None: scheduled.append(f),
    )
    textbox, _ = _insert_text_box(
        prefix='Maak oop. do', suffix='', buffer_text='Maak oop. download'
    )
    ac._textbox_insert_ids = {textbox: 'sigid'}

    ac._on_insert_text(textbox, text='w', offset=12, elem=None)

    scheduled[0]()
    assert textbox.suggestion == {'text': 'nload', 'offset': 13, 'replace': 'Dow'}


def test_on_insert_text_ignores_a_space_even_with_fast_candidates():
    ac = _parsing_ac(general.XMLTagPlaceable.parse)
    ac.set_unit(_unit('Press </b> now'))

    ac._on_insert_text(SimpleNamespace(), text=' ', offset=0, elem=None)  # must not raise


# NameForms - how this file's translator writes names #


def _names(*units):
    names = NameForms(AutoCompletor.word_re)
    for _ in names.load(list(units)):
        pass
    return names


def test_name_forms_default_to_the_name_itself():
    names = _names(_unit('Open GitHub', ''), _unit('Open it', 'খুলুন'))

    assert names.forms('GitHub') == ['GitHub']
    assert names.forms('NASA') == ['NASA']


def test_name_forms_learn_another_script_from_one_unit():
    names = _names(
        _unit('Open GitHub', 'গিটহাব খুলুন'),
        _unit('Open it', 'এটা খুলুন'),
    )

    assert names.forms('GitHub') == ['গিটহাব', 'GitHub']


def test_name_forms_need_one_word_found_only_with_the_name():
    names = _names(_unit('Open GitHub', 'গিটহাব খুলুন'))

    assert names.forms('GitHub') == ['GitHub']  # গিটহাব or খুলুন?


def test_name_forms_keep_latin_when_the_translator_last_did():
    names = _names(
        _unit('Open GitHub', 'গিটহাব খুলুন'),
        _unit('Open it', 'এটা খুলুন'),
        _unit('Close GitHub', 'GitHub বন্ধ'),
    )

    assert names.forms('GitHub') == ['GitHub', 'গিটহাব']


def test_name_forms_ignore_untranslated_units():
    names = _names(_unit('Open NASA', 'নাসা খুলুন'), _unit('Open it', 'খুলুন'), _unit('NASA', ''))

    assert names.forms('NASA') == ['নাসা', 'NASA']


def test_name_forms_learn_from_a_unit_edited_in_this_session():
    open_github = _unit('Open GitHub', 'গিটহাব খুলুন')
    names = _names(open_github, _unit('Open it', 'এটা খুলুন'))

    names.enter(open_github)
    open_github.target = 'GitHub খুলুন'
    names.leave(open_github)

    assert names.forms('GitHub') == ['GitHub']


def test_name_forms_count_an_unchanged_visit_as_no_decision():
    first = _unit('Open GitHub', 'গিটহাব খুলুন')
    latin = _unit('Close GitHub', 'GitHub বন্ধ')
    names = _names(latin, first, _unit('Open it', 'এটা খুলুন'))

    names.enter(latin)
    names.leave(latin)

    assert names.forms('GitHub') == ['গিটহাব', 'GitHub']


def test_name_forms_read_a_unit_entered_before_the_load_reached_it():
    first = _unit('Open GitHub', 'গিটহাব খুলুন')
    names = NameForms(AutoCompletor.word_re)
    reader = names.load([_unit('Open it', 'এটা খুলুন'), first])
    names.enter(first)
    for _ in reader:
        pass

    names.leave(first)

    assert names.forms('GitHub') == ['গিটহাব', 'GitHub']


def test_set_unit_offers_a_name_in_its_learnt_form_from_its_latin_letters():
    ac = _parsing_ac(*general.parsers)
    for _ in ac.names.load([_unit('Open GitHub', 'গিটহাব খুলুন'), _unit('Open it', 'এটা খুলুন')]):
        pass

    ac.set_unit(_unit('Close GitHub'))

    assert ac._fast_candidates == ['গিটহাব']
    assert ac.autocomplete_fast('Gi') == ('গিটহাব', 'টহাব')
    assert ac.autocomplete_fast('গি') == ('গিটহাব', 'টহাব')


def test_set_unit_learns_from_the_unit_left():
    ac = _parsing_ac(*general.parsers)
    open_github = _unit('Open GitHub', '')
    for _ in ac.names.load([open_github, _unit('Open it', 'এটা খুলুন')]):
        pass
    ac.set_unit(open_github)
    open_github.target = 'গিটহাব খুলুন'

    ac.set_unit(_unit('Close GitHub'))

    assert ac._fast_candidates == ['গিটহাব']


def test_plugin_reads_the_files_names_in_the_background(monkeypatch):
    scheduled = []
    monkeypatch.setattr('virtaal.plugins.autocompletor.GLib.idle_add', scheduled.append)
    monkeypatch.setattr(NameForms, 'CHUNK', 1)
    plugin = Plugin.__new__(Plugin)
    plugin.autocomp = AutoCompletor(main_controller=None, comp_len=1)
    units = [_unit('Open GitHub', 'গিটহাব খুলুন'), _unit('Open it', 'এটা খুলুন')]
    cursor = SimpleNamespace(connect=lambda *a: 'cursor-sig', deref=lambda: None)
    storecontroller = SimpleNamespace(
        cursor=cursor, get_store=lambda: SimpleNamespace(get_units=lambda: units)
    )

    plugin._on_store_loaded(storecontroller)
    read = scheduled[1]

    assert [read(), read(), read()] == [True, True, False]
    assert plugin._names_id is None
    assert plugin.autocomp.names.forms('GitHub') == ['গিটহাব', 'GitHub']


# Plugin - completely untested until now #


def _plugin_main_controller(store=None, targets=None):
    calls = []
    store_controller = SimpleNamespace(
        connect=lambda signal, handler: calls.append((signal, handler)) or 'store-sig',
        disconnect=lambda sig_id: calls.append(('disconnect', sig_id)),
        get_store=lambda: store,
    )
    unitview = SimpleNamespace(
        targets=targets or [],
        connect=lambda signal, handler: calls.append((signal, handler)) or 'unitview-sig',
        disconnect=lambda sig_id: calls.append(('disconnect', sig_id)),
    )
    main_controller = SimpleNamespace(
        store_controller=store_controller,
        unit_controller=SimpleNamespace(view=unitview),
    )
    return main_controller, calls


def test_plugin_init_waits_for_targets_when_none_exist_yet():
    main_controller, calls = _plugin_main_controller(store=None, targets=[])

    plugin = Plugin('autocompletor', main_controller)

    assert isinstance(plugin.autocomp, AutoCompletor)
    assert ('store-loaded', plugin._on_store_loaded) in calls
    assert ('targets-created', plugin._connect_to_textboxes) in calls
    assert plugin._unitview_id == 'unitview-sig'


def test_plugin_init_connects_to_textboxes_when_targets_already_exist(monkeypatch):
    added = []
    monkeypatch.setattr(AutoCompletor, 'add_widget', lambda self, widget: added.append(widget))
    target = object()
    main_controller, calls = _plugin_main_controller(store=None, targets=[target])

    plugin = Plugin('autocompletor', main_controller)

    assert added == [target]
    assert plugin._unitview_id is None


def test_plugin_init_connects_to_an_already_loaded_store(monkeypatch):
    monkeypatch.setattr('virtaal.plugins.autocompletor.GLib.idle_add', lambda f: None)
    store = SimpleNamespace(get_units=lambda: [])
    cursor = SimpleNamespace(connect=lambda *a: 'cursor-sig', deref=lambda: None)
    main_controller, calls = _plugin_main_controller(store=store, targets=[])
    main_controller.store_controller.cursor = cursor

    plugin = Plugin('autocompletor', main_controller)

    assert plugin.store_cursor is cursor


def test_plugin_destroy_disconnects_every_signal(monkeypatch):
    monkeypatch.setattr('virtaal.plugins.autocompletor.GLib.idle_add', lambda f: None)
    main_controller, calls = _plugin_main_controller(store=None, targets=[])
    plugin = Plugin('autocompletor', main_controller)
    plugin._cursor_changed_id = 'cursor-sig'
    plugin.store_cursor = SimpleNamespace(
        disconnect=lambda sig: calls.append(('cursor-disconnect', sig))
    )

    plugin.destroy()

    assert plugin.autocomp._word_list == []
    assert ('disconnect', 'store-sig') in calls
    assert ('cursor-disconnect', 'cursor-sig') in calls
    assert ('disconnect', 'unitview-sig') in calls


def test_plugin_destroy_skips_optional_disconnects_when_never_set(monkeypatch):
    added = []
    monkeypatch.setattr(AutoCompletor, 'add_widget', lambda self, widget: added.append(widget))
    main_controller, calls = _plugin_main_controller(store=None, targets=[object()])
    plugin = Plugin('autocompletor', main_controller)

    plugin.destroy()  # must not raise despite no cursor_changed_id, and a None _unitview_id

    assert ('disconnect', 'store-sig') in calls


def _cursor_on(*units):
    remaining = iter(units)
    return SimpleNamespace(deref=lambda: next(remaining))


def test_on_cursor_change_loads_the_new_units_words(monkeypatch):
    scheduled = []
    monkeypatch.setattr('virtaal.plugins.autocompletor.GLib.idle_add', scheduled.append)
    plugin = Plugin.__new__(Plugin)
    plugin.autocomp = AutoCompletor(main_controller=None, comp_len=1)

    plugin._on_cursor_change(_cursor_on(_unit('Hello world', 'Hallo wêreld')))
    scheduled[0]()

    assert plugin.autocomp.autocomplete('wê') == ('wêreld', 'reld')


def test_on_cursor_change_does_not_offer_another_units_words(monkeypatch):
    scheduled = []
    monkeypatch.setattr('virtaal.plugins.autocompletor.GLib.idle_add', scheduled.append)
    plugin = Plugin.__new__(Plugin)
    plugin.autocomp = AutoCompletor(main_controller=None, comp_len=1)
    cursor = _cursor_on(_unit('Helsinki', 'Helsinki'), _unit('Open', 'Oop'))

    plugin._on_cursor_change(cursor)
    scheduled[0]()
    plugin._on_cursor_change(cursor)
    scheduled[1]()

    assert plugin.autocomp.autocomplete('Hel') == (None, '')


def test_on_cursor_change_after_a_cursor_with_no_unit(monkeypatch):
    # #4385: a Replace All that leaves no search matches puts the cursor on
    # no unit; leaving Search then raised AttributeError.
    scheduled = []
    monkeypatch.setattr('virtaal.plugins.autocompletor.GLib.idle_add', scheduled.append)
    plugin = Plugin.__new__(Plugin)
    plugin.autocomp = AutoCompletor(main_controller=None, comp_len=1)
    cursor = _cursor_on(_unit('helloworld', 'helloworld'), None, _unit('nextunit'))

    plugin._on_cursor_change(cursor)
    scheduled[0]()
    plugin._on_cursor_change(cursor)
    scheduled[1]()  # must not raise
    assert plugin.autocomp._word_list == []
    plugin._on_cursor_change(cursor)
    scheduled[2]()

    assert plugin.autocomp._word_list == ['nextunit']


def test_on_store_loaded_wires_the_cursor_and_loads_its_unit(monkeypatch):
    scheduled = []
    monkeypatch.setattr('virtaal.plugins.autocompletor.GLib.idle_add', scheduled.append)
    plugin = Plugin.__new__(Plugin)
    plugin.autocomp = AutoCompletor(main_controller=None, comp_len=1)
    cursor_calls = []
    cursor = SimpleNamespace(
        connect=lambda signal, handler: cursor_calls.append((signal, handler)) or 'cursor-sig',
        deref=lambda: _unit('helloworld'),
    )
    storecontroller = SimpleNamespace(cursor=cursor)

    plugin._on_store_loaded(storecontroller)
    scheduled[0]()

    assert plugin.autocomp._word_list == ['helloworld']
    assert plugin.store_cursor is cursor
    assert cursor_calls == [('cursor-changed', plugin._on_cursor_change)]
    assert plugin._cursor_changed_id == 'cursor-sig'


def test_on_store_loaded_disconnects_a_previous_cursor_subscription(monkeypatch):
    monkeypatch.setattr('virtaal.plugins.autocompletor.GLib.idle_add', lambda f: None)
    plugin = Plugin.__new__(Plugin)
    plugin.autocomp = AutoCompletor(main_controller=None, comp_len=1)
    plugin._cursor_changed_id = 'old-sig'
    disconnected = []
    plugin.store_cursor = SimpleNamespace(disconnect=disconnected.append)
    store = SimpleNamespace(get_units=lambda: [])
    cursor = SimpleNamespace(connect=lambda *a: 'new-sig', deref=lambda: None)
    storecontroller = SimpleNamespace(get_store=lambda: store, cursor=cursor)

    plugin._on_store_loaded(storecontroller)

    assert disconnected == ['old-sig']
    assert plugin._cursor_changed_id == 'new-sig'
