#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
from types import SimpleNamespace

from gi.repository import GObject

from virtaal.plugins.tm._testhelpers import FakeHTTPClient, fake_controller
from virtaal.plugins.tm.models import google_translate
from virtaal.plugins.tm.models.google_translate import TMModel


def _model():
    model = TMModel.__new__(TMModel)
    GObject.GObject.__init__(model)
    model.source_lang = 'en'
    model.target_lang = 'af'
    model.cache = {}
    model._languages = {'en', 'af'}
    model.config = {'api_key': 'test-key'}
    return model


# __init__(): with an API key configured, the languages-list request
# must actually build from languages_url (url_getlanguages doesn't
# exist - a plain AttributeError on every real launch with a key set).


def test_init_builds_the_languages_request_from_languages_url(monkeypatch):
    monkeypatch.setattr(google_translate, 'HTTPClient', FakeHTTPClient)
    monkeypatch.setattr(
        TMModel, 'load_config', lambda self: setattr(self, 'config', {'api_key': 'test-key'})
    )

    model = TMModel('google_translate', fake_controller())

    assert len(model.client.added) == 1
    assert model.client.added[0].url == model.languages_url % {'key': 'test-key'}


# _disable_all()


def test_disable_all_clears_the_language_set():
    model = _model()

    model._disable_all('some reason')

    assert model._languages == set()


# query()


def test_query_truncates_a_source_string_that_exceeds_googles_url_limit():
    # Google's Terms of Service caps the whole URL at 2000 characters.
    from urllib.parse import unquote_plus

    added = []
    model = _model()
    model.client = SimpleNamespace(add=lambda req: added.append(req))
    long_source = 'x' * 3000

    model.query(None, SimpleNamespace(source=long_source))

    query_param = added[0].url.split('q=')[1].split('&')[0]
    assert len(unquote_plus(query_param)) == 2000 - len(model.translate_url)


def test_query_translates_language_codes_before_checking_support():
    model = _model()
    model._languages = {'iw', 'af'}  # Google's own code for Hebrew
    model.source_lang = 'he'
    model.client = SimpleNamespace(add=lambda req: None)

    model.query(None, SimpleNamespace(source='hello'))  # must not raise (he -> iw is supported)


def test_query_replaces_underscore_with_hyphen_in_region_codes():
    model = _model()
    model._languages = {'en', 'pt-BR'}
    model.target_lang = 'pt_BR'
    added = []
    model.client = SimpleNamespace(add=lambda req: added.append(req))

    model.query(None, SimpleNamespace(source='hello'))

    assert len(added) == 1
    assert 'target=pt-BR' in added[0].url


def test_query_does_nothing_for_an_unsupported_language_pair():
    model = _model()
    model._languages = {'fr', 'de'}
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


def test_query_sends_a_translate_request_for_an_uncached_pair():
    added = []
    model = _model()
    model.client = SimpleNamespace(add=lambda req: added.append(req))

    model.query(None, SimpleNamespace(source='hello'))

    assert len(added) == 1
    assert 'key=test-key' in added[0].url
    assert 'source=en' in added[0].url
    assert 'target=af' in added[0].url


# A query made before the language list arrives waits for it (#4677).


def _model_fetching_languages(monkeypatch):
    monkeypatch.setattr(google_translate, 'HTTPClient', FakeHTTPClient)
    monkeypatch.setattr(
        TMModel, 'load_config', lambda self: setattr(self, 'config', {'api_key': 'test-key'})
    )
    return TMModel('google_translate', fake_controller())


def _languages_reply(*codes):
    return json.dumps({'data': {'languages': [{'language': c} for c in codes]}})


def test_query_before_the_languages_arrive_runs_once_they_do(monkeypatch):
    model = _model_fetching_languages(monkeypatch)
    langreq = model.client.added[0]

    model.query(None, SimpleNamespace(source='hello'))
    assert model.client.added == [langreq]

    langreq.emit('http-success', _languages_reply('en', 'af'))

    assert len(model.client.added) == 2
    assert 'q=hello' in model.client.added[1].url


def test_a_failed_languages_request_stops_holding_queries(monkeypatch):
    model = _model_fetching_languages(monkeypatch)

    model.query(None, SimpleNamespace(source='hello'))
    model.client.added[0].emit('http-client-error', 403)

    assert model.languages_known
    assert len(model.client.added) == 1


def test_disable_all_without_an_api_key_leaves_nothing_held(monkeypatch):
    monkeypatch.setattr(
        TMModel, 'load_config', lambda self: setattr(self, 'config', {'api_key': ''})
    )

    model = TMModel('google_translate', fake_controller())

    assert model.languages_known


# got_translation()


def test_got_translation_emits_a_match_on_success():
    model = _model()
    emitted = []
    model.connect('match-found', lambda m, q, r: emitted.append((q, r)))
    response = json.dumps({'data': {'translations': [{'translatedText': 'Hallo'}]}})

    model.got_translation(response, 'hello')

    assert model.cache['hello'] == [{'source': 'hello', 'target': 'Hallo', 'tmsource': 'Google'}]
    assert emitted == [('hello', model.cache['hello'])]


def test_got_translation_unescapes_html_entities():
    model = _model()
    response = json.dumps({'data': {'translations': [{'translatedText': 'Tom &amp; Jerry'}]}})

    model.got_translation(response, 'Tom & Jerry')

    assert model.cache['Tom & Jerry'][0]['target'] == 'Tom & Jerry'


def test_got_translation_disables_all_queries_on_a_malformed_response():
    model = _model()

    model.got_translation('not json at all', 'hello')

    assert model._languages == set()
    assert model.cache == {}


def test_got_translation_disables_all_queries_on_an_unexpected_shape():
    model = _model()

    model.got_translation(json.dumps({'unexpected': 'shape'}), 'hello')

    assert model._languages == set()


# got_languages()


def test_got_languages_populates_the_language_set():
    model = _model()
    model._languages = set()
    response = json.dumps({'data': {'languages': [{'language': 'en'}, {'language': 'af'}]}})

    model.got_languages(response)

    assert model._languages == {'en', 'af'}


def test_got_languages_disables_all_on_a_malformed_response():
    model = _model()

    model.got_languages('not json at all')

    assert model._languages == set()


# got_error()


def test_got_error_disables_all_queries():
    model = _model()

    model.got_error('500 server error', 'hello')

    assert model._languages == set()


# Setting the API key from Configure… (#1519).


def _model_with_key(monkeypatch, tmp_path, key):
    monkeypatch.setattr(google_translate, 'HTTPClient', FakeHTTPClient)
    monkeypatch.setattr('virtaal.common.pan_app.get_config_dir', lambda: str(tmp_path))
    if key:
        (tmp_path / 'tm.ini').write_text('[google_translate]\napi_key = %s\n' % key)
    return TMModel('google_translate', fake_controller())


def test_the_api_key_can_be_configured(monkeypatch, tmp_path):
    model = _model_with_key(monkeypatch, tmp_path, '')

    assert model.configure_func == model.configure
    assert [field.key for field in model.settings] == ['api_key']


def test_a_key_set_from_the_dialog_turns_the_service_on(monkeypatch, tmp_path):
    model = _model_with_key(monkeypatch, tmp_path, '')

    model.update_settings({'api_key': 'new-key'})

    assert 'api_key = new-key' in (tmp_path / 'tm.ini').read_text()
    assert not model.languages_known
    assert [r.url for r in model.client.added] == [model.languages_url % {'key': 'new-key'}]
    model.client.added[0].emit('http-success', _languages_reply('en', 'af'))
    model.query(None, SimpleNamespace(source='hello'))
    assert 'key=new-key' in model.client.added[1].url


def test_clearing_the_key_turns_the_service_off(monkeypatch, tmp_path):
    model = _model_with_key(monkeypatch, tmp_path, 'old-key')
    model.client.added[0].emit('http-success', _languages_reply('en', 'af'))
    sent = len(model.client.added)

    model.update_settings({'api_key': ''})
    model.query(None, SimpleNamespace(source='hello'))

    assert model.languages_known
    assert len(model.client.added) == sent
    assert 'api_key = \n' in (tmp_path / 'tm.ini').read_text()


def test_a_new_key_forgets_the_old_keys_answers(monkeypatch, tmp_path):
    model = _model_with_key(monkeypatch, tmp_path, 'old-key')
    old_langreq = model.client.added[0]
    model.cache = {'hello': [{'target': 'Hallo'}]}

    model.update_settings({'api_key': 'new-key'})
    old_langreq.emit('http-client-error', 400)
    model.client.added[1].emit('http-success', _languages_reply('en', 'af'))

    assert model.cache == {}
    assert model._languages == {'en', 'af'}


def test_the_same_key_again_sends_nothing(monkeypatch, tmp_path):
    model = _model_with_key(monkeypatch, tmp_path, 'old-key')

    model.update_settings({'api_key': 'old-key'})

    assert len(model.client.added) == 1


def test_the_key_is_not_logged(monkeypatch, tmp_path, caplog):
    caplog.set_level('DEBUG')
    model = _model_with_key(monkeypatch, tmp_path, '')

    model.update_settings({'api_key': 'secret-key'})
    model.client.added[0].emit('http-client-error', 403)

    assert 'secret-key' not in caplog.text
