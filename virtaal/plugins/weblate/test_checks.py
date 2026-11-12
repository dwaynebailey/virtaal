#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Weblate's checks and flags against the fake Weblate through the real
curl client: the fake's searches and enforced checks, flags and the
checks units fail read at download and again later, and an older
server without them."""

import pytest
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import TOKEN, demo_checks, run_until
from virtaal.common import pan_app
from virtaal.plugins.weblate import backend, checks
from virtaal.plugins.weblate.test_submit import Copy


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))


@pytest.fixture
def fake():
    server = demo_checks().start()
    yield server
    server.stop()
    assert server.schema_problems == []


def _client(fake):
    return backend.WeblatePlatform.client_factory(fake.url, TOKEN)


def _unit_id(fake, source):
    return next(u['id'] for u in fake.units.values() if u['source'] == [source])


def _search(fake, query):
    found = []
    done = []
    _client(fake).units(
        'hello',
        'program',
        'af',
        lambda results: found.extend(u['source'][0] for u in results),
        done.append,
        done.append,
        query=query,
    )
    run_until(lambda: done)
    return found


# The fake #


def test_the_fake_finds_units_failing_checks(fake):
    assert _search(fake, 'has:check') == ['Close', 'Print']
    assert _search(fake, 'check:same') == ['Print']
    assert _search(fake, 'check:max-length') == []


def test_the_fake_stores_needs_editing_for_a_failing_enforced_check(fake):
    copy = Copy(fake)
    copy.edit('Print', 'Print')
    copy.edit('Close', 'Maak toe!', StateEnum.UNREVIEWED)
    copy.save()

    copy.submit()

    # "same" is enforced, "end_stop" isn't.
    assert copy.server_unit('Print')['state'] == 10
    assert copy.server_unit('Close')['state'] == 20
    copy.edit('Print', 'Druk', StateEnum.UNREVIEWED)
    copy.save()
    copy.submit()
    print_unit = copy.server_unit('Print')
    assert print_unit['state'] == 20 and not print_unit['has_failing_check']


# Read at download #


def test_the_download_keeps_flags_and_the_checks_units_fail(fake):
    copy = Copy(fake)

    record = copy.copy.record
    assert record['enforced_checks'] == ['same', 'max-length']
    assert record['check_flags'] == ''
    assert copy.entry('Save')['flags'] == 'max-length:6'
    assert copy.entry('File')['flags'] == 'ignore-end-stop'
    # Print fails an enforced check, named; Close one that isn't.
    assert (copy.entry('Print')['failing'], copy.entry('Print')['checks']) == (True, ['same'])
    assert (copy.entry('Close')['failing'], copy.entry('Close')['checks']) == (True, [])
    assert (copy.entry('Help')['failing'], copy.entry('Help')['checks']) == (False, [])
    # One search for each enforced check.
    queries = [r['query'].get('q') for r in fake.requests_to('/api/translations/')]
    assert [q for q in queries if q] == ['check:same', 'check:max-length']


def test_a_components_flags_are_its_effective_ones(fake):
    info = fake.projects['hello']['components']['program']['component']
    info['check_flags'] = 'ignore-same'
    info['effective_check_flags'] = 'ignore-same, max-length:30'

    copy = Copy(fake)

    assert copy.copy.record['check_flags'] == 'ignore-same, max-length:30'


def test_a_read_only_flag_locks_the_unit(fake):
    fake.flag(_unit_id(fake, 'Help'), 'read-only')

    copy = Copy(fake)

    assert copy.entry('Help')['read_only']


def test_an_older_server_without_checks_downloads_quietly(fake):
    info = fake.projects['hello']['components']['program']['component']
    for name in ('effective_enforced_checks', 'enforced_checks', 'effective_check_flags'):
        info.pop(name, None)
    for unit in fake.units.values():
        unit.pop('has_failing_check')
    # The schema wants them; this server is older.
    fake.schema_problems = []

    copy = Copy(fake)
    fake.schema_problems = []

    assert copy.copy.record['enforced_checks'] == []
    assert copy.entry('Print')['failing'] is None and copy.entry('Print')['checks'] == []


def test_a_server_refusing_the_check_searches_downloads_quietly(fake, monkeypatch):
    # Searches answer 400, "Unknown search".
    monkeypatch.setattr(fake, '_search', lambda _query, _units: None)

    copy = Copy(fake)

    assert copy.entry('Print')['failing'] is True and copy.entry('Print')['checks'] == []


def test_the_checks_units_fail_are_asked_again(fake):
    session = Copy(fake).session()
    fake.fail_check(_unit_id(fake, 'Help'), 'max-length')
    found = []

    session.checks('hello', 'program', 'af', ['same', 'max-length'], found.append, found.append)
    run_until(lambda: found)

    ids = {source: _unit_id(fake, source) for source in ('Print', 'Close', 'Help')}
    assert found[0] == {
        ids['Print']: ('same',),
        ids['Close']: (),
        ids['Help']: ('max-length',),
    }


# Flags #


@pytest.mark.parametrize(
    'text, flags',
    [
        ('', []),
        ('python-format', [('python-format', [])]),
        ('max-length:40, ignore-end-stop', [('max-length', ['40']), ('ignore-end-stop', [])]),
        ('placeholders:"%(n)s":"a,b"', [('placeholders', ['%(n)s', 'a,b'])]),
        ('placeholders:"say \\"hi\\""', [('placeholders', ['say "hi"'])]),
        (',  ,read-only,', [('read-only', [])]),
    ],
)
def test_check_flags_are_read_as_weblate_writes_them(text, flags):
    assert checks.parse_flags(text) == flags


def test_a_regular_expression_parameter_keeps_its_backslashes():
    ((name, (param,)),) = checks.parse_flags(r'placeholders:r"\{\w+\}"')

    assert name == 'placeholders' and isinstance(param, checks.Regex)
    assert param == r'\{\w+\}'
