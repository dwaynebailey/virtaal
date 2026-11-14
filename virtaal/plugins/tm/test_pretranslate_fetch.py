#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Pre-translate fetching from amaGama first: the real amaGama model,
file fetch and HTTP requests, answered by a stand-in for the server
instead of the network. The GLib timeouts and idle callbacks are run by
the test, not a main loop."""

import io
import json
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from gi.repository import GLib
from translate.storage import po

from virtaal.plugins.tm import pretranslate
from virtaal.plugins.tm._testhelpers import fake_controller
from virtaal.plugins.tm.fetched import FetchedSuggestions
from virtaal.plugins.tm.models.amagama import TMModel as AmagamaModel
from virtaal.plugins.tm.pretranslate import Options
from virtaal.plugins.tm.tmcontroller import TMController
from virtaal.support import tmclient

TRANSLATIONS = {'Open': 'Maak oop', 'Close': 'Maak toe', 'Save': 'Stoor'}
URL = 'https://amagama.example/api/v1/'


class _Server:
    """amaGama's API v2 unit lookup, answering each request the HTTP
    client is given from the main loop. replies holds (status, headers)
    to answer with before the usual answers; down: no answer at all."""

    def __init__(self, monkeypatch):
        self.requests = []
        self.replies = []
        self.down = False
        server = self

        class Request(tmclient.RESTRequest):
            def __init__(self, url, id, *args, params=None, **kwargs):
                super().__init__(url, id, *args, params=params, **kwargs)
                self.sent_to = url
                self.params = dict(params or {})

        monkeypatch.setattr(tmclient, 'RESTRequest', Request)
        monkeypatch.setattr(
            tmclient.TMClient, 'add', lambda client, request: server._add(request) or True
        )

    def _add(self, request):
        self.requests.append(
            SimpleNamespace(
                path=urlsplit(request.sent_to).path,
                q=request.params.get('q', ''),
                purpose=next(
                    (
                        h.split(': ', 1)[1]
                        for h in request.headers or ()
                        if h.startswith('Sec-Purpose')
                    ),
                    None,
                ),
            )
        )
        GLib.idle_add(self._answer, request)

    def _answer(self, request):
        if self.down:
            request.emit('http-failed', 'Could not connect to server')
            return False
        if self.replies:
            status, headers = self.replies.pop(0)
            request.result_headers = io.BytesIO(
                b''.join(b'%s: %s\r\n' % (k.encode(), v.encode()) for k, v in headers.items())
            )
            request.emit('http-client-error', status)
            return False
        q = request.params.get('q', '')
        matches = [{'source': q, 'target': TRANSLATIONS[q]}] if q in TRANSLATIONS else []
        request.emit('http-success', json.dumps({'matches': matches}).encode())
        return False


@pytest.fixture
def server(monkeypatch):
    return _Server(monkeypatch)


class _Paid:
    """A machine translation service that charges: never to be asked."""

    display_name = internal_name = 'paid'
    online_service = 'Paid'
    billed = machine_translation = True
    BULK_MAX = None

    def __init__(self):
        self.sent = []

    def bulk_ready(self):
        return True

    def bulk_send(self, batch):
        self.sent.append(batch)

    def lookup(self, unit, on_done):
        on_done([])


class _Local:
    """A translation memory on this machine."""

    display_name = internal_name = 'local'

    def lookup(self, unit, on_done):
        on_done(
            [{'source': 'Save', 'target': 'Stoor', 'quality': 100}] if unit.source == 'Save' else []
        )


def _store(*sources):
    store = po.pofile()
    for source in sources:
        store.addsourceunit(source)
    return store


def _controller(tmp_path, units):
    controller = TMController.__new__(TMController)
    controller.abstract_placeables = True
    controller.min_quality = 75
    controller.main_controller = SimpleNamespace(
        checks_controller=None,
        lang_controller=SimpleNamespace(get_target_nplurals=lambda: 2),
    )
    controller.fetched = FetchedSuggestions(path=lambda: str(tmp_path / 'tm.db'))
    controller.fetched.load(units, 'en', 'af')
    return controller


def _amagama(monkeypatch, controller, url=URL):
    monkeypatch.setattr(AmagamaModel, '_v1_only', set())
    monkeypatch.setattr(AmagamaModel, '_cached_sources', lambda self: None)
    monkeypatch.setattr(
        AmagamaModel, 'load_config', lambda self: setattr(self, 'config', {'url': url})
    )
    model = AmagamaModel('amagama', fake_controller())
    model.controller = controller
    return model


class _Asked(list):
    pending = None


@pytest.fixture
def waits(monkeypatch):
    """The test's own GLib: callbacks run as _pretranslate() polls, with no
    wait; waits holds the milliseconds each timeout asked for."""
    pending, asked = {}, _Asked()

    def add(ms, func, *args):
        source = len(asked) + len(pending) + 1000
        asked.append(ms)
        pending[source] = (func, args)
        return source

    monkeypatch.setattr(GLib, 'timeout_add', add)
    monkeypatch.setattr(GLib, 'idle_add', lambda func, *args, **kw: add(0, func, *args))
    monkeypatch.setattr(GLib, 'source_remove', lambda source: pending.pop(source, None))
    asked.pending = pending
    return asked


def _pretranslate(controller, models, units, waits, seconds=20):
    """The Result of pre-translating units."""
    results = []
    run = pretranslate.Pretranslation(
        controller, models, units, Options(), lambda *a: None, results.append
    )
    run.start()
    pending = waits.pending
    deadline = time.monotonic() + seconds
    while not results and time.monotonic() < deadline:
        for source, (func, args) in list(pending.items()):
            if pending.pop(source, None) is not None and func(*args):
                pending[source] = (func, args)
        time.sleep(0.005)
    controller.fetched.close()
    assert results, 'Pre-translate never finished'
    return results[0]


def test_it_fetches_only_the_units_to_fill_that_were_not_fetched(
    tmp_path, monkeypatch, server, waits
):
    store = _store('Open', 'Close', 'Save', 'Quit')
    next(u for u in store.units if u.source == 'Quit').target = 'Hou op'
    controller = _controller(tmp_path, store.units)
    controller.fetched.keep(
        'amagama', {'Close': [{'source': 'Close', 'target': 'Sluit', 'quality': 100}]}
    )
    paid = _Paid()
    models = [_amagama(monkeypatch, controller), paid, _Local()]

    result = _pretranslate(controller, models, store.units, waits)

    assert sorted(r.q for r in server.requests) == ['Open', 'Save']
    assert {r.purpose for r in server.requests} == {'prefetch;bulk'}
    assert {r.path for r in server.requests} == {'/api/v2/en/af/unit/'}
    assert paid.sent == []
    assert {str(f.unit.source): f.target for f in result.fills} == {
        'Open': 'Maak oop',
        'Close': 'Sluit',
        'Save': 'Stoor',
    }
    assert result.unreachable == ()


def test_it_waits_as_long_as_the_server_asks(tmp_path, monkeypatch, server, waits):
    server.replies.append((429, {'Retry-After': '7'}))
    store = _store('Open')
    controller = _controller(tmp_path, store.units)
    model = _amagama(monkeypatch, controller)

    result = _pretranslate(controller, [model], store.units, waits)

    first, second = server.requests
    assert first.q == second.q == 'Open'
    assert 7000 in waits
    assert [f.target for f in result.fills] == ['Maak oop']


def test_an_unreachable_server_leaves_the_other_sources_to_fill(
    tmp_path, monkeypatch, server, waits
):
    server.down = True
    store = _store('Open', 'Save')
    controller = _controller(tmp_path, store.units)
    models = [_amagama(monkeypatch, controller), _Local()]

    result = _pretranslate(controller, models, store.units, waits)

    assert [str(f.unit.source) for f in result.fills] == ['Save']
    assert result.unreachable == ('Amagama',)
