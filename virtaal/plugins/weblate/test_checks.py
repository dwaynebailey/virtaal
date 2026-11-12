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
from virtaal.support import checkerspec, extrachecks
from virtaal.support.platforms import workcopy


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


# Flags steer Virtaal's checks #


@pytest.fixture
def opened(fake):
    """A work copy of demo_checks()'s Program in Afrikaans, open."""
    fake.add_unit('hello', 'program', 'af', 'Hello %(name)s, see {page}', 'Hallo %(name)s')
    copy = Copy(fake)
    workcopy.set_open({copy.copy.path: copy.copy})
    yield copy
    workcopy.set_open({})


def _checker():
    from translate.filters import checks as ttk_checks

    return extrachecks.with_extra_checks(ttk_checks.StandardChecker(), object())


def _failures(copy, source, target=None):
    if target is not None:
        copy.edit(source, target)
    return _checker().run_filters(copy.unit(source))


def test_weblates_check_runs_only_with_its_work_copies_open(opened):
    assert [c.name for c in extrachecks.checks_for(object())] == []
    extrachecks.EXTRA_CHECKS.append(checks.WeblateCheck)
    try:
        assert [c.name for c in extrachecks.checks_for(object())] == ['weblate']
        workcopy.set_open({})
        assert extrachecks.checks_for(object()) == []
    finally:
        extrachecks.EXTRA_CHECKS.remove(checks.WeblateCheck)


@pytest.fixture
def registered():
    extrachecks.EXTRA_CHECKS.append(checks.WeblateCheck)
    yield
    extrachecks.EXTRA_CHECKS.remove(checks.WeblateCheck)


def test_max_length_is_checked(opened, registered):
    assert 'maxlength' not in _failures(opened, 'Save')

    failures = _failures(opened, 'Save', 'Bewaar dit')

    assert failures['maxlength'] == 'Longer than 6 characters'


def test_an_ignore_flag_leaves_out_its_check(opened, registered):
    # File's flags ignore end-stop; Open's don't.
    assert 'endpunc' in _failures(opened, 'Open', 'Maak oop.')
    assert 'endpunc' not in _failures(opened, 'File', 'Lêer.')


def test_a_components_flags_apply_to_every_unit(opened, registered):
    opened.copy.record['check_flags'] = 'ignore-end-stop, max-length:3'

    failures = _failures(opened, 'Open', 'Maak oop.')

    assert 'endpunc' not in failures
    assert failures['maxlength'] == 'Longer than 3 characters'


@pytest.mark.parametrize(
    'flags, missing',
    [
        ('placeholders:"%(name)s":"{page}"', '{page}'),
        (r'placeholders:r"\{\w+\}"', '{page}'),
        ('placeholders:"%(name)s"', None),
        ('placeholders:"%(name)s":"{page}", ignore-placeholders', None),
    ],
)
def test_placeholders_are_checked(opened, registered, flags, missing):
    source = 'Hello %(name)s, see {page}'
    opened.copy.units[opened.index(source)]['flags'] = flags

    failures = _failures(opened, source)

    expected = 'Missing placeholders: %s' % missing if missing else None
    assert failures.get('placeholders') == expected


def test_a_check_failing_on_the_server_shows_while_the_text_is_its(opened, registered):
    assert _failures(opened, 'Print')['weblate'] == 'Weblate reports failing checks: Unchanged'
    assert _failures(opened, 'Close')['weblate'] == 'Weblate reports a failing check'
    assert 'weblate' not in _failures(opened, 'Help')

    # Edited here: what the server said is of the old text.
    assert 'weblate' not in _failures(opened, 'Print', 'Druk dit')


def test_a_file_read_for_the_stats_cache_is_checked_too(opened, registered):
    from translate.storage import po

    store = po.pofile.parsefile(opened.copy.path)
    unit = next(u for u in store.units if str(u.source) == 'Print')

    assert _checker().run_filters(unit)['weblate'] == 'Weblate reports failing checks: Unchanged'


def test_what_it_checks_with_is_in_the_checkers_config(opened, registered):
    before = _checker().config.extra_checks

    opened.copy.units[opened.index('Help')]['failing'] = True

    after = _checker().config.extra_checks
    assert before != after and after[0].startswith('weblate@')
    # So the stats cache checks again, here: no child builds it.
    assert checkerspec.describe(_checker()) is None


def test_the_enforced_checks_are_virtaals(opened):
    platform = backend.WeblatePlatform.__new__(backend.WeblatePlatform)

    assert platform.enforced(opened.copy.record) == {'unchanged', 'maxlength'}
    assert checks.virtaal_names('end_stop') == ('endpunc',)


# Read again #


def test_update_reads_again_which_units_fail_checks(fake):
    from virtaal.plugins.weblate.test_update import Updating

    copy = Updating(fake)
    fake.fail_check(copy.server_unit('Help')['id'], 'same')
    print_id = copy.server_unit('Print')['id']
    fake.failing.pop(print_id)
    fake.units[print_id]['has_failing_check'] = False

    result = copy.update()

    assert result.stop is None
    assert (copy.entry('Help')['failing'], copy.entry('Help')['checks']) == (True, ['same'])
    assert (copy.entry('Print')['failing'], copy.entry('Print')['checks']) == (False, [])
    assert workcopy.for_file(copy.copy.path).record == copy.copy.record


def test_an_update_that_cant_read_the_checks_keeps_them(fake, monkeypatch):
    from virtaal.plugins.weblate.test_update import Updating

    copy = Updating(fake)
    original = fake._search
    monkeypatch.setattr(
        fake, '_search', lambda q, units: None if 'check' in q else original(q, units)
    )

    result = copy.update()

    assert result.stop is None and copy.entry('Print')['checks'] == ['same']


def test_submit_reads_whether_the_units_sent_fail_checks(fake):
    copy = Copy(fake)
    copy.edit('Print', 'Druk!', StateEnum.UNREVIEWED)
    copy.save()

    copy.submit()

    # The fake's rule: any other text fixes it.
    assert copy.entry('Print')['failing'] is False and copy.entry('Print')['checks'] == []
