#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""GitHub's REST API, for a Git project whose repository the user can't
push to: fork.py submits through the user's fork and a pull request.
Called from a git thread, so every request blocks. No GTK here.

A repository is on GitHub when its URL's host is github.com (the API
at api.github.com) or one gitprojects.ini maps to a GitHub Enterprise
API ([github] host = API URL). The API takes a personal access token,
sent only to that API URL, never over plain http but to this computer;
Tokens keeps it for the session, or in the system's password store
(keyring) if the user ticks it, never in Virtaal's settings.

Every reply's X-RateLimit headers are the budget() Submit paces itself
by. A rate limit (429, or 403 with no requests left or a Retry-After)
is waited out when that is at most MAX_WAIT seconds, else the request
fails with HTTP 429 and the budget says until when.

TODO: GitHub's device flow (a browser sign-in) needs an OAuth app
registered for Virtaal; until then the user makes a token.
"""

import json
import logging
import re
import time
from urllib import parse

from virtaal.support import gitrepo
from virtaal.support.gitrepo import Credential
from virtaal.support.platforms import AUTH, HTTP, NETWORK, NOT_PLATFORM, Budget, PlatformError

GITHUB = 'github.com'
API = 'https://api.github.com'
SERVICE = 'Virtaal GitHub %s'
"""The password store's entry for a GitHub host's token."""
ACCOUNT = 'token'

FORK_TIMEOUT = 'forge-fork-timeout'
"""A PlatformError's kind: GitHub didn't finish making the fork in time."""
MOVED = 'forge-moved'
"""A PlatformError's kind: the fork's pull request branch has commits
Virtaal didn't push."""

TIMEOUT = 60
_BUSY = (502, 503, 504)


class ForgeError(Exception):
    """A request failed: error, its PlatformError."""

    def __init__(self, kind, status=None, detail=''):
        super().__init__(kind)
        self.error = PlatformError(kind, status, detail)


_SCP = re.compile(r'^(?:[^@/\s]+@)?([^/\s:]+):(.+)$')


class Forge:
    """A project's repository on GitHub: its host, API URL, owner and
    name."""

    def __init__(self, host, api, owner, name):
        self.host = host
        self.api = api.rstrip('/')
        self.owner = owner
        self.name = name

    @property
    def full_name(self):
        return '%s/%s' % (self.owner, self.name)


def _host_and_path(url):
    parts = parse.urlsplit(url)
    if parts.scheme and parts.netloc:
        host = parts.netloc.rpartition('@')[2].lower()
        return host, parts.path
    found = _SCP.match(url)
    if found and '://' not in url:
        return found.group(1).lower(), found.group(2)
    return None, None


def api_allowed(api):
    """Whether a token may go to api: https, or http to this computer."""
    parts = parse.urlsplit(api)
    if parts.scheme == 'https':
        return bool(parts.hostname)
    return parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost', '::1')


def find(url, hosts=None):
    """The Forge of a repository's URL, or None when it isn't GitHub's:
    github.com, or one of hosts ({host[:port]: API URL})."""
    host, path = _host_and_path(url or '')
    if host is None:
        return None
    apis = {GITHUB: API, **{h.lower(): a for h, a in (hosts or {}).items()}}
    api = apis.get(host) or apis.get(host.partition(':')[0])
    if api is None or not api_allowed(api):
        return None
    parts = [p for p in re.sub(r'\.git/*$', '', path).split('/') if p]
    if len(parts) != 2:
        return None
    return Forge(host.partition(':')[0] if host.startswith(GITHUB) else host, api, *parts)


class TokenStore:
    """The system's password store, through keyring, for tokens."""

    def __init__(self, keyring_store=None):
        from .credentials import KeyringStore

        self._store = keyring_store or KeyringStore()

    def usable(self):
        return self._store.usable()

    def get(self, host):
        backend = self._store.backend()
        if backend is None:
            return None
        try:
            return backend.get_password(SERVICE % host, ACCOUNT) or None
        except Exception as error:  # A locked or refused store: as if it had none.
            logging.info("The password store can't be read: %s", error)
            return None

    def put(self, host, token):
        backend = self._store.backend()
        if backend is None:
            return
        try:
            backend.set_password(SERVICE % host, ACCOUNT, token)
        except Exception as error:  # Kept for the session only.
            logging.warning("The password store didn't keep the token: %s", error)


class Tokens:
    """A GitHub host's token: this session's, then the password store's,
    then the user's. ask(host, refused) answers a Credential (its
    password the token) or None; None for no one to ask."""

    def __init__(self, store=None, ask=None):
        self.store = store or TokenStore()
        self.ask = ask
        self.session = {}

    def known(self, host):
        return self.session.get(host) or self.store.get(host)

    def candidates(self, host, ask=True):
        """The tokens to try in turn, the known one first."""
        found = self.known(host)
        if found:
            yield Credential('', found)
        refused = found is not None
        while ask and self.ask is not None:
            answer = self.ask(host, refused)
            if answer is None:
                return
            yield answer
            refused = True

    def accepted(self, host, credential):
        self.session[host] = credential.password
        if credential.remember:
            credential.remember = False
            self.store.put(host, credential.password)

    def refused(self, host):
        self.session.pop(host, None)


def _header(headers, name):
    value = headers.get(name)
    return value if value is None else str(value)


def _seconds(value, default, since=0.0):
    """value (a number of seconds, or a time since since) as seconds to
    wait, at least 1; default if it isn't a number."""
    try:
        return max(1.0, float(value) - since)
    except (TypeError, ValueError):
        return float(default)


def _message(data):
    return data.get('message', '') if isinstance(data, dict) else ''


class GitHub:
    """One GitHub (or Enterprise) API, as a Forge's API URL and a token.
    budgets is shared by every client of the platform."""

    MAX_TRIES = 4
    MAX_WAIT = 120
    """The longest wait for a rate limit: beyond it, the request fails."""
    RETRY_AFTER = 60
    """Seconds to wait after a secondary rate limit without a Retry-After."""
    FORK_WAIT = (1, 2, 4, 8, 15, 30)
    """The waits for a new fork to be ready, in turn."""

    def __init__(self, forge, tokens, budgets=None, http=None, sleep=time.sleep):
        self.forge = forge
        self.tokens = tokens
        self.budgets = {} if budgets is None else budgets
        self._http = http
        self.sleep = sleep
        self._token = None
        self._login = None
        self.ask = True
        """Whether a missing or refused token is asked for; else AUTH."""

    # Requests #

    def http(self):
        if self._http is None:
            from dulwich.client import default_urllib3_manager

            self._http = default_urllib3_manager(gitrepo.config(), timeout=TIMEOUT)
        return self._http

    def _send(self, method, path, body, token):
        from virtaal.__version__ import ver

        headers = {
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'Virtaal/%s' % ver,
            'Authorization': 'Bearer %s' % token,
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        try:
            reply = self.http().request(
                method, self.forge.api + path, body=data, headers=headers, redirect=False
            )
        except Exception as error:  # urllib3's own: no reply.
            raise ForgeError(NETWORK, detail=str(error)) from error
        try:
            found = json.loads(reply.data.decode('utf-8')) if reply.data else None
        except (UnicodeDecodeError, ValueError):
            found = None
        return reply.status, reply.headers, found

    def _tokens(self):
        """The tokens to try: the one in use, then the candidates."""
        if self._token is not None:
            yield self._token
        yield from self.tokens.candidates(self.forge.host, self.ask)

    def request(self, method, path, body=None):
        """(status, JSON) of a request: 2xx, 404 or 422. Signs in, and
        waits out a short rate limit; else raises ForgeError."""
        offers = self._tokens()
        credential = next(offers, None)
        tries = 0
        refused = set()
        while credential is not None:
            if credential.password in refused:
                credential = next(offers, None)
                continue
            status, headers, data = self._send(method, path, body, credential.password)
            self._note_budget(headers, credential.password)
            if status == 401:
                refused.add(credential.password)
                self.tokens.refused(self.forge.host)
                self._token = None
                credential = next(offers, None)
                continue
            if credential is not self._token:
                self.tokens.accepted(self.forge.host, credential)
                self._token = credential
            tries += 1
            if self._wait(status, headers, data, tries):
                continue
            return self._answer(status, data)
        raise ForgeError(AUTH, 401)

    def _answer(self, status, data):
        if 200 <= status < 300 or status in (404, 422):
            return status, data
        if status == 429 or status in _BUSY:
            raise ForgeError(HTTP, 429 if status == 429 else status, _message(data))
        if 300 <= status < 400:
            raise ForgeError(NOT_PLATFORM, status)
        raise ForgeError(HTTP, status, _message(data))

    def _note_budget(self, headers, token):
        try:
            left = int(_header(headers, 'X-RateLimit-Remaining'))
            reset = float(_header(headers, 'X-RateLimit-Reset'))
        except (TypeError, ValueError):
            return
        try:
            limit = int(_header(headers, 'X-RateLimit-Limit'))
        except (TypeError, ValueError):
            limit = None
        reset_at = time.monotonic() + max(0.0, reset - time.time())
        self.budgets[(self.forge.api, token)] = (left, reset_at, limit)

    def budget(self):
        """The Budget as GitHub last said, or None."""
        if self._token is None:
            return None
        known = self.budgets.get((self.forge.api, self._token.password))
        if known is None:
            return None
        left, reset_at, limit = known
        return Budget(left, max(0.0, reset_at - time.monotonic()), limit)

    def _limited(self, status, headers, data):
        """Seconds a rate limit asks to wait, or None if it isn't one."""
        if status not in (403, 429):
            return None
        retry = _header(headers, 'Retry-After')
        if retry is None and _header(headers, 'X-RateLimit-Remaining') == '0':
            reset = _header(headers, 'X-RateLimit-Reset')
            return _seconds(reset, self.RETRY_AFTER, time.time())
        if retry is not None:
            return _seconds(retry, self.RETRY_AFTER)
        if status == 429 or 'rate limit' in _message(data).lower():
            return float(self.RETRY_AFTER)
        return None

    def _wait(self, status, headers, data, tries):
        """Whether to send again: after a rate limit's wait, or a busy
        server's. A wait too long fails with 429, the budget used up."""
        seconds = self._limited(status, headers, data)
        if seconds is None:
            if status in _BUSY and tries < self.MAX_TRIES:
                self.sleep(2 ** (tries - 1))
                return True
            return False
        if seconds > self.MAX_WAIT or tries >= self.MAX_TRIES:
            if self._token is not None:
                limit = (self.budget() or Budget(0, 0)).limit
                key = (self.forge.api, self._token.password)
                self.budgets[key] = (0, time.monotonic() + seconds, limit)
            raise ForgeError(HTTP, 429, _message(data))
        logging.debug('GitHub: HTTP %s, asking again in %d s', status, seconds)
        self.sleep(seconds)
        return True

    def get(self, path):
        return self.request('GET', path)

    # The project and the user #

    def login(self):
        """The user's login, as the token's."""
        if self._login is None:
            _status, data = self.get('/user')
            self._login = (data or {}).get('login') or ''
        return self._login

    def repository(self, full_name):
        status, data = self.get('/repos/%s' % full_name)
        return data if status == 200 and isinstance(data, dict) else None

    # The fork #

    def _forks_of_project(self, data):
        if not data or not data.get('fork'):
            return False
        names = {(data.get(k) or {}).get('full_name') for k in ('parent', 'source')}
        return self.forge.full_name in names

    def fork(self):
        """The user's fork of the project (its API data), made if there
        is none, once GitHub has it ready."""
        login = self.login()
        found = self.repository('%s/%s' % (login, self.forge.name))
        if not self._forks_of_project(found):
            found = self._listed_fork(login)
        if found is None:
            status, found = self.request('POST', '/repos/%s/forks' % self.forge.full_name, {})
            if status not in (200, 202) or not isinstance(found, dict):
                raise ForgeError(HTTP, status, _message(found))
        self.wait_ready(found['full_name'])
        return found

    def _listed_fork(self, login):
        status, forks = self.get('/repos/%s/forks?per_page=100' % self.forge.full_name)
        if status != 200 or not isinstance(forks, list):
            return None
        return next((f for f in forks if (f.get('owner') or {}).get('login') == login), None)

    def wait_ready(self, full_name):
        """Returns once the fork has its branches (GitHub copies them a
        while after the fork is made), else FORK_TIMEOUT."""
        for wait in (0, *self.FORK_WAIT):
            if wait:
                self.sleep(wait)
            status, branches = self.get('/repos/%s/branches?per_page=1' % full_name)
            if status == 200 and branches:
                return
        raise ForgeError(FORK_TIMEOUT)

    # Pull requests #

    def pull(self, number):
        status, data = self.get('/repos/%s/pulls/%d' % (self.forge.full_name, number))
        return data if status == 200 else None

    def open_pull(self, head):
        """The open pull request from head ("user:branch"), or None."""
        path = '/repos/%s/pulls?state=open&head=%s' % (
            self.forge.full_name,
            parse.quote(head, safe=':/'),
        )
        status, found = self.get(path)
        return found[0] if status == 200 and found else None

    def create_pull(self, head, base, title, body):
        """The pull request opened from head, or the one already open."""
        status, data = self.request(
            'POST',
            '/repos/%s/pulls' % self.forge.full_name,
            {
                'title': title,
                'body': body,
                'head': head,
                'base': base,
                'maintainer_can_modify': True,
            },
        )
        if status == 201:
            return data
        found = self.open_pull(head) if status == 422 else None
        if found is None:
            raise ForgeError(HTTP, status, _message(data))
        return found

    def review_activity(self, number, author):
        """The ids of reviews and comments on a pull request by anyone
        but author: "review 12", "comment 7", "note 3"."""
        found = set()
        where = self.forge.full_name
        lists = (
            ('review', '/repos/%s/pulls/%d/reviews?per_page=100'),
            ('comment', '/repos/%s/pulls/%d/comments?per_page=100'),
            ('note', '/repos/%s/issues/%d/comments?per_page=100'),
        )
        for kind, path in lists:
            status, items = self.get(path % (where, number))
            if status != 200 or not isinstance(items, list):
                continue
            for item in items:
                if (item.get('user') or {}).get('login') != author:
                    found.add('%s %s' % (kind, item.get('id')))
        return found
