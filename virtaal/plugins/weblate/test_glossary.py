#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A project's Weblate glossaries through the real curl client, against
the fake server: as TBX, unchanged (304), changed, as stored where the
server can't convert, and none (no glossary, no such language, a server
older than glossary components)."""

import io

import pytest
from translate.storage import factory

from devsupport.testing.fakeweblate import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.weblate import backend
from virtaal.support.platforms import AUTH

TERMS = [('file', 'lêer'), ('save', 'stoor')]


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')


@pytest.fixture
def fake():
    server = demo()
    server.fill_glossary('hello', 'glossary', 'af', TERMS)
    server.start()
    yield server
    server.stop()
    assert server.schema_problems == []


def _session(fake, token=TOKEN):
    return backend.WeblateSession(backend.WeblatePlatform.client_factory(fake.url, token))


def _glossaries(fake, project='hello', language='af', known=None, token=TOKEN):
    found = []
    _session(fake, token).glossaries(
        project, language, known or {}, lambda g: found.append(('done', g)), found.append
    )
    run_until(lambda: found)
    assert found[0][0] == 'done', found[0]
    return found[0][1]


def _terms(data, extension):
    buffer = io.BytesIO(data)
    buffer.name = 'glossary.' + extension
    return [(str(u.source), str(u.target)) for u in factory.getobject(buffer).units]


def _file_requests(fake):
    return [
        r for r in fake.requests_to('/api/translations/hello/glossary/') if '/file/' in r['path']
    ]


def test_the_projects_glossary_comes_as_tbx(fake):
    [glossary] = _glossaries(fake)

    assert (glossary.slug, glossary.name, glossary.extension) == ('glossary', 'Glossary', 'tbx')
    assert _terms(glossary.data, 'tbx') == TERMS
    assert glossary.validator.endswith('GMT')
    assert _file_requests(fake)[0]['query'] == {'format': 'tbx'}


def test_an_unchanged_glossary_comes_without_its_file(fake):
    [first] = _glossaries(fake)

    [again] = _glossaries(fake, known={'glossary': first.validator})

    assert again.data is None and again.validator == first.validator
    asked = _file_requests(fake)[-1]
    assert asked['headers']['If-Modified-Since'] == first.validator


def test_a_glossary_changed_since_comes_again(fake):
    [first] = _glossaries(fake)
    term = next(u for u in fake.units.values() if u['source'] == ['save'])
    fake.edit(term['id'], 'bewaar')

    [again] = _glossaries(fake, known={'glossary': first.validator})

    assert ('save', 'bewaar') in _terms(again.data, 'tbx')


def test_a_language_the_glossary_hasnt_is_left_out(fake):
    assert _glossaries(fake, language='de') == []


def test_a_project_without_a_glossary_has_none(fake):
    assert _glossaries(fake, project='docs') == []


def test_a_server_before_glossary_components_has_none(fake):
    for project in fake.projects.values():
        for parent in project['components'].values():
            parent['component'].pop('is_glossary')

    assert _glossaries(fake) == []
    assert _file_requests(fake) == []


def test_a_server_that_cant_convert_gives_the_file_as_stored(fake):
    fake.converts = set()
    fake.add_component('hello', 'terms', 'Terms', is_glossary=True, file_format='po')
    fake.add_translation('hello', 'terms', 'af', 'Afrikaans', 1, 1)
    fake.fill_po('hello', 'terms', 'af', [('', 'quit', 'verlaat', 20)])

    found = {g.slug: g for g in _glossaries(fake)}

    assert sorted(found) == ['glossary', 'terms']
    assert found['glossary'].extension == 'tbx' and found['terms'].extension == 'po'
    assert ('quit', 'verlaat') in _terms(found['terms'].data, 'po')


def test_a_refused_token_is_an_error(fake):
    found = []
    _session(fake, 'wlu_wrong').glossaries('hello', 'af', {}, found.append, found.append)
    run_until(lambda: found)

    assert found[0].kind == AUTH
