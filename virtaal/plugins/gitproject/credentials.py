#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Where a Git project's https credentials come from, none kept by
Virtaal itself: this session's, then the system's password store
(keyring: Windows Credential Manager, the macOS Keychain, the Secret
Service), then git's credential helper if git is installed, then the
user, who may have the system's store keep them.

ssh needs none of this: the system's ssh uses its own keys and agent.
"""

import logging
import os
import shutil
import subprocess
import sys
from urllib import parse

from virtaal.support import gitrepo
from virtaal.support.gitrepo import Credential, credential_key

SERVICE = 'Virtaal Git %s'
"""The password store's entry for a server, by its credential key."""
ACCOUNT = 'git'
"""Its account; the secret is the username, a newline and the password,
since not every store can find an entry without its account."""
GCM_SERVICE = 'git:%s'
"""Git Credential Manager's entry (Git for Windows' helper)."""
HELPER_TIMEOUT = 120


class KeyringStore:
    """The system's password store, through keyring; usable() is False
    without one."""

    def __init__(self, backend=None):
        self._backend = backend

    def backend(self):
        if self._backend is None:
            try:
                import keyring
                from keyring.backends import fail
            except ImportError:
                return None
            try:
                found = keyring.get_keyring()
            except Exception as error:  # keyring's own, or a broken backend's
                logging.info("No password store: %s", error)
                return None
            if isinstance(found, fail.Keyring):
                return None
            self._backend = found
        return self._backend

    def usable(self):
        return self.backend() is not None

    def get(self, key):
        backend = self.backend()
        if backend is None:
            return None
        try:
            secret = backend.get_password(SERVICE % key, ACCOUNT)
            if secret and '\n' in secret:
                username, password = secret.split('\n', 1)
                return Credential(username, password)
            found = backend.get_credential(GCM_SERVICE % key, None)
        except Exception as error:  # A locked or refused store: as if it had none.
            logging.info("The password store can't be read: %s", error)
            return None
        if found is not None and found.username and found.password:
            return Credential(found.username, found.password)
        return None

    def put(self, key, credential):
        backend = self.backend()
        if backend is None:
            return
        try:
            secret = '%s\n%s' % (credential.username, credential.password)
            backend.set_password(SERVICE % key, ACCOUNT, secret)
        except Exception as error:  # Kept for the session only.
            logging.warning("The password store didn't keep the credential: %s", error)


def from_git_helper(key):
    """git credential fill's answer for key, if git is installed and its
    helper has one; never a prompt in a terminal."""
    git = shutil.which('git')
    if git is None or not _real_git(git):
        return None
    scheme, _sep, host = key.partition('://')
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='', SSH_ASKPASS='')
    env.pop('GIT_DIR', None)
    try:
        done = subprocess.run(
            [git, 'credential', 'fill'],
            input=('protocol=%s\nhost=%s\n\n' % (scheme, host)).encode('utf-8'),
            capture_output=True,
            env=env,
            timeout=HELPER_TIMEOUT,
            **gitrepo.detached(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode:
        return None
    values = {}
    for line in done.stdout.decode('utf-8', 'replace').splitlines():
        name, sep, value = line.partition('=')
        if sep:
            values[name] = value
    if values.get('username') and values.get('password'):
        return Credential(values['username'], values['password'])
    return None


def _real_git(path):
    """Whether path is a git, not macOS's stand-in that offers to install
    the Command Line Tools."""
    if sys.platform != 'darwin' or os.path.realpath(path) != '/usr/bin/git':
        return True
    try:
        found = subprocess.run(['xcode-select', '-p'], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return found.returncode == 0


class GitCredentials(gitrepo.Credentials):
    """ask(key, username, refused) is the user's answer, a Credential or
    None; None for no one to ask."""

    def __init__(self, store=None, helper=from_git_helper, ask=None):
        self.store = store or KeyringStore()
        self.helper = helper
        self.ask = ask
        self.session = {}

    def known(self, url):
        key = credential_key(url)
        return self.session.get(key) if key else None

    def candidates(self, url):
        key = credential_key(url)
        if key is None:
            return
        for found in (self.store.get(key), self.helper(key) if self.helper else None):
            if found is not None:
                yield found
        refused = False
        username = _username(url)
        while self.ask is not None:
            answer = self.ask(key, username, refused)
            if answer is None:
                return
            yield answer
            refused, username = True, answer.username

    def accepted(self, url, credential):
        key = credential_key(url)
        self.session[key] = credential
        if credential.remember:
            credential.remember = False
            self.store.put(key, credential)

    def refused(self, url, credential):
        self.session.pop(credential_key(url), None)


def _username(url):
    return parse.urlsplit(url).username or ''
