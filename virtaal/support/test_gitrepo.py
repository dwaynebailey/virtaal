#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The git layer against a local bare repository, on disk and over
http on localhost: fetch and read without a working tree, a commit of
one file onto the tip, a push the remote refuses once the branch moved
on, signing in, errors sorted by kind, and git's configuration read
without git."""

import os
import pathlib

import pytest

from devsupport.testing.gitremote import Remote, isolate_config, needs_dulwich, serve
from virtaal.support import gitrepo

PO = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'

pytestmark = needs_dulwich


@pytest.fixture
def remote(tmp_path):
    return Remote(tmp_path / 'team', {'po/af.po': PO, 'po/de.po': PO, 'src/main.c': 'int main;\n'})


@pytest.fixture
def repo(tmp_path, remote):
    return gitrepo.Repository(remote.url, 'main', str(tmp_path / 'clone.git'))


class Offers(gitrepo.Credentials):
    """The credentials to try in turn, and what the server said."""

    def __init__(self, *offers):
        self.offers = list(offers)
        self.said = []

    def candidates(self, url):
        yield from self.offers

    def accepted(self, url, credential):
        self.said.append(('accepted', credential.password))

    def refused(self, url, credential):
        self.said.append(('refused', credential.password))


def test_errors_are_sorted_by_kind():
    assert gitrepo.classify('git@h: Permission denied (publickey).') == gitrepo.AUTH
    assert gitrepo.classify(' ! [rejected] main -> main (fetch first)') == gitrepo.REJECTED
    assert gitrepo.classify('ERROR: Repository not found.') == gitrepo.NOT_FOUND
    assert gitrepo.classify('ssh: Could not resolve hostname h') == gitrepo.NETWORK
    assert gitrepo.classify('something else') == gitrepo.FAILED


def test_no_dulwich_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(gitrepo, '_client', None)
    assert not gitrepo.available()
    with pytest.raises(gitrepo.GitError) as raised:
        gitrepo.Repository('/srv/x.git', 'main', str(tmp_path / 'c.git')).fetch()
    assert raised.value.kind == gitrepo.MISSING
    assert gitrepo.identity() == ('', '')


def test_fetch_reads_files_without_a_working_tree(repo, remote):
    tip = repo.fetch()
    assert repo.tip() == tip
    assert sorted(repo.files(tip)) == ['po/af.po', 'po/de.po', 'src/main.c']
    assert repo.read(tip, 'po/af.po') == PO.encode()
    assert repo.read(tip, 'po/xx.po') is None
    assert repo.read(tip, 'po') is None
    assert repo.read(tip, 'po/af.po/x') is None
    assert not os.path.exists(os.path.join(repo.folder, 'po'))


def test_a_commit_changes_only_its_file(repo, remote):
    tip = repo.fetch()
    commit = repo.commit_file(tip, 'po/af.po', b'new\n', 'Update af', ('Translator', 't@x.org'))
    repo.push(commit)
    assert remote.read('po/af.po') == 'new\n'
    assert remote.changed_files() == ['po/af.po']
    [last, start] = remote.commits()
    assert last.author == last.committer == b'Translator <t@x.org>'
    assert last.message == b'Update af'
    assert last.parents == [start.id]
    assert repo.tip() == commit


def test_a_push_after_the_branch_moved_on_is_rejected_not_forced(repo, remote):
    tip = repo.fetch()
    remote.commit({'po/de.po': PO + '\n'}, 'Someone else')
    commit = repo.commit_file(tip, 'po/af.po', b'mine\n', 'Update af', ('T', 't@x.org'))
    with pytest.raises(gitrepo.GitError) as raised:
        repo.push(commit)
    assert raised.value.kind == gitrepo.REJECTED
    assert remote.subjects()[0] == 'Someone else'
    again = repo.commit_file(repo.fetch(), 'po/af.po', b'mine\n', 'Update af', ('T', 't@x.org'))
    repo.push(again)
    assert remote.read('po/af.po') == 'mine\n'
    assert remote.read('po/de.po') == PO + '\n'


def test_a_file_url_works_as_a_path(tmp_path, remote):
    url = pathlib.Path(remote.url).as_uri()
    repo = gitrepo.Repository(url, 'main', str(tmp_path / 'f.git'))
    assert repo.has_branch()
    assert repo.read(repo.fetch(), 'po/af.po') == PO.encode()


def test_a_missing_branch_or_repository_is_not_found(tmp_path, remote):
    branch = gitrepo.Repository(remote.url, 'nope', str(tmp_path / 'a.git'))
    assert not branch.has_branch()
    with pytest.raises(gitrepo.GitError) as raised:
        branch.fetch()
    assert raised.value.kind == gitrepo.NOT_FOUND
    nowhere = gitrepo.Repository(str(tmp_path / 'none.git'), 'main', str(tmp_path / 'b.git'))
    with pytest.raises(gitrepo.GitError) as raised:
        nowhere.fetch()
    assert raised.value.kind == gitrepo.NOT_FOUND


def test_over_http_a_push_after_the_branch_moved_on_is_rejected(tmp_path, remote):
    with serve(remote) as url:
        repo = gitrepo.Repository(url, 'main', str(tmp_path / 'h.git'))
        tip = repo.fetch()
        repo.push(repo.commit_file(tip, 'po/af.po', b'one\n', 'Update af', ('T', 't@x.org')))
        assert remote.read('po/af.po') == 'one\n'
        remote.commit({'po/de.po': PO + '\n'}, 'Someone else')
        late = repo.commit_file(repo.tip(), 'po/af.po', b'two\n', 'Update af', ('T', 't@x.org'))
        with pytest.raises(gitrepo.GitError) as raised:
            repo.push(late)
        assert raised.value.kind == gitrepo.REJECTED
        assert remote.read('po/af.po') == 'one\n'


def test_a_server_wanting_a_password_gets_the_credentials_in_turn(tmp_path, remote):
    with serve(remote, ('thandi', 'token')) as url:
        repo = gitrepo.Repository(url, 'main', str(tmp_path / 'h.git'))
        with pytest.raises(gitrepo.GitError) as raised:
            repo.fetch()
        assert raised.value.kind == gitrepo.AUTH

        offers = Offers(
            gitrepo.Credential('thandi', 'wrong'), gitrepo.Credential('thandi', 'token')
        )
        repo.credentials = offers
        tip = repo.fetch()
        assert offers.said == [('refused', 'wrong'), ('accepted', 'token')]
        repo.push(repo.commit_file(tip, 'po/af.po', b'new\n', 'Update af', ('T', 't@x.org')))
        assert remote.read('po/af.po') == 'new\n'


def test_a_server_that_isn_t_there_is_a_network_error(tmp_path):
    with serve(Remote(tmp_path / 'gone', {'a': 'b'})) as url:
        pass
    with pytest.raises(gitrepo.GitError) as raised:
        gitrepo.Repository(url, 'main', str(tmp_path / 'n.git')).fetch()
    assert raised.value.kind == gitrepo.NETWORK


def test_the_author_comes_from_git_s_config_files(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    assert gitrepo.identity() == ('', '')
    isolate_config(monkeypatch, tmp_path, 'Thandi', 'thandi@example.org', xdg=True)
    assert gitrepo.identity() == ('Thandi', 'thandi@example.org')
    isolate_config(monkeypatch, tmp_path, 'Sipho')
    assert gitrepo.identity() == ('Sipho', 'thandi@example.org')


def test_credentials_are_kept_by_server():
    assert gitrepo.credential_key('https://Me@GitLab.com/team/x.git') == 'https://gitlab.com'
    assert gitrepo.credential_key('http://h:8080/x') == 'http://h:8080'
    assert gitrepo.credential_key('git@example.org:team/x.git') is None
    assert gitrepo.credential_key('/srv/x.git') is None


def test_ssh_never_prompts():
    args = gitrepo.ssh_arguments('example.org', b"git-upload-pack '/x.git'", 'git', 2222)
    assert args[:6] == ['ssh', '-x', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=60']
    assert args[-3:] == ['2222', 'git@example.org', b"git-upload-pack '/x.git'"]
    own = gitrepo.ssh_arguments('h', b'c', ssh_command='plink -batch')
    assert own == ['plink', '-batch', 'h', b'c']
    with pytest.raises(gitrepo.GitError):
        gitrepo.ssh_arguments('-oProxyCommand=x', b'c')
