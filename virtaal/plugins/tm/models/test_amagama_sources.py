#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Choosing and weighting amaGama's sources (#4565). Nothing here reaches
the network: the server's replies are given by hand."""

import json
from types import SimpleNamespace

import pytest
from gi.repository import GObject

from virtaal.common import pan_app
from virtaal.plugins.tm._testhelpers import fake_controller
from virtaal.plugins.tm.models import amagama
from virtaal.plugins.tm.models.amagama import (
    TMModel,
    parse_choices,
    parse_sources,
    source_params,
    sources_for,
)


def _source(name, weight=1.0, targets=('af', 'de'), source_lang='en', **extra):
    return dict(
        {
            'name': name,
            'title': name.title(),
            'licence': 'GPL-2.0-or-later',
            'weight': weight,
            'sourceLanguage': source_lang,
            'targetLanguages': list(targets),
        },
        **extra,
    )


SOURCES = [
    _source('gnome-48'),
    _source('gnome-49'),
    _source('kde'),
    _source('hosted-alpha', 0.8),
    _source('hosted-beta', 0.8),
]


# parse_sources() #


def test_parse_sources_keeps_what_the_dialog_and_lookups_need():
    reply = {
        'lang': 'af',
        'dir': 'ltr',
        'sources': [
            {
                'name': 'gnome-49',
                'title': 'GNOME',
                'version': '49',
                'displayTitle': 'GNOME 49',
                'weight': 1.0,
                'licence': 'GPL-2.0-or-later',
                'sourceLanguage': 'en',
                'targetLanguages': ['af', 'pt_BR'],
                'units': 1000,
                'imported': '2026-11-01T10:30:47Z',
            }
        ],
    }

    assert parse_sources(reply) == [
        {
            'name': 'gnome-49',
            'title': 'GNOME 49',
            'licence': 'GPL-2.0-or-later',
            'weight': 1.0,
            'sourceLanguage': 'en',
            'targetLanguages': ['af', 'pt_br'],
        }
    ]


def test_parse_sources_fills_in_what_a_source_lacks():
    (source,) = parse_sources(
        {'sources': [{'name': 'x', 'licence': None, 'weight': 'heavy'}, {'name': 'a,b'}, 7, {}]}
    )

    assert source == {
        'name': 'x',
        'title': 'x',
        'licence': '',
        'weight': 1.0,
        'sourceLanguage': '',
        'targetLanguages': [],
    }


@pytest.mark.parametrize('reply', [None, [], {'sources': 'none'}, {'error': 'no'}])
def test_parse_sources_of_another_reply_is_none(reply):
    assert parse_sources(reply) is None


# sources_for() #


def test_sources_for_lists_the_sources_with_the_language_pair():
    sources = [
        _source('both', targets=['af']),
        _source('german', targets=['de']),
        _source('french-source', targets=['af'], source_lang='fr'),
    ]

    assert [s['name'] for s in sources_for(sources, 'en', 'af')] == ['both']


def test_sources_for_a_regional_target_falls_back_to_its_language():
    sources = [_source('pt', targets=['pt']), _source('other', targets=['af'])]

    assert [s['name'] for s in sources_for(sources, 'en', 'pt_BR')] == ['pt']
    assert [
        s['name'] for s in sources_for(sources + [_source('br', targets=['pt_br'])], 'en', 'pt-BR')
    ] == ['br']


def test_sources_for_a_target_no_source_lists_shows_every_source_of_the_source_language():
    assert len(sources_for(SOURCES, 'en', 'zu')) == len(SOURCES)


# source_params() #


@pytest.mark.parametrize(
    'excluded, expected',
    [
        (set(), {}),
        ({'kde'}, {'exclude': 'kde'}),
        # Every known source of a family: the family's glob.
        ({'hosted-alpha', 'hosted-beta'}, {'exclude': 'hosted-*'}),
        ({'hosted-alpha', 'kde'}, {'exclude': 'hosted-alpha,kde'}),
        ({s['name'] for s in SOURCES}, {'exclude': '*'}),
    ],
)
def test_source_params_leaves_out_the_excluded_sources(excluded, expected):
    assert source_params(excluded, {}, SOURCES) == expected


def test_source_params_without_the_source_list_names_each_source():
    assert source_params({'hosted-alpha', 'hosted-beta'}, {'kde': 2}, ()) == {
        'exclude': 'hosted-alpha,hosted-beta',
        'weight': 'kde:2',
    }


def test_source_params_matches_a_name_with_glob_characters_only_itself():
    assert source_params({'odd*[name]?'}, {}, ()) == {'exclude': 'odd[*][[]name][?]'}


def test_source_params_sends_a_long_list_as_the_shorter_only(monkeypatch):
    monkeypatch.setattr(amagama, 'LONG_EXCLUDE', 10)

    params = source_params({'gnome-48', 'kde', 'hosted-alpha'}, {}, SOURCES)

    assert params == {'only': 'gnome-49,hosted-beta'}


def test_source_params_sends_only_the_weights_that_differ_from_the_servers():
    weights = {'gnome-49': 1.0, 'kde': 1.5, 'hosted-alpha': 0.8, 'hosted-beta': 2, 'gone': 0}

    params = source_params({'kde'}, weights, SOURCES)

    assert params == {'exclude': 'kde', 'weight': 'gone:0,hosted-beta:2'}


# parse_choices() #


def test_parse_choices_reads_the_config_and_skips_what_amagama_would_refuse():
    config = {'exclude': 'kde, hosted-*,,', 'weights': 'kde:0.5, a:b:2,bad:x,big:11,:3,low:0'}

    assert parse_choices(config) == ({'kde', 'hosted-*'}, {'kde': 0.5, 'a:b': 2.0, 'low': 0.0})


def test_parse_choices_of_an_empty_config():
    assert parse_choices({}) == (set(), {})


# The model #


class _Request:
    def __init__(self):
        self.handlers = {}

    def connect(self, signal, handler, *args):
        self.handlers[signal] = lambda *a: handler(*a, *args)

    def emit(self, signal, *args):
        self.handlers[signal](self, *args)


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path))
    return tmp_path


def _amagama(monkeypatch, url='https://example.com/api/v1/', **config):
    monkeypatch.setattr(
        TMModel, 'load_config', lambda self: setattr(self, 'config', dict(config, url=url))
    )
    model = TMModel('amagama', fake_controller())
    model.requests = []

    def get_sources(lang):
        request = _Request()
        model.requests.append((lang, request))
        return request

    model.tmclient.get_sources = get_sources
    return model


def _fetch(model):
    results = []
    model.fetch_sources(results.append, lambda: results.append('failed'))
    return results, model.requests[-1][1]


def test_amagama_can_be_configured(state_dir, monkeypatch):
    model = _amagama(monkeypatch)

    assert model.configure_func == model.configure


def test_init_reads_the_choices(state_dir, monkeypatch):
    model = _amagama(monkeypatch, exclude='kde', weights='gnome-49:2')

    assert model.excluded == {'kde'}
    assert model.weights == {'gnome-49': 2.0}
    assert model._params() == {'style': 'standard', 'exclude': 'kde', 'weight': 'gnome-49:2'}


def test_fetched_sources_are_kept_for_the_next_start(state_dir, monkeypatch):
    model = _amagama(monkeypatch)
    results, request = _fetch(model)

    request.emit('http-success', json.dumps({'sources': SOURCES}).encode('utf-8'))

    assert results == [parse_sources({'sources': SOURCES})]
    assert model.sources == results[0]
    assert _amagama(monkeypatch).sources == results[0]
    # Another server's list is no use.
    assert _amagama(monkeypatch, url='https://elsewhere.example/api/v1/').sources is None


def test_sources_are_asked_for_in_the_ui_language(state_dir, monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'pt_BR')
    model = _amagama(monkeypatch)

    _fetch(model)

    assert model.requests[0][0] == 'pt_BR'


@pytest.mark.parametrize(
    'signal, args',
    [
        ('http-success', (b'<html>Bad gateway</html>',)),
        ('http-success', (b'{"error": "no sources"}',)),
        ('http-client-error', (404,)),
        ('http-server-error', (502,)),
        ('http-failed', ('Could not resolve host',)),
    ],
)
def test_a_failed_fetch_keeps_the_sources_known(state_dir, monkeypatch, signal, args):
    model = _amagama(monkeypatch)
    model.sources = known = parse_sources({'sources': SOURCES})
    results, request = _fetch(model)

    request.emit(signal, *args)

    assert results == ['failed']
    assert model.sources is known


def test_a_fetch_the_client_cannot_queue_fails(state_dir, monkeypatch):
    model = _amagama(monkeypatch)
    model.tmclient.get_sources = lambda lang: None
    results = []

    model.fetch_sources(results.append, lambda: results.append('failed'))

    assert results == ['failed']


def test_a_cache_that_cannot_be_written_is_left(state_dir, monkeypatch):
    model = _amagama(monkeypatch)
    monkeypatch.setattr(model, '_cache_path', lambda: str(state_dir / 'missing' / 'cache.json'))
    results, request = _fetch(model)

    request.emit('http-success', json.dumps({'sources': SOURCES}).encode('utf-8'))

    assert results == [model.sources]


def test_an_unreadable_cache_is_no_sources(state_dir, monkeypatch):
    (state_dir / amagama.SOURCES_CACHE).write_text('{not json', encoding='utf-8')

    assert _amagama(monkeypatch).sources is None


def test_set_choices_applies_to_the_next_lookup_and_drops_earlier_answers(state_dir, monkeypatch):
    model = _amagama(monkeypatch)
    model.cache = {'Open': [{'source': 'Open', 'target': 'Oop'}]}
    model._pending = {'Save': object()}
    key = model._key()

    model.set_choices({'kde', 'hosted-alpha'}, {'gnome-49': 1.5})

    assert model.config['exclude'] == 'hosted-alpha,kde'
    assert model.config['weights'] == 'gnome-49:1.5'
    assert model.cache == {} and model._pending == {}
    assert model._key() != key
    assert model._params()['exclude'] == 'hosted-alpha,kde'


def test_set_choices_unchanged_keeps_the_answers(state_dir, monkeypatch):
    model = _amagama(monkeypatch, exclude='kde')
    model.cache = {'Open': None}

    model.set_choices({'kde'}, {})

    assert model.cache == {'Open': None}


def test_choices_go_with_a_v2_lookup(state_dir, monkeypatch):
    model = _amagama(monkeypatch, exclude='kde')
    model.source_lang, model.target_lang, model.checker = 'en', 'af', None
    sent = []
    model.tmclient = SimpleNamespace(translate_abstract=lambda *args: sent.append(args[-1]) or None)

    model._send('Open')

    assert sent[0]['exclude'] == 'kde'


def test_model_without_init_sends_no_choices():
    model = TMModel.__new__(TMModel)
    GObject.GObject.__init__(model)
    model.checker = 'gnome'

    assert model._params() == {'style': 'gnome'}
