#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A local bare repository standing in for a team's Git remote, changed
"by someone else" with commits made straight onto its branch, and
served over http on localhost when a test needs the network protocol
or a sign-in. Built with dulwich: no git needed. Never a real host."""

import base64
import contextlib
import os
import threading
import time
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pytest

from devsupport.testing.fakeplatform import LoopbackServer

try:
    from dulwich.diff_tree import tree_changes
    from dulwich.object_store import commit_tree_changes
    from dulwich.objects import Blob, Commit, Tree
    from dulwich.repo import Repo
except ImportError:
    Repo = None

needs_dulwich = pytest.mark.skipif(Repo is None, reason='needs dulwich')

MAINTAINER = b'Maintainer <maintainer@example.org>'


class Remote:
    """A bare repository at folder/remote.git with branch."""

    def __init__(self, folder, files, branch='main'):
        self.folder = str(folder)
        self.branch = branch
        self.url = os.path.join(self.folder, 'remote.git')
        os.makedirs(self.url, exist_ok=True)
        Repo.init_bare(self.url).close()
        self.commit(files, 'Start')

    @property
    def head(self):
        return ('refs/heads/' + self.branch).encode('utf-8')

    def tip(self):
        with Repo(self.url) as repo:
            return repo.refs.as_dict().get(self.head)

    def commit(self, files, message='Change'):
        """files ({path: text}) as one commit on the branch."""
        with Repo(self.url) as repo:
            store = repo.object_store
            tip = repo.refs.as_dict().get(self.head)
            if tip is None:
                tree = Tree()
                store.add_object(tree)
                tree = tree.id
            else:
                tree = repo[tip].tree
            changes = []
            for path, data in files.items():
                blob = Blob.from_string(data.encode('utf-8') if isinstance(data, str) else data)
                store.add_object(blob)
                changes.append((path.encode('utf-8'), 0o100644, blob.id))
            commit = Commit()
            commit.tree = commit_tree_changes(store, tree, changes)
            commit.parents = [tip] if tip else []
            commit.author = commit.committer = MAINTAINER
            commit.author_time = commit.commit_time = int(time.time())
            commit.author_timezone = commit.commit_timezone = 0
            commit.encoding = b'UTF-8'
            commit.message = message.encode('utf-8')
            store.add_object(commit)
            repo.refs[self.head] = commit.id

    def pull(self):
        """Nothing: commit() always builds on the branch's tip."""

    def read(self, path):
        """path as the remote's branch has it now."""
        with Repo(self.url) as repo:
            tree = repo[repo.refs[self.head]].tree
            _mode, sha = repo[tree].lookup_path(repo.__getitem__, path.encode('utf-8'))
            return repo[sha].data.decode('utf-8')

    def commits(self):
        """The branch's commits, newest first."""
        with Repo(self.url) as repo:
            return [entry.commit for entry in repo.get_walker([repo.refs[self.head]])]

    def subjects(self):
        return [c.message.decode('utf-8').split('\n', 1)[0] for c in self.commits()]

    def changed_files(self):
        """The paths the branch's last commit changed."""
        with Repo(self.url) as repo:
            commit = repo[repo.refs[self.head]]
            before = repo[commit.parents[0]].tree if commit.parents else None
            return [
                (change.new.path or change.old.path).decode('utf-8')
                for change in tree_changes(repo.object_store, before, commit.tree)
            ]


def isolate_config(monkeypatch, home, name=None, email=None, xdg=False):
    """git's configuration files read from home alone, with user.name and
    user.email if given (in the XDG git/config if xdg)."""
    home = str(home)
    for variable in ('HOME', 'USERPROFILE'):
        monkeypatch.setenv(variable, home)
    monkeypatch.setenv('XDG_CONFIG_HOME', os.path.join(home, '.config'))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.delenv('GIT_CONFIG_GLOBAL', raising=False)
    if name is None and email is None:
        return
    path = (
        os.path.join(home, '.config', 'git', 'config') if xdg else os.path.join(home, '.gitconfig')
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ['[user]']
    lines += ['\tname = %s' % name] if name else []
    lines += ['\temail = %s' % email] if email else []
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


class FakeStore:
    """A password store in memory: {credential key: Credential}."""

    def __init__(self, kept=None):
        self.kept = dict(kept or {})

    def usable(self):
        return True

    def get(self, key):
        return self.kept.get(key)

    def put(self, key, credential):
        self.kept[key] = credential


class _Server(WSGIServer):
    """WSGIServer without the reverse lookup LoopbackServer skips."""

    def server_bind(self):
        LoopbackServer.server_bind(self)
        self.setup_environ()


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@contextlib.contextmanager
def serve(remote, login=None):
    """remote over http on localhost, its URL. With login (username,
    password), every request needs it (HTTP basic)."""
    from dulwich.server import DictBackend
    from dulwich.web import make_wsgi_chain

    repo = Repo(remote.url)
    app = make_wsgi_chain(DictBackend({'/remote.git': repo}))
    expected = None
    if login:
        expected = 'Basic ' + base64.b64encode(('%s:%s' % login).encode()).decode()

    def guarded(environ, start_response):
        if expected and environ.get('HTTP_AUTHORIZATION') != expected:
            start_response('401 Unauthorized', [('WWW-Authenticate', 'Basic realm="team"')])
            return [b'']
        return app(environ, start_response)

    server = make_server('127.0.0.1', 0, guarded, server_class=_Server, handler_class=_Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%d/remote.git' % server.server_port
    finally:
        server.shutdown()
        server.server_close()
        repo.close()
