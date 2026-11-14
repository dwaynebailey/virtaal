#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The git layer against a local bare repository: fetch and read without
a working tree, a commit of one file onto the tip, a push the remote
refuses once the branch moved on, and git's errors sorted by kind."""

import os

import pytest

from devsupport.testing.gitremote import Remote, needs_git
from virtaal.support import gitrepo

PO = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'


@pytest.fixture
def remote(tmp_path):
    return Remote(tmp_path, {'po/af.po': PO, 'po/de.po': PO, 'src/main.c': 'int main;\n'})


@pytest.fixture
def repo(tmp_path, remote):
    return gitrepo.Repository(remote.url, 'main', str(tmp_path / 'clone.git'))


def test_parse_version():
    assert gitrepo.parse_version('git version 2.48.1') == (2, 48)
    assert gitrepo.parse_version('git version 2.39.5 (Apple Git-154)') == (2, 39)
    assert gitrepo.parse_version('nonsense') is None


def test_errors_are_sorted_by_kind():
    assert gitrepo.classify('fatal: Authentication failed for x') == gitrepo.AUTH
    assert gitrepo.classify('git@h: Permission denied (publickey).') == gitrepo.AUTH
    assert gitrepo.classify(' ! [rejected] main -> main (fetch first)') == gitrepo.REJECTED
    assert gitrepo.classify("fatal: couldn't find remote ref refs/heads/x") == gitrepo.NOT_FOUND
    assert gitrepo.classify('fatal: Could not resolve host: h') == gitrepo.NETWORK
    assert gitrepo.classify('something else') == gitrepo.FAILED


def test_no_git_is_missing(monkeypatch):
    monkeypatch.setattr(gitrepo, 'executable', lambda: None)
    assert not gitrepo.available()
    with pytest.raises(gitrepo.GitError) as raised:
        gitrepo.run(['--version'])
    assert raised.value.kind == gitrepo.MISSING


def test_an_old_git_is_missing(monkeypatch):
    monkeypatch.setattr(gitrepo, 'version', lambda: (2, 11))
    assert not gitrepo.available()


@needs_git
def test_fetch_reads_files_without_a_working_tree(repo, remote):
    tip = repo.fetch()
    assert sorted(repo.files(tip)) == ['po/af.po', 'po/de.po', 'src/main.c']
    assert repo.read(tip, 'po/af.po') == PO.encode()
    assert repo.read(tip, 'po/xx.po') is None
    assert repo.read(tip, 'po') is None
    assert not os.path.exists(os.path.join(repo.folder, 'po'))


@needs_git
def test_a_commit_changes_only_its_file(repo, remote):
    tip = repo.fetch()
    commit = repo.commit_file(tip, 'po/af.po', b'new\n', 'Update af', ('Translator', 't@x.org'))
    repo.push(commit)
    assert remote.read('po/af.po') == 'new\n'
    assert remote.changed_files() == ['po/af.po']
    assert remote.log('-1', '--format=%an <%ae>|%s').strip() == 'Translator <t@x.org>|Update af'
    assert repo.tip() == commit


@needs_git
def test_a_push_after_the_branch_moved_on_is_rejected_not_forced(repo, remote):
    tip = repo.fetch()
    remote.commit({'po/de.po': PO + '\n'}, 'Someone else')
    commit = repo.commit_file(tip, 'po/af.po', b'mine\n', 'Update af', ('T', 't@x.org'))
    with pytest.raises(gitrepo.GitError) as raised:
        repo.push(commit)
    assert raised.value.kind == gitrepo.REJECTED
    assert remote.log('-1', '--format=%s').strip() == 'Someone else'
    again = repo.commit_file(repo.fetch(), 'po/af.po', b'mine\n', 'Update af', ('T', 't@x.org'))
    repo.push(again)
    assert remote.read('po/af.po') == 'mine\n'
    assert remote.read('po/de.po') == PO + '\n'


@needs_git
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


@needs_git
def test_the_origin_follows_the_url(tmp_path, remote):
    repo = gitrepo.Repository(str(tmp_path / 'old.git'), 'main', str(tmp_path / 'c.git'))
    repo.ensure()
    repo.url = remote.url
    assert repo.fetch()
