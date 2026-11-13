#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Transifex's API client and its server list against the fake server
through the real curl client: addresses, the token, connecting,
organisations folded into the projects, resources and languages, the
error kinds, the rate limit's wait in its text, and transifex.ini."""

import os
import stat
import sys

import pytest

from devsupport.testing.faketransifex import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.transifex import backend, client
from virtaal.plugins.transifex.config import KeyFile
from virtaal.support.platforms import AUTH, HTTP, NETWORK, NOT_PLATFORM, Item


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')
    monkeypatch.setattr(client.Client, 'POLL_FIRST', 0.01)


@pytest.fixture
def fake():
    server = demo().start()
    yield server
    server.stop()
    assert server.schema_problems == []


@pytest.fixture
def platform(tmp_path):
    return backend.TransifexPlatform(str(tmp_path / 'transifex.ini'))


def _ask(call, *args):
    """call(*args, on_done, on_error)'s answer."""
    found = []
    call(*args, lambda *reply: found.append(reply), lambda error: found.append(error))
    run_until(lambda: found, timeout=20)
    return found[0]


@pytest.mark.parametrize(
    ('text', 'url'),
    [
        ('https://rest.api.transifex.com', 'https://rest.api.transifex.com/'),
        ('rest.api.transifex.com', 'https://rest.api.transifex.com/'),
        ('http://127.0.0.1:8766', 'http://127.0.0.1:8766/'),
        ('http://rest.api.transifex.com/', None),
        ('https://user:pw@rest.api.transifex.com/', None),
        ('', None),
    ],
)
def test_the_api_address_is_https_or_this_machine(text, url):
    assert client.api_url(text) == url


def test_a_plain_http_address_says_why(platform):
    assert platform.url_problem('http://example.org/') == (
        'Use https: the token would be sent unencrypted.'
    )
    assert platform.url_problem('http://127.0.0.1:1/') is None
    assert platform.default_url == 'https://rest.api.transifex.com/'


def test_the_wait_is_read_from_a_429s_text():
    body = (
        b'{"errors": [{"status": "429", "code": "Throttled", "title": "Throttled",'
        b' "detail": "Request was throttled. Expected available in 42 seconds."}]}'
    )
    assert client.throttle_wait(body, 60) == 42
    assert client.throttle_wait(b'{}', 60) == 60
    assert client.error_detail(body).startswith('Request was throttled.')


def test_links_are_followed_only_under_the_api():
    api = client.Client('http://127.0.0.1:9/v3/', 't', http=object())
    assert api.own('/v3/projects?page[cursor]=c1') == (
        'http://127.0.0.1:9/v3/projects?page[cursor]=c1'
    )
    assert api.own('http://127.0.0.1:9/v3/projects') == 'http://127.0.0.1:9/v3/projects'
    # Another host (a proxy's view of it) is asked at the API's own.
    assert api.own('https://elsewhere.example/v3/projects') == 'http://127.0.0.1:9/v3/projects'
    assert api.own('https://elsewhere.example/other') is None
    assert api.own(None) is None


def test_connecting_counts_the_projects_of_every_organisation(fake, platform):
    session = platform.session(fake.url, {'token': TOKEN})
    assert _ask(session.test_connection) == (2,)
    [request] = fake.requests_to('/organizations')
    assert request['headers']['Authorization'] == 'Bearer ' + TOKEN


def test_a_refused_token_is_an_auth_error(fake, platform):
    error = _ask(platform.session(fake.url, {'token': 'wrong'}).test_connection)
    assert (error.kind, error.status, error.detail) == (AUTH, 401, 'Invalid token.')


def test_no_reply_is_a_network_error(platform):
    error = _ask(platform.session('http://127.0.0.1:9/', {'token': TOKEN}).test_connection)
    assert error.kind == NETWORK


def test_another_site_is_not_transifex(fake, platform):
    fake.fail('/organizations', 200, body=b'<!doctype html><title>Hi</title>')
    error = _ask(platform.session(fake.url, {'token': TOKEN}).test_connection)
    assert error.kind == NOT_PLATFORM
    assert platform.not_platform_message() == 'No Transifex API answered at this address.'


def test_a_busy_server_is_asked_again(fake, platform, monkeypatch):
    monkeypatch.setattr(client.Client, 'schedule', staticmethod(_soon))
    fake.fail('/organizations', 503, times=2)
    assert _ask(platform.session(fake.url, {'token': TOKEN}).test_connection) == (2,)
    assert len(fake.requests_to('/organizations')) == 3


def _soon(_seconds, callback):
    from gi.repository import GLib

    GLib.timeout_add(10, lambda: callback() and False)


def test_other_errors_carry_the_status_and_the_servers_words(fake, platform):
    body = (
        b'{"errors": [{"status": "500", "code": "server_error", "title": "Error",'
        b' "detail": "Something broke."}]}'
    )
    fake.fail('/organizations', 500, body=body)
    error = _ask(platform.session(fake.url, {'token': TOKEN}).test_connection)
    assert (error.kind, error.status, error.detail) == (HTTP, 500, 'Something broke.')


# Browsing #


def test_projects_are_listed_as_organisation_and_project(fake, platform):
    fake.page_size = 1
    fake.add_organisation('empty', 'Empty')
    fake.add_project('hello-org', 'second', 'Second')
    fake.organisations = {k: fake.organisations[k] for k in ('hello-org', 'empty', 'tools')}
    session = platform.session(fake.url, {'token': TOKEN})
    pages = []
    cursor = None
    while True:
        items, cursor = _ask(session.projects, None, cursor)
        pages.append(items)
        if cursor is None:
            break

    # One a page; the organisation without projects is passed over.
    assert pages == [
        [Item('Hello Org / Program', 'hello-org/program')],
        [Item('Hello Org / Second', 'hello-org/second')],
        [Item('Tools / Toolbar', 'tools/toolbar')],
    ]


def test_a_projects_resources_are_its_components(fake, platform):
    session = platform.session(fake.url, {'token': TOKEN})
    (items,) = _ask(session.components, 'hello-org/program')
    assert items == [
        Item('Messages', 'messages', info='PO'),
        Item('Website', 'website', info='KEYVALUEJSON'),
    ]


def test_a_resources_languages_leave_out_the_source(fake, platform):
    fake.organisations['hello-org']['projects']['program']['languages'].append('en')
    session = platform.session(fake.url, {'token': TOKEN})
    (items,) = _ask(session.languages, 'hello-org/program', 'messages')
    assert [(i.name, i.slug) for i in items] == [('Afrikaans', 'af'), ('German', 'de')]
    assert items[0].translated == pytest.approx(5 / 7)
    assert items[1].translated == 0


# transifex.ini #


def test_the_servers_round_trip_owner_only(tmp_path, platform):
    platform.save_servers(
        [('https://rest.api.transifex.com/', {'token': 'secret'})],
        'https://rest.api.transifex.com/',
    )
    path = str(tmp_path / 'transifex.ini')
    if sys.platform != 'win32':
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = backend.TransifexPlatform(path)
    assert again.load_servers() == (
        [('https://rest.api.transifex.com/', {'token': 'secret'})],
        'https://rest.api.transifex.com/',
    )
    with open(path, encoding='utf-8') as f:
        assert '[keys]' in f.read()


def test_an_unusable_line_in_transifex_ini_is_left_out(tmp_path):
    path = tmp_path / 'transifex.ini'
    path.write_text(
        '[keys]\nhttp://example.org/ = t1\nhttps://rest.api.transifex.com/ = t2\n',
        encoding='utf-8',
        newline='',
    )
    assert KeyFile(str(path)).servers == [('https://rest.api.transifex.com/', 't2')]


def test_an_unreadable_transifex_ini_holds_no_servers(tmp_path):
    path = tmp_path / 'transifex.ini'
    path.write_bytes(b'not [an ini')
    assert KeyFile(str(path)).servers == []
