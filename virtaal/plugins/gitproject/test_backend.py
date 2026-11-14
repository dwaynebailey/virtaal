#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A Git project against a local bare repository standing in for a
team's remote: its languages, a language opened as a work copy, Submit
as one commit of that file alone, others' changes merged unit by unit
by Update and Submit, a push refused because the branch moved on, a
unit changed on the branch mid-Submit, the commit's author, and
signing in to a server wanting a password."""

from types import SimpleNamespace

import pytest
from translate.storage import po
from translate.storage.workflow import StateEnum

from devsupport.testing.fakeplatform import run_until
from devsupport.testing.gitremote import FakeStore, Remote, isolate_config, needs_dulwich, serve
from virtaal.common import pan_app
from virtaal.plugins.gitproject import backend
from virtaal.plugins.gitproject.config import ProjectFile
from virtaal.plugins.gitproject.credentials import GitCredentials
from virtaal.support import gitrepo
from virtaal.support.platforms import AUTH, PlatformError, submit, update, workcopy
from virtaal.support.platforms.submit import FAILED, KEEP_MINE, TAKE_THEIRS

HEADER = (
    'msgid ""\nmsgstr ""\n'
    '"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Language: %s\\n"\n'
    '"Last-Translator: Nobody\\n"\n'
)


def po_file(language, units):
    """A PO file of (source, target, fuzzy) units."""
    text = HEADER % language
    for source, target, fuzzy in units:
        text += '\n%smsgid "%s"\nmsgstr "%s"\n' % ('#, fuzzy\n' if fuzzy else '', source, target)
    return text


AF = [('Open', 'Oop', False), ('Save', '', False), ('Print', 'Druk', True), ('Quit', '', False)]


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(pan_app, 'ui_language', 'en')
    monkeypatch.setitem(pan_app.settings.translator, 'name', 'Thandi')
    monkeypatch.setitem(pan_app.settings.translator, 'email', 'thandi@example.org')
    isolate_config(monkeypatch, tmp_path / 'home')


@pytest.fixture
def remote(tmp_path):
    return Remote(
        tmp_path / 'team',
        {
            'po/af.po': po_file('af', AF),
            'po/de.po': po_file('de', [('Open', 'Öffnen', False)]),
            'po/program.pot': po_file('', [('Open', '', False)]),
            'README': 'Read me\n',
        },
    )


@pytest.fixture
def platform(tmp_path):
    found = backend.GitPlatform(str(tmp_path / 'gitprojects.ini'), str(tmp_path / 'clones'))
    found.credentials = GitCredentials(FakeStore(), helper=None)
    return found


def call(method, *args):
    """A session method's answer, or its PlatformError."""
    answers = []
    method(*args, answers.append, answers.append)
    run_until(lambda: answers, timeout=30)
    return answers[0]


class Copy:
    """A language of the project opened as a work copy, its file parsed
    and attached as Virtaal opens it."""

    def __init__(self, platform, remote, language='af'):
        self.platform = platform
        self.remote = remote
        download = call(self.session().download, 'team', None, language)
        self.copy = workcopy.create(platform, remote.url, 'team', None, language, download)
        self.open()

    def session(self):
        return self.platform.session(self.remote.url, {'branch': 'main', 'files': 'po/*.po'})

    def open(self):
        self.copy = workcopy.for_file(self.copy.path)
        self.store = po.pofile.parsefile(self.copy.path)
        self.units = [u for u in self.store.units if not u.isheader()]
        self.copy.attach(SimpleNamespace(get_units=lambda: self.units, pair=None))

    def unit(self, source):
        return next(u for u in self.units if str(u.source) == source)

    def edit(self, source, target, state=StateEnum.UNREVIEWED):
        unit = self.unit(source)
        unit.target = target
        unit.set_state_n(state)

    def save(self):
        self.store.save()
        self.copy.saved()

    def submit(self, decisions=None, session=None):
        done, self.conflicts = [], []

        def on_conflicts(found, resolve):
            self.conflicts.extend(found)
            resolve(decisions or {})

        submit.Submission(
            self.copy,
            self.platform,
            session or self.session(),
            lambda *args: None,
            on_conflicts,
            done.append,
        ).start()
        run_until(lambda: done, timeout=30)
        return done[0]

    def update(self, decisions=None):
        done, self.conflicts = [], []

        def on_conflicts(found, resolve):
            self.conflicts.extend(found)
            resolve(decisions or {})

        update.Update(
            self.copy,
            self.platform,
            self.session(),
            on_conflicts,
            lambda plan, proceed: proceed({}),
            done.append,
        ).start()
        run_until(lambda: done, timeout=30)
        return done[0]


def other(remote, changes, message='Other translator'):
    """Someone else's change to po/af.po, pushed: {source: (target, fuzzy)}."""
    remote.pull()
    units = [(source, *changes.get(source, (target, fuzzy))) for source, target, fuzzy in AF]
    remote.commit({'po/af.po': po_file('af', units)}, message)


def server_units(remote):
    store = po.pofile(remote.read('po/af.po').encode('utf-8'))
    return {str(u.source): (str(u.target), u.isfuzzy()) for u in store.units if not u.isheader()}


# The project's settings #


def test_branch_and_files_are_checked():
    assert backend.valid_branch('main') and backend.valid_branch('release/1.0')
    for bad in ('', 'two words', '-x', 'a..b', 'end/', 'x.lock', 'a:b', 'a~1'):
        assert not backend.valid_branch(bad), bad
    assert backend.valid_files('po/*.po') and backend.valid_files('locale/*/LC_MESSAGES/x.po')
    for bad in ('po/af.po', 'po/*/*.po', '/po/*.po', '../*.po', 'po\\*.po', 'po//*.po'):
        assert not backend.valid_files(bad), bad


def test_the_files_name_each_language():
    paths = ['po/af.po', 'po/pt_BR.po', 'po/program.pot', 'po/old/af.po', 'README']
    assert backend.language_paths(paths, 'po/*.po') == {'af': 'po/af.po', 'pt_BR': 'po/pt_BR.po'}
    assert backend.language_paths(['l/de/LC_MESSAGES/a.po'], 'l/*/LC_MESSAGES/a.po') == {
        'de': 'l/de/LC_MESSAGES/a.po'
    }


def test_addresses_git_takes_are_kept_as_typed(platform):
    for url in (
        'https://example.org/team/program.git',
        'ssh://git@example.org/team/program.git',
        'git@example.org:team/program.git',
        '/home/me/program.git',
        'file:///home/me/program.git',
    ):
        assert platform.normalise_url(' %s ' % url) == url
    for url in ('', 'program', '-uhoh', 'https://example.org/a b'):
        assert platform.normalise_url(url) is None
    assert platform.url_problem('http://example.org/x.git')
    assert platform.url_problem('https://example.org/x.git') is None
    assert platform.url_problem('http://127.0.0.1:8765/team.git') is None
    assert platform.url_problem('http://localhost/team.git') is None
    assert platform.url_problem('http://127.example.org/team.git')


def test_a_project_is_named_and_kept_apart_by_host_path_and_branch():
    assert backend.project_name('git@example.org:team/program.git') == 'program'
    assert backend.project_slug('git@example.org:team/program.git', 'main') == (
        'example.org/team/program/main'
    )
    assert backend.project_slug('https://me@example.org/team/program.git', 'stable') == (
        'example.org/team/program/stable'
    )
    assert backend.project_slug('/srv/program.git', 'main') == 'local/srv/program/main'


def test_the_credential_fields_say_what_is_wrong(platform):
    assert platform.credentials_problem({'branch': 'main', 'files': 'po/*.po'}) is None
    assert platform.credentials_problem({'branch': 'a b', 'files': 'po/*.po'}) == (
        'Git doesn’t allow this branch name.'
    )
    assert platform.credentials_problem({'branch': 'main', 'files': 'po/af.po'}) == (
        'Use one * for the language’s code, such as po/*.po.'
    )


def test_projects_are_kept_without_secrets(tmp_path, platform):
    platform.save_servers(
        [('git@example.org:team/program.git', {'branch': 'main', 'files': 'po/*.po'})],
        'git@example.org:team/program.git',
    )
    again = ProjectFile(platform.projects.path)
    assert again.projects == [
        ('git@example.org:team/program.git', {'branch': 'main', 'files': 'po/*.po'})
    ]
    assert again.last_url == 'git@example.org:team/program.git'
    text = (tmp_path / 'gitprojects.ini').read_text(encoding='utf-8')
    assert 'password' not in text and 'token' not in text


def test_git_s_errors_are_worded_for_git(platform):
    assert 'dulwich' in platform.error_message(PlatformError(backend.GIT_MISSING))
    message = platform.error_message(PlatformError(AUTH, detail='Permission denied (publickey).'))
    assert message.startswith('Virtaal couldn’t sign in')
    assert message.endswith('\nPermission denied (publickey).')
    assert 'Update from Server' in platform.error_message(PlatformError(backend.CHANGED))
    assert platform.error_message(PlatformError('network')) is None


# Browsing and opening #


@needs_dulwich
def test_the_project_lists_the_languages_its_files_have(platform, remote):
    session = backend.GitSession(platform, remote.url, 'main', 'po/*.po')
    assert call(session.test_connection) == 1
    pages = []
    session.projects(None, None, lambda items, _next: pages.append(items), pages.append)
    run_until(lambda: pages)
    [project] = pages[0]
    assert project.name == 'remote'
    assert [(i.slug, i.name) for i in call(session.languages, project.slug, None)] == [
        ('af', 'Afrikaans'),
        ('de', 'German'),
    ]


@needs_dulwich
def test_a_missing_branch_says_so(platform, remote):
    session = backend.GitSession(platform, remote.url, 'nope', 'po/*.po')
    error = call(session.test_connection)
    assert error.kind == 'not-platform'
    assert 'No Git repository' in platform.not_platform_message()


@needs_dulwich
def test_no_dulwich_says_so(platform, remote, monkeypatch):
    monkeypatch.setattr(gitrepo, '_client', None)
    error = call(backend.GitSession(platform, remote.url, 'main', 'po/*.po').test_connection)
    assert error.kind == backend.GIT_MISSING


@needs_dulwich
def test_opening_a_language_makes_a_work_copy_of_its_file(platform, remote):
    copy = Copy(platform, remote)
    assert copy.copy.record['platform'] == 'git'
    assert copy.copy.record['file'] == 'po/af.po'
    assert copy.copy.path.replace('\\', '/').endswith('/af/po/af.po')
    assert [u['id'] for u in copy.copy.units] == [
        'po/af.po\nOpen',
        'po/af.po\nSave',
        'po/af.po\nPrint',
        'po/af.po\nQuit',
    ]
    assert len(copy.copy.matched) == 4
    assert copy.unit('Print').isfuzzy()


# Submit #


@needs_dulwich
def test_submit_commits_only_the_language_s_file_as_the_translator(platform, remote):
    copy = Copy(platform, remote)
    copy.edit('Save', 'Stoor')
    copy.edit('Print', 'Druk')
    copy.save()

    result = copy.submit()

    assert (result.stop, result.sent, result.total) == (None, 2, 2)
    assert server_units(remote)['Save'] == ('Stoor', False)
    assert server_units(remote)['Print'] == ('Druk', False)
    assert remote.changed_files() == ['po/af.po']
    last = remote.commits()[0]
    assert last.author == b'Thandi <thandi@example.org>'
    assert last.message.split(b'\n')[0] == b'Update af translation'
    assert 'Last-Translator: Thandi <thandi@example.org>' in remote.read('po/af.po')
    assert remote.read('README') == 'Read me\n'
    assert copy.copy.pending() == []


@needs_dulwich
def test_nothing_changed_commits_nothing(platform, remote):
    copy = Copy(platform, remote)
    before = remote.tip()
    copy.edit('Open', 'Oop')
    copy.save()
    copy.submit()
    assert remote.tip() == before


@needs_dulwich
def test_others_changes_to_other_units_stay(platform, remote):
    copy = Copy(platform, remote)
    copy.edit('Save', 'Stoor')
    copy.save()
    other(remote, {'Quit': ('Verlaat', False)})

    result = copy.submit()

    assert (result.stop, copy.conflicts) == (None, [])
    assert server_units(remote)['Save'] == ('Stoor', False)
    assert server_units(remote)['Quit'] == ('Verlaat', False)


@needs_dulwich
def test_the_same_unit_changed_by_both_is_a_conflict(platform, remote):
    copy = Copy(platform, remote)
    copy.edit('Save', 'Stoor')
    copy.save()
    other(remote, {'Save': ('Bewaar', False)})

    result = copy.submit({})

    assert [(c.source, c.mine, c.theirs) for c in copy.conflicts] == [
        ('Save', ['Stoor'], ['Bewaar'])
    ]
    assert result.skipped == 1
    assert server_units(remote)['Save'] == ('Bewaar', False)

    copy.submit({copy.conflicts[0].index: KEEP_MINE})
    assert server_units(remote)['Save'] == ('Stoor', False)


@needs_dulwich
def test_a_push_refused_after_the_branch_moved_on_is_done_again(platform, remote, monkeypatch):
    copy = Copy(platform, remote)
    copy.edit('Save', 'Stoor')
    copy.save()
    push = gitrepo.Repository.push
    pushes = []

    def racing(repo, commit):
        if not pushes:
            other(remote, {'Quit': ('Verlaat', False)}, 'In between')
        pushes.append(commit)
        push(repo, commit)

    monkeypatch.setattr(gitrepo.Repository, 'push', racing)

    result = copy.submit()

    assert result.stop is None and len(pushes) == 2
    assert server_units(remote)['Save'] == ('Stoor', False)
    assert server_units(remote)['Quit'] == ('Verlaat', False)
    assert remote.subjects()[:2] == ['Update af translation', 'In between']


@needs_dulwich
def test_the_unit_changed_on_the_branch_mid_submit_stops_it(platform, remote, monkeypatch):
    copy = Copy(platform, remote)
    copy.edit('Save', 'Stoor')
    copy.save()
    push = gitrepo.Repository.push
    pushes = []

    def racing(repo, commit):
        if not pushes:
            other(remote, {'Save': ('Bewaar', False)}, 'In between')
        pushes.append(commit)
        push(repo, commit)

    monkeypatch.setattr(gitrepo.Repository, 'push', racing)

    result = copy.submit()

    assert result.stop == FAILED and result.error.kind == backend.CHANGED
    assert len(pushes) == 1
    assert server_units(remote)['Save'] == ('Bewaar', False)
    assert copy.copy.pending() != []


# Update #


@needs_dulwich
def test_update_takes_others_changes_and_asks_about_both_changed(platform, remote):
    copy = Copy(platform, remote)
    copy.edit('Save', 'Stoor')
    copy.save()
    other(remote, {'Save': ('Bewaar', False), 'Quit': ('Verlaat', False), 'Print': ('Druk', False)})

    result = copy.update({})

    assert [c.source for c in copy.conflicts] == ['Save']
    assert (result.taken, result.skipped) == (2, 1)
    assert str(copy.unit('Quit').target) == 'Verlaat'
    assert not copy.unit('Print').isfuzzy()
    assert str(copy.unit('Save').target) == 'Stoor'

    copy.update({copy.conflicts[0].index: TAKE_THEIRS})
    assert str(copy.unit('Save').target) == 'Bewaar'


@needs_dulwich
def test_update_downloads_again_when_the_strings_changed(platform, remote):
    copy = Copy(platform, remote)
    copy.edit('Save', 'Stoor')
    copy.save()
    remote.pull()
    remote.commit({'po/af.po': po_file('af', [*AF, ('Help', '', False)])}, 'New string')

    result = copy.update()

    assert result.downloaded is not None
    assert [u['id'].split('\n')[1] for u in result.downloaded.units][-1] == 'Help'
    assert [c.index for c in result.carried] == [1]


# The author and signing in #


@needs_dulwich
def test_git_s_own_identity_is_the_author_before_virtaal_s(tmp_path, platform, remote, monkeypatch):
    isolate_config(monkeypatch, tmp_path / 'home', 'Sipho', 'sipho@example.org')
    copy = Copy(platform, remote)
    copy.edit('Save', 'Stoor')
    copy.save()
    copy.submit()
    assert remote.commits()[0].author == b'Sipho <sipho@example.org>'
    assert 'Last-Translator: Thandi <thandi@example.org>' in remote.read('po/af.po')


def test_without_git_s_identity_or_a_translator_virtaal_signs(monkeypatch):
    monkeypatch.setitem(pan_app.settings.translator, 'name', '')
    monkeypatch.setitem(pan_app.settings.translator, 'email', '')
    assert backend._author() == ('Virtaal', '')


@needs_dulwich
def test_a_kept_credential_signs_in_without_asking(platform, remote):
    asked = []
    platform.credentials.ask = lambda *args: asked.append(args)
    with serve(remote, ('thandi', 'token')) as url:
        key = url.rpartition('/remote.git')[0]
        platform.credentials.store.kept[key] = gitrepo.Credential('thandi', 'token')
        assert call(backend.GitSession(platform, url, 'main', 'po/*.po').test_connection) == 1
    assert asked == []
    assert platform.credentials.session[key].password == 'token'


@needs_dulwich
def test_a_server_wanting_a_password_asks_and_may_keep_it(platform, remote):
    asked = []

    def ask(key, username, refused):
        asked.append((key, username, refused))
        password = 'wrong' if not refused else 'token'
        return gitrepo.Credential('thandi', password, remember=refused)

    platform.credentials.ask = ask
    with serve(remote, ('thandi', 'token')) as url:
        key = url.rpartition('/remote.git')[0]
        session = backend.GitSession(platform, url, 'main', 'po/*.po')
        assert call(session.test_connection) == 1
        assert asked == [(key, '', False), (key, 'thandi', True)]
        assert platform.credentials.store.kept[key].password == 'token'
        assert call(session.test_connection) == 1
        assert len(asked) == 2


@needs_dulwich
def test_a_cancelled_sign_in_says_so(platform, remote):
    platform.credentials.ask = lambda *args: None
    with serve(remote, ('thandi', 'token')) as url:
        error = call(backend.GitSession(platform, url, 'main', 'po/*.po').test_connection)
    assert error.kind == AUTH
    assert platform.error_message(error).startswith('Virtaal couldn’t sign in')
    assert platform.credentials.store.kept == {}
