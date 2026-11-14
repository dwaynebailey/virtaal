#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Pre-translate fetching from amaGama first: the real amaGama model and
HTTP client against a stand-in server on this machine. The GLib timeouts
and idle callbacks are run by the test, not a main loop."""

import http.server
import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from gi.repository import GLib
from translate.storage import po

from virtaal.plugins.tm import pretranslate
from virtaal.plugins.tm._testhelpers import fake_controller
from virtaal.plugins.tm.fetched import FetchedSuggestions
from virtaal.plugins.tm.models.amagama import TMModel as AmagamaModel
from virtaal.plugins.tm.pretranslate import Options
from virtaal.plugins.tm.tmcontroller import TMController

TRANSLATIONS = {'Open': 'Maak oop', 'Close': 'Maak toe', 'Save': 'Stoor'}


class _Server:
    """amaGama's API v2 unit lookup. replies holds (status, headers) to
    answer with before the usual answers."""

    def __init__(self):
        self.requests = []
        self.replies = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlsplit(self.path)
                server.requests.append(
                    SimpleNamespace(
                        path=url.path,
                        q=parse_qs(url.query).get('q', [''])[0],
                        purpose=self.headers.get('Sec-Purpose'),
                    )
                )
                if server.replies:
                    status, headers = server.replies.pop(0)
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                q = server.requests[-1].q
                matches = [{'source': q, 'target': TRANSLATIONS[q]}] if q in TRANSLATIONS else []
                body = json.dumps({'matches': matches}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d/api/v1/' % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    found = _Server()
    yield found
    found.close()


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


def _amagama(monkeypatch, url, controller):
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
    models = [_amagama(monkeypatch, server.url, controller), paid, _Local()]

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
    model = _amagama(monkeypatch, server.url, controller)

    result = _pretranslate(controller, [model], store.units, waits)

    first, second = server.requests
    assert first.q == second.q == 'Open'
    assert 7000 in waits
    assert [f.target for f in result.fills] == ['Maak oop']


def test_an_unreachable_server_leaves_the_other_sources_to_fill(
    tmp_path, monkeypatch, server, waits
):
    url = server.url
    server.close()
    store = _store('Open', 'Save')
    controller = _controller(tmp_path, store.units)
    models = [_amagama(monkeypatch, url, controller), _Local()]

    result = _pretranslate(controller, models, store.units, waits)

    assert [str(f.unit.source) for f in result.fills] == ['Save']
    assert result.unreachable == ('Amagama',)
