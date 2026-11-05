#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""translate-toolkit 3.18+ takes a TMX unit's source from the tuv in the
header's srclang and gives None when no tuv has it, so a Trados
Workbench export with srclang EN-GB and tuvs in EN-US opened as an empty
file (#4626). Such a unit's first tuv is its source here, as in
translate-toolkit 3.17 and earlier and in Virtaal's TM import."""

from translate.storage import tmx

_get_source_dom = tmx.tmxunit.get_source_dom


def get_source_dom(self):
    node = _get_source_dom(self)
    if node is None:
        node = next(iter(self.getlanguageNodes()), None)
    return node


tmx.tmxunit.get_source_dom = get_source_dom
tmx.tmxunit.source_dom = property(get_source_dom, tmx.tmxunit.set_source_dom)
