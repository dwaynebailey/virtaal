#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import configparser
import os
import stat
import sys

import pytest

from virtaal.plugins.weblate.config import KeyFile, read_wlc, wlc_config_path

posix_only = pytest.mark.skipif(sys.platform == 'win32', reason="Windows has no POSIX modes")

HOSTED = 'https://hosted.weblate.org/api/'
OURS = 'https://weblate.example.org/api/'


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_servers_and_the_last_one_used_survive_a_round_trip(tmp_path):
    path = str(tmp_path / 'weblate.ini')
    servers = KeyFile(path)
    servers.put('weblate.example.org', 'wlu_one')
    servers.put(HOSTED, 'wlp_two')
    servers.last_url = HOSTED
    servers.save()

    again = KeyFile(path)

    assert again.servers == [(OURS, 'wlu_one'), (HOSTED, 'wlp_two')]
    assert again.last_url == HOSTED


def test_the_file_has_wlcs_shape(tmp_path):
    path = tmp_path / 'weblate.ini'
    servers = KeyFile(str(path))
    servers.put(OURS, 'wlu_one')
    servers.last_url = OURS
    servers.save()

    parser = configparser.RawConfigParser(delimiters=('=',))
    parser.read(path, encoding='utf-8')
    assert parser.get('weblate', 'url') == OURS
    assert dict(parser.items('keys')) == {OURS: 'wlu_one'}


@posix_only
def test_a_new_file_is_owner_only(tmp_path):
    path = str(tmp_path / 'weblate.ini')
    servers = KeyFile(path)
    servers.put(OURS, 'wlu_one')
    servers.save()

    assert _mode(path) == 0o600


@posix_only
def test_saving_makes_a_readable_file_owner_only(tmp_path):
    path = tmp_path / 'weblate.ini'
    path.write_text('[keys]\n%s = wlu_one\n' % OURS, encoding='utf-8')
    os.chmod(path, 0o644)

    KeyFile(str(path)).save()

    assert _mode(path) == 0o600
    assert KeyFile(str(path)).servers == [(OURS, 'wlu_one')]


def test_adding_a_listed_server_again_changes_its_token(tmp_path):
    servers = KeyFile(str(tmp_path / 'weblate.ini'))
    servers.put(OURS, 'old')
    servers.put('https://weblate.example.org', 'new')

    assert servers.servers == [(OURS, 'new')]


@pytest.mark.parametrize('url, token', [('', 'tok'), (OURS, '  '), ('http://example.org', 't')])
def test_a_server_needs_a_usable_address_and_a_token(tmp_path, url, token):
    servers = KeyFile(str(tmp_path / 'weblate.ini'))

    assert not servers.put(url, token)
    assert servers.servers == []


def test_a_missing_or_broken_file_lists_no_servers(tmp_path):
    assert KeyFile(str(tmp_path / 'none.ini')).servers == []
    broken = tmp_path / 'broken.ini'
    broken.write_text('no section here\n', encoding='utf-8')
    assert KeyFile(str(broken)).servers == []


# wlc #


def _wlc(tmp_path, text):
    path = tmp_path / 'xdg' / 'weblate'
    path.parent.mkdir()
    path.write_text(text, encoding='utf-8')
    return path


_WLC = """[weblate]
url = https://hosted.weblate.org/api/

[keys]
https://hosted.weblate.org/api/ = wlu_hosted
https://weblate.example.org/api/ = wlu_ours
https://empty.example.org/api/ =
"""


def test_wlcs_file_is_found_where_wlc_looks(tmp_path, monkeypatch):
    path = _wlc(tmp_path, _WLC)
    for name in ('APPDATA', 'LOCALAPPDATA'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(path.parent))
    monkeypatch.setenv('XDG_CONFIG_DIRS', str(tmp_path / 'nowhere'))

    assert wlc_config_path() == str(path)


def test_no_wlc_file_is_none(tmp_path, monkeypatch):
    for name in ('APPDATA', 'LOCALAPPDATA'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_DIRS', str(tmp_path / 'nowhere'))

    assert wlc_config_path() is None


def test_wlcs_appdata_file_comes_first(tmp_path, monkeypatch):
    path = tmp_path / 'weblate.ini'
    path.write_text(_WLC, encoding='utf-8')
    monkeypatch.setenv('APPDATA', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(_wlc(tmp_path, _WLC).parent))

    assert wlc_config_path() == str(path)


def test_wlcs_keys_are_read_without_empty_ones(tmp_path):
    path = _wlc(tmp_path, _WLC)

    assert read_wlc(str(path)) == [(HOSTED, 'wlu_hosted'), (OURS, 'wlu_ours')]


def test_importing_never_writes_wlcs_file(tmp_path):
    wlc = _wlc(tmp_path, _WLC)
    servers = KeyFile(str(tmp_path / 'weblate.ini'))

    for url, token in read_wlc(str(wlc)):
        servers.put(url, token)
    servers.save()

    assert wlc.read_text(encoding='utf-8') == _WLC
