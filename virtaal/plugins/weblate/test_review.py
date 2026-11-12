#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Review against the fake Weblate through the real curl client:
approving with and without the right to, suggestions (read and
dismissed), comments (read, added, in their scopes), servers too old
for them, refusals, and keeping to the request budget."""

import pytest
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeweblate import TOKEN, demo, demo_review, run_until
from virtaal.common import pan_app
from virtaal.plugins.weblate import backend
from virtaal.plugins.weblate.test_submit import Copy
from virtaal.support.platforms import (
    ON_SOURCE,
    ON_TRANSLATION,
    REVIEW,
    SOURCE_REPORT,
    UNSUPPORTED,
    Budget,
    states,
)
from virtaal.support.platforms.review import RESERVE, Reviews


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))


def _serve(server):
    server.start()
    yield server
    server.stop()
    assert server.schema_problems == []


@pytest.fixture
def fake():
    yield from _serve(demo_review())


@pytest.fixture
def plain():
    """demo(): no suggestions or comments yet."""
    yield from _serve(demo())


def _session(fake, features=None):
    client = backend.WeblatePlatform.client_factory(fake.url, TOKEN)
    return backend.WeblateSession(client, features)


def _call(method, *args):
    """(done, value) of a Session method's callbacks."""
    found = []
    method(*args, lambda *value: found.append((True, value)), lambda e: found.append((False, e)))
    run_until(lambda: found)
    ok, value = found[0]
    return ok, value if not ok else (value[0] if len(value) == 1 else value)


def _unit_id(fake, source):
    return next(u['id'] for u in fake.units.values() if u['source'] == [source])


# Approving #


def test_the_download_tells_whether_the_project_reviews(fake):
    copy = Copy(fake)

    assert copy.copy.record['review'] is True and copy.copy.record['source_review'] is True
    assert copy.copy.may_review()
    assert states.reviewable(copy.unit('Save'))


def test_without_reviews_reviewed_is_not_offered(plain):
    plain.projects['hello']['project']['translation_review'] = False
    copy = Copy(plain)

    assert copy.copy.record['review'] is False
    assert not copy.copy.may_review()
    assert not states.reviewable(copy.unit('Save'))


def test_a_reviewer_approves_on_submit(fake):
    copy = Copy(fake)
    copy.edit('Save', state=StateEnum.FINAL)
    copy.save()

    result = copy.submit()

    assert result.sent == 1 and result.failures == {}
    assert fake.units[_unit_id(fake, 'Save')]['state'] == 30
    assert copy.copy.may_review()


def test_an_approval_refused_stops_reviewed_being_offered(fake):
    fake.revoke('unit.review')
    copy = Copy(fake)
    copy.edit('Save', state=StateEnum.FINAL)
    copy.edit('Help', 'Hulp')
    copy.save()

    result = copy.submit()

    # Reported per unit, the other unit sent.
    assert result.sent == 1
    assert result.failures == {copy.index('Save'): 'You do not have permission to approve strings.'}
    assert copy.copy.record['refused'] == [REVIEW]
    assert not states.reviewable(copy.unit('Save'))
    # Remembered with the work copy, after a restart too.
    copy.open()
    assert not copy.copy.may_review() and not states.reviewable(copy.unit('Help'))


def test_no_right_to_translate_is_not_a_review_refusal(fake):
    fake.revoke('unit.edit')
    copy = Copy(fake)
    copy.edit('Save', state=StateEnum.FINAL)
    copy.save()

    result = copy.submit()

    assert list(result.failures.values()) == ['You do not have permission to perform this action.']
    assert copy.copy.may_review()


# Flags, suggestions and comments read #


def test_the_units_with_suggestions_or_comments_are_found_in_one_query(fake):
    ok, (suggested, commented) = _call(_session(fake).flagged, 'hello', 'program', 'af')

    assert ok
    assert suggested == {_unit_id(fake, 'Save')}
    assert commented == {_unit_id(fake, 'Save'), _unit_id(fake, 'Print')}
    (request,) = fake.requests_to('/api/translations/hello/program/af/units/')
    assert request['query']['q'] == 'has:suggestion OR has:comment'


def test_the_download_keeps_flags_and_the_source_unit(fake):
    copy = Copy(fake)

    save = copy.entry('Save')
    assert save['has_suggestion'] and save['has_comment']
    assert save['source_id'] == fake._source_id(fake.units[save['id']])
    assert not copy.entry('Help')['has_comment']


def test_suggestions_are_read_with_who_made_them(fake):
    ok, found = _call(_session(fake).suggestions, _unit_id(fake, 'Save'))

    assert ok
    assert [(s.target, s.user) for s in found] == [(['Berg'], 'anna'), (['Bewaar'], 'ben')]


def test_comments_are_read_with_those_on_the_source_text(fake):
    unit = fake.units[_unit_id(fake, 'Print')]
    fake.comment(unit['id'], 'Druk is fine.', user='carl')

    ok, found = _call(_session(fake).comments, unit['id'], fake._source_id(unit))

    assert ok
    assert [(c.text, c.user) for c in found] == [
        ('Druk is fine.', 'carl'),
        ('Is this the verb or the noun?', 'ben'),
    ]


# Older servers #


@pytest.mark.parametrize('version', [(2026, 7), (5, 15), (5, 6)])
def test_a_server_before_2026_8_has_no_suggestions_and_asks_only_once(fake, version):
    fake.version = version
    missing = set()
    unit_id = _unit_id(fake, 'Save')

    ok, error = _call(_session(fake, missing).suggestions, unit_id)
    again, _error = _call(_session(fake, missing).suggestions, unit_id)

    assert not ok and error.kind == UNSUPPORTED and not again
    assert len(fake.requests_to('/api/units/%d/suggestions/' % unit_id)) == 1


def test_a_server_before_5_15_adds_comments_but_cant_list_them(fake):
    fake.version = (5, 14)
    missing = set()
    unit_id = _unit_id(fake, 'Help')

    listed, error = _call(_session(fake, missing).comments, unit_id, None)
    added, comment = _call(_session(fake, missing).add_comment, unit_id, 'Hulp?', ON_TRANSLATION)

    assert not listed and error.kind == UNSUPPORTED and error.status == 405
    assert added and (comment.text, comment.user) == ('Hulp?', 'tester')
    assert missing == {backend.COMMENTS_LISTED}


def test_a_server_before_5_12_has_no_comments_at_all(fake):
    fake.version = (5, 11)
    missing = set()
    unit_id = _unit_id(fake, 'Help')

    _call(_session(fake, missing).comments, unit_id, None)
    added, error = _call(_session(fake, missing).add_comment, unit_id, 'Hulp?', ON_TRANSLATION)

    assert not added and error.kind == UNSUPPORTED
    assert missing == {backend.COMMENTS_LISTED, backend.COMMENTS_ADDED}
    # Known: adding wasn't even asked.
    assert [r['method'] for r in fake.requests_to('/api/units/%d/comments/' % unit_id)] == ['GET']


def test_the_platform_shares_what_a_server_lacks_between_its_sessions(fake, tmp_path):
    fake.version = (5, 6)
    platform = backend.WeblatePlatform(str(tmp_path / 'weblate.ini'))
    unit_id = _unit_id(fake, 'Save')

    _call(platform.session(fake.url, {'token': TOKEN}).suggestions, unit_id)
    ok, error = _call(platform.session(fake.url, {'token': TOKEN}).suggestions, unit_id)

    assert not ok and error.kind == UNSUPPORTED
    assert len(fake.requests_to('/api/units/%d/suggestions/' % unit_id)) == 1


# Dismissing and commenting #


def test_a_reviewer_dismisses_a_suggestion(fake):
    session = _session(fake)
    unit_id = _unit_id(fake, 'Save')
    _ok, found = _call(session.suggestions, unit_id)

    ok, _value = _call(session.dismiss_suggestion, found[0].id)

    assert ok
    assert [s['target'] for s in fake.suggestions.values()] == [['Bewaar']]
    request = fake.requests_to('/api/suggestions/')[-1]
    assert request['method'] == 'DELETE'


def test_dismissing_others_suggestions_needs_the_right(fake):
    fake.revoke('suggestion.delete')
    session = _session(fake)
    _ok, found = _call(session.suggestions, _unit_id(fake, 'Save'))

    ok, error = _call(session.dismiss_suggestion, found[0].id)

    assert not ok and error.status == 403
    assert len(fake.suggestions) == 2


@pytest.mark.parametrize(
    'scope, sent, on_source',
    [
        (ON_TRANSLATION, 'translation', False),
        (ON_SOURCE, 'global', True),
        (SOURCE_REPORT, 'report', True),
    ],
)
def test_a_comments_scope_goes_as_weblates(fake, scope, sent, on_source):
    unit = fake.units[_unit_id(fake, 'Help')]

    ok, comment = _call(_session(fake).add_comment, unit['id'], 'Typo: Hlep', scope)

    assert ok and comment.text == 'Typo: Hlep'
    stored = fake.comments[comment.id]
    assert stored['scope'] == sent
    assert stored['unit_id'] == (fake._source_id(unit) if on_source else unit['id'])
    assert unit['has_comment']


def test_a_report_on_a_project_without_source_reviews_is_a_source_comment(plain, tmp_path):
    plain.projects['hello']['project']['source_review'] = False
    copy = Copy(plain)
    reviews = Reviews(lambda _copy: copy.platform, None)

    assert reviews.scope(copy.copy, about_source=True) == ON_SOURCE
    assert reviews.scope(copy.copy, about_source=False) == ON_TRANSLATION
    copy.copy.record['source_review'] = True
    assert reviews.scope(copy.copy, about_source=True) == SOURCE_REPORT


def test_commenting_needs_the_right(fake):
    fake.revoke('comment.add')

    ok, error = _call(_session(fake).add_comment, _unit_id(fake, 'Help'), 'x', ON_TRANSLATION)

    assert not ok and error.status == 403


# Read as each unit is shown #


class Shown:
    """Reviews of one Copy, its server asked through one session."""

    def __init__(self, copy):
        self.copy = copy
        self.session = copy.session()
        server = type('Server', (), {'session': lambda _self: self.session})()
        self.reviews = Reviews(lambda _copy: copy.platform, lambda _copy: server)

    def fetch(self, source):
        found = []
        self.reviews.fetch(self.copy.copy, self.copy.index(source), lambda *f: found.append(f))
        run_until(lambda: found)
        return found[0]


def _review_requests(fake):
    return [
        r
        for r in fake.requests
        if r['path'].endswith(('/suggestions/', '/comments/'))
        or r['query'].get('q', '').startswith('has:')
    ]


def test_only_a_flagged_unit_is_asked_and_what_it_said_is_kept(fake):
    shown = Shown(Copy(fake))

    suggestions, comments = shown.fetch('Save')
    shown.fetch('Save')
    nothing = shown.fetch('Help')

    assert [s.target for s in suggestions] == [['Berg'], ['Bewaar']]
    assert [c.text for c in comments] == ['Most apps say "Stoor" here.']
    assert nothing == ((), ())
    # The flags once, then Save's suggestions and comments, once.
    asked = [r['path'] for r in _review_requests(fake)]
    assert asked == [
        '/api/translations/hello/program/af/units/',
        '/api/units/%d/suggestions/' % _unit_id(fake, 'Save'),
        '/api/units/%d/comments/' % _unit_id(fake, 'Save'),
        '/api/units/%d/comments/' % shown.copy.entry('Save')['source_id'],
    ]


def test_a_suggestion_made_since_the_download_is_found(fake):
    copy = Copy(fake)
    fake.suggest(_unit_id(fake, 'Help'), 'Hulp', user='dan')

    suggestions, _comments = Shown(copy).fetch('Help')

    assert [(s.target, s.user) for s in suggestions] == [(['Hulp'], 'dan')]


def test_nothing_is_asked_when_the_budget_is_low(fake):
    shown = Shown(Copy(fake))
    shown.session.budget = lambda: Budget(RESERVE, 600, 5000)
    fake.requests.clear()

    assert shown.fetch('Save') == ((), ())
    assert _review_requests(fake) == []


def test_an_older_server_shows_no_suggestions_quietly(fake):
    fake.version = (5, 6)
    shown = Shown(Copy(fake))

    suggestions, comments = shown.fetch('Save')

    assert suggestions == () and comments == ()


def test_dismissing_and_commenting_update_what_is_known(fake):
    shown = Shown(Copy(fake))
    copy, r = shown.copy.copy, shown.copy.index('Save')
    suggestions, _comments = shown.fetch('Save')
    done = []

    shown.reviews.dismiss(copy, r, suggestions[0], lambda: done.append('dismissed'), done.append)
    run_until(lambda: done)
    shown.reviews.add_comment(copy, r, 'Bewaar is better.', False, done.append, done.append)
    run_until(lambda: len(done) == 2)

    suggestions, comments = shown.reviews.known(copy, r)
    assert [s.target for s in suggestions] == [['Bewaar']]
    assert [c.text for c in comments][-1] == 'Bewaar is better.'
    assert fake.comments[comments[-1].id]['scope'] == 'translation'


def test_a_server_too_old_to_add_comments_offers_it_no_more(fake):
    fake.version = (5, 11)
    shown = Shown(Copy(fake))
    copy, r = shown.copy.copy, shown.copy.index('Help')
    failed = []

    assert shown.reviews.can_comment(copy)
    shown.reviews.add_comment(copy, r, 'x', False, failed.append, failed.append)
    run_until(lambda: failed)

    assert failed[0].kind == UNSUPPORTED
    assert not shown.reviews.can_comment(copy)


def test_the_fake_accepts_a_suggestion_as_weblate_does(fake):
    client = backend.WeblatePlatform.client_factory(fake.url, TOKEN)
    unit_id = _unit_id(fake, 'Save')
    berg = next(s for s in fake.suggestions.values() if s['target'] == ['Berg'])
    found = []

    url = client.url('suggestions/%d/accept/' % berg['id'])
    client.send_json('POST', url, {'approve': True}, found.append, found.append)
    run_until(lambda: found)

    assert found == [{'result': 'accepted'}]
    assert fake.units[unit_id]['target'] == ['Berg'] and fake.units[unit_id]['state'] == 30
    assert berg['id'] not in fake.suggestions
