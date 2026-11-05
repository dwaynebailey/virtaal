#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
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
