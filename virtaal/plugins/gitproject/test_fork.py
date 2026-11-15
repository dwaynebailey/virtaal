#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Submit as a pull request from the user's fork, against the fake
GitHub (devsupport/testing/fakeforge.py): which repositories are
GitHub's, the push-permission check, the fork made, waited for and
reused, one commit a pull request made again on each Submit, a commit
added for a review or when asked, a fork branch someone else pushed to,
a merged pull request starting again, the notices, rate limits, the
token kept only in the password store, and Update still reading the
project. Nothing is ever pushed to the project."""

import pytest
from translate.storage import po

from devsupport.testing import fakeforge
from devsupport.testing.fakeforge import MAINTAINER_TOKEN, TOKEN, USER, FakeForge
from devsupport.testing.gitremote import FakeStore, isolate_config, needs_dulwich
from virtaal.common import pan_app
from virtaal.plugins.gitproject import backend, forge, fork
from virtaal.plugins.gitproject.credentials import GitCredentials
from virtaal.plugins.gitproject.test_backend import AF, Copy, call, po_file
from virtaal.support.gitrepo import Credential
from virtaal.support.platforms import AUTH, HTTP, SubmitPlan, submit

PROJECT = 'team/program'
FORK = '%s/program' % USER


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(pan_app, 'ui_language', 'en')
    monkeypatch.setitem(pan_app.settings.translator, 'name', 'Thandi')
    monkeypatch.setitem(pan_app.settings.translator, 'email', 'thandi@example.org')
    isolate_config(monkeypatch, tmp_path / 'home')
    monkeypatch.setattr(forge.GitHub, 'FORK_WAIT', (0.01,) * 6)


class TokenKeyring:
    """A keyring backend in memory."""

    def __init__(self, kept=None):
        self.kept = dict(kept or {})

    def get_password(self, service, account):
        return self.kept.get((service, account))

    def set_password(self, service, account, secret):
        self.kept[(service, account)] = secret


class Keyring:
    def __init__(self, backend):
        self._backend = backend

    def usable(self):
        return True

    def backend(self):
        return self._backend


@pytest.fixture
def github(tmp_path):
    fake = FakeForge(tmp_path / 'github')
    fake.add_repository(
        PROJECT,
        {'po/af.po': po_file('af', AF), 'po/de.po': po_file('de', [('Open', 'Öffnen', False)])},
        pushers=[fakeforge.MAINTAINER],
    )
    with fake:
        yield fake
    assert not [p for p in fake.pushes if p[0] == PROJECT and p[4]], 'forced on the project'


def platform_for(tmp_path, github, token=TOKEN, keyring=None, fork_setting=''):
    found = backend.GitPlatform(str(tmp_path / 'gitprojects.ini'), str(tmp_path / 'clones'))
    found.credentials = GitCredentials(FakeStore(), helper=None)
    found.tokens = forge.Tokens(forge.TokenStore(Keyring(keyring or TokenKeyring())))
    if token:
        found.tokens.session[github.host] = token
    found.projects.github_hosts = {github.host: github.api}
    settings = {'branch': 'main', 'files': 'po/*.po'}
    if fork_setting:
        settings['fork'] = fork_setting
    found.projects.projects = [(github.clone_url(PROJECT), settings)]
    found.projects.save()
    return found


class Remote:
    """The project on the fake GitHub, as test_backend's Copy wants it."""

    def __init__(self, github):
        self.github = github
        self.url = github.clone_url(PROJECT)

    def read(self, path, ref='refs/heads/main'):
        return self.github.read(PROJECT, ref, path).decode('utf-8')


@pytest.fixture
def platform(tmp_path, github):
    return platform_for(tmp_path, github)


@pytest.fixture
def copy(platform, github):
    return Copy(platform, Remote(github))


def branch_commits(github, ref='refs/heads/virtaal/af'):
    """The fork branch's commits since the project's tip, newest first."""
    tip = github.refs(PROJECT)['refs/heads/main']
    return github.subjects(FORK, ref, tip)


def unit_of(text, source):
    store = po.pofile(text.encode('utf-8'))
    return next(u for u in store.units if str(u.source) == source)


def fork_pushes(github):
    return [p for p in github.pushes if p[0] == FORK]


# Which repositories are GitHub's #


def test_github_and_configured_hosts_are_found_from_the_url():
    for url in (
        'https://github.com/team/program.git',
        'https://github.com/team/program',
        'git@github.com:team/program.git',
        'ssh://git@github.com/team/program.git',
    ):
        found = forge.find(url)
        assert (found.host, found.api, found.full_name) == (
            'github.com',
            forge.API,
            'team/program',
        ), url
    hosts = {'git.example.org': 'https://git.example.org/api/v3'}
    enterprise = forge.find('https://git.example.org/team/program.git', hosts)
    assert enterprise.api == 'https://git.example.org/api/v3'
    assert forge.find('https://gitlab.com/team/program.git', hosts) is None
    assert forge.find('https://github.com/team', hosts) is None
    assert forge.find('/srv/git/program.git') is None
    # A token never goes over plain http but to this computer.
    assert forge.find('https://h.example/a/b', {'h.example': 'http://h.example/api'}) is None


def test_a_github_project_s_fork_option_is_on_unless_turned_off(tmp_path, github):
    platform = platform_for(tmp_path, github)
    assert platform.default_credentials['fork'] is True
    servers, _last = platform.load_servers()
    assert servers[0][1]['fork'] is True
    platform.save_servers(servers, '')
    assert 'fork' not in platform.projects.settings(servers[0][0])
    url, credentials = servers[0]
    platform.save_servers([(url, dict(credentials, fork=False))], '')
    assert platform.projects.settings(url)['fork'] == 'no'
    assert 'fork = no' in (tmp_path / 'gitprojects.ini').read_text(encoding='utf-8')
    assert platform.load_servers()[0][0][1]['fork'] is False


def maintainer_platform(tmp_path, github, fork_setting=''):
    platform = platform_for(tmp_path, github, token=MAINTAINER_TOKEN, fork_setting=fork_setting)
    platform.credentials = GitCredentials(
        FakeStore({'http://' + github.host: Credential('maintainer', MAINTAINER_TOKEN)}),
        helper=None,
    )
    return platform


# Submit through the fork #


@needs_dulwich
def test_a_project_set_to_push_directly_is_pushed_to(tmp_path, github):
    copy = Copy(maintainer_platform(tmp_path, github, 'no'), Remote(github))
    copy.edit('Save', 'Stoor')
    copy.save()
    assert copy.submit().sent == 1
    assert github.subjects(PROJECT, 'refs/heads/main')[0] == 'Update af translation'
    assert not github.pulls and FORK not in github.repos


@needs_dulwich
def test_a_user_who_may_push_still_submits_from_a_fork(tmp_path, github):
    copy = Copy(maintainer_platform(tmp_path, github), Remote(github))
    copy.edit('Save', 'Stoor')
    copy.save()
    assert copy.submit().sent == 1
    assert github.subjects(PROJECT, 'refs/heads/main') == ['Start']
    assert [p['user'] for p in github.pulls] == [fakeforge.MAINTAINER]


@needs_dulwich
def test_without_a_token_submit_asks_for_one(tmp_path, github):
    platform = platform_for(tmp_path, github, token=None)
    asked = []

    def ask(host, refused):
        asked.append(host)
        return Credential('', TOKEN) if len(asked) > 1 else None

    platform.tokens.ask = ask
    copy = Copy(platform, Remote(github))
    copy.edit('Save', 'Stoor')
    copy.save()
    # None given: nothing goes anywhere.
    result = copy.submit()
    assert (result.stop, result.error.kind) == (submit.FAILED, AUTH)
    assert github.subjects(PROJECT, 'refs/heads/main') == ['Start'] and not github.pulls
    assert copy.submit().sent == 1
    assert asked == [github.host, github.host]
    assert len(github.pulls) == 1


@needs_dulwich
def test_submit_forks_waits_pushes_a_branch_and_opens_a_pull_request(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    result = copy.submit()
    assert (result.sent, result.stop) == (1, None)
    assert github.repos[FORK]['fork_of'] == PROJECT
    assert ('POST', '/repos/%s/forks' % PROJECT) in github.requests
    assert github.subjects(PROJECT, 'refs/heads/main') == ['Start']
    assert branch_commits(github) == ['Update Afrikaans (af) translation']
    assert unit_of(github.read(FORK, 'refs/heads/virtaal/af', 'po/af.po').decode(), 'Save').target
    [pull] = github.pulls
    assert (pull['head_ref'], pull['base_ref'], pull['user']) == ('virtaal/af', 'main', USER)
    assert pull['title'] == 'Update Afrikaans (af) translation'
    assert pull['body'] == (
        'Updates the Afrikaans (af) translation in po/af.po.\n\nSubmitted from Virtaal.\n'
    )


@needs_dulwich
def test_each_submit_makes_the_pull_request_s_one_commit_again(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    github.commit(PROJECT, {'README': 'Read me again\n'}, 'Maintainer')
    copy.edit('Quit', 'Verlaat')
    copy.save()
    assert copy.submit().sent == 1
    assert branch_commits(github) == ['Update Afrikaans (af) translation']
    text = github.read(FORK, 'refs/heads/virtaal/af', 'po/af.po').decode()
    assert [unit_of(text, s).target for s in ('Save', 'Quit')] == ['Stoor', 'Verlaat']
    assert len(github.pulls) == 1
    assert [p[4] for p in fork_pushes(github)] == [False, True]
    # The fork was made once.
    assert github.requests.count(('POST', '/repos/%s/forks' % PROJECT)) == 1
    assert github.subjects(PROJECT, 'refs/heads/main') == ['Maintainer', 'Start']


@needs_dulwich
def test_an_existing_fork_is_used(tmp_path, github):
    github.repos[PROJECT]['ready_in'] = 0
    fakeforge._create_fork(github, USER, {}, {}, PROJECT)
    copy = Copy(platform_for(tmp_path, github), Remote(github))
    copy.edit('Save', 'Stoor')
    copy.save()
    assert copy.submit().sent == 1
    assert ('POST', '/repos/%s/forks' % PROJECT) not in github.requests


@needs_dulwich
def test_a_fork_github_never_finishes_says_so(copy, github, monkeypatch):
    github.fork_delay = 100
    copy.edit('Save', 'Stoor')
    copy.save()
    result = copy.submit()
    assert (result.stop, result.error.kind) == (submit.FAILED, forge.FORK_TIMEOUT)
    assert 'finished making your fork' in copy.platform.error_message(result.error)


@needs_dulwich
def test_a_review_since_the_last_push_adds_a_commit(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    github.review(1)
    copy.edit('Quit', 'Verlaat')
    copy.save()
    session = copy.session()
    assert call_plan(session).text == 'Adds a commit for the review comments.'
    copy.submit(session=session)
    assert branch_commits(github) == [
        'Address review comments',
        'Update Afrikaans (af) translation',
    ]
    assert [p[4] for p in fork_pushes(github)] == [False, False]
    # The same review asks for no other commit: the next one updates it.
    copy.edit('Open', 'Maak oop')
    copy.save()
    copy.submit()
    assert branch_commits(github) == [
        'Address review comments',
        'Update Afrikaans (af) translation',
    ]
    text = github.read(FORK, 'refs/heads/virtaal/af', 'po/af.po').decode()
    assert unit_of(text, 'Open').target == 'Maak oop'


@needs_dulwich
def test_the_user_s_own_comments_are_no_review(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    github.review(1, 'note', login=USER)
    copy.edit('Quit', 'Verlaat')
    copy.save()
    copy.submit()
    assert len(branch_commits(github)) == 1


@needs_dulwich
def test_a_separate_commit_when_asked_with_its_summary(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    copy.edit('Quit', 'Verlaat')
    copy.save()
    session = copy.session()
    plan = call_plan(session)
    assert (plan.text, plan.option, plan.option_on) == (
        'Updates your pull request’s commit.',
        'Add as a _separate commit',
        False,
    )
    session.plan_chosen(True, 'Batch of menu strings')
    copy.submit(session=session)
    assert branch_commits(github) == ['Batch of menu strings', 'Update Afrikaans (af) translation']
    # For that Submit alone.
    assert call_plan(session).option_on is False
    assert 'separate' not in copy.platform.projects.settings(copy.remote.url)
    copy.edit('Open', 'Maak oop')
    copy.save()
    copy.submit(session=session)
    assert branch_commits(github) == ['Batch of menu strings', 'Update Afrikaans (af) translation']


@needs_dulwich
def test_a_fork_branch_someone_else_pushed_to_is_left_alone(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    github.commit(FORK, {'NOTES': 'mine\n'}, 'Someone else', 'virtaal/af')
    moved = github.refs(FORK)['refs/heads/virtaal/af']
    copy.edit('Quit', 'Verlaat')
    copy.save()
    result = copy.submit()
    assert (result.stop, result.error.kind) == (submit.FAILED, forge.MOVED)
    assert github.refs(FORK)['refs/heads/virtaal/af'] == moved
    assert 'virtaal/af' in copy.platform.error_message(result.error)


@needs_dulwich
def test_a_merged_pull_request_starts_a_new_one(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    github.merge(1)
    assert unit_of(Remote(github).read('po/af.po'), 'Save').target == 'Stoor'
    copy.edit('Quit', 'Verlaat')
    copy.save()
    assert call_plan(copy.session()).text == (
        'Submit pushes to your fork and opens a pull request.'
    )
    copy.submit()
    assert [p['state'] for p in github.pulls] == ['closed', 'open']
    assert branch_commits(github) == ['Update Afrikaans (af) translation']
    text = github.read(FORK, 'refs/heads/virtaal/af', 'po/af.po').decode()
    assert unit_of(text, 'Quit').target == 'Verlaat'


@needs_dulwich
def test_the_pull_request_s_state_is_a_notice(copy, github):
    session = copy.session()
    assert call_notice(session) is None
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    text, url = call_notice(copy.session())
    assert text == 'Your pull request #1 is open.'
    assert url == '%s/%s/pull/1' % (github.base, PROJECT)
    github.merge(1)
    assert call_notice(copy.session())[0] == 'Your pull request #1 was merged.'


@needs_dulwich
def test_a_closed_pull_request_is_a_notice_and_the_next_submit_opens_another(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    github.close(1)
    assert call_notice(copy.session())[0] == 'Your pull request #1 was closed.'
    copy.edit('Quit', 'Verlaat')
    copy.save()
    copy.submit()
    assert [p['state'] for p in github.pulls] == ['closed', 'open']
    text = github.read(FORK, 'refs/heads/virtaal/af', 'po/af.po').decode()
    assert [unit_of(text, s).target for s in ('Save', 'Quit')] == ['Stoor', 'Verlaat']
    # The description counts nothing, so the new one says the same.
    first, second = (p['body'] for p in github.pulls)
    assert first == second and not any(c.isdigit() for c in first)


@needs_dulwich
def test_no_notice_without_a_token_and_no_one_asked(tmp_path, github, copy):
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    platform = platform_for(tmp_path, github, token=None)
    asked = []
    platform.tokens.ask = lambda host, refused: asked.append(host)
    assert call_notice(Copy(platform, Remote(github)).session()) is None
    assert asked == []


@needs_dulwich
def test_update_reads_the_project_and_keeps_the_pull_request_s_units(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    text = Remote(github).read('po/af.po').replace('msgstr "Oop"', 'msgstr "Open maak"')
    github.commit(PROJECT, {'po/af.po': text}, 'Lindiwe')
    result = copy.update()
    assert (result.taken, copy.conflicts) == (1, [])
    assert str(copy.unit('Open').target) == 'Open maak'
    assert str(copy.unit('Save').target) == 'Stoor'


def both_changed(copy, github):
    """The pull request has Open as "Maak oop"; then the project's branch
    has it as "Open maak". The index of Open in the side record."""
    copy.edit('Open', 'Maak oop')
    copy.save()
    copy.submit()
    text = Remote(github).read('po/af.po').replace('msgstr "Oop"', 'msgstr "Open maak"')
    github.commit(PROJECT, {'po/af.po': text}, 'Lindiwe')
    return next(r for r, u in enumerate(copy.copy.units) if u['id'].endswith('\nOpen'))


def fork_unit(github, source):
    return str(
        unit_of(github.read(FORK, 'refs/heads/virtaal/af', 'po/af.po').decode(), source).target
    )


@needs_dulwich
def test_update_asks_about_a_unit_the_project_changed_in_the_pull_request(copy, github):
    r = both_changed(copy, github)
    result = copy.update()
    assert [tuple(c[:4]) for c in copy.conflicts] == [(r, 'Open', ['Maak oop'], ['Open maak'])]
    assert result.skipped == 1
    # Skipped, it comes up again, and the pull request keeps its text.
    copy.update()
    assert len(copy.conflicts) == 1
    assert fork_unit(github, 'Open') == 'Maak oop'


@needs_dulwich
def test_keep_mine_keeps_the_pull_request_s_text(copy, github):
    r = both_changed(copy, github)
    copy.update({r: submit.KEEP_MINE})
    copy.save()
    assert str(copy.unit('Open').target) == 'Maak oop'
    assert copy.copy.unsubmitted() == 1
    copy.edit('Quit', 'Verlaat')
    copy.save()
    assert copy.submit().sent == 2
    assert fork_unit(github, 'Open') == 'Maak oop'
    assert fork_unit(github, 'Quit') == 'Verlaat'
    # Answered: asked no more, until the project changes it again.
    assert copy.update().changed is False and copy.conflicts == []
    text = Remote(github).read('po/af.po').replace('"Open maak"', '"Oopmaak"')
    github.commit(PROJECT, {'po/af.po': text}, 'Lindiwe')
    copy.update()
    assert [c.theirs for c in copy.conflicts] == [['Oopmaak']]


@needs_dulwich
def test_take_theirs_drops_the_unit_from_the_pull_request(copy, github):
    r = both_changed(copy, github)
    copy.update({r: submit.TAKE_THEIRS})
    copy.save()
    assert str(copy.unit('Open').target) == 'Open maak'
    assert fork_unit(github, 'Open') == 'Maak oop'
    # The next Submit takes it out of the pull request.
    assert copy.copy.unsubmitted() == 1
    copy.edit('Quit', 'Verlaat')
    copy.save()
    copy.submit()
    assert fork_unit(github, 'Open') == unit_of(Remote(github).read('po/af.po'), 'Open').target
    assert fork_unit(github, 'Quit') == 'Verlaat'
    assert copy.copy.unsubmitted() == 0
    copy.update()
    assert copy.conflicts == []
    assert str(copy.unit('Open').target) == 'Open maak'


@needs_dulwich
def test_a_submit_before_the_answer_keeps_the_question(copy, github):
    both_changed(copy, github)
    copy.edit('Quit', 'Verlaat')
    copy.save()
    copy.submit()
    assert fork_unit(github, 'Open') == 'Maak oop'
    copy.update()
    assert [c.mine for c in copy.conflicts] == [['Maak oop']]


@needs_dulwich
def test_a_file_opened_again_has_the_pull_request_s_text_and_the_question(copy, github):
    both_changed(copy, github)
    again = Copy(copy.platform, Remote(github))
    assert str(again.unit('Open').target) == 'Maak oop'
    again.update()
    assert [c.theirs for c in again.conflicts] == [['Open maak']]


# Rate limits and tokens #


def client_for(github, tokens=None, sleeps=None):
    found = forge.find(github.clone_url(PROJECT), {github.host: github.api})
    if tokens is None:
        tokens = forge.Tokens(forge.TokenStore(Keyring(TokenKeyring())))
        tokens.session[github.host] = TOKEN
    client = forge.GitHub(found, tokens)
    client.sleep = (sleeps if sleeps is not None else []).append
    return client


def test_a_secondary_rate_limit_is_waited_out(github):
    sleeps = []
    client = client_for(github, sleeps=sleeps)
    github.throttle(3)
    assert client.login() == USER
    assert sleeps == [3.0]
    budget = client.budget()
    assert (budget.limit, budget.left) == (fakeforge.LIMIT, fakeforge.LIMIT - 1)


def test_a_used_up_rate_limit_fails_saying_until_when(github):
    github.limit(0, reset_in=1800)
    client = client_for(github)
    with pytest.raises(forge.ForgeError) as raised:
        client.login()
    assert (raised.value.error.kind, raised.value.error.status) == (HTTP, 429)
    assert 1700 < client.budget().reset_in <= 1800
    assert client.budget().left == 0


@needs_dulwich
def test_submit_out_of_budget_stops_resumable(copy, github):
    copy.edit('Save', 'Stoor')
    copy.save()
    github.limit(0, reset_in=1800)
    result = copy.submit()
    assert result.stop == submit.BUDGET
    assert 1700 < result.reset_in <= 1800
    assert copy.copy.unsubmitted() == 1


def test_a_pull_request_already_open_is_used(github):
    fakeforge._create_fork(github, USER, {}, {}, PROJECT)
    github.repos[FORK]['ready_in'] = 0
    github.commit(FORK, {'x': 'y'}, 'Branch', 'virtaal/af')
    client = client_for(github)
    first = client.create_pull('%s:virtaal/af' % USER, 'main', 'Title', 'Body')
    again = client.create_pull('%s:virtaal/af' % USER, 'main', 'Title', 'Body')
    assert first['number'] == again['number'] == 1


def test_the_token_is_kept_only_in_the_password_store(tmp_path, github):
    keyring = TokenKeyring()
    tokens = forge.Tokens(forge.TokenStore(Keyring(keyring)))
    tokens.ask = lambda host, refused: Credential('', TOKEN, remember=True)
    assert client_for(github, tokens).login() == USER
    assert keyring.kept == {('Virtaal GitHub %s' % github.host, 'token'): TOKEN}
    # A session of its own finds it there.
    again = forge.Tokens(forge.TokenStore(Keyring(keyring)))
    assert client_for(github, again).login() == USER
    for path in tmp_path.rglob('*'):
        if path.is_file() and path.suffix in ('.ini', '.json'):
            assert TOKEN not in path.read_text(encoding='utf-8', errors='replace'), path


def test_a_refused_token_asks_again(github):
    keyring = TokenKeyring({('Virtaal GitHub %s' % github.host, 'token'): 'ghp_expired'})
    tokens = forge.Tokens(forge.TokenStore(Keyring(keyring)))
    asked = []

    def ask(host, refused):
        asked.append((host, refused))
        return Credential('', TOKEN, remember=True)

    tokens.ask = ask
    assert client_for(github, tokens).login() == USER
    assert asked == [(github.host, True)]
    assert keyring.kept[('Virtaal GitHub %s' % github.host, 'token')] == TOKEN


def test_no_token_given_is_a_sign_in_failure(github):
    tokens = forge.Tokens(forge.TokenStore(Keyring(TokenKeyring())))
    tokens.ask = lambda host, refused: None
    with pytest.raises(forge.ForgeError) as raised:
        client_for(github, tokens).login()
    assert raised.value.error.kind == AUTH


def test_pr_messages_count_what_changed():
    assert fork.summary(1, 0) == '1 string translated'
    assert fork.summary(3, 2) == '3 strings translated, 2 updated'
    assert fork.summary(0, 1) == '1 string updated'
    assert fork.language_name('af') == 'Afrikaans'
    before = po.pofile(po_file('af', AF).encode())
    after = po.pofile(po_file('af', AF).encode())
    next(u for u in after.units if str(u.source) == 'Save').target = 'Stoor'
    next(u for u in after.units if str(u.source) == 'Open').target = 'Maak oop'
    assert fork.counts(before, after) == (1, 1)


def call_plan(session):
    return call(session.submit_plan, 'team', None, 'af')


def call_notice(session):
    return call(session.notice, 'team', None, 'af')


def test_a_plan_is_none_for_a_project_not_on_github(tmp_path):
    platform = backend.GitPlatform(str(tmp_path / 'gitprojects.ini'), str(tmp_path / 'clones'))
    session = platform.session('/srv/git/program.git', {'branch': 'main', 'files': 'po/*.po'})
    assert call(session.submit_plan, 'p', None, 'af') is None
    assert call(session.notice, 'p', None, 'af') is None
    assert isinstance(backend.plan_of(fork.FRESH, False), SubmitPlan)
