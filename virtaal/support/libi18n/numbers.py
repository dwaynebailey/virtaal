#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Numbers formatted for the UI or a target language: Babel's CLDR
patterns and symbols, with the digits of the locale's default numbering
system."""

import copy
import functools
import re
from decimal import Decimal

from babel import Locale, UnknownLocaleError
from babel.numbers import (
    UnsupportedNumberingSystemError,
    get_decimal_symbol,
    get_group_symbol,
    get_plus_sign_symbol,
)
from babel.units import format_unit

from virtaal.support.libi18n.cldr_numbering_systems import DIGITS

LATIN = "0123456789"

# gettext's @modifiers as CLDR subtags.
_MODIFIERS = {"latin": "_Latn", "cyrillic": "_Cyrl", "valencia": "_ES_VALENCIA"}


def ui_locale(lang=None):
    """The Babel Locale for a UI language code such as "pt_BR" or
    "sr@latin" (the current UI language by default), or CLDR's root
    locale for one Babel doesn't know."""
    if lang is None:
        from virtaal.common import pan_app

        lang = pan_app.ui_language or "en"
    code, _, modifier = lang.partition("@")
    try:
        return Locale.parse(code + _MODIFIERS.get(modifier, ""))
    except (ValueError, UnknownLocaleError):
        return Locale.parse("root")


# Numbering systems for translations into a language where they differ
# from CLDR's default for its locale, as language code: CLDR numbering
# system ("latn", "deva", ...).
TARGET_NUMBERING_SYSTEMS = {}


def target_numbering_system(lang):
    """The numbering system of translations into lang: an entry of
    TARGET_NUMBERING_SYSTEMS, or the CLDR default for its locale."""
    code = lang.partition("@")[0]
    for key in (lang, code, code.partition("_")[0]):
        if key in TARGET_NUMBERING_SYSTEMS:
            return TARGET_NUMBERING_SYSTEMS[key]
    return ui_locale(lang).default_numbering_system


def native_numbering_system(lang):
    """CLDR's native-digit numbering system for lang when it isn't the
    one translations into lang use (hi's deva, ar's arab, th's thai),
    else None."""
    native = ui_locale(lang).other_numbering_systems.get("native")
    if native not in DIGITS or native == target_numbering_system(lang):
        return None
    return native


def localise_digits(text, locale=None, numbering_system=None):
    """text with its ASCII digits replaced by those of numbering_system,
    by default the locale's default numbering system."""
    digits = DIGITS.get(numbering_system or (locale or ui_locale()).default_numbering_system, LATIN)
    if digits == LATIN:
        return text
    return text.translate(str.maketrans(LATIN, digits))


def format_number(number):
    """An integer in the UI language's digits and grouping."""
    locale = ui_locale()
    return localise_digits(
        locale.decimal_formats[None].apply(number, locale, numbering_system="default"), locale
    )


def format_percent(fraction, decimals=1):
    """fraction (0.25 for 25%) as a percentage in the UI language's
    own pattern, with exactly decimals decimal places."""
    locale = ui_locale()
    pattern = copy.copy(locale.percent_formats[None])
    pattern.frac_prec = (decimals, decimals)
    return localise_digits(pattern.apply(fraction, locale, numbering_system="default"), locale)


def format_size(size):
    """A file size of size bytes in the UI language, in decimal units like
    GLib.format_size: bytes below 1,000, then kB, MB, GB or TB with
    one decimal place. CLDR's short unit names, because many
    locales lack the long ones."""
    locale = ui_locale()
    if size < 1000:
        text = format_unit(size, "digital-byte", "short", locale=locale, numbering_system="default")
    else:
        for unit in ("kilobyte", "megabyte", "gigabyte", "terabyte"):  # noqa: B007 - used after the loop
            size /= 1000
            if size < 1000:
                break
        text = format_unit(
            size, "digital-" + unit, "short", "#,##0.0", locale, numbering_system="default"
        )
    return localise_digits(text, locale)


@functools.lru_cache(maxsize=32)
def _number_re(lang):
    """A regular expression for a plain number as lang writes it: sign,
    integer part (in lang's groups, or not grouped) and decimals."""
    locale = ui_locale(lang)
    group = re.escape(get_group_symbol(locale, numbering_system="default"))
    decimal = re.escape(get_decimal_symbol(locale, numbering_system="default"))
    primary, secondary = locale.decimal_formats[None].grouping
    grouped = r"[0-9]{1,%d}(?:%s[0-9]{%d})*%s[0-9]{%d}" % (
        secondary,
        group,
        secondary,
        group,
        primary,
    )
    return re.compile(
        r"([-+]?)(?:(?P<grouped>%s)|(?P<plain>[0-9]+))(?:%s([0-9]+))?\Z" % (grouped, decimal)
    )


def localise_number(text, source_lang, target_lang):
    """text, a number as source_lang writes it, in target_lang's decimal
    and group separators and digits, grouped only if text is; None for
    anything else (versions, codes, numbers with leading zeros)."""
    forms = localise_number_forms(text, source_lang, target_lang)
    return forms[0] if forms else None


def localise_number_forms(text, source_lang, target_lang):
    """L{localise_number()}'s form of text, then the same in
    target_lang's native digits if it has others (L{native_numbering_system}),
    or [] for anything but a plain number."""
    match = _number_re(source_lang).match(text)
    if not match:
        return []
    sign, grouped, plain, decimals = match.group(1, "grouped", "plain", 4)
    integer = re.sub("[^0-9]", "", grouped or plain)
    if len(integer) > 1 and integer.startswith("0"):
        return []
    locale = ui_locale(target_lang)
    pattern = copy.copy(locale.decimal_formats[None])
    pattern.frac_prec = (len(decimals or ""), len(decimals or ""))
    value = Decimal(integer + "." + (decimals or "0"))
    systems = (target_numbering_system(target_lang), native_numbering_system(target_lang))
    return [
        _formatted(pattern, -value if sign == "-" else value, sign, bool(grouped), locale, system)
        for system in systems
        if system
    ]


def _formatted(pattern, value, sign, grouped, locale, system):
    """value by pattern in locale, with system's digits and separators
    (arab's ٫ and ٬) where CLDR has them for locale."""
    symbols = "default" if system == locale.default_numbering_system else system
    try:
        result = pattern.apply(value, locale, group_separator=grouped, numbering_system=symbols)
    except UnsupportedNumberingSystemError:
        symbols = "default"
        result = pattern.apply(value, locale, group_separator=grouped, numbering_system=symbols)
    if sign == "+":
        result = get_plus_sign_symbol(locale, numbering_system=symbols) + result
    return localise_digits(result, locale, system)
