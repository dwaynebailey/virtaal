#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from translate.misc.multistring import multistring
from translate.storage import po, xliff
from translate.storage.workflow import StateEnum

from virtaal.support import unitsnapshot

_XLIFF = b"""<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
<file original="a" source-language="en" target-language="af" datatype="plaintext"><body>
<trans-unit id="1"><source>Click <g id="1">here</g></source></trans-unit>
</body></file></xliff>"""


def test_a_po_unit_gets_its_translation_and_state_back():
    unit = po.pofile().addsourceunit('Open')
    before = unitsnapshot.take(unit)
    unit.target = 'Maak oop'
    unit.markfuzzy()
    after = unitsnapshot.take(unit)

    unitsnapshot.restore(unit, before)
    assert (unit.target, unit.isfuzzy()) == ('', False)
    unitsnapshot.restore(unit, after)
    assert (unit.target, unit.isfuzzy()) == ('Maak oop', True)


def test_a_plural_gets_every_form_back():
    unit = po.pofile().addsourceunit(multistring(['%d file', '%d files']))
    unit.target = multistring(['%d lêer', '%d lêers'])
    after = unitsnapshot.take(unit)
    unit.target = multistring(['', ''])

    unitsnapshot.restore(unit, after)

    assert unit.target.strings == ['%d lêer', '%d lêers']


def test_an_xliff_unit_keeps_its_codes_and_loses_a_target_it_had_not():
    unit = xliff.xlifffile.parsestring(_XLIFF).units[0]
    before = unitsnapshot.take(unit)
    unit.rich_target = [unit.rich_source[0].copy()]
    unit.set_state_n(StateEnum.NEEDS_REVIEW)
    after = unitsnapshot.take(unit)

    unitsnapshot.restore(unit, before)
    assert unit.target is None
    assert unit.get_state_n() == StateEnum.EMPTY
    unitsnapshot.restore(unit, after)
    assert '>Click <g id="1">here</g></target>' in bytes(unit._store).decode()
    assert unit.get_state_n() == StateEnum.NEEDS_REVIEW
