#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Downloading a Weblate translation, its units and their states, from
the fake server through the real curl client."""

import json

import pytest
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import DEMO_JSON, DEMO_PO, TOKEN, demo, run_until
from virtaal.plugins.weblate import backend
from virtaal.plugins.weblate.client import Client
from virtaal.support.platforms import AUTH, HTTP, NETWORK, NOT_PLATFORM


@pytest.fixture
def fake():
    server = demo().start()
    yield server
    server.stop()
    assert server.schema_problems == []


def _download(fake, component='program', language='af', project='hello'):
    session = backend.WeblatePlatform.client_factory(fake.url, TOKEN)
    outcome = {}
    backend.WeblateSession(session).download(
        project,
        component,
        language,
        lambda d: outcome.setdefault('download', d),
        lambda e: outcome.setdefault('error', e),
    )
    run_until(lambda: outcome)
    return outcome.get('download'), outcome.get('error')


# States #


@pytest.mark.parametrize(
    'code, state',
    [
        (0, StateEnum.EMPTY),
        (10, StateEnum.NEEDS_WORK),
        (11, StateEnum.REJECTED),
        (12, StateEnum.NEEDS_REVIEW),
        (20, StateEnum.UNREVIEWED),
        (30, StateEnum.FINAL),
    ],
)
def test_weblates_states_map_both_ways(code, state):
    assert backend.virtaal_state(code) == state
    assert backend.weblate_state(state) == code


def test_read_only_has_no_state_of_virtaals():
    assert backend.virtaal_state(100) is None
    assert backend.virtaal_state(42) is None


def test_a_state_between_maps_to_the_one_below():
    assert backend.weblate_state(StateEnum.UNREVIEWED + 5) == 20
    assert backend.weblate_state(StateEnum.EMPTY + 1) == 0


# The download #


@pytest.mark.parametrize(
    'filename, filemask, path',
    [
        ('res/values-af/strings.xml', 'res/values-*/strings.xml', 'res/values-af/strings.xml'),
        ('', 'res/values-*/strings.xml', 'res/values-af/strings.xml'),
        (None, '', 'af'),
    ],
)
def test_the_files_path_is_weblates(filename, filemask, path):
    assert backend.file_path(filename, filemask, 'af') == path


def test_a_po_translation_downloads_with_its_units(fake):
    download, error = _download(fake)

    assert error is None
    assert download.filename == 'po/af.po'
    assert download.data.startswith(b'msgid ""')
    assert download.template is None
    assert download.names == {'project': 'Hello', 'component': 'Program'}
    assert [(u.context, u.source[0], u.code) for u in download.units] == [
        (context, source, state) for context, source, _target, state in DEMO_PO
    ]
    approved = download.units[0]
    assert approved.state == StateEnum.FINAL and not approved.read_only
    assert approved.id and approved.last_updated and approved.position == 1
    read_only = download.units[6]
    assert read_only.read_only and read_only.state is None


def test_the_file_is_asked_for_as_weblate_stores_it(fake):
    _download(fake)

    (asked,) = fake.requests_to('/api/translations/hello/program/af/file/')
    assert asked['query'] == {}
    assert asked['headers']['Authorization'] == 'Token ' + TOKEN


def test_units_come_a_page_at_a_time(fake, monkeypatch):
    monkeypatch.setattr(Client, 'UNITS_PAGE_SIZE', 3)

    download, _error = _download(fake)

    assert len(download.units) == len(DEMO_PO)
    assert len(fake.requests_to('/api/translations/hello/program/af/units/')) == 3


def test_a_monolingual_component_brings_its_template(fake):
    download, error = _download(fake, 'website')

    assert error is None
    assert download.filename == 'locales/af.json'
    assert download.template[0] == 'locales/en.json'
    assert json.loads(download.template[1].decode('utf-8')) == DEMO_JSON
    assert [(u.context, u.code) for u in download.units] == [
        ('welcome', 20),
        ('open', 30),
        ('save', 0),
    ]


@pytest.mark.parametrize(
    'path, status, kind',
    [
        ('/api/translations/hello/program/af/file/', 500, HTTP),
        ('/api/translations/hello/program/af/file/', 403, HTTP),
        ('/api/translations/hello/program/af/units/', 401, AUTH),
        ('/api/translations/hello/program/af/file/', None, NETWORK),
        ('/api/components/hello/website/monolingual_base/', 404, NOT_PLATFORM),
    ],
)
def test_a_failed_request_fails_the_download(fake, path, status, kind):
    fake.fail(path, status, times=5)

    download, error = _download(fake, 'website' if 'website' in path else 'program')

    assert download is None
    assert error.kind == kind
    if kind == HTTP:
        assert error.status == status


def test_a_language_the_server_lacks_is_not_found(fake):
    download, error = _download(fake, language='xx')

    assert download is None
    assert error.kind == NOT_PLATFORM and error.status == 404
