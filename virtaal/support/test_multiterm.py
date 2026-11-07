#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import builtins
import gzip
import os
import sys

import pytest
from translate.search.match import terminologymatcher

from virtaal.support import multiterm

TRADOS = os.path.join('devsupport', 'testfiles', 'trados')
MILI = os.path.join(TRADOS, 'kaur2k-termconv', 'mili_1.xml')
SDLTB = os.path.join(TRADOS, 'rws-trados-studio-api-samples', 'Sample.sdltb.gz')

# The shape of a MultiTerm XML export: fields at the concept, language
# and term levels, a synonym, a language named only by its type.
MTF = """<?xml version="1.0" encoding="UTF-16"?>
<mtf>
  <conceptGrp>
    <concept>1</concept>
    <descripGrp><descrip type="Definition">A <xref Tlink="x">device</xref> that
      prints</descrip></descripGrp>
    <languageGrp>
      <language type="German" lang="DE"/>
      <termGrp><term>Drucker</term>
        <descripGrp><descrip type="Gender">m</descrip></descripGrp></termGrp>
      <termGrp><term>Printer</term>
        <descripGrp><descrip type="Status">not allowed</descrip></descripGrp></termGrp>
      <termGrp><term>Druckgerät</term></termGrp>
    </languageGrp>
    <languageGrp>
      <language type="English" lang="EN-US"/>
      <termGrp><term>printer</term></termGrp>
      <termGrp><term>print device</term></termGrp>
    </languageGrp>
  </conceptGrp>
  <conceptGrp>
    <concept>2</concept>
    <languageGrp>
      <language type="English"/>
      <termGrp><term>spelling checker</term></termGrp>
    </languageGrp>
    <languageGrp>
      <language type="French"/>
      <termGrp><term>vérificateur orthographique</term></termGrp>
    </languageGrp>
  </conceptGrp>
</mtf>
"""


def _store(text=MTF):
    return multiterm.MultiTermXmlFile.parsestring(text.encode('utf-16'))


def test_each_source_term_gets_the_first_target_term():
    store = _store()

    assert (store.getsourcelanguage(), store.gettargetlanguage()) == ('en', 'DE')
    assert [(u.source, u.target) for u in store.units] == [
        ('printer', 'Drucker'),
        ('print device', 'Drucker'),
    ]


def test_fields_and_the_other_target_terms_are_notes():
    unit = _store().units[0]

    assert unit.getnotes().split('\n') == [
        'Definition: A device that prints',
        'Gender: m',
        'Printer (Status: not allowed)',
        'Druckgerät',
    ]


def test_a_language_without_a_code_is_found_by_its_name():
    store = _store()

    assert store.select_languages('en', 'fr')
    assert [(u.source, u.target) for u in store.units] == [
        ('spelling checker', 'vérificateur orthographique'),
    ]
    assert store.gettargetlanguage() == 'fr'


def test_a_language_only_the_plural_table_has_is_found_by_its_name():
    store = _store(MTF.replace('type="French"', 'type="Ganda"'))

    assert store.select_languages('en', 'lg')
    assert store.gettargetlanguage() == 'lg'


def test_a_pair_the_termbase_lacks_keeps_the_current_one():
    store = _store()

    assert not store.select_languages('en', 'af')
    assert not store.select_languages('af', 'de')
    assert not store.select_languages('en', 'de')
    assert store.gettargetlanguage() == 'DE'
    assert len(store.units) == 2


def test_a_regional_code_matches_a_termbase_language():
    store = _store()

    assert store.select_languages('de_CH', 'en_GB')
    assert [(u.source, u.target) for u in store.units][:2] == [
        ('Drucker', 'printer'),
        ('Printer', 'printer'),
    ]


def test_globalsight_entries_name_languages_by_locale():
    store = _store(
        '<entries><conceptGrp><languageGrp><language name="English" locale="en"/>'
        '<termGrp><term>cotton picker</term></termGrp></languageGrp>'
        '<languageGrp><language name="Swedish" locale="sv"/>'
        '<termGrp><term>bomullsplockningsmaskin</term></termGrp></languageGrp>'
        '</conceptGrp></entries>'
    )

    assert [(u.source, u.target) for u in store.units] == [
        ('cotton picker', 'bomullsplockningsmaskin'),
    ]
    assert store.gettargetlanguage() == 'sv'


def test_an_external_entity_is_not_read(tmp_path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('leaked', encoding='utf-8')
    text = (
        '<!DOCTYPE mtf [<!ENTITY x SYSTEM "%s">]><mtf><conceptGrp>'
        '<languageGrp><language lang="EN"/><termGrp><term>a&x;</term></termGrp></languageGrp>'
        '<languageGrp><language lang="DE"/><termGrp><term>b</term></termGrp></languageGrp>'
        '</conceptGrp></mtf>'
    ) % secret.as_uri()

    store = multiterm.MultiTermXmlFile.parsestring(text.encode('utf-8'))

    assert 'leaked' not in store.units[0].source


def test_the_store_is_read_only():
    with pytest.raises(NotImplementedError):
        bytes(_store())


def test_detects_a_multiterm_export_in_any_encoding():
    assert multiterm.is_multiterm_xml(MTF.encode('utf-16'))
    assert multiterm.is_multiterm_xml(MTF.encode('utf-8'))
    assert not multiterm.is_multiterm_xml(b'<?xml version="1.0"?><tmx version="1.4"/>')


def test_the_kaur2k_export_finds_its_terms_in_a_sentence():
    store = multiterm.MultiTermXmlFile.parsefile(MILI)

    assert store.languages() == ['EN-GB', 'FR', 'ET']
    assert store.select_languages('en', 'et')
    matches = terminologymatcher([store]).matches('Pilots may abort the mission.')
    assert [(m.source, m.target) for m in matches] == [('abort', 'katkestus')]


def _sdltb(tmp_path):
    path = str(tmp_path / 'Sample.sdltb')
    with gzip.open(SDLTB) as f, open(path, 'wb') as out:
        out.write(f.read())
    return path


def test_an_sdltb_without_access_parser_says_what_is_missing(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def no_access_parser(name, *args, **kwargs):
        if name == 'access_parser':
            raise ImportError('No module named access_parser')
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, 'access_parser', raising=False)
    monkeypatch.setattr(builtins, '__import__', no_access_parser)

    with pytest.raises(multiterm.MissingReaderError):
        multiterm.SdltbFile.parsefile(_sdltb(tmp_path))


def test_the_sdltb_sample_reads_its_concepts(tmp_path):
    pytest.importorskip('access_parser')
    store = multiterm.SdltbFile.parsefile(_sdltb(tmp_path))

    assert store.languages() == ['EN-US', 'DE', 'FR']
    assert len(store.concepts) == 25
    unit = store.units[1]
    assert (unit.source, unit.target) == ('tab', 'Reiter')
    assert 'Registerkarte (Status: not allowed; Grammar: f)' in unit.getnotes().split('\n')
    assert store.select_languages('en', 'fr')
    matches = terminologymatcher([store]).matches('Run the spelling checker.')
    assert [(m.source, m.target) for m in matches] == [
        ('spelling checker', 'vérificateur orthographique'),
    ]


def test_the_sdltb_reads_the_same_from_its_bytes(tmp_path):
    pytest.importorskip('access_parser')
    with open(_sdltb(tmp_path), 'rb') as f:
        store = multiterm.SdltbFile.parsestring(f.read())

    assert len(store.concepts) == 25
