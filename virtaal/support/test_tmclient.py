#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json

import pycurl
import pytest
from translate.storage import pypo

from virtaal.support import tmclient
from virtaal.support.tmclient import TMClient


def test_forget_unit_builds_the_url_without_raising():
    """forget_unit()'s URL format string had 2 placeholders but a
    3-value tuple (self.user_agent left in by mistake - it's already
    passed separately as the user_agent= kwarg) - raised TypeError
    immediately on every call, before any network I/O."""
    client = TMClient('http://example.com')
    client.forget_unit('unit source', 'en', 'af')


class _FakeCurl:
    def __init__(self):
        self.opts = {}

    def setopt(self, key, value):
        self.opts[key] = value


class _FakeRequest:
    def __init__(
        self, url, id, method='GET', data=None, headers=None, user_agent=None, params=None
    ):
        self.url = url
        self.id = id
        self.method = method
        self.data = data
        self.headers = headers
        self.user_agent = user_agent
        self.params = params
        self.curl = _FakeCurl()
        self.connected = []

    def connect(self, signal, handler):
        self.connected.append((signal, handler))
        return len(self.connected)


def _make_client(monkeypatch):
    monkeypatch.setattr(tmclient, 'RESTRequest', _FakeRequest)
    client = TMClient('http://example.com')
    client.user_agent = 'Virtaal/test'
    requests = []
    monkeypatch.setattr(client, 'add', lambda request: requests.append(request) or True)
    return client, requests


def _fire_callback(request, payload):
    signal, handler = request.connected[0]
    assert signal == 'http-success'
    handler(request, json.dumps(payload).encode())


def test_translate_unit_builds_a_get_request(monkeypatch):
    client, requests = _make_client(monkeypatch)

    client.translate_unit('hello', 'en', 'af', params={'max': '3'})

    request = requests[0]
    assert request.url == 'http://example.com/en/af/unit'
    assert request.method == 'GET'
    assert request.id == 'hello'
    assert request.user_agent == 'Virtaal/test'
    assert request.params == {'max': '3'}
    assert request.curl.opts[pycurl.TIMEOUT] == 30


def test_translate_unit_returns_the_request_it_sent(monkeypatch):
    client, requests = _make_client(monkeypatch)

    assert client.translate_unit('hello', 'en', 'af') is requests[0]


def test_translate_unit_returns_none_for_a_request_it_could_not_queue(monkeypatch):
    client, _requests = _make_client(monkeypatch)
    monkeypatch.setattr(client, 'add', lambda request: False)

    assert client.translate_unit('hello', 'en', 'af', lambda *a: None) is None


def test_translate_unit_wires_the_callback_with_decoded_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    calls = []

    client.translate_unit(
        'hello', 'en', 'af', callback=lambda widget, id, data: calls.append((id, data))
    )

    _fire_callback(requests[0], {'target': 'hallo'})
    assert calls == [('hello', {'target': 'hallo'})]


def test_add_unit_builds_a_put_request_with_the_unit_as_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    unit = {'source': 'hello', 'target': 'hallo'}

    client.add_unit(unit, 'en', 'af')

    request = requests[0]
    assert request.url == 'http://example.com/en/af/unit'
    assert request.method == 'PUT'
    assert request.id == 'hello'
    assert request.data == json.dumps(unit)
    assert request.user_agent == 'Virtaal/test'


def test_add_unit_wires_the_callback_with_decoded_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    calls = []

    client.add_unit(
        {'source': 'hello'}, 'en', 'af', callback=lambda widget, id, data: calls.append((id, data))
    )

    _fire_callback(requests[0], {'status': 'ok'})
    assert calls == [('hello', {'status': 'ok'})]


def test_update_unit_builds_a_post_request_with_the_unit_as_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    unit = {'source': 'hello', 'target': 'hallo'}

    client.update_unit(unit, 'en', 'af')

    request = requests[0]
    assert request.url == 'http://example.com/en/af/unit'
    assert request.method == 'POST'
    assert request.id == 'hello'
    assert request.data == json.dumps(unit)
    assert request.user_agent == 'Virtaal/test'


def test_update_unit_wires_the_callback_with_decoded_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    calls = []

    client.update_unit(
        {'source': 'hello'}, 'en', 'af', callback=lambda widget, id, data: calls.append((id, data))
    )

    _fire_callback(requests[0], {'status': 'ok'})
    assert calls == [('hello', {'status': 'ok'})]


def test_forget_unit_builds_a_delete_request(monkeypatch):
    client, requests = _make_client(monkeypatch)

    client.forget_unit('hello', 'en', 'af')

    request = requests[0]
    assert request.url == 'http://example.com/en/af/unit'
    assert request.method == 'DELETE'
    assert request.id == 'hello'
    assert json.loads(request.data) == {'target': None, 'context': None}
    assert request.user_agent == 'Virtaal/test'


def test_forget_unit_sends_the_target_and_context(monkeypatch):
    client, requests = _make_client(monkeypatch)

    client.forget_unit('hello', 'en', 'af', target='hallo', context='menu')

    assert json.loads(requests[0].data) == {'target': 'hallo', 'context': 'menu'}


def test_forget_unit_wires_the_callback_with_decoded_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    calls = []

    client.forget_unit(
        'hello', 'en', 'af', callback=lambda widget, id, data: calls.append((id, data))
    )

    _fire_callback(requests[0], {'status': 'ok'})
    assert calls == [('hello', {'status': 'ok'})]


class _FakeStore:
    def __init__(self, filename, content='store content'):
        self.filename = filename
        self._content = content

    def __bytes__(self):
        return self._content.encode('utf-8')

    def __str__(self):
        return '<_FakeStore object>'


def test_get_store_stats_builds_a_get_request(monkeypatch):
    client, requests = _make_client(monkeypatch)
    store = _FakeStore('test.po')

    client.get_store_stats(store, 'en', 'af')

    request = requests[0]
    # The server's route; without the languages it was a 404.
    assert request.url == 'http://example.com/en/af/store'
    assert request.method == 'GET'
    assert request.id == 'test.po'
    assert request.user_agent == 'Virtaal/test'


def test_get_store_stats_wires_the_callback_with_decoded_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    calls = []
    store = _FakeStore('test.po')

    client.get_store_stats(
        store, 'en', 'af', callback=lambda widget, id, data: calls.append((id, data))
    )

    _fire_callback(requests[0], {'total': 10})
    assert calls == [('test.po', {'total': 10})]


def test_upload_store_builds_a_put_request_with_the_stores_bytes(monkeypatch):
    client, requests = _make_client(monkeypatch)
    store = _FakeStore('test.po')

    client.upload_store(store, 'en', 'af')

    request = requests[0]
    assert request.url == 'http://example.com/en/af/store'
    assert request.method == 'PUT'
    assert request.id == 'test.po'
    assert request.data == b'store content'
    assert request.user_agent == 'Virtaal/test'


def test_upload_store_sends_a_real_stores_file_content(monkeypatch):
    # str() of a toolkit store is its repr, which a PUT couldn't send.
    client = TMClient('http://example.com')
    requests = []
    monkeypatch.setattr(client, 'add', lambda request: requests.append(request) or True)
    store = pypo.pofile.parsestring(b'msgid "tree"\nmsgstr "arbre"\n')
    store.filename = 'test.po'

    client.upload_store(store, 'en', 'fr')

    assert b'msgstr "arbre"' in requests[0].data.getvalue()


def test_upload_store_wires_the_callback_with_decoded_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    calls = []
    store = _FakeStore('test.po')

    client.upload_store(
        store, 'en', 'af', callback=lambda widget, id, data: calls.append((id, data))
    )

    _fire_callback(requests[0], {'status': 'ok'})
    assert calls == [('test.po', {'status': 'ok'})]


def test_add_store_builds_a_post_request_with_the_store_as_json(monkeypatch):
    client, requests = _make_client(monkeypatch)

    client.add_store('test.po', {'units': []}, 'en', 'af')

    request = requests[0]
    assert request.url == 'http://example.com/en/af/store'
    assert request.method == 'POST'
    assert request.id == 'test.po'
    assert request.data == json.dumps({'units': []})
    assert request.user_agent == 'Virtaal/test'


def test_forget_store_builds_a_delete_request(monkeypatch):
    client, requests = _make_client(monkeypatch)
    store = _FakeStore('test.po')

    client.forget_store(store, 'en', 'af')

    request = requests[0]
    assert request.url == 'http://example.com/en/af/store'
    assert request.method == 'DELETE'
    assert request.id == 'test.po'
    assert request.user_agent == 'Virtaal/test'


def test_forget_store_wires_the_callback_with_decoded_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    calls = []
    store = _FakeStore('test.po')

    client.forget_store(
        store, 'en', 'af', callback=lambda widget, id, data: calls.append((id, data))
    )

    _fire_callback(requests[0], {'status': 'ok'})
    assert calls == [('test.po', {'status': 'ok'})]


def test_add_store_wires_the_callback_with_decoded_json(monkeypatch):
    client, requests = _make_client(monkeypatch)
    calls = []

    client.add_store(
        'test.po',
        {'units': []},
        'en',
        'af',
        callback=lambda widget, id, data: calls.append((id, data)),
    )

    _fire_callback(requests[0], {'status': 'ok'})
    assert calls == [('test.po', {'status': 'ok'})]


@pytest.mark.parametrize(
    'reply', [b'<html>502 Bad Gateway</html>', b'', b'[{"source": "x"', b'\xff\xfe']
)
def test_a_reply_that_is_not_json_is_dropped_without_raising(monkeypatch, reply):
    # #4437: an error page or a cut-off reply showed the crash dialog.
    client, requests = _make_client(monkeypatch)
    calls = []
    client.translate_unit('hello', 'en', 'af', lambda *args: calls.append(args))
    signal, handler = requests[0].connected[0]

    handler(requests[0], reply)  # must not raise

    assert calls == []


# Against the real server.


@pytest.fixture
def tm_server(tmp_path):
    from virtaal.support import test_tmserver

    app, port = test_tmserver._start_server(tmp_path / 'tm.db')
    yield app, TMClient('http://127.0.0.1:%d' % port)
    while test_tmserver._servers:
        test_tmserver._servers.pop().stop()


def _send(client, monkeypatch, call, *args):
    """Make one request with C{call} and wait for it; returns the callback's data."""
    requests = []
    replies = []
    monkeypatch.setattr(client, 'add', lambda request: requests.append(request) or True)
    call(*args, callback=lambda widget, id, data: replies.append(data))
    requests[0].curl.perform()
    requests[0].handle_result()
    return requests[0].status, replies


def test_store_stats_and_forget_store_reach_the_servers_routes(tm_server, monkeypatch):
    app, client = tm_server
    app.tmdb.sync_store('/home/user/fr.po', [{'source': 'cat', 'target': 'chat'}], 'en', 'fr')
    store = _FakeStore('/home/user/fr.po')

    status, replies = _send(client, monkeypatch, client.get_store_stats, store, 'en', 'fr')
    assert status == 200
    assert replies[0]['units'] == 1

    status, replies = _send(client, monkeypatch, client.forget_store, store, 'en', 'fr')
    assert (status, replies) == (200, [{'removed': 1}])
    assert app.tmdb.translate_unit('cat', 'en', 'fr') == []


def test_upload_store_imports_the_file_on_the_server(tm_server, monkeypatch):
    app, client = tm_server
    store = pypo.pofile.parsestring(b'msgid "tree"\nmsgstr "arbre"\n')
    store.filename = '/home/user/fr.po'

    status, replies = _send(client, monkeypatch, client.upload_store, store, 'en', 'fr')

    assert (status, replies) == (200, [{'added': 1, 'replaced': 0, 'removed': 0}])
    assert app.tmdb.translate_unit('tree', 'en', 'fr')[0]['target'] == 'arbre'


def test_forget_unit_removes_the_target_on_the_server(tm_server, monkeypatch):
    app, client = tm_server
    app.tmdb.add_dict({'source': 'cat', 'target': 'chat', 'context': ''}, 'en', 'fr')

    status, replies = _send(
        client,
        monkeypatch,
        lambda *a, **kw: client.forget_unit(*a, target='chat', **kw),
        'cat',
        'en',
        'fr',
    )

    assert (status, replies) == (200, [{'removed': 1}])
    assert app.tmdb.translate_unit('cat', 'en', 'fr') == []


def test_import_file_puts_the_files_content_at_its_path(monkeypatch):
    client, requests = _make_client(monkeypatch)

    request = client.import_file('/home/user/memory.tmx', b'<tmx/>', 'en', 'af')

    assert request is requests[0]
    assert request.url == 'http://example.com/en/af/store'
    assert request.method == 'PUT'
    assert request.id == '/home/user/memory.tmx'
    assert request.data == b'<tmx/>'
    assert request.curl.opts[pycurl.TIMEOUT] == 600


def test_import_file_is_none_when_the_queue_is_full(monkeypatch):
    client, _requests = _make_client(monkeypatch)
    monkeypatch.setattr(client, 'add', lambda request: False)

    assert client.import_file('/home/user/fr.po', b'x', 'en', 'fr') is None
