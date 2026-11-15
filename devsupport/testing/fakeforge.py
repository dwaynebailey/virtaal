#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A fake GitHub on 127.0.0.1 for tests and hand checks: the REST API
calls Virtaal makes for a pull request from a fork, under /api/v3 (as
GitHub Enterprise has it), and the repositories over git's smart HTTP
at /<owner>/<name>.git, served by dulwich. No git needed.

It holds users (a token each), repositories (bare dulwich repositories
in a folder) and pull requests. The API serves GET /user, GET
/repos/<o>/<r> (permissions with a token), GET and POST
/repos/<o>/<r>/forks (the fork has no branches, and refuses git, for
fork_delay requests of its branches), GET /repos/<o>/<r>/branches,
GET and POST /repos/<o>/<r>/pulls (head=user:branch, state=open; 422
for a second open one), GET /repos/<o>/<r>/pulls/<n>, its reviews and
comments, and GET /repos/<o>/<r>/issues/<n>/comments. Every reply has
X-RateLimit headers; the budget used up answers 403 with none left, as
GitHub does. git push needs a user's token (HTTP basic, any username)
and the right to push.

Every push is kept in pushes, with whether it was forced (the new
commit doesn't follow on from the old). merge(), close(), review() and
commit() change things as the project's maintainer would; limit() and
throttle() play GitHub's primary and secondary rate limits.

Never point Virtaal at a real GitHub from a test.

Run by hand:  python -m devsupport.testing.fakeforge --folder DIR --port 8770
(--seed DIR: a project team/program with the seed's files under po/;
POST /_fake/merge/<n>, /_fake/close/<n>, /_fake/review/<n> {"body"};
GET /_fake/pushes.)
"""

import argparse
import base64
import json
import os
import re
import shutil
import threading
import time
from socketserver import ThreadingMixIn
from urllib import parse
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from devsupport.testing.fakeplatform import LoopbackServer, run_until

try:
    from dulwich.diff_tree import tree_changes
    from dulwich.object_store import commit_tree_changes
    from dulwich.objects import Blob, Commit, Tree
    from dulwich.repo import Repo
    from dulwich.server import DictBackend
    from dulwich.web import make_wsgi_chain
except ImportError:
    Repo = None

__all__ = ['MAINTAINER', 'TOKEN', 'USER', 'FakeForge', 'run_until']

USER = 'thandi'
TOKEN = 'ghp_fake0123456789abcdef0123456789abcdef'
"""The token of USER, a translator with no right to push to the project."""
MAINTAINER = 'maintainer'
MAINTAINER_TOKEN = 'ghp_maintainer0123456789abcdef01234567'
API = '/api/v3'
LIMIT = 5000
_AUTHOR = b'Maintainer <maintainer@example.org>'


class _Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True

    def server_bind(self):
        LoopbackServer.server_bind(self)
        self.setup_environ()


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class _Reply(Exception):
    def __init__(self, status, data, headers=()):
        super().__init__(status)
        self.status = status
        self.data = data
        self.headers = list(headers)


def _commit(repo, parent, tree, message, parents=None):
    commit = Commit()
    commit.tree = tree
    commit.parents = parents if parents is not None else ([parent] if parent else [])
    commit.author = commit.committer = _AUTHOR
    commit.author_time = commit.commit_time = int(time.time())
    commit.author_timezone = commit.commit_timezone = 0
    commit.encoding = b'UTF-8'
    commit.message = message.encode('utf-8')
    repo.object_store.add_object(commit)
    return commit.id


class FakeForge:
    """The server's data and behaviour; start() serves it."""

    def __init__(self, folder):
        self.folder = str(folder)
        self.tokens = {TOKEN: USER, MAINTAINER_TOKEN: MAINTAINER}
        self.repos = {}
        """{full name: {'owner', 'name', 'fork_of', 'pushers', 'ready_in'}}."""
        self.pulls = []
        self.reviews = []
        """[(number, kind, id, login, body)]."""
        self.pushes = []
        """[(repository, ref, old, new, forced)] in order."""
        self.requests = []
        """[(method, path)] of the API."""
        self.fork_delay = 2
        self.remaining = LIMIT
        self.reset_in = 3600
        self.throttled = []
        """Retry-After seconds of the next API replies to answer 403."""
        self._lock = threading.RLock()
        self._ids = 100
        self._server = None

    # Data #

    def path_of(self, full_name):
        return os.path.join(self.folder, full_name + '.git')

    def add_repository(self, full_name, files, branch='main', pushers=()):
        """A repository with files ({path: bytes or text}) on branch."""
        owner, name = full_name.split('/')
        os.makedirs(self.path_of(full_name), exist_ok=True)
        Repo.init_bare(self.path_of(full_name)).close()
        self.repos[full_name] = {
            'owner': owner,
            'name': name,
            'fork_of': None,
            'pushers': {owner, *pushers},
            'ready_in': 0,
            'default_branch': branch,
        }
        self.commit(full_name, files, 'Start', branch)

    def commit(self, full_name, files, message='Change', branch='main'):
        """files as one commit on branch, as the maintainer."""
        with Repo(self.path_of(full_name)) as repo:
            ref = ('refs/heads/' + branch).encode()
            tip = repo.refs.as_dict().get(ref)
            if tip is None:
                empty = Tree()
                repo.object_store.add_object(empty)
                tree = empty.id
            else:
                tree = repo[tip].tree
            changes = []
            for path, data in files.items():
                blob = Blob.from_string(data.encode('utf-8') if isinstance(data, str) else data)
                repo.object_store.add_object(blob)
                changes.append((path.encode('utf-8'), 0o100644, blob.id))
            tree = commit_tree_changes(repo.object_store, tree, changes)
            repo.refs[ref] = _commit(repo, tip, tree, message)

    def refs(self, full_name):
        with Repo(self.path_of(full_name)) as repo:
            return {
                k.decode(): v.decode()
                for k, v in repo.refs.as_dict().items()
                if k.startswith(b'refs/heads/')
            }

    def read(self, full_name, ref, path):
        with Repo(self.path_of(full_name)) as repo:
            tree = repo[repo.refs[ref.encode()]].tree
            _mode, sha = repo[tree].lookup_path(repo.__getitem__, path.encode('utf-8'))
            return repo[sha].data

    def commits(self, full_name, ref, stop=None):
        """The commits of ref, newest first, down to stop (not included)."""
        with Repo(self.path_of(full_name)) as repo:
            found = []
            for entry in repo.get_walker([repo.refs[ref.encode()]]):
                if stop and entry.commit.id.decode() == stop:
                    break
                found.append(entry.commit)
            return found

    def subjects(self, full_name, ref, stop=None):
        return [
            c.message.decode('utf-8').split('\n', 1)[0] for c in self.commits(full_name, ref, stop)
        ]

    # The maintainer #

    def pull(self, number):
        return next(p for p in self.pulls if p['number'] == number)

    def merge(self, number):
        """Merges a pull request: its changes from its first commit's
        parent onto the base branch, as one merge commit."""
        with self._lock:
            pull = self.pull(number)
            base_name = pull['base_repo']
            head_name = pull['head_repo']
            head_ref = 'refs/heads/' + pull['head_ref']
            head = self.refs(head_name)[head_ref].encode()
            self._copy_objects(head_name, base_name)
            with Repo(self.path_of(base_name)) as repo:
                ref = ('refs/heads/' + pull['base_ref']).encode()
                tip = repo.refs[ref]
                start = self._start_of(repo, head, tip)
                changes = [
                    (c.new.path, c.new.mode, c.new.sha)
                    for c in tree_changes(repo.object_store, repo[start].tree, repo[head].tree)
                    if c.new.path
                ]
                tree = commit_tree_changes(repo.object_store, repo[tip].tree, changes)
                message = 'Merge pull request #%d' % number
                repo.refs[ref] = _commit(repo, tip, tree, message, [tip, head])
            pull.update(state='closed', merged=True)

    def _start_of(self, repo, head, base_tip):
        """The last commit of head's that the base branch has."""
        known = {e.commit.id for e in repo.get_walker([base_tip])}
        for entry in repo.get_walker([head]):
            if entry.commit.id in known:
                return entry.commit.id
        raise ValueError('nothing in common')

    def _copy_objects(self, source, target):
        with Repo(self.path_of(source)) as one, Repo(self.path_of(target)) as other:
            for sha in one.object_store:
                if sha not in other.object_store:
                    other.object_store.add_object(one[sha])

    def close(self, number):
        self.pull(number).update(state='closed', merged=False)

    def review(self, number, kind='review', login=MAINTAINER, body='Please fix'):
        """A review ('review'), review comment ('comment') or comment
        ('note') on a pull request."""
        with self._lock:
            self._ids += 1
            self.reviews.append((number, kind, self._ids, login, body))

    def limit(self, remaining, reset_in=3600):
        """Requests left before 403s, and seconds until they start again."""
        self.remaining, self.reset_in = remaining, reset_in

    def throttle(self, *seconds):
        """The next replies answer 403 with these Retry-Afters (GitHub's
        secondary rate limit)."""
        self.throttled.extend(seconds)

    # Serving #

    def start(self, port=0):
        self._server = make_server(
            '127.0.0.1', port, self.app, server_class=_Server, handler_class=_Quiet
        )
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def base(self):
        return 'http://127.0.0.1:%d' % self._server.server_port

    @property
    def host(self):
        return '127.0.0.1:%d' % self._server.server_port

    @property
    def api(self):
        return self.base + API

    def clone_url(self, full_name):
        return '%s/%s.git' % (self.base, full_name)

    def app(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path.startswith(API + '/') or path == API:
            return self._api(environ, start_response)
        if path.startswith('/_fake/'):
            return self._control(environ, start_response)
        return self._git(environ, start_response)

    # git #

    def _git(self, environ, start_response):
        found = re.match(r'^/([^/]+/[^/]+)\.git(/.*)$', environ.get('PATH_INFO', ''))
        name = found and found.group(1)
        info = self.repos.get(name)
        if info is None or info['ready_in'] > 0:
            start_response('404 Not Found', [('Content-Type', 'text/plain')])
            return [b'Repository not found.']
        pushing = 'git-receive-pack' in found.group(2) or 'receive-pack' in environ.get(
            'QUERY_STRING', ''
        )
        if pushing:
            user = self._basic_user(environ)
            if user is None:
                start_response('401 Unauthorized', [('WWW-Authenticate', 'Basic realm="GitHub"')])
                return [b'']
            if user not in info['pushers']:
                start_response('403 Forbidden', [('Content-Type', 'text/plain')])
                return [b'Permission denied']
        with self._lock:
            before = self.refs(name) if pushing else None
            repo = Repo(self.path_of(name))
            try:
                served = make_wsgi_chain(DictBackend({'/%s.git' % name: repo}))
                answer, written = [], []

                def capture(status, headers, exc_info=None):
                    answer.append((status, headers))
                    return written.append

                body = b''.join(served(environ, capture))
                body = b''.join(written) + body
            finally:
                repo.close()
            if pushing and environ.get('REQUEST_METHOD') == 'POST':
                self._note_pushes(name, before)
        start_response(*answer[0])
        return [body]

    def _basic_user(self, environ):
        header = environ.get('HTTP_AUTHORIZATION', '')
        if not header.startswith('Basic '):
            return None
        try:
            _user, _sep, password = base64.b64decode(header[6:]).decode().partition(':')
        except ValueError:
            return None
        return self.tokens.get(password)

    def _note_pushes(self, name, before):
        after = self.refs(name)
        with Repo(self.path_of(name)) as repo:
            for ref, new in after.items():
                old = before.get(ref)
                if old == new:
                    continue
                forced = bool(old) and old.encode() not in {
                    e.commit.id for e in repo.get_walker([new.encode()])
                }
                self.pushes.append((name, ref, old, new, forced))

    # The API #

    def _api(self, environ, start_response):
        method = environ['REQUEST_METHOD']
        path = environ['PATH_INFO'][len(API) :]
        query = dict(parse.parse_qsl(environ.get('QUERY_STRING', '')))
        self.requests.append((method, path))
        headers = []
        try:
            user = self._token_user(environ)
            headers = self._budget(user)
            body = self._body(environ)
            status, data = self._route(method, path, query, user, body)
        except _Reply as reply:
            status, data = reply.status, reply.data
            headers = headers + reply.headers
        start_response(
            '%d %s' % (status, _REASONS.get(status, 'Status')),
            [('Content-Type', 'application/json'), *headers],
        )
        return [json.dumps(data).encode('utf-8')]

    def _token_user(self, environ):
        header = environ.get('HTTP_AUTHORIZATION', '')
        token = header.partition(' ')[2] if header.split(' ')[0] in ('Bearer', 'token') else ''
        if not token:
            return None
        user = self.tokens.get(token)
        if user is None:
            raise _Reply(401, {'message': 'Bad credentials'})
        return user

    def _budget(self, user):
        if self.throttled:
            seconds = self.throttled.pop(0)
            raise _Reply(
                403,
                {'message': 'You have exceeded a secondary rate limit.'},
                [('Retry-After', str(seconds))],
            )
        reset = str(int(time.time() + self.reset_in))
        if self.remaining <= 0:
            raise _Reply(
                403,
                {'message': 'API rate limit exceeded for user.'},
                [
                    ('X-RateLimit-Limit', str(LIMIT)),
                    ('X-RateLimit-Remaining', '0'),
                    ('X-RateLimit-Reset', reset),
                ],
            )
        self.remaining -= 1
        return [
            ('X-RateLimit-Limit', str(LIMIT)),
            ('X-RateLimit-Remaining', str(self.remaining)),
            ('X-RateLimit-Reset', reset),
        ]

    def _body(self, environ):
        try:
            length = int(environ.get('CONTENT_LENGTH') or 0)
        except ValueError:
            length = 0
        raw = environ['wsgi.input'].read(length) if length else b''
        try:
            return json.loads(raw.decode('utf-8')) if raw else {}
        except ValueError as error:
            raise _Reply(400, {'message': 'Problems parsing JSON'}) from error

    def _route(self, method, path, query, user, body):
        for verb, pattern, handler in _ROUTES:
            found = re.match(pattern + r'\Z', path)
            if verb == method and found:
                if user is None:
                    if not getattr(handler, 'public', False):
                        raise _Reply(401, {'message': 'Requires authentication'})
                return handler(self, user, query, body, *found.groups())
        raise _Reply(404, {'message': 'Not Found'})

    def _repository(self, full_name):
        info = self.repos.get(full_name)
        if info is None:
            raise _Reply(404, {'message': 'Not Found'})
        return info

    def repo_json(self, full_name, user=None):
        info = self._repository(full_name)
        data = {
            'id': abs(hash(full_name)) % 100000,
            'name': info['name'],
            'full_name': full_name,
            'owner': {'login': info['owner']},
            'fork': info['fork_of'] is not None,
            'default_branch': info['default_branch'],
            'clone_url': self.clone_url(full_name),
            'ssh_url': 'git@%s:%s.git' % (self.host, full_name),
            'html_url': '%s/%s' % (self.base, full_name),
        }
        if info['fork_of']:
            data['parent'] = data['source'] = {'full_name': info['fork_of']}
        if user is not None:
            data['permissions'] = {
                'admin': user == info['owner'],
                'push': user in info['pushers'],
                'pull': True,
            }
        return data

    def pull_json(self, pull):
        return {
            'number': pull['number'],
            'state': pull['state'],
            'merged': pull['merged'],
            'title': pull['title'],
            'body': pull['body'],
            'html_url': '%s/%s/pull/%d' % (self.base, pull['base_repo'], pull['number']),
            'user': {'login': pull['user']},
            'head': {
                'ref': pull['head_ref'],
                'label': '%s:%s' % (pull['head_repo'].split('/')[0], pull['head_ref']),
                'repo': {'full_name': pull['head_repo']},
                'sha': self.refs(pull['head_repo']).get('refs/heads/' + pull['head_ref']),
            },
            'base': {'ref': pull['base_ref'], 'repo': {'full_name': pull['base_repo']}},
        }

    # Control, for hand checks #

    def _control(self, environ, start_response):
        if environ['PATH_INFO'] == '/_fake/pushes':
            start_response('200 OK', [('Content-Type', 'application/json')])
            return [json.dumps(self.pushes).encode()]
        found = re.match(r'^/_fake/(merge|close|review)/(\d+)$', environ['PATH_INFO'])
        if environ['REQUEST_METHOD'] != 'POST' or not found:
            start_response('404 Not Found', [('Content-Type', 'text/plain')])
            return [b'']
        action, number = found.group(1), int(found.group(2))
        if action == 'review':
            body = self._body(environ)
            self.review(number, body=body.get('body') or 'Please fix')
        else:
            getattr(self, action)(number)
        start_response('200 OK', [('Content-Type', 'application/json')])
        return [json.dumps(self.pull_json(self.pull(number))).encode()]


def _public(handler):
    handler.public = True
    return handler


def _user(fake, user, query, body):
    return 200, {'login': user}


@_public
def _get_repository(fake, user, query, body, full_name):
    return 200, fake.repo_json(full_name, user)


def _list_forks(fake, user, query, body, full_name):
    fake._repository(full_name)
    forks = [n for n, i in fake.repos.items() if i['fork_of'] == full_name]
    return 200, [fake.repo_json(n) for n in forks]


def _create_fork(fake, user, query, body, full_name):
    info = fake._repository(full_name)
    name = '%s/%s' % (user, info['name'])
    with fake._lock:
        if name not in fake.repos:
            shutil.copytree(fake.path_of(full_name), fake.path_of(name))
            fake.repos[name] = {
                'owner': user,
                'name': info['name'],
                'fork_of': full_name,
                'pushers': {user},
                'ready_in': fake.fork_delay,
                'default_branch': info['default_branch'],
            }
    return 202, fake.repo_json(name, user)


@_public
def _branches(fake, user, query, body, full_name):
    info = fake._repository(full_name)
    if info['ready_in'] > 0:
        info['ready_in'] -= 1
        return 200, []
    found = sorted(fake.refs(full_name).items())
    return 200, [{'name': ref[len('refs/heads/') :], 'commit': {'sha': sha}} for ref, sha in found]


@_public
def _list_pulls(fake, user, query, body, full_name):
    fake._repository(full_name)
    state = query.get('state', 'open')
    found = []
    for pull in fake.pulls:
        label = '%s:%s' % (pull['head_repo'].split('/')[0], pull['head_ref'])
        if pull['base_repo'] != full_name or (state != 'all' and pull['state'] != state):
            continue
        if query.get('head') and query['head'] != label:
            continue
        found.append(fake.pull_json(pull))
    return 200, found


def _create_pull(fake, user, query, body, full_name):
    info = fake._repository(full_name)
    owner, _sep, ref = (body.get('head') or '').partition(':')
    head_repo = '%s/%s' % (owner, info['name'])
    if head_repo not in fake.repos or 'refs/heads/' + ref not in fake.refs(head_repo):
        raise _Reply(422, {'message': 'Validation Failed', 'errors': [{'field': 'head'}]})
    for pull in fake.pulls:
        if pull['state'] == 'open' and (pull['head_repo'], pull['head_ref']) == (head_repo, ref):
            raise _Reply(422, {'message': 'A pull request already exists for %s.' % body['head']})
    pull = {
        'number': len(fake.pulls) + 1,
        'state': 'open',
        'merged': False,
        'title': body.get('title', ''),
        'body': body.get('body', ''),
        'user': user,
        'head_repo': head_repo,
        'head_ref': ref,
        'base_repo': full_name,
        'base_ref': body.get('base') or info['default_branch'],
    }
    fake.pulls.append(pull)
    return 201, fake.pull_json(pull)


@_public
def _get_pull(fake, user, query, body, full_name, number):
    fake._repository(full_name)
    found = [p for p in fake.pulls if p['number'] == int(number) and p['base_repo'] == full_name]
    if not found:
        raise _Reply(404, {'message': 'Not Found'})
    return 200, fake.pull_json(found[0])


def _activity(kind):
    @_public
    def handler(fake, user, query, body, full_name, number):
        _get_pull(fake, user, query, body, full_name, number)
        return 200, [
            {'id': i, 'user': {'login': login}, 'body': text}
            for n, k, i, login, text in fake.reviews
            if n == int(number) and k == kind
        ]

    return handler


_NAME = r'/repos/([^/]+/[^/]+)'
_ROUTES = (
    ('GET', r'/user', _user),
    ('GET', _NAME, _get_repository),
    ('GET', _NAME + r'/forks', _list_forks),
    ('POST', _NAME + r'/forks', _create_fork),
    ('GET', _NAME + r'/branches', _branches),
    ('GET', _NAME + r'/pulls', _list_pulls),
    ('POST', _NAME + r'/pulls', _create_pull),
    ('GET', _NAME + r'/pulls/(\d+)', _get_pull),
    ('GET', _NAME + r'/pulls/(\d+)/reviews', _activity('review')),
    ('GET', _NAME + r'/pulls/(\d+)/comments', _activity('comment')),
    ('GET', r'/repos/([^/]+/[^/]+)/issues/(\d+)/comments', _activity('note')),
)

_REASONS = {
    200: 'OK',
    201: 'Created',
    202: 'Accepted',
    400: 'Bad Request',
    401: 'Unauthorized',
    403: 'Forbidden',
    404: 'Not Found',
    422: 'Unprocessable Entity',
}


def main():
    parser = argparse.ArgumentParser(description='A fake GitHub on 127.0.0.1.')
    parser.add_argument('--folder', required=True, help='where the repositories are kept')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--seed', help="a folder of PO files: the project team/program's po/")
    args = parser.parse_args()
    fake = FakeForge(args.folder)
    if args.seed:
        shutil.rmtree(args.folder, ignore_errors=True)
        files = {}
        for name in sorted(os.listdir(args.seed)):
            with open(os.path.join(args.seed, name), 'rb') as f:
                files['po/' + name] = f.read()
        files['README'] = b'Team Program\n'
        fake.add_repository('%s/program' % 'team', files, pushers=[MAINTAINER])
    fake.start(args.port)
    print('Fake GitHub at %s (API %s), user %s, token %s' % (fake.base, fake.api, USER, TOKEN))
    print('Project: %s' % fake.clone_url('team/program'), flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        fake.stop()


if __name__ == '__main__':
    main()
