#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
from types import SimpleNamespace

import pytest

from virtaal.plugins.tm import keyedmt
from virtaal.plugins.tm._testhelpers import FakeHTTPClient, fake_controller
from virtaal.plugins.tm.models.deepl import TMModel, deepl_code

FREE_KEY = '279a2e9d-83b3-c416-7e2d-f721593e42a0:fx'
PRO_KEY = '279a2e9d-83b3-c416-7e2d-f721593e42a0'

# The shape of DeepL's GET /v3/languages?resource=translate_text reply.
LANGUAGES = [
    {'lang': 'de', 'usable_as_source': True, 'usable_as_target': True},
    {'lang': 'en', 'usable_as_source': True, 'usable_as_target': False},
    {'lang': 'en-GB', 'usable_as_source': False, 'usable_as_target': True},
    {'lang': 'en-US', 'usable_as_source': False, 'usable_as_target': True},
    {'lang': 'es', 'usable_as_source': True, 'usable_as_target': True},
    {'lang': 'es-419', 'usable_as_source': False, 'usable_as_target': True},
    {'lang': 'nb', 'usable_as_source': True, 'usable_as_target': True},
    {'lang': 'pt', 'usable_as_source': True, 'usable_as_target': False},
    {'lang': 'pt-BR', 'usable_as_source': False, 'usable_as_target': True},
    {'lang': 'pt-PT', 'usable_as_source': False, 'usable_as_target': True},
    {'lang': 'zh', 'usable_as_source': True, 'usable_as_target': False},
    {'lang': 'zh-Hans', 'usable_as_source': False, 'usable_as_target': True},
    {'lang': 'zh-Hant', 'usable_as_source': False, 'usable_as_target': True},
]
for _lang in LANGUAGES:
    _lang.update(name=_lang['lang'], status='stable', features={})


def _languages_reply(languages=LANGUAGES):
    return json.dumps(languages).encode('utf-8')


def _translation_reply(text):
    return json.dumps({'translations': [{'detected_source_language': 'EN', 'text': text}]}).encode(
        'utf-8'
    )


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(keyedmt.time, 'monotonic', lambda: now[0])
    return now


def _model(monkeypatch, tmp_path, key=FREE_KEY, source='en', target='de'):
    monkeypatch.setattr(keyedmt, 'HTTPClient', FakeHTTPClient)
    monkeypatch.setattr('virtaal.common.pan_app.get_config_dir', lambda: str(tmp_path))
    if key:
        (tmp_path / 'tm.ini').write_text('[deepl]\napi_key = %s\n' % key)
    return TMModel('deepl', fake_controller(source, target))


def _ready(monkeypatch, tmp_path, **kwargs):
    """A model whose language list arrived."""
    model = _model(monkeypatch, tmp_path, **kwargs)
    model.client.added[0].emit('http-success', _languages_reply())
    return model


def _unit(source='hello'):
    return SimpleNamespace(source=source)


def _query(model, source='hello'):
    """The requests a query for source sent."""
    before = len(model.client.added)
    model.query(None, _unit(source))
    return model.client.added[before:]


def _matches(model):
    found = []
    model.connect('match-found', lambda m, query, matches: found.append((query, matches)))
    return found


def _fail(request, status, retry_after=None):
    if retry_after is not None:
        request.result_headers.write(b'HTTP/2 %d\r\nRetry-After: %s\r\n' % (status, retry_after))
    request.emit('http-failed' if isinstance(status, str) else 'http-client-error', status)


# Without a key nothing is sent.


def test_without_a_key_nothing_is_sent(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, key='')

    model.query(None, _unit())

    assert model.languages_known
    assert getattr(model, 'client', None) is None
    assert model.has_cached('hello')


# Free and Pro keys use their own hosts; the key goes in a header.


def test_a_free_key_asks_the_free_host_for_its_languages(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, key=FREE_KEY)

    (request,) = model.client.added
    assert request.url == 'https://api-free.deepl.com/v3/languages?resource=translate_text'
    assert request.method == 'GET'
    assert request.headers == ['Authorization: DeepL-Auth-Key %s' % FREE_KEY]
    assert not model.languages_known


def test_a_pro_key_uses_the_pro_host(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path, key=PRO_KEY)

    (request,) = _query(model)

    assert model.client.added[0].url.startswith('https://api.deepl.com/v3/languages?')
    assert request.url == 'https://api.deepl.com/v2/translate'
    assert request.headers[0] == 'Authorization: DeepL-Auth-Key %s' % PRO_KEY


def test_a_query_posts_the_text_and_languages_as_json(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    (request,) = _query(model, 'Open file…')

    assert request.url == 'https://api-free.deepl.com/v2/translate'
    assert request.method == 'POST'
    assert request.headers == [
        'Authorization: DeepL-Auth-Key %s' % FREE_KEY,
        'Content-Type: application/json',
    ]
    assert json.loads(request.data.decode('utf-8')) == {
        'text': ['Open file…'],
        'source_lang': 'en',
        'target_lang': 'de',
        'preserve_formatting': True,
    }


def test_a_query_before_the_languages_arrive_runs_once_they_do(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path)

    assert _query(model) == []
    model.client.added[0].emit('http-success', _languages_reply())

    assert len(model.client.added) == 2
    assert model.client.added[1].url.endswith('/v2/translate')


# Replies


def test_a_translation_becomes_a_suggestion(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)
    (request,) = _query(model)

    request.emit('http-success', _translation_reply('Hallo'))

    match = {'source': 'hello', 'target': 'Hallo', 'tmsource': 'DeepL'}
    assert found == [('hello', [match])]
    assert model.cache['hello'] == 'Hallo'


def test_a_cached_string_is_not_sent_again(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    _query(model)[0].emit('http-success', _translation_reply('Hallo'))
    found = _matches(model)

    assert _query(model) == []
    assert found == [('hello', [{'source': 'hello', 'target': 'Hallo', 'tmsource': 'DeepL'}])]


def test_a_string_on_its_way_is_not_sent_twice(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    assert len(_query(model)) == 1
    assert _query(model) == []


def test_text_is_not_unescaped_as_html(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    found = _matches(model)

    _query(model, 'Tom & Jerry')[0].emit('http-success', _translation_reply('Tom &amp; Jerry'))

    assert found[0][1][0]['target'] == 'Tom &amp; Jerry'


@pytest.mark.parametrize(
    'reply', [b'not json', b'{}', b'{"translations": []}', b'{"translations": [{"text": ""}]}']
)
def test_an_unusable_reply_gives_no_suggestion_but_keeps_deepl_on(monkeypatch, tmp_path, reply):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)

    _query(model)[0].emit('http-success', reply)

    assert found == []
    assert 'hello' not in model.cache
    assert len(_query(model, 'other')) == 1


def test_an_answer_for_the_previous_languages_is_dropped(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)
    (request,) = _query(model)

    model._set_target_lang(None, 'nb')
    request.emit('http-success', _translation_reply('Hallo'))

    assert found == []
    assert model.cache == {}
    assert len(_query(model)) == 1


def test_an_unusable_language_list_turns_deepl_off(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path)

    model.client.added[0].emit('http-success', b'{"not": "a list"}')

    assert model.languages_known
    assert _query(model) == []


# Languages: Virtaal's codes to DeepL's.


@pytest.mark.parametrize(
    'code, expected',
    [
        ('de', 'de'),
        ('de_CH', 'de'),
        # DeepL translates into English, Portuguese and Chinese by variant only.
        ('en', 'en-US'),
        ('en_US', 'en-US'),
        ('en_GB', 'en-GB'),
        ('en_ZA', 'en-GB'),
        ('en_CA', 'en-US'),
        ('pt', 'pt-PT'),
        ('pt_PT', 'pt-PT'),
        ('pt_BR', 'pt-BR'),
        ('zh', 'zh-Hans'),
        ('zh_CN', 'zh-Hans'),
        ('zh_TW', 'zh-Hant'),
        ('zh_HK', 'zh-Hant'),
        ('es', 'es'),
        ('es_ES', 'es'),
        ('es_MX', 'es-419'),
        ('es_419', 'es-419'),
        ('no', 'nb'),
        ('nb_NO', 'nb'),
        ('af', None),
        ('nn', None),
        # A variant DeepL doesn't have is not the plain language.
        ('ca@valencia', None),
        ('de@test', None),
    ],
)
def test_target_codes(code, expected):
    targets = {lang['lang'].lower(): lang['lang'] for lang in LANGUAGES if lang['usable_as_target']}

    assert deepl_code(code, targets) == expected


@pytest.mark.parametrize(
    'code, expected',
    [
        ('en', 'en'),
        ('en_GB', 'en'),
        ('en_US', 'en'),
        ('pt_BR', 'pt'),
        ('zh_TW', 'zh'),
        ('zh_CN', 'zh'),
        ('es_MX', 'es'),
        ('no', 'nb'),
        ('af', None),
    ],
)
def test_source_codes(code, expected):
    sources = {lang['lang'].lower(): lang['lang'] for lang in LANGUAGES if lang['usable_as_source']}

    assert deepl_code(code, sources) == expected


def test_a_query_sends_deepls_variant_code(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path, source='en_GB', target='pt_BR')

    (request,) = _query(model)

    body = json.loads(request.data)
    assert (body['source_lang'], body['target_lang']) == ('en', 'pt-BR')


@pytest.mark.parametrize('source, target', [('en', 'af'), ('af', 'en'), ('en', 'en')])
def test_an_unsupported_pair_sends_nothing(monkeypatch, tmp_path, caplog, source, target):
    caplog.set_level('INFO')
    model = _ready(monkeypatch, tmp_path, source=source, target=target)
    found = _matches(model)

    assert _query(model) == []
    assert found == []
    assert caplog.text == ''


# Errors are quiet: a debug message, never one for the user.


def test_too_many_requests_waits_as_long_as_deepl_asks(monkeypatch, tmp_path, clock, caplog):
    caplog.set_level('INFO')
    model = _ready(monkeypatch, tmp_path)

    _fail(_query(model)[0], 429, b'30')

    assert _query(model, 'other') == []
    assert model.has_cached('other')
    clock[0] += 31
    assert len(_query(model, 'other')) == 1
    assert caplog.text == ''


def test_too_many_requests_without_retry_after_waits_a_minute(monkeypatch, tmp_path, clock):
    model = _ready(monkeypatch, tmp_path)

    _fail(_query(model)[0], 429)

    clock[0] += 59
    assert _query(model, 'other') == []
    clock[0] += 2
    assert len(_query(model, 'other')) == 1


@pytest.mark.parametrize('status', [500, 503, 504, 529, 'Connection timed out'])
def test_a_server_problem_or_no_reply_waits_too(monkeypatch, tmp_path, clock, status):
    model = _ready(monkeypatch, tmp_path)
    request = _query(model)[0]

    if isinstance(status, int) and status >= 500:
        request.result_headers.write(b'Retry-After: 5\r\n')
        request.emit('http-server-error', status)
    else:
        _fail(request, status)

    assert _query(model) == []
    clock[0] += 61
    assert len(_query(model)) == 1


@pytest.mark.parametrize('status', [456, 403])
def test_quota_used_up_or_a_refused_key_stops_deepl(monkeypatch, tmp_path, clock, status):
    model = _ready(monkeypatch, tmp_path)

    _fail(_query(model)[0], status)
    clock[0] += 100000

    assert _query(model, 'other') == []
    assert model.has_cached('other')


def test_a_new_key_starts_again_after_quota_used_up(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    _fail(_query(model)[0], 456)

    model.update_settings({'api_key': PRO_KEY})
    model.client.added[-1].emit('http-success', _languages_reply())

    assert len(_query(model, 'other')) == 1


def test_a_refused_string_skips_only_that_string(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    _fail(_query(model)[0], 400)

    assert len(_query(model, 'other')) == 1
    assert len(_query(model)) == 1


def test_a_language_list_server_problem_asks_again_later(monkeypatch, tmp_path, clock):
    model = _model(monkeypatch, tmp_path)
    assert _query(model) == []

    model.client.added[0].emit('http-server-error', 503)

    assert model.languages_known
    assert len(model.client.added) == 1
    clock[0] += 61
    model.query(None, _unit())
    assert model.client.added[1].url.endswith('/v3/languages?resource=translate_text')
    model.client.added[1].emit('http-success', _languages_reply())
    assert model.client.added[2].url.endswith('/v2/translate')


def test_no_reply_to_the_language_list_asks_again_later(monkeypatch, tmp_path, clock):
    model = _model(monkeypatch, tmp_path)

    model.client.added[0].emit('http-failed', 'Could not resolve host')

    assert model.languages_known
    clock[0] += 61
    model.query(None, _unit())
    assert len(model.client.added) == 2


def test_a_refused_key_for_the_language_list_stops_deepl(monkeypatch, tmp_path, clock):
    model = _model(monkeypatch, tmp_path)

    model.client.added[0].emit('http-client-error', 403)
    clock[0] += 100000

    assert model.languages_known
    assert _query(model) == []


# Setting the API key from Configure…


def test_the_api_key_can_be_configured(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, key='')

    assert model.configure_func == model.configure
    assert [field.key for field in model.settings] == ['api_key']


def test_a_key_set_from_the_dialog_turns_deepl_on(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, key='')

    model.update_settings({'api_key': FREE_KEY})

    assert 'api_key = %s' % FREE_KEY in (tmp_path / 'tm.ini').read_text()
    assert not model.languages_known
    model.client.added[0].emit('http-success', _languages_reply())
    assert len(_query(model)) == 1


def test_clearing_the_key_turns_deepl_off(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    sent = len(model.client.added)

    model.update_settings({'api_key': ''})

    assert model.languages_known
    assert _query(model) == []
    assert len(model.client.added) == sent


def test_a_new_key_forgets_the_old_keys_answers(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    old_translation = _query(model)[0]
    model.cache = {'cached': [{'target': 'old'}]}

    model.update_settings({'api_key': PRO_KEY})
    old_translation.emit('http-client-error', 456)
    old_translation.emit('http-success', _translation_reply('Hallo'))
    new_langreq = model.client.added[-1]
    new_langreq.emit('http-success', _languages_reply())

    assert new_langreq.url.startswith('https://api.deepl.com/')
    assert model.cache == {}
    assert len(_query(model)) == 1


def test_the_same_key_again_sends_nothing(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path)

    model.update_settings({'api_key': FREE_KEY})

    assert len(model.client.added) == 1


def test_the_key_is_never_logged(monkeypatch, tmp_path, clock, caplog):
    caplog.set_level('DEBUG')
    model = _ready(monkeypatch, tmp_path)

    _fail(_query(model, 'a')[0], 429, b'1')
    clock[0] += 2
    _fail(_query(model, 'b')[0], 'Connection refused')
    clock[0] += 61
    _query(model, 'c')[0].emit('http-success', b'not json')
    _fail(_query(model, 'd')[0], 400)
    _fail(_query(model, 'e')[0], 456)
    model.update_settings({'api_key': PRO_KEY})
    model.client.added[-1].emit('http-success', b'not json')

    assert caplog.text
    assert FREE_KEY not in caplog.text
    assert PRO_KEY not in caplog.text
    assert all(PRO_KEY not in repr(r) and FREE_KEY not in repr(r) for r in model.client.added)


def test_requests_in_flight_counts_the_clients_requests(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, key='')
    assert model.requests_in_flight() == 0

    model.client = SimpleNamespace(requests={1, 2})

    assert model.requests_in_flight() == 2


# Placeables go as XML tags, which DeepL keeps (#1423).


def test_placeables_go_as_xml_tags(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    (request,) = _query(model, 'Click <b>here</b> to delete %(count)s files')

    body = json.loads(request.data.decode('utf-8'))
    assert body['text'] == ['Click <g id="0">here</g> to delete <x id="2"/> files']
    assert body['tag_handling'] == 'xml'


def test_text_without_placeables_goes_without_tag_handling(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    (request,) = _query(model, 'Tom & Jerry')

    assert 'tag_handling' not in json.loads(request.data.decode('utf-8'))


def test_the_tags_become_the_units_placeables(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)
    (request,) = _query(model, 'Click <b>here</b> to delete %(count)s files')

    # As DeepL's XML handling returns it: tags kept, words moved.
    request.emit(
        'http-success', _translation_reply('Klik <g id="0">hier</g> en skrap dan <x id="2"/> lêers')
    )

    (match,) = found[0][1]
    assert match['target'] == 'Klik <b>hier</b> en skrap dan %(count)s lêers'
    assert 'tm_target' not in match


def test_units_differing_only_in_placeables_share_a_translation(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    _query(model, 'Delete %(count)s files')[0].emit(
        'http-success', _translation_reply('Verwyder <x id="0"/> lêers')
    )
    found = _matches(model)

    assert _query(model, 'Delete {0} files') == []
    assert model.has_cached('Delete %d files')
    assert found[0][1][0]['target'] == 'Verwyder {0} lêers'


def test_a_placeable_deepl_lost_is_added_and_the_suggestion_marked(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)
    (request,) = _query(model, 'Delete %(count)s files')

    request.emit('http-success', _translation_reply('Verwyder lêers'))

    (match,) = found[0][1]
    assert match['target'] == 'Verwyder lêers %(count)s'
    assert match['tm_target'] == 'Verwyder lêers'
    assert len(model.client.added) == 2, 'never sent again'


def test_a_tag_deepl_repeated_is_dropped(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)
    (request,) = _query(model, 'Open %s now')

    request.emit('http-success', _translation_reply('Maak <x id="0"/> nou <x id="0"/> oop'))

    (match,) = found[0][1]
    assert match['target'] == 'Maak %s nou  oop'
    assert match['tm_target'] == 'Maak %s nou %s oop'
