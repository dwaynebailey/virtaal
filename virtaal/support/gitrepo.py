#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A branch of a remote Git repository, through the user's own git. No
GTK here.

Virtaal keeps a bare clone of its own (Repository) and never a working
tree: files are read from the fetched branch (read()), and a change is
one file's new contents committed straight onto the branch's tip
(commit_file()) and pushed, never forced. Nothing else in the
repository is ever written. A push the remote refuses because the
branch moved on raises REJECTED: fetch again and redo the change on the
new tip.

git itself does the transport and the credentials: its credential
helpers, the SSH agent, its proxy settings. It never prompts: with no
terminal and GIT_TERMINAL_PROMPT=0, missing credentials fail at once
(AUTH) instead of waiting for an answer no one can give.
"""

import os
import re
import shutil
import subprocess
import tempfile
import threading

MIN_VERSION = (2, 20)
"""The oldest git used: Debian 10 and Ubuntu 20.04 have it."""

# GitError kinds.
MISSING = 'missing'
"""No git, or one older than MIN_VERSION."""
AUTH = 'auth'
"""The remote wants credentials git doesn't have, or refused them."""
NETWORK = 'network'
"""The remote can't be reached."""
NOT_FOUND = 'not-found'
"""No repository at the address, or no such branch."""
REJECTED = 'rejected'
"""A push refused because the branch moved on."""
FAILED = 'failed'
"""Anything else."""

NETWORK_TIMEOUT = 300
LOCAL_TIMEOUT = 60

_PATTERNS = (
    (
        AUTH,
        (
            'authentication failed',
            'could not read username',
            'could not read password',
            'terminal prompts disabled',
            'permission denied',
            'host key verification failed',
            'access denied',
            'the requested url returned error: 401',
            'the requested url returned error: 403',
        ),
    ),
    (
        REJECTED,
        ('[rejected]', 'non-fast-forward', 'fetch first', '[remote rejected]'),
    ),
    (
        NOT_FOUND,
        (
            "couldn't find remote ref",
            'does not appear to be a git repository',
            'repository not found',
            'not found',
        ),
    ),
    (
        NETWORK,
        (
            'could not resolve host',
            'unable to access',
            'connection refused',
            'connection timed out',
            'timed out',
            'could not read from remote repository',
            'network is unreachable',
        ),
    ),
)


class GitError(Exception):
    """Why a git command failed: kind, and git's own words (English)."""

    def __init__(self, kind, detail=''):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def classify(stderr):
    """The GitError kind for a failed command's error output."""
    text = stderr.lower()
    for kind, needles in _PATTERNS:
        if any(needle in text for needle in needles):
            return kind
    return FAILED


def _first_line(stderr):
    """git's message, without its hints."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith('hint:')]
    return lines[0] if lines else ''


_found = {}


def executable():
    """The git found on PATH, or None."""
    return shutil.which('git')


def parse_version(text):
    """(major, minor) of `git --version`'s output, or None."""
    found = re.search(r'(\d+)\.(\d+)', text or '')
    return (int(found.group(1)), int(found.group(2))) if found else None


def version():
    """(major, minor) of the git found, or None without one. Asked once."""
    path = executable()
    if path is None:
        return None
    if path not in _found:
        try:
            out = subprocess.run(
                [path, '--version'],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=LOCAL_TIMEOUT,
                **_detached(),
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = b''
        _found[path] = parse_version(out.decode('utf-8', 'replace'))
    return _found[path]


def available():
    """Whether a git of at least MIN_VERSION is there."""
    found = version()
    return found is not None and found >= MIN_VERSION


def _detached():
    """No terminal for git or ssh to prompt on, and no console window."""
    if os.name == 'nt':
        return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    return {'start_new_session': True}


def _environment(extra=None):
    env = dict(os.environ)
    env.update(GIT_TERMINAL_PROMPT='0', LC_ALL='C', LANGUAGE='C')
    env.pop('GIT_DIR', None)
    env.pop('GIT_WORK_TREE', None)
    env.pop('GIT_INDEX_FILE', None)
    env.update(extra or {})
    return env


def run(args, data=None, env=None, timeout=LOCAL_TIMEOUT):
    """git's output (bytes) for args, given data on its input. Raises
    GitError."""
    if not available():
        raise GitError(MISSING)
    try:
        done = subprocess.run(
            [executable(), *args],
            input=data,
            stdin=None if data is not None else subprocess.DEVNULL,
            capture_output=True,
            env=_environment(env),
            timeout=timeout,
            **_detached(),
        )
    except subprocess.TimeoutExpired as error:
        raise GitError(NETWORK, 'timed out after %d s' % error.timeout) from error
    except OSError as error:
        raise GitError(MISSING, str(error)) from error
    if done.returncode != 0:
        said = (done.stderr + b'\n' + done.stdout).decode('utf-8', 'replace')
        detail = _first_line(said) or 'git exited with %d' % done.returncode
        raise GitError(classify(said), detail)
    return done.stdout


class Repository:
    """One branch of the repository at url, in a bare clone at folder."""

    def __init__(self, url, branch, folder):
        self.url = url
        self.branch = branch
        self.folder = folder
        self.lock = threading.Lock()
        """Held by whoever is working in the clone."""

    @property
    def ref(self):
        """The fetched branch, in the clone."""
        return 'refs/remotes/origin/' + self.branch

    def git(self, *args, **options):
        return run(['--git-dir', self.folder, *args], **options)

    def ensure(self):
        """The bare clone, made if it isn't there yet, with url as its
        origin."""
        if not os.path.isfile(os.path.join(self.folder, 'HEAD')):
            os.makedirs(self.folder, exist_ok=True)
            run(['init', '--bare', '--quiet', self.folder])
        try:
            current = self.git('config', '--get', 'remote.origin.url').decode().strip()
        except GitError:
            current = None
        if current != self.url:
            self.git('config', 'remote.origin.url', self.url)

    def fetch(self):
        """The branch as the remote has it now. Its tip."""
        self.ensure()
        refspec = '+refs/heads/%s:%s' % (self.branch, self.ref)
        self.git('fetch', '--quiet', '--no-tags', 'origin', refspec, timeout=NETWORK_TIMEOUT)
        return self.tip()

    def tip(self):
        """The fetched branch's last commit, or None before a fetch."""
        try:
            out = self.git('rev-parse', '--verify', '--quiet', self.ref + '^{commit}')
        except GitError:
            return None
        return out.decode().strip()

    def files(self, commit):
        """The paths of every file of commit, "/" between parts."""
        out = self.git('ls-tree', '-r', '-z', '--name-only', commit)
        return [path for path in out.decode('utf-8', 'surrogateescape').split('\0') if path]

    def read(self, commit, path):
        """The bytes of path in commit, or None if it has no such file."""
        try:
            kind = self.git('cat-file', '-t', '%s:%s' % (commit, path)).decode().strip()
        except GitError:
            return None
        if kind != 'blob':
            return None
        return self.git('cat-file', 'blob', '%s:%s' % (commit, path))

    def _mode(self, commit, path):
        out = self.git('ls-tree', '-z', commit, '--', path).decode('utf-8', 'surrogateescape')
        mode = out.split(' ', 1)[0] if out else ''
        return mode if mode in ('100644', '100755') else '100644'

    def commit_file(self, parent, path, data, message, author=None):
        """A new commit on parent with path's contents data, and every
        other file as parent has it. author is (name, email), both
        empty for git's own user.name and user.email. Its id."""
        blob = self.git('hash-object', '-w', '--stdin', data=data).decode().strip()
        mode = self._mode(parent, path)
        handle, index = tempfile.mkstemp(prefix='index-', dir=self.folder)
        os.close(handle)
        os.remove(index)
        try:
            env = {'GIT_INDEX_FILE': index}
            self.git('read-tree', parent, env=env)
            entry = '%s,%s,%s' % (mode, blob, path)
            self.git('update-index', '--add', '--cacheinfo', entry, env=env)
            tree = self.git('write-tree', env=env).decode().strip()
        finally:
            if os.path.exists(index):
                os.remove(index)
        env = {}
        name, email = author or ('', '')
        if name:
            env.update(GIT_AUTHOR_NAME=name, GIT_COMMITTER_NAME=name)
            env.update(GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_EMAIL=email)
        text = message.encode('utf-8')
        out = self.git('commit-tree', tree, '-p', parent, '-F', '-', data=text, env=env)
        return out.decode().strip()

    def push(self, commit):
        """commit as the branch's tip on the remote, only if it follows
        on from the remote's tip (never forced). Raises REJECTED when the
        branch moved on."""
        target = '%s:refs/heads/%s' % (commit, self.branch)
        self.git('push', 'origin', target, timeout=NETWORK_TIMEOUT)
        self.git('update-ref', self.ref, commit)

    def has_branch(self):
        """Whether the remote has the branch."""
        self.ensure()
        out = self.git(
            'ls-remote', '--heads', 'origin', 'refs/heads/' + self.branch, timeout=NETWORK_TIMEOUT
        )
        return bool(out.strip())
