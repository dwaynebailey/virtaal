#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import io
import time
from types import SimpleNamespace

import pytest
from gi.repository import GObject

from virtaal.plugins.tm.models.remotetm import TMModel, unit2dict


def _model(source_lang='en', target_lang='af', checker=None, cache=None):
    model = TMModel.__new__(TMModel)
    GObject.GObject.__init__(model)
    model.source_lang = source_lang
    model.target_lang = target_lang
    model.checker = checker
    model.cache = cache if cache is not None else {}
    model.shortname = 'Remote TM'
    return model


def _unit(source='hello'):
    return SimpleNamespace(source=source)


# query()


def test_query_does_nothing_when_source_and_target_languages_match():
    model = _model(source_lang='en', target_lang='en')
    model.tmclient = SimpleNamespace(
        translate_unit=lambda *a: (_ for _ in ()).throw(
            AssertionError('should not query with a misconfigured en->en pair')
        )
    )
    matches = []
    model.connect('match-found', lambda m, q, r: matches.append((q, r)))

    model.query(None, _unit('hello'))

    assert matches == []


def test_query_emits_a_cached_match_without_calling_the_client():
    model = _model(cache={'hello': [{'target': 'Hallo'}]})
    model.tmclient = SimpleNamespace(
        translate_unit=lambda *a: (_ for _ in ()).throw(AssertionError('should use the cache'))
    )
    matches = []
    model.connect('match-found', lambda m, q, r: matches.append((q, r)))

    model.query(None, _unit('hello'))

    assert matches == [('hello', [{'target': 'Hallo'}])]


def test_query_emits_nothing_for_a_cached_negative_result():
    model = _model(cache={'hello': None})
    model.tmclient = SimpleNamespace(
        translate_unit=lambda *a: (_ for _ in ()).throw(AssertionError('should use the cache'))
    )
    matches = []
    model.connect('match-found', lambda m, q, r: matches.append((q, r)))

    model.query(None, _unit('hello'))

    assert matches == []


def test_query_asks_the_tmclient_when_not_cached():
    calls = []
    model = _model()
    model.tmclient = SimpleNamespace(translate_unit=lambda *a: calls.append(a))

    model.query(None, _unit('hello'))

    (query_str, source_lang, target_lang, callback, params) = calls[0]
    assert (query_str, source_lang, target_lang) == ('hello', 'en', 'af')
    assert callback == model._handle_matches
    assert params == {}


def test_query_includes_the_checker_style_when_set():
    calls = []
    model = _model(checker='standard')
    model.tmclient = SimpleNamespace(translate_unit=lambda *a: calls.append(a))

    model.query(None, _unit('hello'))

    assert calls[0][4] == {'style': 'standard'}


# _handle_matches()


def test_handle_matches_caches_none_and_emits_nothing_when_there_are_no_matches():
    model = _model()
    matches = []
    model.connect('match-found', lambda m, q, r: matches.append((q, r)))

    model._handle_matches(None, 'hello', [])

    assert model.cache['hello'] is None
    assert matches == []


def test_handle_matches_labels_each_match_then_emits():
    model = _model()
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))
    raw_matches = [{'target': 'Hallo', 'source': 'hello'}]

    model._handle_matches(None, 'hello', raw_matches)

    assert model.cache['hello'] == raw_matches
    assert raw_matches[0]['target'] == 'Hallo'
    assert raw_matches[0]['tmsource'] == 'Remote TM'
    assert emitted == [('hello', raw_matches)]


# push_store() / upload_store()


def test_push_store_only_sends_translated_units():
    added = []
    model = _model()
    model.tmclient = SimpleNamespace(add_store=lambda *a: added.append(a))
    translated = SimpleNamespace(
        source='hello', target='Hallo', getcontext=lambda: '', istranslated=lambda: True
    )
    untranslated = SimpleNamespace(
        source='bye', target='', getcontext=lambda: '', istranslated=lambda: False
    )
    store_controller = SimpleNamespace(
        store=SimpleNamespace(
            get_units=lambda: [translated, untranslated], get_filename=lambda: 'file.po'
        )
    )

    model.push_store(store_controller)

    (filename, units, source_lang, target_lang) = added[0]
    assert filename == 'file.po'
    assert units == [{'source': 'hello', 'target': 'Hallo', 'context': ''}]
    assert (source_lang, target_lang) == ('en', 'af')


def test_push_store_resets_the_cache():
    model = _model(cache={'stale': 'entry'})
    model.tmclient = SimpleNamespace(add_store=lambda *a: None)
    store_controller = SimpleNamespace(
        store=SimpleNamespace(get_units=lambda: [], get_filename=lambda: 'file.po')
    )

    model.push_store(store_controller)

    assert model.cache == {}


def test_upload_store_resets_the_cache():
    model = _model(cache={'stale': 'entry'})
    model.tmclient = SimpleNamespace(upload_store=lambda *a: None)
    store_controller = SimpleNamespace(store=SimpleNamespace(_trans_store='store'))

    model.upload_store(store_controller)

    assert model.cache == {}


def test_unit2dict_extracts_the_relevant_fields():
    unit = SimpleNamespace(source='hello', target='Hallo', getcontext=lambda: 'greeting')

    assert unit2dict(unit) == {'source': 'hello', 'target': 'Hallo', 'context': 'greeting'}


@pytest.mark.parametrize(
    'reply', [{'source': 'x'}, 'text', 42, None, [1, 'x'], [{'target': 'only'}]]
)
def test_handle_matches_treats_a_reply_of_another_shape_as_no_matches(reply):
    # #4437: JSON that isn't a list of matches raised TypeError.
    model = _model()
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))

    model._handle_matches(None, 'hello', reply)

    assert model.cache['hello'] is None
    assert emitted == []


# prefetch(), the pending requests and back-off


class _Request:
    """A sent request whose replies a test plays."""

    def __init__(self, query_str, callback, headers=b''):
        self.query_str = query_str
        self.callback = callback
        self.result_headers = io.BytesIO(headers)
        self.handlers = []

    def connect(self, signal, handler, *args):
        self.handlers.append((signal, handler, args))

    def reply(self, matches):
        self.callback(self, self.query_str, matches)
        self._fire('http-success', b'[]')

    def fail(self, signal, result):
        self._fire(signal, result)

    def _fire(self, signal, result):
        for name, handler, args in self.handlers:
            if name == signal:
                handler(self, result, *args)


class _Client:
    def __init__(self):
        self.sent = []
        self.requests = set()
        self.full = False

    def translate_unit(self, query_str, source_lang, target_lang, callback, params):
        if self.full:
            return None
        request = _Request(query_str, callback)
        self.sent.append(request)
        return request


def _prefetching_model(**kwargs):
    model = _model(**kwargs)
    model.tmclient = _Client()
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))
    return model, emitted


_MATCH = {'source': 'hello', 'target': 'Hallo'}


def test_prefetch_fills_the_cache_for_a_later_query():
    model, emitted = _prefetching_model()

    model.prefetch(_unit('hello'))
    model.tmclient.sent[0].reply([dict(_MATCH)])
    model.query(None, _unit('hello'))

    assert len(model.tmclient.sent) == 1
    assert [q for q, _r in emitted] == ['hello', 'hello']


def test_a_query_for_a_pending_string_waits_for_that_reply():
    model, emitted = _prefetching_model()

    model.prefetch(_unit('hello'))
    assert model.has_cached('hello')
    model.query(None, _unit('hello'))
    model.prefetch(_unit('hello'))
    model.tmclient.sent[0].reply([dict(_MATCH)])

    assert len(model.tmclient.sent) == 1
    assert [q for q, _r in emitted] == ['hello']
    assert model._pending == {}


def test_prefetch_skips_what_is_cached():
    model, _emitted = _prefetching_model(cache={'hello': None})

    model.prefetch(_unit('hello'))

    assert model.tmclient.sent == []


@pytest.mark.parametrize(
    'signal, result',
    [
        ('http-client-error', 404),
        ('http-server-error', 500),
        ('http-failed', 'timed out'),
        ('http-success', b'not json'),  # never reaches the callback
    ],
)
def test_a_request_that_fails_is_sent_again_next_time(signal, result):
    model, emitted = _prefetching_model()

    model.query(None, _unit('hello'))
    model.tmclient.sent[0].fail(signal, result)

    assert not model.has_cached('hello')
    model.query(None, _unit('hello'))
    assert len(model.tmclient.sent) == 2
    assert emitted == []


def test_a_request_the_client_could_not_queue_is_not_pending():
    model, _emitted = _prefetching_model()
    model.tmclient.full = True

    model.prefetch(_unit('hello'))

    assert not model.has_cached('hello')


def test_a_reply_for_the_previous_languages_is_dropped():
    model, emitted = _prefetching_model()

    model.query(None, _unit('hello'))
    model.target_lang = 'de'
    assert not model.has_cached('hello')
    model.query(None, _unit('hello'))
    model.tmclient.sent[0].reply([dict(_MATCH)])

    assert emitted == []
    assert 'hello' not in model.cache
    assert model.has_cached('hello')  # the new request is still on its way
    model.tmclient.sent[1].reply([dict(_MATCH)])
    assert [q for q, _r in emitted] == ['hello']


def test_a_reply_for_a_previous_style_is_dropped():
    model, emitted = _prefetching_model(checker='gnome')

    model.query(None, _unit('hello'))
    model.checker = 'mozilla'
    model.tmclient.sent[0].reply([dict(_MATCH)])

    assert emitted == [] and model.cache == {}


@pytest.mark.parametrize(
    'status, headers, seconds',
    [
        (429, b'HTTP/1.1 429 Too Many Requests\r\nRetry-After: 120\r\n\r\n', 120),
        (429, b'HTTP/1.1 429 Too Many Requests\r\n\r\n', TMModel.RETRY_AFTER),
        (429, b'Retry-After: soon\r\n', TMModel.RETRY_AFTER),
        (429, b'Retry-After: 100000\r\n', TMModel.MAX_RETRY_AFTER),
        (503, b'Retry-After: 30\r\n', 30),
        # A redirect's headers come first; the last reply's count.
        (429, b'HTTP/1.1 302 Found\r\nRetry-After: 5\r\n\r\nHTTP/1.1 429\r\nRetry-After: 9\r\n', 9),
    ],
)
def test_too_many_requests_stops_requests_for_a_while(monkeypatch, status, headers, seconds):
    now = [1000.0]
    monkeypatch.setattr('virtaal.plugins.tm.models.remotetm.time.monotonic', lambda: now[0])
    model, emitted = _prefetching_model()

    model.query(None, _unit('hello'))
    request = model.tmclient.sent[0]
    request.result_headers = io.BytesIO(headers)
    request.fail('http-server-error' if status >= 500 else 'http-client-error', status)

    now[0] += seconds - 1
    model.query(None, _unit('hello'))
    model.prefetch(_unit('other'))
    assert len(model.tmclient.sent) == 1
    assert model.has_cached('other')  # nothing to wait for
    assert emitted == []

    now[0] += 1
    model.query(None, _unit('hello'))
    assert len(model.tmclient.sent) == 2


def test_too_many_requests_until_a_date(monkeypatch):
    monkeypatch.setattr('virtaal.plugins.tm.models.remotetm.time.time', lambda: 784111717.0)
    model, _emitted = _prefetching_model()
    model.query(None, _unit('hello'))
    request = model.tmclient.sent[0]
    # 60 s after time.time() above
    request.result_headers = io.BytesIO(b'Retry-After: Sun, 06 Nov 1994 08:49:37 GMT\r\n')

    request.fail('http-client-error', 429)

    assert 59 <= model._retry_at - time.monotonic() <= 60


def test_a_server_error_without_retry_after_does_not_back_off():
    model, _emitted = _prefetching_model()
    model.query(None, _unit('hello'))

    model.tmclient.sent[0].fail('http-server-error', 503)
    model.query(None, _unit('hello'))

    assert len(model.tmclient.sent) == 2


def test_has_cached_with_nothing_to_send_for_the_same_languages():
    model, _emitted = _prefetching_model(source_lang='en', target_lang='en')

    assert model.has_cached('hello')
    model.prefetch(_unit('hello'))
    assert model.tmclient.sent == []


def test_requests_in_flight_counts_the_clients_requests():
    model, _emitted = _prefetching_model()
    model.tmclient.requests = {1, 2, 3}

    assert model.requests_in_flight() == 3


def test_a_reply_asked_for_before_a_save_is_dropped():
    # The server may not have had the saved translations yet.
    model, emitted = _prefetching_model()
    model.tmclient.add_store = lambda *a: None
    store_controller = SimpleNamespace(
        store=SimpleNamespace(get_units=lambda: [], get_filename=lambda: 'file.po')
    )

    model.prefetch(_unit('hello'))
    model.push_store(store_controller)
    model.tmclient.sent[0].reply([dict(_MATCH)])

    assert emitted == [] and model.cache == {}
    model.query(None, _unit('hello'))
    assert len(model.tmclient.sent) == 2


# Setting the server from Configure… (#1647).


def _configured_model(monkeypatch, tmp_path):
    from virtaal.plugins.tm._testhelpers import fake_controller
    from virtaal.support import tmclient

    monkeypatch.setattr('virtaal.common.pan_app.get_config_dir', lambda: str(tmp_path))
    monkeypatch.setattr(tmclient.TMClient, 'set_virtaal_useragent', lambda self: None)
    return TMModel('remotetm', fake_controller())


def test_the_server_can_be_configured(monkeypatch, tmp_path):
    model = _configured_model(monkeypatch, tmp_path)

    assert model.configure_func == model.configure
    assert [field.key for field in model.settings] == ['host', 'port']
    assert model.tmclient.base_url == 'http://localhost:55555/tmserver'


def test_a_new_server_is_used_at_once(monkeypatch, tmp_path):
    model = _configured_model(monkeypatch, tmp_path)
    model.cache = {'hello': [dict(_MATCH)]}

    model.update_settings({'host': 'tm.example.org', 'port': '8080'})

    assert model.tmclient.base_url == 'http://tm.example.org:8080/tmserver'
    assert model.cache == {}
    ini = (tmp_path / 'tm.ini').read_text()
    assert 'host = tm.example.org' in ini and 'port = 8080' in ini


def test_a_reply_from_the_previous_server_is_dropped(monkeypatch, tmp_path):
    model = _configured_model(monkeypatch, tmp_path)
    old_client = model.tmclient = _Client()
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))
    model.prefetch(_unit('hello'))

    model.update_settings({'host': 'tm.example.org', 'port': '8080'})
    old_client.sent[0].reply([dict(_MATCH)])

    assert emitted == [] and model.cache == {}
    assert model._pending == {}


def test_the_same_server_again_changes_nothing(monkeypatch, tmp_path):
    model = _configured_model(monkeypatch, tmp_path)
    client = model.tmclient

    model.update_settings({'host': 'localhost', 'port': '55555'})

    assert model.tmclient is client
    assert not (tmp_path / 'tm.ini').exists()


def test_the_local_tm_server_is_not_configurable():
    from virtaal.plugins.tm.models import localtm

    assert localtm.TMModel.settings == ()
