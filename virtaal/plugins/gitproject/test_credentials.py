#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Where a Git project's https credentials come from: this session, the
system's password store (a fake keyring backend here, never the real
one), git's credential helper, then the user."""

import pytest

from devsupport.testing.gitremote import FakeStore
from virtaal.plugins.gitproject import credentials
from virtaal.support.gitrepo import Credential

keyring = pytest.importorskip('keyring')
from keyring.backend import KeyringBackend  # noqa: E402
from keyring.credentials import SimpleCredential  # noqa: E402

URL = 'https://me@example.org/team/program.git'
KEY = 'https://example.org'


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.passwords = {}

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def delete_password(self, service, username):
        self.passwords.pop((service, username), None)

    def get_credential(self, service, username):
        for (kept_service, user), password in self.passwords.items():
            if kept_service == service and username in (None, user):
                return SimpleCredential(user, password)
        return None


def test_the_password_store_keeps_the_username_with_the_password():
    backend = MemoryKeyring()
    store = credentials.KeyringStore(backend)
    assert store.usable() and store.get(KEY) is None
    store.put(KEY, Credential('thandi', 'to:ken'))
    assert backend.passwords == {('Virtaal Git https://example.org', 'git'): 'thandi\nto:ken'}
    found = store.get(KEY)
    assert (found.username, found.password) == ('thandi', 'to:ken')


def test_git_credential_manager_s_entry_is_read_too():
    backend = MemoryKeyring()
    backend.set_password('git:https://example.org', 'thandi', 'gcm-token')
    found = credentials.KeyringStore(backend).get(KEY)
    assert (found.username, found.password) == ('thandi', 'gcm-token')


def test_a_store_that_fails_is_as_good_as_none():
    class Locked(MemoryKeyring):
        def get_password(self, service, username):
            raise RuntimeError('locked')

        def set_password(self, service, username, password):
            raise RuntimeError('locked')

    store = credentials.KeyringStore(Locked())
    assert store.get(KEY) is None
    store.put(KEY, Credential('thandi', 'token'))


def test_the_store_comes_before_the_helper_and_the_user():
    asked = []
    found = credentials.GitCredentials(
        FakeStore({KEY: Credential('a', '1')}),
        helper=lambda key: Credential('b', '2'),
        ask=lambda key, username, refused: (
            asked.append((username, refused)) or (Credential('c', '3') if not refused else None)
        ),
    )
    assert [c.username for c in found.candidates(URL)] == ['a', 'b', 'c']
    assert asked == [('me', False), ('c', True)]
    assert list(found.candidates('git@example.org:team/program.git')) == []


def test_an_accepted_credential_is_kept_for_the_session_and_stored_if_asked():
    store = FakeStore()
    found = credentials.GitCredentials(store, helper=None)
    found.accepted(URL, Credential('thandi', 'token'))
    assert found.known(URL).password == 'token' and store.kept == {}
    found.refused(URL, found.known(URL))
    assert found.known(URL) is None
    found.accepted(URL, Credential('thandi', 'token', remember=True))
    assert store.kept[KEY].password == 'token'


def test_without_git_the_helper_has_nothing(monkeypatch):
    monkeypatch.setattr(credentials.shutil, 'which', lambda name: None)
    assert credentials.from_git_helper(KEY) is None


def test_git_s_helper_answers_without_a_prompt(monkeypatch):
    ran = []

    def run(args, **options):
        ran.append((args, options['input'], options['env']['GIT_TERMINAL_PROMPT']))
        out = b'protocol=https\nhost=example.org\nusername=thandi\npassword=token\n'
        return credentials.subprocess.CompletedProcess(args, 0, out, b'')

    monkeypatch.setattr(credentials.shutil, 'which', lambda name: '/usr/bin/git')
    monkeypatch.setattr(credentials.subprocess, 'run', run)
    found = credentials.from_git_helper(KEY)
    assert (found.username, found.password) == ('thandi', 'token')
    assert ran == [
        (['/usr/bin/git', 'credential', 'fill'], b'protocol=https\nhost=example.org\n\n', '0')
    ]


def test_macos_s_git_stand_in_is_never_run(monkeypatch):
    ran = []

    def run(args, **options):
        ran.append(args[0])
        return credentials.subprocess.CompletedProcess(args, 2, b'', b'no developer tools')

    monkeypatch.setattr(credentials.sys, 'platform', 'darwin')
    monkeypatch.setattr(credentials.os.path, 'realpath', lambda path: path)
    monkeypatch.setattr(credentials.shutil, 'which', lambda name: '/usr/bin/git')
    monkeypatch.setattr(credentials.subprocess, 'run', run)
    assert credentials.from_git_helper(KEY) is None
    assert ran == ['xcode-select']
