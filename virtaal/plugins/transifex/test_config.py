#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Importing tx's servers and tokens from a synthetic ~/.transifexrc:
read only, a token or the older password of the user "api", Transifex's
own host as its REST API, another host only with its rest_hostname,
and TX_TOKEN first."""

import pytest

from virtaal.plugins.transifex import backend
from virtaal.plugins.transifex.client import API_URL
from virtaal.plugins.transifex.config import KeyFile, read_tx, tx_config_path

OURS = 'https://rest.tx.example.org/'


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('TX_TOKEN', raising=False)
    monkeypatch.delenv('TX_HOSTNAME', raising=False)


def _rc(tmp_path, text):
    path = tmp_path / '.transifexrc'
    path.write_text(text, encoding='utf-8')
    return path


_TOKEN = """[https://app.transifex.com]
rest_hostname = https://rest.api.transifex.com
token = 1/current
"""

_LEGACY = """[https://www.transifex.com]
api_hostname = https://api.transifex.com
hostname = https://www.transifex.com
username = api
password = 1/legacy
"""


def test_no_file_is_nothing_to_import(tmp_path):
    assert tx_config_path() is None
    assert read_tx() == []
    assert not backend.TransifexPlatform(str(tmp_path / 'transifex.ini')).can_import()


def test_the_file_is_found_in_home(tmp_path):
    path = _rc(tmp_path, _TOKEN)
    assert tx_config_path() == str(path)
    assert backend.TransifexPlatform(str(tmp_path / 'transifex.ini')).can_import()


def test_a_token_is_read_for_its_rest_hostname(tmp_path):
    _rc(tmp_path, _TOKEN)
    assert read_tx() == [(API_URL, '1/current')]


def test_the_older_password_of_api_is_the_token(tmp_path):
    _rc(tmp_path, _LEGACY)
    assert read_tx() == [(API_URL, '1/legacy')]


def test_a_password_of_another_user_is_no_token(tmp_path):
    _rc(tmp_path, _LEGACY.replace('username = api', 'username = someone'))
    assert read_tx() == []


def test_an_unknown_host_needs_its_rest_hostname(tmp_path):
    _rc(
        tmp_path,
        """[https://tx.example.org]
token = 1/unknown

[https://tx2.example.org]
rest_hostname = https://rest.tx.example.org
token = 1/ours
""",
    )
    assert read_tx() == [(OURS, '1/ours')]


def test_one_server_a_url_the_first_kept(tmp_path):
    _rc(tmp_path, _TOKEN + '\n' + _LEGACY)
    assert read_tx() == [(API_URL, '1/current')]


def test_tx_token_comes_first(tmp_path, monkeypatch):
    _rc(tmp_path, _TOKEN)
    monkeypatch.setenv('TX_TOKEN', '1/environment')
    assert read_tx() == [(API_URL, '1/environment')]


def test_tx_hostname_names_a_section_or_a_rest_address(tmp_path, monkeypatch):
    _rc(tmp_path, '[ours]\nrest_hostname = https://rest.tx.example.org\ntoken = 1/ours\n')
    monkeypatch.setenv('TX_TOKEN', '1/environment')
    monkeypatch.setenv('TX_HOSTNAME', 'ours')
    assert read_tx() == [(OURS, '1/environment')]
    monkeypatch.setenv('TX_HOSTNAME', 'https://rest.other.example.org')
    assert read_tx()[0] == ('https://rest.other.example.org/', '1/environment')


def test_tx_token_alone_can_be_imported(tmp_path, monkeypatch):
    monkeypatch.setenv('TX_TOKEN', '1/environment')
    assert backend.TransifexPlatform(str(tmp_path / 'transifex.ini')).can_import()


def test_a_broken_file_is_nothing(tmp_path):
    _rc(tmp_path, 'no section here\n')
    assert read_tx() == []


def test_importing_never_writes_txs_file(tmp_path):
    rc = _rc(tmp_path, _TOKEN + '\n' + _LEGACY)
    platform = backend.TransifexPlatform(str(tmp_path / 'transifex.ini'))

    found = platform.import_servers()
    keys = KeyFile(str(tmp_path / 'transifex.ini'))
    for url, credentials in found:
        keys.put(url, credentials['token'])
    keys.save()

    assert found == [(API_URL, {'token': '1/current'})]
    assert rc.read_text(encoding='utf-8') == _TOKEN + '\n' + _LEGACY


def test_the_button_says_tx():
    assert backend.TransifexPlatform.import_label == 'Import from Trans_ifex Client'
