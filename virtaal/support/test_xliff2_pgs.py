#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""XLIFF 2.2's plural, gender and select module: a <unit>'s cases as one
plural unit."""

import os

from translate.misc.multistring import multistring
from translate.storage import factory, xliff2
from translate.storage.placeables import StringElem
from translate.storage.workflow import StateEnum

from virtaal.support import tmdb, xliff2_compat
from virtaal.views.unitview import UnitView

PGS = os.path.join('devsupport', 'testfiles', 'xliff2', 'pgs.xlf')


def _store():
    with open(PGS, 'rb') as f:
        data = f.read()
    return xliff2.Xliff2File.parsestring(data), data


def _forms(value):
    return [str(form) for form in value.strings]


def _units():
    return {unit.getid(): unit for unit in _store()[0].units}


def _changed_lines(before, after):
    old, new = before.splitlines(), after.splitlines()
    return [line for line in old if line not in new], [line for line in new if line not in old]


def test_a_units_cases_are_one_plural_unit():
    units = _units()
    deleted = units['deleted']

    assert isinstance(deleted, xliff2_compat.VariantUnit)
    assert deleted.hasplural()
    assert deleted.plural_tags == ['one', 'few', 'many', 'other']
    assert _forms(deleted.source) == ['You deleted file.'] + ['You deleted files.'] * 3
    # A placeable's text in its form, with nothing for the plain text.
    assert [str(form) for form in deleted.rich_target] == [
        'Вы удалили  файл.',
        'Вы удалили  файла.',
        'Вы удалили  файлов.',
        '',
    ]
    assert len(_forms(deleted.target)) == 4
    assert deleted.getnotes() == '2~4, 22~24'
    assert deleted.getcontext() == ''
    assert units['title'].getcontext() == ''
    assert list(units)[:5] == ['title', 'deleted', 'copied', 'invited', 'place']


def test_the_factory_gives_the_same_units():
    store = factory.getobject(PGS)

    assert [type(u).__name__ for u in store.units[:5]] == ['Xliff2Unit'] + ['VariantUnit'] * 4


def test_which_cases_are_cldr_plural_forms():
    units = _units()

    assert units['deleted'].plural_cldr
    assert (units['deleted'].plural_numbered, units['deleted'].plural_ordinal) == (True, False)
    assert not units['copied'].plural_cldr
    assert not units['invited'].plural_cldr and not units['invited'].plural_numbered
    # Russian's only ordinal category.
    assert units['place'].plural_ordinal and units['place'].plural_cldr
    assert tmdb.plural_layout(units['deleted']) == 'cldr'
    assert tmdb.plural_layout(units['copied']) is None


def test_the_state_is_the_least_advanced_cases():
    units = _units()

    assert units['deleted'].get_state_n() == StateEnum.NEEDS_WORK
    assert units['deleted'].isfuzzy()
    assert units['copied'].get_state_n() == StateEnum.EMPTY
    assert units['invited'].get_state_n() == StateEnum.FINAL
    assert units['invited'].istranslated() and units['invited'].isapproved()


def test_an_unchanged_save_is_byte_for_byte_after_showing_every_unit():
    store, data = _store()
    for unit in store.units:
        assert unit.rich_source
        unit.rich_target = unit.rich_target
        unit.target = unit.target

    assert bytes(store) == data


def test_translating_a_case_changes_only_its_segment():
    store, data = _store()
    deleted = store.units[1]
    forms = deleted.rich_target
    forms[3] = StringElem('Вы удалили файлы.')

    deleted.rich_target = forms
    deleted.set_state_n(StateEnum.UNREVIEWED)

    saved = bytes(store)
    removed, added = _changed_lines(data, saved)
    assert removed == [
        b'      <segment id="deleted_many" pgs:case="many" state="initial">',
        b'      <segment id="deleted_other" pgs:case="other">',
    ]
    assert added == [
        b'      <segment id="deleted_many" pgs:case="many" state="translated">',
        b'      <segment id="deleted_other" pgs:case="other" xml:space="preserve">',
        b'        <target>\xd0\x92\xd1\x8b \xd1\x83\xd0\xb4\xd0\xb0\xd0\xbb\xd0\xb8\xd0\xbb\xd0\xb8 '
        b'\xd1\x84\xd0\xb0\xd0\xb9\xd0\xbb\xd1\x8b.</target>',
    ]
    reread = xliff2.Xliff2File.parsestring(saved).units[1]
    assert _forms(reread.target)[3] == 'Вы удалили файлы.'
    assert reread.get_state_n() == StateEnum.UNREVIEWED


def test_emptying_a_case_removes_its_target_and_leaves_it_initial():
    store, _data = _store()
    invited = store.units[3]

    invited.target = multistring(
        ['', 'Вас пригласили на его вечеринку.', 'Вас пригласили на их вечеринку.']
    )

    segment = invited.segments[0].xmlelement
    assert segment.get('state') == 'initial'
    assert invited.segments[0].get_target_dom() is None
    assert invited.get_state_n() == StateEnum.NEEDS_WORK


def test_more_cases_than_virtaal_shows_are_a_unit_each_with_the_case_as_context():
    units = _units()

    party = [unit for key, unit in units.items() if key.startswith('party::')]
    assert len(party) == 7 > xliff2_compat.MAX_FORMS
    assert not any(isinstance(unit, xliff2_compat.VariantUnit) for unit in party)
    assert party[0].getcontext() == 'gender:host plural:guests = feminine 0'
    assert party[0].source == 'She invited nobody.'


def test_max_forms_is_the_target_boxes_virtaal_shows():
    assert xliff2_compat.MAX_FORMS == UnitView.MAX_TARGETS


def test_repeated_or_missing_cases_stay_a_unit_each():
    pgs = 'xmlns:pgs="urn:oasis:names:tc:xliff:pgs:1.0"'
    data = (
        '<xliff xmlns="urn:oasis:names:tc:xliff:document:2.2" %s version="2.2" srcLang="en">'
        '<file id="f"><unit id="u" pgs:switch="plural:n">'
        '<segment id="a" pgs:case="one"><source>A</source></segment>'
        '<segment id="b" pgs:case="one"><source>B</source></segment>'
        '</unit><unit id="v" pgs:switch="plural:n">'
        '<segment id="c" pgs:case="one"><source>C</source></segment>'
        '<segment id="d"><source>D</source></segment>'
        '</unit></file></xliff>' % pgs
    ).encode()

    store = xliff2.Xliff2File.parsestring(data)

    assert [u.getid() for u in store.units] == ['u::a', 'u::b', 'v::c', 'v::d']
    assert bytes(store) == data


def test_setting_the_step_a_unit_is_in_leaves_its_cases_states():
    store, data = _store()
    deleted, invited = store.units[1], store.units[3]

    deleted.set_state_n(StateEnum.NEEDS_WORK)
    invited.set_state_n(StateEnum.FINAL)
    assert bytes(store) == data
    invited.set_state_n(StateEnum.UNREVIEWED)

    assert [s.xmlelement.get('state') for s in invited.segments] == ['translated'] * 3
