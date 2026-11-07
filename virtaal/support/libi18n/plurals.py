#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A language's gettext Plural-Forms, for the catalogs Virtaal's tools
write: translate-toolkit's, else PLURAL_RULES'.

Kept free of virtaal imports so the tools in devsupport/ and po/ can
load it from its file.
"""

# Languages translate-toolkit has no plural rule for: code -> (nplurals,
# plural), from CLDR's plural rules.
PLURAL_RULES = {
    # Luganda: one (n == 1) and other.
    "lg": (2, "(n != 1)"),
}


def plural_rule(lang):
    """(nplurals, plural) for lang, or None if neither translate-toolkit
    nor PLURAL_RULES has one."""
    from translate.lang import factory

    language = factory.getlanguage(lang)
    if language.nplurals:
        return language.nplurals, language.pluralequation
    return PLURAL_RULES.get(lang)


def plural_forms(lang):
    """lang's Plural-Forms header value, or None."""
    rule = plural_rule(lang)
    return "nplurals=%d; plural=%s;" % rule if rule else None
