#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import logging
from types import SimpleNamespace

import pytest
import yaml
from gi.repository import GObject

from virtaal.plugins.tm._testhelpers import FakeHTTPClient, fake_controller
from virtaal.plugins.tm.models import apertium
from virtaal.plugins.tm.models.apertium import TMModel


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    """A config dir of the test's own: no manifest or saved pair list
    unless the test writes one (#4678)."""
    monkeypatch.setattr(apertium, '_config_dir', lambda: str(tmp_path))
    return tmp_path


def _model():
    model = TMModel.__new__(TMModel)
    GObject.GObject.__init__(model)
    model.source_lang = 'en'
    model.target_lang = 'af'
    model.cache = {}
    model.language_pairs = [('en', 'af')]
    model.url_translate = 'https://www.apertium.org/apy/translate'
    return model


# __init__(): requests the server's language pairs up front and wires
# got_language_pairs() as the callback.


def test_init_requests_language_pairs_from_the_apy_endpoint(monkeypatch):
    monkeypatch.setattr(apertium, 'HTTPClient', FakeHTTPClient)
    monkeypatch.setattr(TMModel, 'load_config', lambda self: setattr(self, 'config', {}))

    model = TMModel('apertium', fake_controller())

    assert model.url_getpairs == 'https://www.apertium.org/apy/listPairs'
    assert model.url_translate == 'https://www.apertium.org/apy/translate'
    assert len(model.client.added) == 1
    assert model.client.added[0].url == model.url_getpairs


def test_init_wires_the_response_to_got_language_pairs(monkeypatch):
    monkeypatch.setattr(apertium, 'HTTPClient', FakeHTTPClient)
    monkeypatch.setattr(TMModel, 'load_config', lambda self: setattr(self, 'config', {}))
    received = []
    monkeypatch.setattr(
        TMModel, 'got_language_pairs', lambda self, response: received.append(response)
    )

    model = TMModel('apertium', fake_controller())
    model.client.added[0].emit('http-success', 'the response body')

    assert received == ['the response body']


# got_language_pairs()


def test_got_language_pairs_stores_the_pairs_on_success():
    model = _model()
    model.language_pairs = []
    response = json.dumps(
        {
            'responseStatus': 200,
            'responseData': [
                {'sourceLanguage': 'en', 'targetLanguage': 'af'},
                {'sourceLanguage': 'af', 'targetLanguage': 'en'},
            ],
        }
    )

    model.got_language_pairs(response)

    assert model.language_pairs == [('en', 'af'), ('af', 'en')]


def test_got_language_pairs_ignores_a_non_200_response():
    model = _model()
    model.language_pairs = []
    response = json.dumps({'responseStatus': 500, 'responseDetails': 'boom'})

    model.got_language_pairs(response)

    assert model.language_pairs == []


# query()


def test_query_does_nothing_for_an_unsupported_language_pair():
    model = _model()
    model.language_pairs = [('fr', 'de')]
    model.client = SimpleNamespace(
        add=lambda req: (_ for _ in ()).throw(
            AssertionError('should not query an unsupported pair')
        )
    )

    model.query(None, SimpleNamespace(source='hello'))  # must not raise


def test_query_emits_a_cached_match():
    model = _model()
    model.cache = {'hello': [{'target': 'Hallo'}]}
    model.client = SimpleNamespace(
        add=lambda req: (_ for _ in ()).throw(AssertionError('should use the cache'))
    )
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))

    model.query(None, SimpleNamespace(source='hello'))

    assert emitted == [('hello', [{'target': 'Hallo'}])]


def test_query_uses_apy_language_codes_for_a_supported_pair():
    model = _model()
    model.source_lang = 'en'
    model.target_lang = 'ca'
    model.language_pairs = [('eng', 'cat')]
    added = []
    model.client = SimpleNamespace(add=lambda req: added.append(req))

    model.query(None, SimpleNamespace(source='The house is big.'))

    assert len(added) == 1
    assert 'langpair=eng%7Ccat' in added[0].url


def test_query_sends_a_translate_request_for_an_uncached_pair():
    added = []
    model = _model()
    model.client = SimpleNamespace(add=lambda req: added.append(req))

    model.query(None, SimpleNamespace(source='hello'))

    assert len(added) == 1
    assert 'langpair=en%7Caf' in added[0].url


# A query made before the pair list arrives waits for it (#4677).


def _model_fetching_pairs(monkeypatch):
    monkeypatch.setattr(apertium, 'HTTPClient', FakeHTTPClient)
    monkeypatch.setattr(TMModel, 'load_config', lambda self: setattr(self, 'config', {}))
    return TMModel('apertium', fake_controller())


def _pairs_reply(*pairs):
    return json.dumps(
        {
            'responseStatus': 200,
            'responseData': [{'sourceLanguage': s, 'targetLanguage': t} for s, t in pairs],
        }
    )


def test_query_before_the_pairs_arrive_runs_once_they_do(monkeypatch):
    model = _model_fetching_pairs(monkeypatch)
    langreq = model.client.added[0]

    model.query(None, SimpleNamespace(source='hello'))
    assert model.client.added == [langreq]

    langreq.emit('http-success', _pairs_reply(('en', 'af')))

    assert len(model.client.added) == 2
    assert 'q=hello' in model.client.added[1].url


def test_only_the_last_query_before_the_pairs_arrive_runs(monkeypatch):
    model = _model_fetching_pairs(monkeypatch)

    model.query(None, SimpleNamespace(source='first'))
    model.query(None, SimpleNamespace(source='second'))
    model.client.added[0].emit('http-success', _pairs_reply(('en', 'af')))

    assert [r.url for r in model.client.added[1:]] == [
        model.url_translate + '?q=second&langpair=en%7Caf&markUnknown=no&format=html'
    ]


def test_query_held_for_an_unlisted_pair_sends_nothing(monkeypatch):
    model = _model_fetching_pairs(monkeypatch)

    model.query(None, SimpleNamespace(source='hello'))
    model.client.added[0].emit('http-success', _pairs_reply(('fr', 'de')))

    assert len(model.client.added) == 1


def test_queries_after_the_pairs_arrive_are_not_held(monkeypatch):
    model = _model_fetching_pairs(monkeypatch)
    model.client.added[0].emit('http-success', _pairs_reply(('en', 'af')))

    model.query(None, SimpleNamespace(source='hello'))

    assert len(model.client.added) == 2


def test_an_unreadable_pair_list_stops_holding_queries(monkeypatch):
    model = _model_fetching_pairs(monkeypatch)

    model.client.added[0].emit('http-success', 'not json')

    assert model.languages_known


def test_a_failed_pair_list_request_stops_holding_queries(monkeypatch):
    model = _model_fetching_pairs(monkeypatch)

    model.client.added[0].emit('http-server-error', 503)

    assert model.languages_known


# got_translation()


def test_got_translation_emits_a_match_on_success():
    model = _model()
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))
    response = json.dumps({'responseStatus': 200, 'responseData': {'translatedText': 'Hallo'}})

    model.got_translation(response, 'hello')

    assert model.cache['hello'] == [{'source': 'hello', 'target': 'Hallo', 'tmsource': 'Apertium'}]
    assert emitted == [('hello', model.cache['hello'])]


def test_got_translation_strips_a_trailing_newline_the_query_did_not_have():
    model = _model()
    response = json.dumps({'responseStatus': 200, 'responseData': {'translatedText': 'Hallo\n'}})

    model.got_translation(response, 'hello')

    assert model.cache['hello'][0]['target'] == 'Hallo'


def test_got_translation_keeps_a_trailing_newline_the_query_also_had():
    model = _model()
    response = json.dumps({'responseStatus': 200, 'responseData': {'translatedText': 'Hallo\n'}})

    model.got_translation(response, 'hello\n')

    assert model.cache['hello\n'][0]['target'] == 'Hallo\n'


def test_got_translation_unescapes_html_entities():
    model = _model()
    response = json.dumps(
        {'responseStatus': 200, 'responseData': {'translatedText': 'Tom &amp; Jerry'}}
    )

    model.got_translation(response, 'Tom & Jerry')

    assert model.cache['Tom & Jerry'][0]['target'] == 'Tom & Jerry'


def test_got_translation_ignores_a_non_200_response(caplog):
    # Regression: the debug log call here passed (query_str, details) as
    # a single tuple instead of two separate args, so actually
    # formatting the message raised TypeError instead of just logging.
    model = _model()
    response = json.dumps({'responseStatus': 500, 'responseDetails': 'boom'})

    with caplog.at_level(logging.DEBUG):
        model.got_translation(response, 'hello')  # must not raise

    assert model.cache == {}
    assert "Failed to translate 'hello':\nboom" in caplog.text


_BAD_REPLIES = [
    '<html>502</html>',
    '',
    '{"responseStatus": 200, "responseData": {"transl',
    'null',
    '[]',
    '{"responseData": {}}',
    '{"responseStatus": 200, "responseData": {"translatedText": null}}',
]


@pytest.mark.parametrize('reply', _BAD_REPLIES)
def test_got_translation_ignores_a_reply_it_cannot_read(reply):
    # #4438: these showed the crash dialog.
    model = _model()
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))

    model.got_translation(reply, 'hello')

    assert emitted == []
    assert 'hello' not in model.cache


@pytest.mark.parametrize(
    'reply', _BAD_REPLIES + ['{"responseStatus": 200, "responseData": [{"x": 1}]}']
)
def test_got_language_pairs_ignores_a_reply_it_cannot_read(reply):
    model = _model()
    model.language_pairs = []

    model.got_language_pairs(reply)

    assert model.language_pairs == []


# apy_codes() / _apy_pair(): APY lists ISO 639-3 codes, Virtaal uses
# two-letter codes where they exist (#4664).


@pytest.mark.parametrize(
    'code, expected',
    [
        ('ca', ['ca', 'cat']),
        ('ca@valencia', ['ca@valencia', 'cat_valencia', 'cat']),
        ('pt_BR', ['pt_BR', 'por_BR', 'por']),
        ('en_GB', ['en_GB', 'eng_GB', 'eng']),
        ('sr', ['sr', 'hbs_SR', 'hbs']),
        ('ms', ['ms', 'zlm']),
        ('sc', ['sc', 'srd']),
        ('crh', ['crh']),
    ],
)
def test_apy_codes_most_specific_first(code, expected):
    assert apertium.apy_codes(code) == expected


@pytest.mark.parametrize(
    'source, target, pairs, langpair',
    [
        ('en', 'ca@valencia', [('eng', 'cat'), ('eng', 'cat_valencia')], 'eng|cat_valencia'),
        ('en_GB', 'ca@valencia', [('eng', 'cat')], 'eng|cat'),
        ('es', 'pt_BR', [('spa', 'por'), ('spa', 'por_BR')], 'spa|por_BR'),
    ],
)
def test_query_sends_the_most_specific_listed_apy_pair(source, target, pairs, langpair):
    model = _model()
    model.source_lang, model.target_lang = source, target
    model.language_pairs = pairs
    model.client = FakeHTTPClient()

    model.query(None, SimpleNamespace(source='The house is big.'))

    assert len(model.client.added) == 1
    assert 'langpair=' + langpair.replace('|', '%7C') in model.client.added[0].url


def test_query_sends_nothing_when_no_apy_pair_matches():
    model = _model()
    model.source_lang, model.target_lang = 'en', 'zu'
    model.language_pairs = [('eng', 'cat')]
    model.client = FakeHTTPClient()

    model.query(None, SimpleNamespace(source='The house is big.'))

    assert model.client.added == []


# The pair list at start: the cached asset manifest's, else the saved
# copy of the last live reply, else a live request (#4678).


def _write_manifest(config_dir, pairs):
    manifest = {'dictionary': {}, 'apertium': {'pairs': [list(p) for p in pairs]}}
    (config_dir / 'asset-manifest.yaml').write_text(yaml.safe_dump(manifest), encoding='utf-8')


def _write_saved(config_dir, pairs):
    (config_dir / apertium.PAIRS_FILENAME).write_text(
        json.dumps({'pairs': [list(p) for p in pairs]}), encoding='utf-8'
    )


def _saved(config_dir):
    return json.loads((config_dir / apertium.PAIRS_FILENAME).read_text(encoding='utf-8'))


def test_start_uses_the_manifest_pairs_without_a_request(monkeypatch, config_dir):
    _write_manifest(config_dir, [('eng', 'afr')])
    _write_saved(config_dir, [('eng', 'spa')])

    model = _model_fetching_pairs(monkeypatch)

    assert model.client.added == []
    assert model.languages_known
    assert model.language_pairs == [('eng', 'afr')]


def test_start_uses_the_saved_pairs_without_a_manifest(monkeypatch, config_dir):
    _write_saved(config_dir, [('eng', 'afr')])

    model = _model_fetching_pairs(monkeypatch)

    assert model.client.added == []
    assert model.languages_known
    assert model.language_pairs == [('eng', 'afr')]


def test_start_uses_the_saved_pairs_when_the_manifest_has_none(monkeypatch, config_dir):
    (config_dir / 'asset-manifest.yaml').write_text(
        yaml.safe_dump({'dictionary': {}}), encoding='utf-8'
    )
    _write_saved(config_dir, [('eng', 'afr')])

    model = _model_fetching_pairs(monkeypatch)

    assert model.language_pairs == [('eng', 'afr')]


def test_start_asks_apertium_with_neither(monkeypatch):
    model = _model_fetching_pairs(monkeypatch)

    assert [r.url for r in model.client.added] == [model.url_getpairs]
    assert not model.languages_known


_BAD_MANIFESTS = [
    'not: [valid',
    '- a list, not a mapping',
    'apertium: [[eng, afr]]',
    'apertium: {pairs: eng-afr}',
    'apertium: {pairs: [[eng]]}',
    'apertium: {pairs: [[eng, 3]]}',
    'apertium: {pairs: []}',
]


@pytest.mark.parametrize('text', _BAD_MANIFESTS)
def test_an_unreadable_manifest_falls_back_to_the_saved_pairs(monkeypatch, config_dir, text):
    (config_dir / 'asset-manifest.yaml').write_text(text, encoding='utf-8')
    _write_saved(config_dir, [('eng', 'afr')])

    model = _model_fetching_pairs(monkeypatch)

    assert model.language_pairs == [('eng', 'afr')]


@pytest.mark.parametrize(
    'text',
    ['', '{"pairs": [["eng"', '[]', '{"pairs": "eng-afr"}', '{"pairs": [[1, 2]]}', b'\xff\xfe'],
)
def test_an_unreadable_saved_list_falls_back_to_asking_apertium(monkeypatch, config_dir, text):
    path = config_dir / apertium.PAIRS_FILENAME
    path.write_bytes(text if isinstance(text, bytes) else text.encode('utf-8'))

    model = _model_fetching_pairs(monkeypatch)

    assert [r.url for r in model.client.added] == [model.url_getpairs]


def test_a_live_reply_updates_the_saved_pairs(monkeypatch, config_dir):
    model = _model_fetching_pairs(monkeypatch)

    model.client.added[0].emit('http-success', _pairs_reply(('eng', 'spa'), ('afr', 'nld')))

    assert _saved(config_dir) == {'pairs': [['afr', 'nld'], ['eng', 'spa']]}


@pytest.mark.parametrize('reply', ['not json', json.dumps({'responseStatus': 500})])
def test_a_failed_live_reply_keeps_the_saved_pairs(monkeypatch, config_dir, reply):
    _write_saved(config_dir, [('eng', 'afr')])
    model = _model_fetching_pairs(monkeypatch)
    model.query(None, SimpleNamespace(source='hello'))

    model.client.added[-1].emit('http-success', reply)

    assert model.language_pairs == [('eng', 'afr')]
    assert _saved(config_dir) == {'pairs': [['eng', 'afr']]}


def test_an_unwritable_config_dir_still_uses_the_live_pairs(monkeypatch, config_dir):
    monkeypatch.setattr(apertium, '_config_dir', lambda: str(config_dir / 'missing' / 'dir'))
    model = _model_fetching_pairs(monkeypatch)
    (config_dir / 'missing').write_text('a file, not a dir', encoding='utf-8')

    model.client.added[0].emit('http-success', _pairs_reply(('en', 'af')))

    assert model.language_pairs == [('en', 'af')]


def test_the_first_query_on_a_start_list_translates_then_refreshes(monkeypatch, config_dir):
    _write_saved(config_dir, [('en', 'af')])
    model = _model_fetching_pairs(monkeypatch)

    model.query(None, SimpleNamespace(source='hello'))
    model.query(None, SimpleNamespace(source='again'))

    urls = [r.url for r in model.client.added]
    assert 'q=hello' in urls[0]
    assert urls[1] == model.url_getpairs
    assert 'q=again' in urls[2]
    assert len(urls) == 3


def test_the_refresh_replaces_the_start_list_and_saves_it(monkeypatch, config_dir):
    _write_manifest(config_dir, [('en', 'af')])
    model = _model_fetching_pairs(monkeypatch)
    model.query(None, SimpleNamespace(source='hello'))

    model.client.added[1].emit('http-success', _pairs_reply(('en', 'af'), ('en', 'nl')))

    assert model.language_pairs == [('en', 'af'), ('en', 'nl')]
    assert _saved(config_dir) == {'pairs': [['en', 'af'], ['en', 'nl']]}


def test_a_pair_missing_from_the_start_list_waits_for_the_live_list(monkeypatch, config_dir):
    _write_saved(config_dir, [('en', 'nl')])
    model = _model_fetching_pairs(monkeypatch)

    model.query(None, SimpleNamespace(source='hello'))
    assert [r.url for r in model.client.added] == [model.url_getpairs]
    assert not model.languages_known  # prefetch holds off meanwhile

    model.client.added[0].emit('http-success', _pairs_reply(('en', 'nl'), ('en', 'af')))

    assert len(model.client.added) == 2
    assert 'q=hello' in model.client.added[1].url


def test_a_pair_missing_from_the_live_list_too_sends_nothing(monkeypatch, config_dir):
    _write_saved(config_dir, [('en', 'nl')])
    model = _model_fetching_pairs(monkeypatch)
    model.query(None, SimpleNamespace(source='hello'))

    model.client.added[0].emit('http-server-error', 503)
    model.query(None, SimpleNamespace(source='again'))

    assert [r.url for r in model.client.added] == [model.url_getpairs]
    assert model.languages_known
