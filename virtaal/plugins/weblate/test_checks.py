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
from virtaal.plugins.weblate import backend
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
