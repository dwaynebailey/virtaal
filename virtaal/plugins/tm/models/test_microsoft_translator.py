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
from virtaal.plugins.tm.models.microsoft_translator import TMModel, microsoft_code

KEY = '3f1c9a7e5b2d4e8f9a0b1c2d3e4f5a6b'
OTHER_KEY = '9b8a7c6d5e4f3a2b1c0d9e8f7a6b5c4d'
HOST = 'https://api.cognitive.microsofttranslator.com'
LANGUAGES_URL = HOST + '/languages?api-version=3.0&scope=translation'

# The codes in GET /languages?api-version=3.0&scope=translation, 2026-11-04.
CODES = (
    'af am ar as az ba be bg bho bn bo brx bs ca cs cy da de doi dsb dv el en es es-MX et eu '
    'fa fi fil fj fo fr fr-CA ga gl gom gu ha he hi hne hr hsb ht hu hy id ig ikt is it iu '  # codespell:ignore fo
    'iu-Latn ja ka kk km kmr kn ko ks ku ky lb ln lo lt lug lv lzh mai mg mi mk ml mn-Cyrl '
    'mn-Mong mni mr ms mt mww my nb ne nl nso nya or otq pa pl prs ps pt pt-PT ro ru run rw sd '
    'si sk sl sm sn so sq sr-Cyrl sr-Latn st sv sw ta te th ti tk tlh-Latn tlh-Piqd tn to tr '  # codespell:ignore te
    'tt ty ug uk ur uz vi xh yo yua yue zh-Hans zh-Hant zu'
).split()
SUPPORTED = {code.lower(): code for code in CODES}

# The German source of #3137: a reST substitution and a literal "<".
GERMAN = (
    'Eine Farbabweichung von |deltaE|\\ < 6 wird als akzeptabel festgelegt und führt '  # codespell:ignore als
    'zu einer deutlichen Senkung der Rücklaufquote.'
)


def _languages_reply(codes=CODES):
    # The reply's shape, with one entry's details.
    return json.dumps(
        {'translation': {code: {'name': code, 'nativeName': code, 'dir': 'ltr'} for code in codes}}
    ).encode('utf-8')


def _translation_reply(text, to='de'):
    return json.dumps([{'translations': [{'text': text, 'to': to}]}]).encode('utf-8')


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(keyedmt.time, 'monotonic', lambda: now[0])
    return now


def _model(monkeypatch, tmp_path, key=KEY, region='', source='en', target='de'):
    monkeypatch.setattr(keyedmt, 'HTTPClient', FakeHTTPClient)
    monkeypatch.setattr('virtaal.common.pan_app.get_config_dir', lambda: str(tmp_path))
    if key or region:
        (tmp_path / 'tm.ini').write_text(
            '[microsoft_translator]\napi_key = %s\nregion = %s\n' % (key, region)
        )
    return TMModel('microsoft_translator', fake_controller(source, target))


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
    assert model.client is None
    assert model.has_cached('hello')


def test_a_region_without_a_key_sends_nothing(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, key='', region='westeurope')

    model.query(None, _unit())

    assert model.client is None


# The language list needs no key; translations send it in a header.


def test_the_language_list_is_asked_for_without_the_key(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, region='westeurope')

    (request,) = model.client.added
    assert request.url == LANGUAGES_URL
    assert request.method == 'GET'
    assert not request.headers
    assert not model.languages_known


def test_a_query_posts_the_text_as_json(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    (request,) = _query(model, 'Open file…')

    assert request.url == (HOST + '/translate?api-version=3.0&from=en&to=de&textType=plain')
    assert request.method == 'POST'
    assert request.headers == [
        'Ocp-Apim-Subscription-Key: %s' % KEY,
        'Content-Type: application/json; charset=UTF-8',
    ]
    assert json.loads(request.data.decode('utf-8')) == [{'Text': 'Open file…'}]


def test_a_region_is_sent_for_a_regional_resource(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path, region=' westeurope ')

    (request,) = _query(model)

    assert request.headers == [
        'Ocp-Apim-Subscription-Key: %s' % KEY,
        'Ocp-Apim-Subscription-Region: westeurope',
        'Content-Type: application/json; charset=UTF-8',
    ]


def test_the_issues_german_source_is_one_exact_request_body(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path, source='de', target='en')
    found = _matches(model)

    (request,) = _query(model, GERMAN)

    # The 6 is a placeable, so the text goes as HTML.
    sent = '<div>%s</div>' % GERMAN.replace('<', '&lt;').replace(
        ' 6 ', ' <span class="notranslate">6</span> '
    )
    assert request.data == json.dumps([{'Text': sent}]).encode('utf-8')
    assert b'|deltaE|\\\\ &lt; <span' in request.data
    reply = '<div>|deltaE|\\ &lt; <span class="notranslate">6</span></div>'
    request.emit('http-success', _translation_reply(reply, to='en'))
    assert found[0][1][0]['target'] == '|deltaE|\\ < 6'


def test_a_query_before_the_languages_arrive_runs_once_they_do(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path)

    assert _query(model) == []
    model.client.added[0].emit('http-success', _languages_reply())

    assert len(model.client.added) == 2
    assert '/translate?' in model.client.added[1].url


# Replies


def test_a_translation_becomes_a_suggestion(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)
    (request,) = _query(model)

    request.emit('http-success', _translation_reply('Hallo'))

    match = {'source': 'hello', 'target': 'Hallo', 'tmsource': 'Microsoft'}
    assert found == [('hello', [match])]
    assert model.cache['hello'] == 'Hallo'


def test_a_cached_string_is_not_sent_again(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    _query(model)[0].emit('http-success', _translation_reply('Hallo'))
    found = _matches(model)

    assert _query(model) == []
    assert found == [('hello', [{'source': 'hello', 'target': 'Hallo', 'tmsource': 'Microsoft'}])]
    assert model.has_cached('hello')
    assert not model.has_cached('other')


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
    'reply',
    [
        b'not json',
        b'{}',
        b'[]',
        b'[{"translations": []}]',
        b'[{"translations": [{"text": ""}]}]',
        b'[{"translations": [{"text": null}]}]',
        b'{"error": {"code": 400000, "message": "?"}}',
    ],
)
def test_an_unusable_reply_gives_no_suggestion_but_keeps_microsoft_on(monkeypatch, tmp_path, reply):
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


@pytest.mark.parametrize('reply', [b'not json', b'[]', b'{"dictionary": {}}'])
def test_an_unusable_language_list_turns_microsoft_off(monkeypatch, tmp_path, reply):
    model = _model(monkeypatch, tmp_path)

    model.client.added[0].emit('http-success', reply)

    assert model.languages_known
    assert _query(model) == []


# Languages: Virtaal's codes to Microsoft's.


@pytest.mark.parametrize(
    'code, expected',
    [
        ('de', 'de'),
        ('de_CH', 'de'),
        ('en', 'en'),
        ('en_GB', 'en'),
        ('af', 'af'),
        ('zu_ZA', 'zu'),
        # Microsoft's pt is Brazil's.
        ('pt', 'pt-PT'),
        ('pt_PT', 'pt-PT'),
        ('pt_AO', 'pt-PT'),
        ('pt_BR', 'pt'),
        ('zh', 'zh-Hans'),
        ('zh_CN', 'zh-Hans'),
        ('zh_SG', 'zh-Hans'),
        ('zh_TW', 'zh-Hant'),
        ('zh_HK', 'zh-Hant'),
        ('zh_Hant', 'zh-Hant'),
        ('es', 'es'),
        ('es_MX', 'es-MX'),
        ('es_AR', 'es'),
        ('fr_CA', 'fr-CA'),
        ('fr_BE', 'fr'),
        ('sr', 'sr-Cyrl'),
        ('sr_RS', 'sr-Cyrl'),
        ('sr@latin', 'sr-Latn'),
        ('sr_RS@latin', 'sr-Latn'),
        ('sr_Latn', 'sr-Latn'),
        ('no', 'nb'),
        ('nb_NO', 'nb'),
        ('tl', 'fil'),
        ('fil', 'fil'),
        ('mn', 'mn-Cyrl'),
        ('ku', 'kmr'),
        ('ckb', 'ku'),
        ('fa_AF', 'prs'),
        ('fa', 'fa'),
        ('ny', 'nya'),
        ('rn', 'run'),
        ('lg', 'lug'),
        ('kok', 'gom'),
        ('he', 'he'),
        ('nso', 'nso'),
        ('nn', None),
        ('ast', None),
        # A variant Microsoft doesn't have is not the plain language.
        ('ca@valencia', None),
        ('de@test', None),
        ('uz@cyrillic', None),
    ],
)
def test_codes(code, expected):
    assert microsoft_code(code, SUPPORTED) == expected


def test_a_query_sends_microsofts_variant_codes(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path, source='zh_CN', target='sr@latin')

    (request,) = _query(model)

    assert '&from=zh-Hans&to=sr-Latn&' in request.url


@pytest.mark.parametrize(
    'source, target', [('en', 'nn'), ('ast', 'en'), ('en', 'en'), ('en_GB', 'en_US')]
)
def test_an_unsupported_pair_or_one_language_sends_nothing(
    monkeypatch, tmp_path, caplog, source, target
):
    caplog.set_level('INFO')
    model = _ready(monkeypatch, tmp_path, source=source, target=target)
    found = _matches(model)

    assert _query(model) == []
    assert found == []
    assert caplog.text == ''


# Errors are quiet: a debug message, never one for the user.


def test_too_many_requests_waits_as_long_as_microsoft_asks(monkeypatch, tmp_path, clock, caplog):
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


@pytest.mark.parametrize('status', [500, 503, 408, 'Connection timed out'])
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


@pytest.mark.parametrize('status', [401, 403])
def test_a_refused_key_or_quota_used_up_stops_microsoft(monkeypatch, tmp_path, clock, status):
    model = _ready(monkeypatch, tmp_path)

    _fail(_query(model)[0], status)
    clock[0] += 100000

    assert _query(model, 'other') == []
    assert model.has_cached('other')


def test_a_new_key_starts_again_without_asking_for_the_languages(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    _fail(_query(model)[0], 403)

    model.update_settings({'api_key': OTHER_KEY, 'region': ''})

    (request,) = _query(model, 'other')
    assert request.headers[0] == 'Ocp-Apim-Subscription-Key: %s' % OTHER_KEY


def test_a_region_set_after_a_refusal_starts_again(monkeypatch, tmp_path):
    # A regional key without its region is refused with 401.
    model = _ready(monkeypatch, tmp_path)
    _fail(_query(model)[0], 401)

    model.update_settings({'api_key': KEY, 'region': 'eastus'})

    (request,) = _query(model, 'other')
    assert 'Ocp-Apim-Subscription-Region: eastus' in request.headers


@pytest.mark.parametrize('status', [400, 413, 415])
def test_a_refused_string_skips_only_that_string(monkeypatch, tmp_path, status):
    model = _ready(monkeypatch, tmp_path)

    _fail(_query(model)[0], status)

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
    assert model.client.added[1].url == LANGUAGES_URL
    model.client.added[1].emit('http-success', _languages_reply())
    assert '/translate?' in model.client.added[2].url


def test_no_reply_to_the_language_list_asks_again_later(monkeypatch, tmp_path, clock):
    model = _model(monkeypatch, tmp_path)

    model.client.added[0].emit('http-failed', 'Could not resolve host')

    assert model.languages_known
    clock[0] += 61
    model.query(None, _unit())
    assert len(model.client.added) == 2


def test_a_refused_language_list_stops_microsoft(monkeypatch, tmp_path, clock):
    model = _model(monkeypatch, tmp_path)

    model.client.added[0].emit('http-client-error', 404)
    clock[0] += 100000

    assert model.languages_known
    assert _query(model) == []


def test_a_full_client_queue_is_a_failed_request(monkeypatch, tmp_path, clock):
    model = _ready(monkeypatch, tmp_path)
    model.client.add = lambda request: False

    model.query(None, _unit())

    assert model.has_cached('other')
    assert model._pending == set()


# Setting the API key and region from Configure…


def test_the_key_and_region_can_be_configured(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, key='')

    assert model.configure_func == model.configure
    assert [field.key for field in model.settings] == ['api_key', 'region']
    assert [field.kind for field in model.settings] == ['secret', 'text']
    assert not any(field.required for field in model.settings)
    assert model.settings[1].label == '_Region:'


def test_a_key_set_from_the_dialog_turns_microsoft_on(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, key='')

    model.update_settings({'api_key': KEY, 'region': 'westeurope'})

    ini = (tmp_path / 'tm.ini').read_text()
    assert 'api_key = %s' % KEY in ini
    assert 'region = westeurope' in ini
    assert not model.languages_known
    model.client.added[0].emit('http-success', _languages_reply())
    assert len(_query(model)) == 1


def test_clearing_the_key_turns_microsoft_off(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    sent = len(model.client.added)

    model.update_settings({'api_key': '', 'region': ''})

    assert model.languages_known
    assert _query(model) == []
    assert len(model.client.added) == sent


@pytest.mark.parametrize(
    'settings', [{'api_key': OTHER_KEY, 'region': ''}, {'api_key': KEY, 'region': 'eastus'}]
)
def test_a_new_key_or_region_forgets_the_old_answers(monkeypatch, tmp_path, settings):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)
    old_translation = _query(model)[0]
    model.cache = {'cached': [{'target': 'old'}]}

    model.update_settings(settings)
    old_translation.emit('http-client-error', 401)
    old_translation.emit('http-success', _translation_reply('Hallo'))

    assert found == []
    assert model.cache == {}
    assert len(_query(model)) == 1


def test_a_new_key_while_the_languages_are_on_their_way_asks_once(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path)

    model.update_settings({'api_key': '', 'region': ''})
    model.update_settings({'api_key': OTHER_KEY, 'region': ''})
    model.query(None, _unit())

    assert len(model.client.added) == 1
    model.client.added[0].emit('http-success', _languages_reply())
    assert len(model.client.added) == 2
    assert model.client.added[1].headers[0] == 'Ocp-Apim-Subscription-Key: %s' % OTHER_KEY


def test_the_same_settings_again_send_nothing(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path)

    model.update_settings({'api_key': KEY, 'region': ''})

    assert len(model.client.added) == 1


def test_the_key_is_never_logged(monkeypatch, tmp_path, clock, caplog):
    caplog.set_level('DEBUG')
    model = _ready(monkeypatch, tmp_path, region='westeurope')

    _fail(_query(model, 'a')[0], 429, b'1')
    clock[0] += 2
    _fail(_query(model, 'b')[0], 'Connection refused')
    clock[0] += 61
    _query(model, 'c')[0].emit('http-success', b'not json')
    _fail(_query(model, 'd')[0], 400)
    _fail(_query(model, 'e')[0], 401)
    model.update_settings({'api_key': OTHER_KEY, 'region': ''})
    _fail(_query(model, 'f')[0], 403)

    assert caplog.text
    assert KEY not in caplog.text
    assert OTHER_KEY not in caplog.text
    assert all(KEY not in r.url and OTHER_KEY not in r.url for r in model.client.added)
    assert all(OTHER_KEY not in repr(r) and KEY not in repr(r) for r in model.client.added)


def test_requests_in_flight_counts_the_clients_requests(monkeypatch, tmp_path):
    model = _model(monkeypatch, tmp_path, key='')
    assert model.requests_in_flight() == 0

    model.client = SimpleNamespace(requests={1, 2})

    assert model.requests_in_flight() == 2


# Placeables go in notranslate elements, which Translator leaves (#1423).


def test_placeables_go_as_html_in_one_element(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    (request,) = _query(model, 'Delete %(count)s files & <b>folders</b>')

    assert request.url == HOST + '/translate?api-version=3.0&from=en&to=de&textType=html'
    assert json.loads(request.data.decode('utf-8')) == [
        {
            'Text': '<div>Delete <span class="notranslate">%(count)s</span> files &amp; '
            '<span class="notranslate">&lt;b&gt;</span>folders'
            '<span class="notranslate">&lt;/b&gt;</span></div>'
        }
    ]


def test_the_elements_become_the_units_placeables(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)
    (request,) = _query(model, 'Copy %1 to %2')

    # As Translator returns HTML: the notranslate elements as sent, moved.
    reply = (
        '<div>Kopiere nach <span class="notranslate">%2</span> von '
        '<span class="notranslate">%1</span> &amp; mehr</div>'
    )
    request.emit('http-success', _translation_reply(reply))

    (match,) = found[0][1]
    assert match['target'] == 'Kopiere nach %2 von %1 & mehr'
    assert 'tm_target' not in match


def test_a_placeable_translator_lost_is_added_and_the_suggestion_marked(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    found = _matches(model)
    (request,) = _query(model, 'Copy %1 to %2')

    request.emit(
        'http-success', _translation_reply('<div>Kopiere <span class="notranslate">%1</span></div>')
    )

    (match,) = found[0][1]
    assert match['target'] == 'Kopiere %1 %2'
    assert match['tm_target'] == 'Kopiere %1'


# A file's suggestions (#1679)


def test_a_file_fetch_sends_an_array_of_texts(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    assert model.bulk_ready() and model.billed
    request = model.bulk_send(['Open', 'Close'])
    reply = [{'translations': [{'text': 'Öffnen'}]}, {'translations': [{'text': 'Schließen'}]}]
    found = model.bulk_parse(['Open', 'Close'], json.dumps(reply).encode('utf-8'))

    assert json.loads(request.data.decode('utf-8')) == [{'Text': 'Open'}, {'Text': 'Close'}]
    assert 'textType=plain' in request.url
    assert [found[s][0]['target'] for s in ('Open', 'Close')] == ['Öffnen', 'Schließen']


def test_a_file_fetch_keeps_to_microsofts_limits(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)
    assert [len(b) for b in model.bulk_batches(['y'] * 150)] == [100, 50]

    monkeypatch.setattr(TMModel, 'BULK_CHARS', 100)
    batches = model.bulk_batches(['x' * 40] * 3 + ['y'] * 150)

    assert [len(b) for b in batches] == [2, 61, 90]


def test_a_file_fetch_stops_for_a_refused_key(monkeypatch, tmp_path):
    model = _ready(monkeypatch, tmp_path)

    assert model.bulk_wait(model.bulk_send(['Open']), 401) is None
    assert not model.bulk_ready()
