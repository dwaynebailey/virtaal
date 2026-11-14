#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import io
import json
from types import SimpleNamespace

import pytest
from gi.repository import GObject

from virtaal.plugins.tm._testhelpers import fake_controller
from virtaal.plugins.tm.models.amagama import TMModel, v2_url
from virtaal.support.abstraction import ABSTRACTION_VERSION, abstract


def _model():
    model = TMModel.__new__(TMModel)
    GObject.GObject.__init__(model)
    return model


# __init__(): only overrides push_store/upload_store/__init__ itself -
# calls BaseTMModel.__init__() directly (not remotetm.TMModel's, which
# does more than amagama wants) and builds a real TMClient from the
# configured url.


def test_init_loads_config_and_builds_a_tmclient(monkeypatch):
    monkeypatch.setattr(
        TMModel,
        'load_config',
        lambda self: setattr(self, 'config', {'url': 'https://example.com/api/v1/'}),
    )

    model = TMModel('amagama', fake_controller())

    assert model.internal_name == 'amagama'
    assert model.tmclient.base_url == 'https://example.com/api/v1/'
    assert model.tmclient.user_agent.startswith('Virtaal')


def test_amagama_inherits_remotetms_query_logic():
    # Without API v2, the query()/_handle_matches() logic is remotetm's own.
    model = _model()
    model.source_lang = 'en'
    model.target_lang = 'af'
    model.checker = None
    model.cache = {}
    calls = []
    model.tmclient = SimpleNamespace(translate_unit=lambda *a: calls.append(a))

    model.query(None, SimpleNamespace(source='hello'))

    assert calls[0][:3] == ('hello', 'en', 'af')


def test_push_store_is_a_no_op():
    model = _model()

    model.push_store(SimpleNamespace())  # must not raise


def test_upload_store_is_a_no_op():
    model = _model()

    model.upload_store(SimpleNamespace())  # must not raise


# API v2: the abstract goes to /api/v2, with a fallback to v1


@pytest.fixture(autouse=True)
def _fresh_session(monkeypatch):
    """Each test starts a session that knows no server's API version."""
    monkeypatch.setattr(TMModel, '_v1_only', set())


@pytest.mark.parametrize(
    'url, expected',
    [
        (
            'https://amagama-live.translatehouse.org/api/v1/',
            'https://amagama-live.translatehouse.org/api/v2',
        ),
        ('http://localhost:8888/api/v1', 'http://localhost:8888/api/v2'),
        ('http://localhost:8888/tmserver/', None),
        ('https://example.com/api/v1/extra', None),
    ],
)
def test_v2_url(url, expected):
    assert v2_url(url) == expected


class _Request:
    """A sent request whose replies a test plays."""

    def __init__(self, api, query_str, sent, callback):
        self.api = api
        self.query_str = query_str
        self.sent = sent
        self.callback = callback
        self.result_headers = io.BytesIO(b'')
        self.handlers = []

    def connect(self, signal, handler, *args):
        self.handlers.append((signal, handler, args))

    def reply(self, data):
        self.callback(self, self.query_str, data)
        self.fire('http-success', b'')

    def fire(self, signal, result):
        for name, handler, args in self.handlers:
            if name == signal:
                handler(self, result, *args)


class _Client:
    def __init__(self):
        self.sent = []
        self.requests = set()

    def translate_unit(self, query_str, source_lang, target_lang, callback, params):
        self.sent.append(_Request('v1', query_str, (source_lang, target_lang, params), callback))
        return self.sent[-1]

    def translate_abstract(
        self, base_url, query_str, abstract, version, source_lang, target_lang, callback, params
    ):
        sent = (base_url, abstract, version, source_lang, target_lang, params)
        self.sent.append(_Request('v2', query_str, sent, callback))
        return self.sent[-1]


def _v2_model(url='https://amagama.example/api/v1/', source_lang='en', checker=None, **controller):
    model = _model()
    model.controller = SimpleNamespace(
        **{'abstract_placeables': True, 'min_quality': 70, **controller}
    )
    model.source_lang = source_lang
    model.target_lang = 'af'
    model.checker = checker
    model.cache = {}
    model.shortname = 'Amagama'
    model.v2_base = v2_url(url)
    model.tmclient = _Client()
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))
    return model, emitted


QUERY = 'Delete %(count)s files'
STORED = {'source': 'Delete {0} files', 'target': 'Skrap {0} lêers', 'quality': 100}


def test_v2_matches_differing_only_in_case_are_near_exact():
    model, emitted = _v2_model(source_lang='tr')

    model.query(None, SimpleNamespace(source='KAYIT'))
    model.tmclient.sent[0].reply(
        {'abstraction': ABSTRACTION_VERSION, 'matches': [{'source': 'Kayıt', 'target': 'x'}]}
    )

    [(_query_str, [match])] = emitted
    assert (match['quality'], match['raw_quality']) == (97, 97)


def test_sends_the_abstract_to_v2():
    model, _emitted = _v2_model(checker='gnome')

    model.query(None, SimpleNamespace(source=QUERY))

    [request] = model.tmclient.sent
    assert request.api == 'v2'
    assert request.sent == (
        'https://amagama.example/api/v2',
        'Delete \ue001 files',
        ABSTRACTION_VERSION,
        'en',
        'af',
        {'style': 'gnome'},
    )


def test_v2_matches_are_scored_on_the_raw_text():
    model, emitted = _v2_model()

    model.query(None, SimpleNamespace(source=QUERY))
    model.tmclient.sent[0].reply({'abstraction': ABSTRACTION_VERSION, 'matches': [dict(STORED)]})

    [(query_str, [match])] = emitted
    assert query_str == QUERY
    # The target is rebuilt by the controller, from the raw quality.
    assert match['target'] == 'Skrap {0} lêers'
    assert match['raw_quality'] < 70
    assert 90 < match['quality'] < 100
    assert match['tmsource'] == 'Amagama'
    assert model.cache[QUERY] == [match]


def test_v2_matches_below_the_minimum_are_dropped():
    model, emitted = _v2_model(min_quality=100)

    model.query(None, SimpleNamespace(source=QUERY))
    model.tmclient.sent[0].reply({'matches': [dict(STORED)]})

    assert emitted == []
    assert model.cache == {QUERY: None}


@pytest.mark.parametrize(
    'reply',
    [[dict(STORED)], {'matches': None}, {'matches': [{'source': 1, 'target': 'x'}]}, 'oops'],
)
def test_a_v2_reply_of_another_shape_is_no_matches(reply):
    model, emitted = _v2_model()

    model.query(None, SimpleNamespace(source=QUERY))
    model.tmclient.sent[0].reply(reply)

    assert emitted == []


@pytest.mark.parametrize('status', [404, 409])
def test_a_server_refusing_v2_is_asked_with_v1(status):
    model, emitted = _v2_model()

    model.query(None, SimpleNamespace(source=QUERY))
    model.tmclient.sent[0].fire('http-client-error', status)

    assert [(r.api, r.query_str) for r in model.tmclient.sent] == [('v2', QUERY), ('v1', QUERY)]
    model.tmclient.sent[1].reply([dict(STORED, quality=75)])
    assert [q for q, _r in emitted] == [QUERY]
    # For the rest of the session, also from a new model for that server.
    model.query(None, SimpleNamespace(source='Save file'))
    other, _emitted = _v2_model()
    other.query(None, SimpleNamespace(source='Save file'))
    assert model.tmclient.sent[2].api == other.tmclient.sent[0].api == 'v1'


def test_every_request_the_server_refused_is_asked_with_v1():
    model, _emitted = _v2_model()

    model.query(None, SimpleNamespace(source=QUERY))
    model.prefetch(SimpleNamespace(source='Save file'))
    for request in list(model.tmclient.sent):
        request.fire('http-client-error', 404)

    assert [(r.api, r.query_str) for r in model.tmclient.sent[2:]] == [
        ('v1', QUERY),
        ('v1', 'Save file'),
    ]


def test_v2_is_remembered_per_server_and_source_language():
    model, _emitted = _v2_model()
    model.query(None, SimpleNamespace(source=QUERY))
    model.tmclient.sent[0].fire('http-client-error', 409)

    for other in (
        _v2_model(url='http://localhost:8888/api/v1/')[0],
        _v2_model(source_lang='fr')[0],
    ):
        other.query(None, SimpleNamespace(source=QUERY))
        assert other.tmclient.sent[0].api == 'v2'


def test_too_many_v2_requests_back_off_without_v1():
    model, _emitted = _v2_model()

    model.query(None, SimpleNamespace(source=QUERY))
    request = model.tmclient.sent[0]
    request.result_headers = io.BytesIO(b'Retry-After: 120\r\n')
    request.fire('http-client-error', 429)
    model.query(None, SimpleNamespace(source=QUERY))

    assert len(model.tmclient.sent) == 1
    assert TMModel._v1_only == set()


def test_a_refusal_after_a_language_change_changes_nothing():
    model, _emitted = _v2_model()

    model.query(None, SimpleNamespace(source=QUERY))
    model.target_lang = 'de'
    model.tmclient.sent[0].fire('http-client-error', 404)

    assert len(model.tmclient.sent) == 1
    assert TMModel._v1_only == set()


def test_prefetch_uses_v2():
    model, _emitted = _v2_model()

    model.prefetch(SimpleNamespace(source=QUERY))
    model.tmclient.sent[0].reply({'matches': [dict(STORED)]})
    model.query(None, SimpleNamespace(source=QUERY))

    assert [r.api for r in model.tmclient.sent] == ['v2']


@pytest.mark.parametrize(
    'url, controller',
    [
        ('https://amagama.example/api/v1/', {'abstract_placeables': False}),
        ('http://localhost:8888/tmserver/', {}),
    ],
)
def test_v1_without_abstraction_or_a_v2_url(url, controller):
    model, _emitted = _v2_model(url=url)
    model.controller.__dict__.update(controller)

    model.query(None, SimpleNamespace(source=QUERY))

    assert model.tmclient.sent[0].api == 'v1'


def test_init_finds_the_v2_url(monkeypatch):
    monkeypatch.setattr(
        TMModel,
        'load_config',
        lambda self: setattr(self, 'config', {'url': 'https://example.com/api/v1/'}),
    )

    assert TMModel('amagama', fake_controller()).v2_base == 'https://example.com/api/v2'


# A file's suggestions (#1679)


def test_a_file_fetch_sends_the_abstract_to_v2_and_scores_the_reply():
    model, emitted = _v2_model()

    request = model.bulk_send([QUERY])
    found = model.bulk_parse([QUERY], json.dumps({'matches': [STORED]}).encode())

    assert request.api == 'v2'
    assert request.sent[1] == abstract(QUERY)[0]
    assert found[QUERY][0]['target'] == 'Skrap {0} lêers'
    assert 90 < found[QUERY][0]['quality'] < 100
    assert found[QUERY][0]['tmsource'] == 'Amagama'
    assert emitted == []


@pytest.mark.parametrize('status', [404, 409])
def test_a_file_fetch_the_server_refuses_on_v2_is_asked_with_v1(status):
    model, _emitted = _v2_model()
    request = model.bulk_send([QUERY])

    assert model.bulk_wait(request, status) == 0
    assert model.bulk_send([QUERY]).api == 'v1'
    assert model.bulk_parse([QUERY], json.dumps([STORED]).encode())[QUERY][0]['quality'] == 100


# lookup(), for Pre-translate: nothing is sent


def test_lookup_answers_from_the_file_fetch_then_this_sessions_answers():
    model = _model()
    fetched = {'Open': [{'source': 'Open', 'target': 'Maak oop'}]}
    model.controller = SimpleNamespace(
        fetched=SimpleNamespace(matches=lambda service, source: fetched.get(source))
    )
    model.internal_name = 'amagama'
    model.cache = {'Close': [{'source': 'Close', 'target': 'Maak toe'}], 'Quit': None}
    model.tmclient = SimpleNamespace()  # anything sent raises AttributeError
    found = []

    for source in ('Open', 'Close', 'Quit', 'Save'):
        model.lookup(SimpleNamespace(source=source), found.append)

    assert [[m['target'] for m in matches] for matches in found] == [
        ['Maak oop'],
        ['Maak toe'],
        [],
        [],
    ]
