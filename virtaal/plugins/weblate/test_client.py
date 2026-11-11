#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Weblate client against the fake server on 127.0.0.1, through the
real curl client."""

import logging
import socket

import pytest

from devsupport.testing.fakeweblate import TOKEN, FakeWeblate, run_until
from virtaal.plugins.weblate.client import Client, api_url, component_slug, path_part
from virtaal.support import platforms


@pytest.fixture
def fake():
    server = FakeWeblate().start()
    yield server
    server.stop()
    assert server.schema_problems == []


@pytest.fixture
def waits(monkeypatch):
    """The waits clients ask for, each run at once instead."""
    from gi.repository import GLib

    asked = []

    def schedule(seconds, callback):
        asked.append(seconds)
        GLib.idle_add(lambda: callback() and False)

    monkeypatch.setattr(Client, 'schedule', staticmethod(schedule))
    return asked


class Outcome:
    def __init__(self):
        self.pages = []
        self.result = None
        self.error = None

    def page(self, items):
        self.pages.append(items)

    def done(self, value):
        self.result = value

    def failed(self, error):
        self.error = error

    def finished(self):
        return self.result is not None or self.error is not None

    def wait(self):
        run_until(self.finished)
        return self


def _test(fake_or_url, token=TOKEN):
    url = fake_or_url if isinstance(fake_or_url, str) else fake_or_url.url
    outcome = Outcome()
    Client(url, token).test_connection(outcome.done, outcome.failed)
    return outcome.wait()


def _projects(fake, n):
    for i in range(n):
        fake.add_project('p%02d' % i, 'Project %d' % i)


# Addresses #


@pytest.mark.parametrize(
    'typed, url',
    [
        ('weblate.example.org', 'https://weblate.example.org/api/'),
        ('https://Weblate.Example.org/', 'https://weblate.example.org/api/'),
        ('https://hosted.weblate.org/api', 'https://hosted.weblate.org/api/'),
        ('https://example.org/weblate/', 'https://example.org/weblate/api/'),
        ('http://localhost:8080', 'http://localhost:8080/api/'),
        ('http://127.0.0.1:8765/api/', 'http://127.0.0.1:8765/api/'),
        # A token never travels in the clear to another machine.
        ('http://weblate.example.org', None),
        ('ftp://weblate.example.org', None),
        ('https://user:secret@weblate.example.org', None),
        ('', None),
        ('https://', None),
    ],
)
def test_the_api_url_of_what_a_user_types(typed, url):
    assert api_url(typed) == url


def test_a_category_slug_is_escaped_twice():
    assert path_part('guides/manual') == 'guides%252Fmanual'


def test_a_components_slug_includes_its_category():
    component = {'slug': 'manual', 'url': 'https://w/api/components/docs/guides%252Fmanual/'}

    assert component_slug(component) == 'guides/manual'
    assert component_slug({'slug': 'program', 'url': 'https://w/api/components/p/program/'}) == (
        'program'
    )


# Test connection #


def test_a_good_token_connects_and_counts_the_projects(fake):
    _projects(fake, 3)

    outcome = _test(fake)

    assert outcome.result == 3
    assert fake.requests[0]['path'] == '/api/projects/'
    assert fake.requests[0]['query'] == {'page_size': '1'}
    assert fake.requests[0]['headers']['Authorization'] == 'Token ' + TOKEN


def test_the_user_agent_is_virtaals_without_an_email_address(fake):
    _test(fake)

    agent = fake.requests[0]['headers']['User-Agent']
    assert agent.startswith('Virtaal/')
    assert '@' not in agent


def test_a_project_token_sees_its_project_only(fake):
    _projects(fake, 3)
    fake.tokens['wlp_project'] = 'p01'

    assert _test(fake, 'wlp_project').result == 1


@pytest.mark.parametrize('style', ['standard', 'legacy'])
def test_a_bad_token_is_refused(fake, style):
    fake.error_style = style

    outcome = _test(fake, 'wlu_wrong')

    assert outcome.error.kind == platforms.AUTH
    assert outcome.error.status == 401
    assert outcome.error.detail == 'Invalid token.'


def test_the_fake_starts_without_a_reverse_lookup(monkeypatch):
    # socket.getfqdn("127.0.0.1") takes over 30 s on macOS CI runners.
    def lookup(*_args):
        raise AssertionError('reverse lookup')

    monkeypatch.setattr(socket, 'getfqdn', lookup)
    with FakeWeblate() as server:
        assert server.url.startswith('http://127.0.0.1:')


def test_no_server_is_a_network_error():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # Nothing listens on port now.

    outcome = _test('http://127.0.0.1:%d/' % port)

    assert outcome.error.kind == platforms.NETWORK
    assert outcome.error.detail


def test_a_dropped_connection_is_a_network_error(fake):
    fake.fail('/api/projects/', None)

    assert _test(fake).error.kind == platforms.NETWORK


@pytest.mark.parametrize('status', [200, 404, 302])
def test_another_web_site_is_not_weblate(fake, status):
    fake.not_weblate = status == 200
    if status != 200:
        fake.fail('/api/projects/', status, body=b'<html></html>', headers={'Location': '/'})

    outcome = _test(fake)

    assert outcome.error.kind == platforms.NOT_PLATFORM


def test_json_that_is_not_a_list_is_not_weblate(fake):
    fake.fail('/api/projects/', 200, body=b'{"hello": "world"}')

    assert _test(fake).error.kind == platforms.NOT_PLATFORM


def test_a_server_error_gives_its_status_and_words(fake):
    body = fake.error(500, 'error', 'Something broke.')
    fake.fail('/api/projects/', 500, body=body)

    outcome = _test(fake)

    assert (outcome.error.kind, outcome.error.status) == (platforms.HTTP, 500)
    assert outcome.error.detail == 'Something broke.'


def test_the_token_is_never_logged(fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake.fail('/api/projects/', 503, headers={'Retry-After': '1'})
    fake.fail('/api/projects/', None)

    _test(fake)

    assert TOKEN not in caplog.text


# Lists #


def test_a_list_is_followed_across_its_pages(fake, monkeypatch):
    _projects(fake, 7)
    monkeypatch.setattr(Client, 'PAGE_SIZE', 3)
    outcome = Outcome()

    Client(fake.url, TOKEN).get_list('projects/', outcome.page, outcome.done, outcome.failed)
    outcome.wait()

    assert [[p['slug'] for p in page] for page in outcome.pages] == [
        ['p00', 'p01', 'p02'],
        ['p03', 'p04', 'p05'],
        ['p06'],
    ]
    assert outcome.result == 7
    assert [r['query'].get('page') for r in fake.requests] == [None, '2', '3']


def test_projects_come_a_page_at_a_time(fake, monkeypatch):
    _projects(fake, 5)
    monkeypatch.setattr(Client, 'PAGE_SIZE', 2)
    client = Client(fake.url, TOKEN)
    pages = []

    def page(results, following):
        pages.append(([p['slug'] for p in results], following))

    client.projects_page(None, page, None)
    run_until(lambda: pages)
    client.projects_page(pages[0][1], page, None)
    run_until(lambda: len(pages) == 2)

    assert pages[0] == (['p00', 'p01'], fake.api_url + 'projects/?page_size=2&page=2')
    assert pages[1][0] == ['p02', 'p03']
    assert len(fake.requests) == 2


def test_the_last_page_of_projects_has_no_next(fake):
    _projects(fake, 1)
    pages = []

    Client(fake.url, TOKEN).projects_page(None, lambda *page: pages.append(page), None)
    run_until(lambda: pages)

    assert pages[0][1] is None


def test_a_next_link_elsewhere_is_followed_on_this_server_only(fake, monkeypatch):
    _projects(fake, 2)
    monkeypatch.setattr(Client, 'PAGE_SIZE', 1)
    client = Client(fake.url, TOKEN)

    assert client._own('http://proxy.internal/api/projects/?page=2') == (
        fake.api_url + 'projects/?page=2'
    )
    assert client._own('https://evil.example.org/steal/') is None


def test_components_and_translations_of_a_category(fake):
    fake.add_project('docs')
    fake.add_component('docs', 'manual', 'Manual', category='guides')
    fake.add_translation('docs', 'guides/manual', 'af', 'Afrikaans', translated=3, total=4)
    client = Client(fake.url, TOKEN)
    components, translations = Outcome(), Outcome()

    client.components('docs', components.page, components.done, components.failed)
    client.translations('docs', 'guides/manual', translations.page, translations.done, None)
    run_until(lambda: components.finished() and translations.finished())

    assert component_slug(components.pages[0][0]) == 'guides/manual'
    assert translations.pages[0][0]['translated_percent'] == 75.0
    assert fake.requests_to('/api/components/docs/guides%252Fmanual/translations/')


def test_cancel_drops_the_replies(fake):
    outcome = Outcome()
    client = Client(fake.url, TOKEN)
    client.test_connection(outcome.done, outcome.failed)
    client.cancel()

    run_until(lambda: fake.requests and not client.http.requests)

    assert not outcome.finished()


# Rate limits #


def test_a_429_is_waited_out_as_retry_after_asks(fake, waits):
    fake.fail('/api/projects/', 429, headers={'Retry-After': '7'})

    outcome = _test(fake)

    assert outcome.result == 0
    assert waits == [pytest.approx(7, abs=0.5)]
    assert len(fake.requests) == 2


def test_a_429_without_retry_after_waits_the_default(fake, waits):
    fake.fail('/api/projects/', 429)

    assert _test(fake).result == 0
    assert waits == [pytest.approx(Client.RETRY_AFTER, abs=0.5)]


def test_a_busy_server_is_asked_again_with_growing_waits(fake, waits):
    fake.fail('/api/projects/', 503, times=3)

    assert _test(fake).result == 0
    assert waits == [
        pytest.approx(1, abs=0.5),
        pytest.approx(2, abs=0.5),
        pytest.approx(4, abs=0.5),
    ]


def test_retries_give_up_after_max_tries(fake, waits):
    fake.fail('/api/projects/', 503, times=Client.MAX_TRIES)

    outcome = _test(fake)

    assert (outcome.error.kind, outcome.error.status) == (platforms.HTTP, 503)
    assert len(fake.requests) == Client.MAX_TRIES


def test_a_wait_too_long_fails_at_once(fake, waits):
    fake.fail('/api/projects/', 429, headers={'Retry-After': '3600'})

    outcome = _test(fake)

    assert (outcome.error.kind, outcome.error.status) == (platforms.HTTP, 429)
    assert waits == []


def test_a_used_up_rate_limit_stops_requests_before_sending(fake, waits):
    fake.rate_limit = 2
    client = Client(fake.url, TOKEN)
    outcomes = [Outcome() for _i in range(3)]

    for outcome in outcomes[:2]:
        client.test_connection(outcome.done, outcome.failed)
        outcome.wait()
    client.test_connection(outcomes[2].done, outcomes[2].failed)
    outcomes[2].wait()

    assert [o.result for o in outcomes[:2]] == [0, 0]
    # X-RateLimit-Remaining: 0 with an hour to its reset.
    assert outcomes[2].error.status == 429
    assert len(fake.requests) == 2


def test_the_budget_has_the_limit_and_is_shared_by_clients_of_one_server(fake):
    fake.rate_limit = 50
    budgets = {}
    first = Client(fake.url, TOKEN, budgets=budgets)
    second = Client(fake.url, TOKEN, budgets=budgets)
    other = Client(fake.url, 'wlu_other', budgets=budgets)
    assert second.budget() is None
    outcome = Outcome()

    first.test_connection(outcome.done, outcome.failed)
    outcome.wait()

    left, reset_in, limit = second.budget()
    assert (left, limit) == (49, 50) and 0 < reset_in <= 3600
    assert other.budget() is None


def test_the_fakes_rate_limit_answers_429_with_retry_after(fake):
    fake.rate_limit = 1
    _test(fake)
    fake.rate_window = 3600

    request = {'method': 'GET', 'path': '/api/projects/', 'query': {}, 'base': fake.url}
    request['headers'] = {'Authorization': 'Token ' + TOKEN}
    status, headers, body = fake.handle(request)

    assert status == 429
    assert int(headers['Retry-After']) > 0
    assert body['errors'][0]['code'] == 'throttled'
