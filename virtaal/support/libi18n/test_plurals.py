#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from translate.lang import factory

from virtaal.support.libi18n import plurals


def test_translate_toolkits_rule_comes_first():
    assert plurals.plural_rule("de") == (2, "(n != 1)")
    assert plurals.plural_forms("ja") == "nplurals=1; plural=0;"


def test_the_table_fills_in_what_translate_toolkit_lacks(monkeypatch):
    assert plurals.plural_forms("lg") == "nplurals=2; plural=(n != 1);"
    monkeypatch.setitem(plurals.PLURAL_RULES, "xx", (3, "(n == 1 ? 0 : n == 2 ? 1 : 2)"))
    assert plurals.plural_forms("xx") == "nplurals=3; plural=(n == 1 ? 0 : n == 2 ? 1 : 2);"


def test_a_language_with_no_rule_has_no_plural_forms():
    assert plurals.plural_rule("xx-unknown") is None
    assert plurals.plural_forms("xx-unknown") is None


def test_the_table_only_holds_languages_translate_toolkit_lacks():
    # Once translate-toolkit has one, its entry here is stale.
    assert [lang for lang in plurals.PLURAL_RULES if factory.getlanguage(lang).nplurals] == []
