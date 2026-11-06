#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from translate.misc import xml_helpers
from translate.storage import lisa, tmx

from virtaal.support import statsdb, tmx_compat

TMX = b"""<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4"><header creationtool="x" creationtoolversion="1"
  segtype="sentence" o-tmf="x" adminlang="en-US" srclang="%s"
  datatype="plaintext"/><body>
<tu><tuv xml:lang="en-US"><seg>Hello world</seg></tuv>
<tuv xml:lang="de-DE"><seg>Hallo Welt</seg></tuv></tu>
</body></tmx>"""


def _unit(srclang):
    return tmx.tmxfile.parsestring(TMX % srclang).units[0]


@pytest.mark.skipif(
    not hasattr(lisa, "MultilingualLISAunit"),
    reason="translate-toolkit before 3.18 ignores the header's srclang",
)
def test_translate_toolkit_still_finds_no_source_for_an_unknown_srclang():
    # Once this fails translate-toolkit falls back by itself, and
    # tmx_compat can go.
    assert tmx_compat._get_source_dom(_unit(b"en-GB")) is None


def test_a_srclang_no_tuv_has_takes_the_first_tuv_as_source():
    unit = _unit(b"en-GB")

    assert unit.source == "Hello world"
    assert unit.target == "Hallo Welt"
    assert unit.istranslatable()


def test_a_new_target_goes_in_the_second_tuv():
    unit = _unit(b"en-GB")

    unit.target = "Guten Tag"

    reopened = tmx.tmxfile.parsestring(bytes(unit._store)).units[0]
    assert (reopened.source, reopened.target) == ("Hello world", "Guten Tag")


def test_the_stats_count_the_unit(tmp_path):
    path = tmp_path / "workbench.tmx"
    path.write_bytes(TMX % b"en-GB")

    totals = statsdb.StatsCache(str(tmp_path / "stats.db")).filetotals(str(path))

    assert totals["translatedsourcewords"] == 2


TMX_1_1 = b"""<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.1"><header creationtool="x" creationtoolversion="1"
  segtype="sentence" o-tmf="x" adminlang="en" srclang="en"
  datatype="plaintext"/><body>
<tu><tuv lang="af"><seg>Oop</seg></tuv><tuv lang="en"><seg>Open</seg></tuv>
<tuv lang="de"><seg>Auf</seg></tuv></tu>
<tu><tuv lang="en"><seg>Close</seg></tuv></tu>
</body></tmx>"""


needs_languages = pytest.mark.skipif(
    not hasattr(lisa, "MultilingualLISAunit"),
    reason="translate-toolkit before 3.18 finds a tuv by position, not language",
)


def _store_1_1(targetlanguage="af"):
    store = tmx.tmxfile.parsestring(TMX_1_1)
    store.settargetlanguage(targetlanguage)
    return store


def test_translate_toolkit_still_reads_only_xml_lang():
    # Once this fails translate-toolkit reads lang itself, and the lang
    # half of tmx_compat can go.
    tuv = tmx.tmxfile.parsestring(TMX_1_1).units[0].getlanguageNodes()[0]
    assert xml_helpers.getXMLlang(tuv) is None


@needs_languages
@pytest.mark.parametrize(("lang", "target"), [("af", "Oop"), ("de", "Auf")])
def test_a_tmx_1_1_unit_is_read_by_its_tuvs_lang(lang, target):
    unit = _store_1_1(lang).units[0]

    assert (unit.source, unit.target) == ("Open", target)


@needs_languages
def test_a_tmx_1_1_file_keeps_lang_on_save():
    store = _store_1_1()
    store.units[0].target = "Maak oop"
    store.units[1].target = "Maak toe"

    saved = bytes(store)

    assert b"xml:lang" not in saved
    reopened = tmx.tmxfile.parsestring(saved)
    reopened.settargetlanguage("af")
    assert [(u.source, u.target) for u in reopened.units] == [
        ("Open", "Maak oop"),
        ("Close", "Maak toe"),
    ]
    assert reopened.units[0].gettarget("de") == "Auf"


def test_a_new_tuv_in_a_tmx_1_4_file_uses_xml_lang():
    store = tmx.tmxfile.parsestring(TMX_1_1.replace(b" lang=", b" xml:lang="))
    store.settargetlanguage("af")
    store.units[1].target = "Maak toe"

    saved = bytes(store)

    assert b' lang="' not in saved
    assert tmx.tmxfile.parsestring(saved).units[1].target == "Maak toe"


@needs_languages
def test_a_rich_target_follows_a_target_language_change():
    store = _store_1_1("af")
    unit = store.units[0]
    assert str(unit.rich_target[0]) == "Oop"

    store.settargetlanguage("de")

    assert str(unit.rich_target[0]) == "Auf"


@pytest.mark.parametrize(
    ("data", "language"),
    [
        (TMX % b"en-US", "de-DE"),
        (TMX_1_1, None),
        pytest.param(
            TMX_1_1.replace(b'<tuv lang="de"><seg>Auf</seg></tuv>', b""),
            "af",
            marks=needs_languages,
        ),
    ],
)
def test_the_translation_language_is_the_only_one_besides_the_source(data, language):
    assert tmx_compat.translation_language(tmx.tmxfile.parsestring(data)) == language
