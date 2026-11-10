#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest

from virtaal.common import pan_app
from virtaal.support.libi18n.numbers import (
    format_number,
    format_percent,
    format_size,
    localise_digits,
    localise_number,
    localise_number_forms,
    native_numbering_system,
    target_numbering_system,
    ui_locale,
)


@pytest.mark.parametrize(
    'lang, expected',
    [
        ('pt_BR', 'pt_BR'),
        ('zh_CN', 'zh_Hans_CN'),
        ('sr@latin', 'sr_Latn'),
        ('ca@valencia', 'ca_ES_VALENCIA'),
        # Babel doesn't know Acholi; the pseudo-translation locales aren't real.
        ('ach', 'root'),
        ('pseudo-source', 'root'),
    ],
)
def test_ui_locale(lang, expected):
    assert str(ui_locale(lang)) == expected


@pytest.mark.parametrize(
    'lang, expected',
    [
        ('en', 'Page 12'),
        ('ar', 'Page 12'),
        ('ar_EG', 'Page ١٢'),
        ('fa', 'Page ۱۲'),
        ('bn_IN', 'Page ১২'),
    ],
)
def test_localise_digits_uses_the_default_numbering_system(monkeypatch, lang, expected):
    monkeypatch.setattr(pan_app, 'ui_language', lang)

    assert localise_digits('Page 12') == expected


@pytest.mark.parametrize(
    'lang, expected',
    [
        ('en', '1,234,567'),
        ('de', '1.234.567'),
        ('fa', '۱٬۲۳۴٬۵۶۷'),
        ('pseudo-source', '1,234,567'),
    ],
)
def test_format_number(monkeypatch, lang, expected):
    monkeypatch.setattr(pan_app, 'ui_language', lang)

    assert format_number(1234567) == expected


@pytest.mark.parametrize(
    'lang, fraction, decimals, expected',
    [
        ('en', 0.205, 1, '20.5%'),
        ('de', 0.205, 1, '20,5\xa0%'),
        ('tr', 0.205, 1, '%20,5'),
        ('bn_IN', 0.205, 1, '২০.৫%'),
        ('en', 1, 0, '100%'),
    ],
)
def test_format_percent(monkeypatch, lang, fraction, decimals, expected):
    monkeypatch.setattr(pan_app, 'ui_language', lang)

    assert format_percent(fraction, decimals) == expected


@pytest.mark.parametrize(
    'lang, size, expected',
    [
        ('en', 512, '512 byte'),
        ('en', 18_700, '18.7 kB'),
        ('de', 18_700, '18,7 kB'),
        ('fr', 18_700, '18,7\u202fko'),
        ('ne', 18_700, '१८.७ kB'),
        ('bn_IN', 512, '৫১২ বাইট'),
        ('en', 5_242_880, '5.2 MB'),
        ('en', 1_234_567_890_123_456, '1,234.6 TB'),
    ],
)
def test_format_size(monkeypatch, lang, size, expected):
    monkeypatch.setattr(pan_app, 'ui_language', lang)

    assert format_size(size) == expected


@pytest.mark.parametrize(
    'text, lang, expected',
    [
        ('1,234.5', 'af', '1\xa0234,5'),
        ('1,234.5', 'de', '1.234,5'),
        ('1,234.5', 'fr', '1\u202f234,5'),
        ('1,234,567.89', 'hi', '12,34,567.89'),
        ('1,234.5', 'bn_IN', '১,২৩৪.৫'),
        ('1,234.5', 'ar_EG', '١٬٢٣٤٫٥'),
        ('1234.5', 'de', '1234,5'),
        ('2026', 'de', '2026'),
        ('2026', 'ne', '२०२६'),
        ('0.50', 'af', '0,50'),
        ('-3', 'bn', '-৩'),
        ('+4', 'de', '+4'),
        ('1,234.5', 'ach', '1,234.5'),
    ],
)
def test_localise_number(text, lang, expected):
    assert localise_number(text, 'en', lang) == expected


@pytest.mark.parametrize('text', ['1.2.3', '007', '05', '2,5', '1,23,456', '1e5', 'x'])
def test_localise_number_leaves_out_what_is_not_a_plain_number(text):
    assert localise_number(text, 'en', 'de') is None


def test_localise_number_reads_the_source_languages_separators():
    assert localise_number('1.234,5', 'de', 'en') == '1,234.5'


def test_target_numbering_system_defaults_to_cldr():
    assert target_numbering_system('hi') == 'latn'
    assert target_numbering_system('bn_IN') == 'beng'
    assert target_numbering_system('sr@latin') == 'latn'


def test_target_numbering_system_entries_override_cldr(monkeypatch):
    monkeypatch.setattr('virtaal.support.libi18n.numbers.TARGET_NUMBERING_SYSTEMS', {'hi': 'deva'})

    assert target_numbering_system('hi_IN') == 'deva'
    assert localise_number('1,234', 'en', 'hi') == '१,२३४'


# Q36: a language's native digits where CLDR defaults to others


@pytest.mark.parametrize(
    'lang, expected',
    [
        ('hi', 'deva'),
        ('ur', 'arabext'),
        ('ta', 'tamldec'),
        ('th', 'thai'),
        ('ar', 'arab'),
        ('bn', None),  # its default has Bengali digits already
        ('ar_EG', None),
        ('en', None),
        ('zh', None),  # hanidec's digits aren't decimal digits
    ],
)
def test_native_numbering_system(lang, expected):
    assert native_numbering_system(lang) == expected


@pytest.mark.parametrize(
    'lang, expected',
    [
        ('hi', ['1,234.5', '१,२३४.५']),
        ('ar', ['1,234.5', '١٬٢٣٤٫٥']),
        ('th', ['1,234.5', '๑,๒๓๔.๕']),
        ('bn', ['১,২৩৪.৫']),
        ('de', ['1.234,5']),
    ],
)
def test_localise_number_forms_default_first(lang, expected):
    assert localise_number_forms('1,234.5', 'en', lang) == expected


def test_localise_number_forms_has_none_for_a_version():
    assert localise_number_forms('1.2.3', 'en', 'hi') == []


def test_an_entry_making_the_native_digits_the_default_gives_one_form(monkeypatch):
    monkeypatch.setattr('virtaal.support.libi18n.numbers.TARGET_NUMBERING_SYSTEMS', {'hi': 'deva'})

    assert localise_number_forms('1,234', 'en', 'hi') == ['१,२३४']
