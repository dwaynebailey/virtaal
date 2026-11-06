#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os
import time
from types import SimpleNamespace

import pytest
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.plugins.terminology.models import microsoft
from virtaal.plugins.terminology.models.microsoft import (
    MEMBERS,
    TerminologyModel,
    microsoft_member,
)
from virtaal.support.test_remote_zip import BIG, FakeRangeServer, make_zip

# Shaped like Microsoft's own files: en-US source, then the target.
TBX = '''<?xml version="1.0" encoding="utf-8"?><martif type="TBX" xml:lang="en-US"><martifHeader>
<fileDesc><sourceDesc><p>Microsoft Terminology Collection</p></sourceDesc></fileDesc>
</martifHeader><text><body>
<termEntry id="1"><langSet xml:lang="en-US"><descripGrp><descrip type="definition">A
named collection of data.</descrip></descripGrp><ntig><termGrp><term id="1">file</term>
</termGrp></ntig></langSet><langSet xml:lang="%s"><ntig><termGrp><term id="2">%s</term>
</termGrp></ntig></langSet></termEntry>
</body></text></martif>'''


def tbx(lang, term):
    return (TBX % (lang, term)).encode('utf-8')


@pytest.fixture(autouse=True)
def _isolate_terminology_matchers():
    original = list(TerminologyPlaceable.matchers)
    yield
    TerminologyPlaceable.matchers[:] = original


def _model(tmp_path, server, source='en', target='af'):
    model = TerminologyModel.__new__(TerminologyModel)
    model.TERMDIR = str(tmp_path)
    model.config = {}
    model.matcher = None
    model.client = server
    model.source_lang = source
    model.target_lang = target
    model.rescans = []
    model.term_controller = SimpleNamespace(rescan_current_unit=lambda: model.rescans.append(1))
    return model


def _server(**members):
    files = {'AFRIKAANS.tbx': tbx('af', 'lêer'), 'GERMAN.tbx': tbx('de', 'Datei'), 'X.tbx': BIG}
    files.update(members)
    return FakeRangeServer(make_zip(files))


def _terms(model):
    return [(unit.source, unit.target) for unit in model.store.units]


def test_downloads_the_target_languages_file_only(tmp_path):
    server = _server()
    model = _model(tmp_path, server)

    model.update_terms()

    assert _terms(model) == [('file', 'lêer')]
    assert model.matcher in TerminologyPlaceable.matchers
    assert model.rescans == [1]
    assert os.listdir(tmp_path) == ['af.tbx']
    assert model.config == {'af.tbx': '"v1"'}
    assert len(server.requests) == 2


def test_a_fresh_cached_file_is_used_without_asking_microsoft(tmp_path):
    (tmp_path / 'af.tbx').write_bytes(tbx('af', 'kaart'))
    server = _server()
    model = _model(tmp_path, server)

    model.update_terms()

    assert _terms(model) == [('file', 'kaart')]
    assert server.requests == []


def test_a_week_old_file_is_checked_and_kept_when_unchanged(tmp_path):
    cached = tmp_path / 'af.tbx'
    cached.write_bytes(tbx('af', 'kaart'))
    eight_days_ago = time.time() - 8 * 24 * 3600
    os.utime(cached, (eight_days_ago, eight_days_ago))
    server = _server()
    model = _model(tmp_path, server)
    model.config['af.tbx'] = '"v1"'

    model.update_terms()

    assert len(server.requests) == 1
    assert server.requests[0]['If-None-Match'] == '"v1"'
    assert _terms(model) == [('file', 'kaart')]
    assert cached.stat().st_mtime > eight_days_ago + 3600


def test_a_changed_file_replaces_the_cached_one(tmp_path):
    cached = tmp_path / 'af.tbx'
    cached.write_bytes(tbx('af', 'kaart'))
    os.utime(cached, (0, 0))
    model = _model(tmp_path, _server())
    model.config['af.tbx'] = '"old"'

    model.update_terms()

    assert _terms(model) == [('file', 'lêer')]
    assert model.config['af.tbx'] == '"v1"'


def test_a_failed_check_keeps_the_cached_terms(tmp_path):
    cached = tmp_path / 'af.tbx'
    cached.write_bytes(tbx('af', 'kaart'))
    os.utime(cached, (0, 0))
    model = _model(tmp_path, FakeRangeServer(b'<html>Sign in</html>'))

    model.update_terms()

    assert _terms(model) == [('file', 'kaart')]


def test_nothing_for_a_source_language_other_than_english(tmp_path):
    server = _server()
    model = _model(tmp_path, server, source='de', target='af')

    model.update_terms()

    assert _terms(model) == []
    assert server.requests == []


def test_nothing_for_a_language_microsoft_has_no_file_for(tmp_path):
    server = _server()
    model = _model(tmp_path, server, target='xx')

    model.update_terms()

    assert _terms(model) == []
    assert server.requests == []


def test_switching_language_loads_that_languages_file(tmp_path):
    model = _model(tmp_path, _server())
    model.update_terms()

    model._on_lang_changed(None, 'de', 'target')

    assert _terms(model) == [('file', 'Datei')]
    assert sorted(os.listdir(tmp_path)) == ['af.tbx', 'de.tbx']


def test_a_download_for_a_language_no_longer_current_is_kept_but_not_shown(tmp_path):
    model = _model(tmp_path, _server())
    model.target_lang = 'de'

    model._on_downloaded(str(tmp_path / 'af.tbx'), tbx('af', 'lêer'), '"v1"', 'af')

    assert (tmp_path / 'af.tbx').exists()
    assert model.rescans == []


@pytest.mark.parametrize(
    'code, member',
    [
        ('af', 'AFRIKAANS.tbx'),
        ('af_ZA', 'AFRIKAANS.tbx'),
        ('pt_BR', 'PORTUGUESE (BRAZIL).tbx'),
        ('pt-br', 'PORTUGUESE (BRAZIL).tbx'),
        ('pt', 'PORTUGUESE (PORTUGAL).tbx'),
        ('pt_AO', 'PORTUGUESE (PORTUGAL).tbx'),
        ('zh_CN', 'CHINESE (SIMPLIFIED).tbx'),
        ('zh_HK', 'CHINESE (TRADITIONAL).tbx'),
        ('sr', 'SERBIAN (CYRILLIC).tbx'),
        ('sr@latin', 'SERBIAN (LATIN).tbx'),
        ('ca@valencia', 'VALENCIAN.tbx'),
        ('nb', 'NORWEGIAN BOKMÅL.tbx'),
        ('mi', 'MĀORI.tbx'),
        ('bn_IN', 'BANGLA.tbx'),
        ('de@formal', 'GERMAN.tbx'),
        ('xx', None),
        ('', None),
        (None, None),
    ],
)
def test_microsoft_member(code, member):
    assert microsoft_member(code) == member


def test_every_code_names_one_file():
    codes = [code.lower() for codes in MEMBERS.values() for code in codes]

    assert len(codes) == len(set(codes))
    assert len(MEMBERS) == 111


def test_the_zip_is_on_microsofts_own_server():
    assert microsoft.ZIP_URL.startswith('https://download.microsoft.com/')
