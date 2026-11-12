#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The fake Weblate's hand-check demo (demo_hand()) through the real
curl client: what each project holds, approving refused in one project
only, and the control route a hand check changes a unit with."""

import json
import threading
import urllib.error
import urllib.request

import pytest
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import TOKEN, FakeWeblate, demo_hand, run_until
from virtaal.common import pan_app
from virtaal.plugins.weblate import backend
from virtaal.plugins.weblate.test_submit import Copy


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))


@pytest.fixture
def fake():
    server = demo_hand().start()
    yield server
    server.stop()
    assert server.schema_problems == []


def _session(fake):
    return backend.WeblateSession(backend.WeblatePlatform.client_factory(fake.url, TOKEN))


def _call(method, *args):
    found = []
    method(*args, lambda *value: found.append((True, value)), lambda e: found.append((False, e)))
    run_until(lambda: found)
    return found[0]


def _post(fake, path, data):
    """(status, JSON) of a POST to the fake, with no token."""
    request = urllib.request.Request(
        fake.url + path, json.dumps(data).encode('utf-8'), method='POST'
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_projects_come_first_then_the_many_others(fake):
    assert list(fake.projects)[:3] == ['hello', 'docs', 'tools']
    reviews = {slug: p['project']['translation_review'] for slug, p in fake.projects.items()}
    assert reviews['hello'] and reviews['docs'] and not reviews['tools']


@pytest.mark.parametrize(
    'project, component', [('hello', 'program'), ('docs', 'guides/manual'), ('tools', 'toolbar')]
)
def test_each_afrikaans_translation_downloads(fake, project, component):
    ok, value = _call(_session(fake).download, project, component, 'af')

    assert ok, value
    assert value[0].units


def test_approving_is_refused_in_documentation_only(fake):
    manual = Copy(fake, 'guides/manual', project='docs')
    manual.edit('Getting started', state=StateEnum.FINAL)
    manual.save()
    program = Copy(fake)
    program.edit('Save', state=StateEnum.FINAL)
    program.save()

    refused = manual.submit()
    approved = program.submit()

    assert refused.sent == 0 and list(refused.failures.values()) == [
        'You do not have permission to approve strings.'
    ]
    assert approved.sent == 1 and approved.failures == {}


def test_toolbar_enforces_same_and_max_length(fake):
    toolbar = fake.projects['tools']['components']['toolbar']['component']
    flags = {u['source'][0]: u['flags'] + u['extra_flags'] for u in fake.units.values()}

    assert toolbar['effective_enforced_checks'] == ['same', 'max-length']
    assert flags['Save'] == 'max-length:6' and flags['Open file.'] == 'ignore-end-stop'


def test_the_control_route_edits_a_unit_as_someone_else(fake):
    fake.controls = True

    status, body = _post(fake, '_fake/edit/', {'source': 'Help', 'target': 'Hulp'})

    assert status == 200 and body['target'] == ['Hulp'] and body['state'] == 20
    unit = next(u for u in fake.units.values() if u['source'] == ['Help'])
    assert unit['target'] == ['Hulp'] and fake.patched == []


def test_the_control_route_says_when_no_unit_has_the_source(fake):
    fake.controls = True

    status, _body = _post(fake, '_fake/edit/', {'source': 'Nothing', 'project': 'tools'})

    assert status == 404


def test_without_controls_there_is_no_control_route(fake):
    status, _body = _post(fake, '_fake/edit/', {'source': 'Help', 'target': 'Hulp'})

    assert status == 401


def test_serve_says_its_url_once_it_listens():
    fake = FakeWeblate()
    heard = []
    threading.Thread(target=fake.serve, args=(0, heard.append), daemon=True).start()
    run_until(lambda: heard)

    assert heard[0].startswith('http://127.0.0.1:') and heard[0] == fake.url
    fake.stop()
