# -*- coding: utf-8 -*-
#
# Copyright 2026 Zuza Software Foundation
#
# This file is part of Virtaal.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import pytest

from translate.storage import po

from virtaal.support import gettextpo

pytestmark = pytest.mark.skipif(not gettextpo.available(),
                                reason="libgettextpo is not available")

HEADER = b'''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"

'''


def check(entry, header=HEADER):
    store = po.pofile.parsestring(header + entry)
    checker = gettextpo.MsgfmtChecker()
    checker.set_header(store.header().target)
    return checker.check_unit(store.units[-1])


def test_good():
    assert check(b'#, c-format\nmsgid "Hello %s"\nmsgstr "Hallo %s"\n') == []


def test_untranslated():
    assert check(b'#, c-format\nmsgid "Hello %s"\nmsgstr ""\n') == []


def test_c_format():
    problems = check(b'#, c-format\nmsgid "Hello %s"\nmsgstr "Hallo"\n')
    assert len(problems) == 1
    assert "format specifications" in problems[0]


def test_no_format_flag():
    assert check(b'msgid "Hello %s"\nmsgstr "Hallo"\n') == []
    assert check(b'#, no-c-format\nmsgid "Hello %s"\nmsgstr "Hallo"\n') == []


def test_python_format():
    problems = check(b'#, python-format\nmsgid "%(name)s"\nmsgstr "%(naam)s"\n')
    assert len(problems) == 1
    assert "naam" in problems[0]


def test_fuzzy_is_checked():
    assert check(b'#, fuzzy, c-format\nmsgid "Hello %s"\nmsgstr "Hallo"\n')


def test_newlines():
    problems = check(b'msgid "line\\n"\nmsgstr "lyn"\n')
    assert len(problems) == 1
    assert "\\n" in problems[0]


def test_plural_format():
    entry = (b'#, c-format\nmsgid "one %d"\nmsgid_plural "many %d"\n'
             b'msgstr[0] "een"\nmsgstr[1] "baie %s"\n')
    problems = check(entry)
    assert len(problems) == 1
    assert "msgstr[1]" in problems[0]


def test_plural_count():
    entry = (b'msgid "one"\nmsgid_plural "many"\n'
             b'msgstr[0] "een"\nmsgstr[1] "baie"\n')
    assert check(entry) == []
    header = HEADER.replace(b"nplurals=2; plural=(n != 1)",
                            b"nplurals=3; plural=(n==1 ? 0 : n==2 ? 1 : 2)")
    assert check(entry, header)


def test_header_problems_only_on_header():
    store = po.pofile.parsestring(HEADER + b'msgid "a"\nmsgstr "b"\n')
    checker = gettextpo.MsgfmtChecker()
    assert checker.check_unit(store.units[0])
    assert checker.check_unit(store.units[1]) == []
