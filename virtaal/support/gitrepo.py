#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A branch of a remote Git repository, through dulwich (Git in pure
Python), so no git needs to be installed. No GTK here.

Virtaal keeps a bare clone of its own (Repository) and never a working
tree: files are read from the fetched branch (read()), and a change is
one file's new contents committed straight onto the branch's tip
(commit_file()) and pushed, never forced. Nothing else in the
repository is ever written. A push the remote refuses because the
branch moved on raises REJECTED: fetch again and redo the change on the
new tip.

ssh addresses go through the system's ssh, with its keys and agent and
never a prompt (BatchMode). https asks a Credentials for a username and
password only when the server wants one; Virtaal stores none itself.
git's own configuration files are read (user.name, url.insteadOf,
http.proxy, core.sshCommand) without git installed.
"""

import logging
import os
import shlex
import stat
import subprocess
import threading
import time
from urllib import parse

try:
    from dulwich import client as _client
    from dulwich.config import ConfigDict, StackedConfig
    from dulwich.errors import HangupException, NotGitRepository, NotTreeError
    from dulwich.object_store import commit_tree_changes, tree_lookup_path
    from dulwich.objects import Blob, Commit
    from dulwich.repo import Repo
except ImportError:
    _client = None

# GitError kinds.
MISSING = 'missing'
"""dulwich isn't installed."""
AUTH = 'auth'
"""The remote wants credentials Virtaal doesn't have, or refused them."""
NETWORK = 'network'
"""The remote can't be reached."""
NOT_FOUND = 'not-found'
"""No repository at the address, or no such branch."""
REJECTED = 'rejected'
"""A push refused because the branch moved on."""
FAILED = 'failed'
"""Anything else."""

TIMEOUT = 60
"""Seconds an https request or an ssh connection may stall."""
ATTEMPTS = 3
"""How many credentials one request tries before AUTH."""

_PATTERNS = (
    (
        AUTH,
        (
            'authentication failed',
            'permission denied',
            'host key verification failed',
            'access denied',
            'returned error: 401',
            'returned error: 403',
        ),
    ),
    (REJECTED, ('[rejected]', 'non-fast-forward', 'fetch first', 'unable to set')),
    (
        NOT_FOUND,
        ('does not appear to be a git repository', 'repository not found', 'not found'),
    ),
    (
        NETWORK,
        (
            'could not resolve',
            'connection refused',
            'connection timed out',
            'timed out',
            'network is unreachable',
            'no route to host',
            'max retries exceeded',
        ),
    ),
)


class GitError(Exception):
    """Why a request failed: kind, and the library's or server's own
    words (English)."""

    def __init__(self, kind, detail=''):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def classify(text):
    """The GitError kind for an error message."""
    text = text.lower()
    for kind, needles in _PATTERNS:
        if any(needle in text for needle in needles):
            return kind
    return FAILED


def _first_line(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else ''


def available():
    """Whether dulwich is there."""
    return _client is not None


def _ca_bundle():
    try:
        import certifi
    except ImportError:
        return None
    return certifi.where()


def config():
    """git's configuration files (the user's ~/.gitconfig, the XDG
    git/config, the system's), read by dulwich. certifi's CA bundle
    when they name none."""
    backends = StackedConfig.default_backends()
    bundle = _ca_bundle()
    if bundle:
        ours = ConfigDict()
        ours.set((b'http',), b'sslCAInfo', bundle.encode('utf-8'))
        backends.append(ours)
    return StackedConfig(backends)


def identity():
    """(name, email) of user.name and user.email in git's configuration
    files, each '' if unset."""
    if not available():
        return '', ''
    try:
        found = config()
    except (OSError, ValueError) as error:
        logging.warning("git's configuration can't be read: %s", error)
        return '', ''
    values = []
    for name in (b'name', b'email'):
        try:
            value = found.get((b'user',), name)
        except KeyError:
            value = b''
        values.append((value or b'').decode('utf-8', 'replace').strip())
    return tuple(values)


def credential_key(url):
    """'https://host[:port]' of an http(s) URL, the key its credentials
    are kept under; None for any other."""
    parts = parse.urlsplit(url)
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if parts.port:
        host += ':%d' % parts.port
    return '%s://%s' % (parts.scheme.lower(), host)


class Credential:
    """A username and password (or token) for one https server."""

    def __init__(self, username, password, remember=False):
        self.username = username
        self.password = password
        self.remember = remember
        """Whether to keep it in the system's password store once the
        server accepts it."""


class Credentials:
    """Where credentials for https come from: none here. GitPlatform's
    looks in its session, the system's password store, git's credential
    helper, then asks the user."""

    def known(self, url):
        """A credential already accepted for url this session, or None."""
        return None

    def candidates(self, url):
        """The credentials to try, in turn, once url refused the last."""
        return iter(())

    def accepted(self, url, credential):
        """url accepted credential."""

    def refused(self, url, credential):
        """url refused credential."""


def detached():
    """No terminal for ssh to prompt on, and no console window."""
    if os.name == 'nt':
        return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    return {'start_new_session': True}


def ssh_arguments(host, command, username=None, port=None, key_filename=None, ssh_command=None):
    """The command line for running command (bytes) on host by ssh: the
    system's ssh (or GIT_SSH_COMMAND / core.sshCommand's), never asking
    for a password or passphrase."""
    args = shlex.split(ssh_command, posix=os.name != 'nt') if ssh_command else ['ssh']
    if os.path.basename(args[0]).lower().startswith('ssh'):
        args += ['-x', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=%d' % TIMEOUT]
    if port:
        args += ['-p', str(port)]
    if key_filename:
        args += ['-i', str(key_filename)]
    if username:
        host = '%s@%s' % (username, host)
    if host.startswith('-'):
        raise GitError(FAILED, 'strange host name: %s' % host)
    return [*args, host, command]


if _client is not None:

    class _QuietSSH(_client.SSHVendor):
        def run_command(
            self,
            host,
            command,
            username=None,
            port=None,
            password=None,
            key_filename=None,
            ssh_command=None,
            protocol_version=None,
        ):
            args = ssh_arguments(host, command, username, port, key_filename, ssh_command)
            proc = subprocess.Popen(
                args,
                bufsize=0,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **detached(),
            )
            return _client.SubprocessWrapper(proc)


def _own_kind(error):
    """The GitError kind of an exception, by its type alone."""
    import urllib3

    kinds = (
        ((_client.HTTPUnauthorized, _client.HTTPProxyUnauthorized), AUTH),
        (NotGitRepository, NOT_FOUND),
        (FileNotFoundError, FAILED),
        ((HangupException, OSError, urllib3.exceptions.HTTPError), NETWORK),
    )
    for types, kind in kinds:
        if isinstance(error, types):
            return kind
    return FAILED


def _git_error(error):
    """The GitError for a failed request's exception: what its message
    says, else what its type does."""
    if isinstance(error, GitError):
        return error
    detail = _first_line(str(error)) or type(error).__name__
    kind = _own_kind(error)
    if kind in (NETWORK, FAILED):
        kind = classify(detail) if classify(detail) != FAILED else kind
    return GitError(kind, detail)


class Repository:
    """One branch of the repository at url, in a bare clone at folder."""

    def __init__(self, url, branch, folder, credentials=None):
        self.url = url
        self.branch = branch
        self.folder = folder
        self.credentials = credentials or Credentials()
        self.lock = threading.Lock()
        """Held by whoever is working in the clone."""

    @property
    def ref(self):
        """The fetched branch, in the clone."""
        return 'refs/remotes/origin/' + self.branch

    @property
    def _head(self):
        return ('refs/heads/' + self.branch).encode('utf-8')

    def _repo(self):
        if not available():
            raise GitError(MISSING)
        if os.path.isfile(os.path.join(self.folder, 'HEAD')):
            return Repo(self.folder)
        os.makedirs(self.folder, exist_ok=True)
        return Repo.init_bare(self.folder)

    def ensure(self):
        """The bare clone, made if it isn't there yet."""
        self._repo().close()

    def _transport(self, credential):
        found = config()
        login = {}
        if credential is not None:
            login = {'username': credential.username, 'password': credential.password}
        transport, path = _client.get_transport_and_path(
            self.url,
            config=found,
            ssh_command=os.environ.get('GIT_SSH_COMMAND') or None,
            **login,
        )
        if isinstance(transport, _client.SSHGitClient):
            transport.ssh_vendor = _QuietSSH()
        if isinstance(transport, _client.AbstractHttpGitClient):
            transport._timeout = TIMEOUT
        return transport, path

    def _remote(self, request):
        """request(transport, path)'s answer, signing in when the remote
        asks."""
        if not available():
            raise GitError(MISSING)
        credential = self.credentials.known(self.url)
        offers = None
        for _attempt in range(ATTEMPTS + 1):
            try:
                answer = request(*self._transport(credential))
            except _client.HTTPUnauthorized as error:
                if credential is not None:
                    self.credentials.refused(self.url, credential)
                if offers is None:
                    offers = self.credentials.candidates(self.url)
                credential = next(offers, None)
                if credential is None:
                    raise GitError(AUTH, _first_line(str(error))) from error
                continue
            except Exception as error:
                raise _git_error(error) from error
            if credential is not None:
                self.credentials.accepted(self.url, credential)
            return answer
        raise GitError(AUTH)

    def fetch(self):
        """The branch as the remote has it now. Its tip."""
        head = self._head
        with self._repo() as repo:

            def wants(refs, depth=None):
                if refs.get(head) is None:
                    raise GitError(NOT_FOUND, "couldn't find remote ref %s" % head.decode())
                return [] if refs[head] in repo.object_store else [refs[head]]

            result = self._remote(
                lambda transport, path: transport.fetch(
                    path, repo, determine_wants=wants, ref_prefix=[head]
                )
            )
            tip = result.refs.get(head)
            if tip is None:
                raise GitError(NOT_FOUND, "couldn't find remote ref %s" % head.decode())
            repo.refs[self.ref.encode('utf-8')] = tip
        return tip.decode('ascii')

    def tip(self):
        """The fetched branch's last commit, or None before a fetch."""
        if not available() or not os.path.isfile(os.path.join(self.folder, 'HEAD')):
            return None
        with Repo(self.folder) as repo:
            found = repo.refs.as_dict().get(self.ref.encode('utf-8'))
        return found.decode('ascii') if found else None

    def files(self, commit):
        """The paths of every file of commit, "/" between parts."""
        with self._repo() as repo:
            tree = repo[commit.encode('ascii')].tree
            return [
                entry.path.decode('utf-8', 'surrogateescape')
                for entry in repo.object_store.iter_tree_contents(tree)
                if stat.S_ISREG(entry.mode)
            ]

    def _entry(self, repo, commit, path):
        """(mode, id) of path's file in commit, or None."""
        tree = repo[commit.encode('ascii')].tree
        try:
            mode, sha = tree_lookup_path(repo.__getitem__, tree, path.encode('utf-8'))
        except (KeyError, NotTreeError):
            return None
        return (mode, sha) if stat.S_ISREG(mode) else None

    def read(self, commit, path):
        """The bytes of path in commit, or None if it has no such file."""
        with self._repo() as repo:
            found = self._entry(repo, commit, path)
            return repo[found[1]].as_raw_string() if found else None

    def commit_file(self, parent, path, data, message, author):
        """A new commit on parent with path's contents data, and every
        other file as parent has it, by author (name, email). Its id."""
        with self._repo() as repo:
            found = self._entry(repo, parent, path)
            blob = Blob.from_string(data)
            repo.object_store.add_object(blob)
            base = repo[parent.encode('ascii')]
            mode = found[0] if found else 0o100644
            tree = commit_tree_changes(
                repo.object_store, base.tree, [(path.encode('utf-8'), mode, blob.id)]
            )
            name, email = author
            commit = Commit()
            commit.tree = tree
            commit.parents = [base.id]
            commit.author = commit.committer = ('%s <%s>' % (name, email)).encode('utf-8')
            commit.author_time = commit.commit_time = int(time.time())
            offset = time.localtime().tm_gmtoff
            commit.author_timezone = commit.commit_timezone = offset
            commit.encoding = b'UTF-8'
            commit.message = message.encode('utf-8')
            repo.object_store.add_object(commit)
            return commit.id.decode('ascii')

    def push(self, commit):
        """commit as the branch's tip on the remote, only if it follows
        on from the remote's tip (never forced). Raises REJECTED when the
        branch moved on."""
        head = self._head
        with self._repo() as repo:
            new = commit.encode('ascii')
            parent = repo[new].parents[0]
            moved = []

            def update_refs(refs):
                del moved[:]
                if refs.get(head) != parent:
                    moved.append(refs.get(head))
                    return {}
                return {head: new}

            result = self._remote(
                lambda transport, path: transport.send_pack(
                    path, update_refs, repo.generate_pack_data
                )
            )
            if moved:
                raise GitError(REJECTED, '[rejected] %s (fetch first)' % self.branch)
            refused = (result.ref_status or {}).get(head)
            if refused:
                raise GitError(REJECTED, '[remote rejected] %s (%s)' % (self.branch, refused))
            repo.refs[self.ref.encode('utf-8')] = new

    def has_branch(self):
        """Whether the remote has the branch."""
        head = self._head
        result = self._remote(lambda transport, path: transport.get_refs(path, ref_prefix=[head]))
        refs = getattr(result, 'refs', result)
        return refs.get(head) is not None
