#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""XLIFF 2's size and length restriction module: a <unit>'s
slr:sizeRestriction in code points is the "maxlength" check, for each
form of a plural."""

import pytest
from translate.storage import xliff2

from virtaal.common import pan_app
from virtaal.support import extrachecks, xliff2_compat
from virtaal.support.xliff2_compat import SizeRestrictionCheck, size_limit

_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0"
 xmlns:pgs="urn:oasis:names:tc:xliff:pgs:1.0"
 xmlns:slr="urn:oasis:names:tc:xliff:sizerestriction:2.0"
 version="2.2" srcLang="en" trgLang="af">
 <file id="f1">
  %(profiles)s
  <unit id="short" slr:sizeRestriction="5">
   <segment><source>Save</source><target>%(short)s</target></segment>
  </unit>
  <unit id="files" pgs:switch="plural:count" slr:sizeRestriction="8">
   <segment pgs:case="one"><source>%%d file</source><target>%%d lêer</target></segment>
   <segment pgs:case="other"><source>%%d files</source><target>%%d lêers</target></segment>
  </unit>
  <unit id="free"><segment><source>Open</source><target>Maak oop</target></segment></unit>
 </file>
</xliff>
"""
_PROFILES = '<slr:profiles generalProfile="xliff:codepoints"/>'


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')


def _units(short='Stoor', profiles=_PROFILES):
    text = _DOC % {'profiles': profiles, 'short': short}
    store = xliff2.Xliff2File.parsestring(text.encode('utf-8'))
    return store, {unit.getid(): unit for unit in store.units}


def test_the_limit_is_the_units_size_restriction_in_code_points():
    _store, units = _units()
    assert size_limit(units['short']) == 5
    assert size_limit(units['files']) == 8
    assert size_limit(units['free']) is None


def test_without_the_code_points_profile_there_is_no_limit():
    _store, units = _units(profiles='')
    assert size_limit(units['short']) is None
    _store, units = _units(profiles='<slr:profiles generalProfile="xliff:utf8"/>')
    assert size_limit(units['short']) is None


def test_a_translation_within_its_limit_passes():
    _store, units = _units()
    assert SizeRestrictionCheck().check(units['short']) is None
    assert SizeRestrictionCheck().check(units['free']) is None


def test_a_longer_translation_fails():
    _store, units = _units(short='Stoor dit')
    assert SizeRestrictionCheck().check(units['short']) == 'Longer than 5 characters'


def test_each_plural_form_is_measured():
    _store, units = _units()
    assert isinstance(units['files'], xliff2_compat.VariantUnit)
    # "%d lêers" is 7 code points; "%d lêer" 6.
    assert SizeRestrictionCheck().check(units['files']) is None
    units['files'].target = ['%d lêer', '%d lêertjies']
    assert SizeRestrictionCheck().check(units['files']) == 'Longer than 8 characters'


def test_code_points_are_counted_composed():
    # e and a combining circumflex: one code point once composed.
    _store, units = _units(short='Lêer')
    assert SizeRestrictionCheck().check(units['short']) is None


def test_it_applies_to_xliff_2_files_only():
    store, _units_found = _units()
    assert SizeRestrictionCheck.applies_to(store)
    assert not SizeRestrictionCheck.applies_to(object())
    assert SizeRestrictionCheck in extrachecks.EXTRA_CHECKS
