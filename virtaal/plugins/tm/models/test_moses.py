#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import xmlrpc.client as xmlrpclib
from types import SimpleNamespace

from gi.repository import GObject

from virtaal.plugins.tm._testhelpers import fake_controller
from virtaal.plugins.tm.models.moses import TMModel


def _model():
    model = TMModel.__new__(TMModel)
    GObject.GObject.__init__(model)
    model.source_lang = None
    model.target_lang = None
    model.cache = {}
    model.clients = {}
    return model


class _FakeMosesClient:
    def __init__(self, server):
        self.server = server
        self.multilang = False
        self.translated = []

    def set_multilang(self):
        self.multilang = True

    def serves(self, target_lang):
        return True

    def translate_unit(self, query_str, callback, target_lang):
        self.translated.append((query_str, callback, target_lang))


# _init_plugin(): maps "src->tgt" config entries to per-server clients,
# reusing one client (and flagging it multilang) when the same server
# backs more than one language pair. MosesClient itself is imported
# locally inside _init_plugin(), so patch it at its source module.


def _init_plugin(monkeypatch, model, config):
    import virtaal.support.mosesclient as mosesclient_module

    monkeypatch.setattr(mosesclient_module, 'MosesClient', _FakeMosesClient)
    model.config = config
    model._init_plugin()


def test_init_loads_config_and_builds_clients(monkeypatch):
    import virtaal.support.mosesclient as mosesclient_module

    monkeypatch.setattr(mosesclient_module, 'MosesClient', _FakeMosesClient)
    monkeypatch.setattr(
        TMModel, 'load_config', lambda self: setattr(self, 'config', {'fr->en': 'http://a'})
    )

    model = TMModel('moses', fake_controller('fr', 'en'))

    assert model.internal_name == 'moses'
    assert model.clients['fr']['en'].server == 'http://a'


def test_init_plugin_maps_each_pair_to_a_client(monkeypatch):
    model = _model()
    _init_plugin(monkeypatch, model, {'fr->en': 'http://a'})

    assert model.clients['fr']['en'].server == 'http://a'


def test_init_plugin_reuses_one_client_for_a_shared_server(monkeypatch):
    model = _model()
    _init_plugin(monkeypatch, model, {'fr->en': 'http://a', 'de->en': 'http://a'})

    client_fr = model.clients['fr']['en']
    client_de = model.clients['de']['en']
    assert client_fr is client_de
    assert client_fr.multilang is True


def test_init_plugin_creates_separate_clients_for_different_servers(monkeypatch):
    model = _model()
    _init_plugin(monkeypatch, model, {'fr->en': 'http://a', 'de->en': 'http://b'})

    client_fr = model.clients['fr']['en']
    client_de = model.clients['de']['en']
    assert client_fr is not client_de
    assert client_fr.multilang is False
    assert client_de.multilang is False


# query() / _handle_response()


def test_query_does_nothing_for_an_unconfigured_language_pair():
    model = _model()
    model.source_lang, model.target_lang = 'fr', 'en'

    model.query(None, SimpleNamespace(source='bonjour'))  # must not raise


def test_query_emits_a_cached_match():
    model = _model()
    model.source_lang, model.target_lang = 'fr', 'en'
    model.clients = {'fr': {'en': _FakeMosesClient('http://a')}}
    model.cache = {'bonjour': {'target': 'hello'}}
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))

    model.query(None, SimpleNamespace(source='bonjour'))

    assert emitted == [('bonjour', [{'target': 'hello'}])]


def test_query_delegates_to_the_matching_language_pairs_client():
    model = _model()
    model.source_lang, model.target_lang = 'fr', 'en'
    client = _FakeMosesClient('http://a')
    model.clients = {'fr': {'en': client}}

    model.query(None, SimpleNamespace(source='bonjour'))

    assert client.translated == [('bonjour', model._handle_response, 'en')]


def test_one_unknown_system_leaves_the_other_pairs_on_a_shared_server(monkeypatch):
    from virtaal.support import mosesclient

    sent = []
    monkeypatch.setattr(mosesclient.MosesClient, 'add', lambda self, request: sent.append(request))
    model = _model()
    model.config = {'en->af': 'http://moses', 'en->fr': 'http://moses'}
    model._init_plugin()
    model.source_lang, model.target_lang = 'en', 'af'
    model.query(None, SimpleNamespace(source='one'))
    fault = xmlrpclib.dumps(
        xmlrpclib.Fault(1, 'Unknown translation system id: af'), methodresponse=True
    )
    sent[0].emit('http-success', fault)

    model.query(None, SimpleNamespace(source='two'))
    assert len(sent) == 1
    assert not model.bulk_ready()

    model.target_lang = 'fr'
    model.query(None, SimpleNamespace(source='three'))
    (params,), _method = xmlrpclib.loads(sent[1].data)
    assert params['system'] == 'fr'


def test_handle_response_ignores_an_empty_response():
    model = _model()

    model._handle_response('bonjour', None)  # must not raise

    assert model.cache == {}


def test_handle_response_caches_and_emits_a_translation():
    model = _model()
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))

    model._handle_response('bonjour', 'hello')

    assert model.cache['bonjour'] == {'source': 'bonjour', 'target': 'hello', 'tmsource': 'Moses'}
    assert emitted == [('bonjour', [model.cache['bonjour']])]


# A file's suggestions (#1679)


def test_a_file_fetch_asks_the_language_pairs_server_a_unit_at_a_time():
    model = _model()
    model.source_lang, model.target_lang = 'en', 'af'
    sent = []
    client = SimpleNamespace(
        translate_unit=lambda q, callback, target: sent.append((q, callback, target)) or 'request',
        serves=lambda target: True,
        _handle_response=lambda q, reply, target: {b'good': 'Goed', b'': None}[reply],
    )
    assert not model.bulk_ready()
    model.clients = {'en': {'af': client}}

    assert model.bulk_ready()
    assert model.bulk_send(['good']) == 'request'
    assert sent == [('good', None, 'af')]
    assert model.bulk_parse(['good'], b'good') == {
        'good': [{'source': 'good', 'target': 'Goed', 'tmsource': 'Moses'}]
    }
    assert model.bulk_parse(['bad'], b'') == {'bad': []}
