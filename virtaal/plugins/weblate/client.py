#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Weblate's REST API over httpclient (curl), on the GLib main loop and
never blocking it (#3337). No GTK here.

Every request sends the token in an Authorization header, which nothing
logs, and only to URLs under the server's API URL. Lists are paged:
each page's "next" link is followed. A 429 or a busy server is waited
out (Retry-After), and so is a used-up X-RateLimit-Remaining; the last
X-RateLimit headers seen are the budget() a Submit paces itself by,
kept in budgets, which the platform shares between its clients of one
server and token.
A unit is changed with PATCH, which is safe to send again.
"""

import json
import logging
import time
from urllib import parse

from gi.repository import GLib

from virtaal.plugins.tm.models.remotetm import retry_after
from virtaal.support.httpclient import HTTPClient, HTTPRequest, loggable_url
from virtaal.support.platforms import AUTH, HTTP, NETWORK, NOT_PLATFORM, Budget, PlatformError

_LOOPBACK = ('localhost', '127.0.0.1', '::1')
_BUSY = (502, 503, 504)


def api_url(text):
    """The API URL ("https://host/api/") for what a user typed, a server
    or its API address, or None. A token only travels over https, or
    plain http to this machine."""
    text = (text or '').strip()
    if not text:
        return None
    if '://' not in text:
        text = 'https://' + text
    try:
        parts = parse.urlsplit(text)
        host = parts.hostname
    except ValueError:
        return None
    if not host or parts.username or parts.password:
        return None
    if parts.scheme != 'https' and not (parts.scheme == 'http' and host in _LOOPBACK):
        return None
    path = parts.path.rstrip('/')
    if not path.endswith('/api'):
        path += '/api'
    return parse.urlunsplit((parts.scheme, parts.netloc.lower(), path + '/', '', ''))


def unencrypted(text):
    """Whether text is a plain http address of another machine, the one
    kind of address api_url() refuses for the token's sake."""
    text = (text or '').strip()
    try:
        parts = parse.urlsplit(text)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme == 'http' and bool(host) and host not in _LOOPBACK


def path_part(slug):
    """A slug in a URL path; a category's '/' is escaped twice, as
    Weblate wants it ("category%252Fcomponent")."""
    return parse.quote(parse.quote(slug, safe=''), safe='')


def error_detail(body):
    """The message in a Weblate error reply: 5.10's
    {"errors": [{"detail": ...}]}, or the older {"detail": ...}."""
    try:
        reply = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return ''
    if not isinstance(reply, dict):
        return ''
    errors = reply.get('errors')
    if isinstance(errors, list):
        details = [e.get('detail') for e in errors if isinstance(e, dict)]
        return ' '.join(str(d) for d in details if d)
    return str(reply.get('detail') or '')


def _later(seconds, callback):
    GLib.timeout_add(int(seconds * 1000), lambda: callback() and False)


class Client:
    """One Weblate server, as its API URL and a token."""

    MAX_TRIES = 4
    """Tries of one request, when the server asks to wait."""
    RETRY_AFTER = 30
    """Seconds to wait after a 429 without a Retry-After."""
    MAX_WAIT = 120
    """The longest wait before a request: beyond it, the request fails."""
    PAGE_SIZE = 100
    UNITS_PAGE_SIZE = 1000

    schedule = staticmethod(_later)
    """Calls callback() after seconds: GLib's timer, or a test's."""

    def __init__(self, url, token, http=None, budgets=None):
        self.api = api_url(url)
        self.token = token
        if http is None:
            http = HTTPClient()
            http.set_virtaal_useragent()
        self.http = http
        self._resume_at = 0.0
        self._generation = 0
        self._budgets = {} if budgets is None else budgets

    def __repr__(self):
        return '<Weblate %s>' % self.api

    def cancel(self):
        """Drops the replies to every request made so far."""
        self._generation += 1

    def url(self, path, params=None):
        url = self.api + path.lstrip('/')
        if params:
            url += '?' + parse.urlencode(params)
        return url

    def _own(self, url):
        """url if it is under the API URL, else the same path and query
        under it (a proxy may report another scheme or host), or None."""
        if url.startswith(self.api):
            return url
        parts = parse.urlsplit(url)
        api_path = parse.urlsplit(self.api).path
        if not parts.path.startswith(api_path):
            return None
        return self.api + parts.path[len(api_path) :] + ('?' + parts.query if parts.query else '')

    # One request #

    def get_json(self, url, on_reply, on_error):
        """Asks for url and calls on_reply(data) with its JSON, or
        on_error(PlatformError)."""
        self._get(url, on_reply, on_error, False)

    def get_bytes(self, url, on_reply, on_error):
        """Downloads url (a file) and calls on_reply(its bytes), or
        on_error(PlatformError)."""
        self._get(url, on_reply, on_error, True)

    def patch_json(self, url, data, on_reply, on_error):
        """Sends data as JSON to url with PATCH, and calls on_reply(the
        JSON reply), or on_error(PlatformError)."""
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self._get(url, on_reply, on_error, False, ('PATCH', body))

    def _get(self, url, on_reply, on_error, raw, write=None):
        generation = self._generation

        def current(handler):
            return lambda *args: generation == self._generation and handler(*args)

        self._send(url, current(on_reply), current(on_error), 1, raw=raw, write=write)

    def _send(self, url, on_reply, on_error, attempt, waited=False, raw=False, write=None):
        wait = 0 if waited else self._resume_at - time.monotonic()
        if wait > self.MAX_WAIT:
            on_error(PlatformError(HTTP, 429))
            return
        if wait > 0:
            self.schedule(
                wait,
                lambda: self._send(url, on_reply, on_error, attempt, True, raw=raw, write=write),
            )
            return
        accept = 'Accept: */*' if raw else 'Accept: application/json'
        headers = ['Authorization: Token ' + self.token, accept]
        method, body = write or ('GET', None)
        if body is not None:
            headers.append('Content-Type: application/json')
        request = HTTPRequest(
            url,
            method=method,
            data=body,
            headers=headers,
            user_agent=self.http.user_agent,
            # A file: stall detection instead of the short total timeout.
            download=raw,
        )
        if self.http.add(request) is False:
            on_error(PlatformError(NETWORK))
            return

        def done(request, _value):
            self._replied(request, url, on_reply, on_error, attempt, raw, write)

        def failed(request, reason):
            on_error(PlatformError(NETWORK, detail=str(reason)))

        for signal in ('http-success', 'http-redirect', 'http-client-error', 'http-server-error'):
            request.connect(signal, done)
        request.connect('http-failed', failed)

    def _note_limit(self, request):
        """Keeps the budget the server reports, and waits out a used-up
        rate limit before the next request."""
        try:
            remaining = int(request.response_header('X-RateLimit-Remaining'))
            reset = max(0.0, float(request.response_header('X-RateLimit-Reset')))
        except (TypeError, ValueError):
            return
        try:
            limit = int(request.response_header('X-RateLimit-Limit'))
        except (TypeError, ValueError):
            limit = None
        now = time.monotonic()
        self._budgets[self._budget_key()] = (remaining, now + reset, limit)
        if remaining <= 0:
            self._resume_at = now + reset

    def _budget_key(self):
        return self.api, self.token

    def budget(self):
        """The Budget as the server last reported it, or None if it
        doesn't."""
        known = self._budgets.get(self._budget_key())
        if known is None:
            return None
        remaining, reset_at, limit = known
        return Budget(remaining, max(0.0, reset_at - time.monotonic()), limit)

    def _replied(self, request, url, on_reply, on_error, attempt, raw=False, write=None):
        status = request.status
        body = request.result.getvalue()
        self._note_limit(request)
        if status == 429 or status in _BUSY:
            if self._retry(request, url, on_reply, on_error, attempt, raw, write):
                return
            on_error(PlatformError(HTTP, status, error_detail(body)))
        elif status == 401:
            on_error(PlatformError(AUTH, status, error_detail(body)))
        elif status == 200 and raw:
            on_reply(body)
        elif status in (200, 201):
            try:
                data = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                on_error(PlatformError(NOT_PLATFORM, status))
                return
            on_reply(data)
        elif status == 404 or status < 400:
            on_error(PlatformError(NOT_PLATFORM, status))
        else:
            on_error(PlatformError(HTTP, status, error_detail(body)))

    def _retry(self, request, url, on_reply, on_error, attempt, raw=False, write=None):
        """Sends the request again after the wait the server asks for
        (or one that doubles), if that is soon enough."""
        if attempt >= self.MAX_TRIES:
            return False
        default = self.RETRY_AFTER if request.status == 429 else 2 ** (attempt - 1)
        headers = request.result_headers.getvalue()
        seconds = retry_after(headers, default, self.MAX_WAIT + 1)
        if seconds > self.MAX_WAIT:
            return False
        logging.debug(
            '%s: HTTP %s, asking again in %d s', loggable_url(url), request.status, seconds
        )
        self._resume_at = max(self._resume_at, time.monotonic() + seconds)
        self._send(url, on_reply, on_error, attempt + 1, raw=raw, write=write)
        return True

    # Lists #

    def get_list(self, path, on_page, on_done, on_error, params=None):
        """Follows a paged list from path: on_page(results) for each
        page, then on_done(count), or on_error(PlatformError)."""
        params = {'page_size': self.PAGE_SIZE, **(params or {})}

        def page(data):
            if not _is_page(data):
                on_error(PlatformError(NOT_PLATFORM, 200))
                return
            on_page(data['results'])
            following = data.get('next') and self._own(data['next'])
            if following:
                self.get_json(following, page, on_error)
            else:
                on_done(data['count'])

        self.get_json(self.url(path, params), page, on_error)

    def test_connection(self, on_done, on_error):
        """on_done(number of projects the token sees), or on_error."""

        def reply(data):
            if _is_page(data):
                on_done(data['count'])
            else:
                on_error(PlatformError(NOT_PLATFORM, 200))

        self.get_json(self.url('projects/', {'page_size': 1}), reply, on_error)

    def projects_page(self, url, on_page, on_error):
        """One page of the projects: the first if url is None, else the
        page at url (a "next" link). on_page(results, next URL or None)."""

        def page(data):
            if not _is_page(data):
                on_error(PlatformError(NOT_PLATFORM, 200))
                return
            on_page(data['results'], data.get('next') and self._own(data['next']))

        if url is None:
            url = self.url('projects/', {'page_size': self.PAGE_SIZE})
        self.get_json(url, page, on_error)

    def components(self, project, on_page, on_done, on_error):
        path = 'projects/%s/components/' % path_part(project)
        self.get_list(path, on_page, on_done, on_error)

    def translations(self, project, component, on_page, on_done, on_error):
        path = 'components/%s/%s/translations/' % (path_part(project), path_part(component))
        self.get_list(path, on_page, on_done, on_error)

    # One translation #

    @staticmethod
    def _translation_path(project, component, language):
        return 'translations/%s/%s/%s/' % (
            path_part(project),
            path_part(component),
            path_part(language),
        )

    def translation(self, project, component, language, on_done, on_error):
        """on_done(the translation): its file name, its component..."""

        def reply(data):
            if isinstance(data, dict) and isinstance(data.get('component'), dict):
                on_done(data)
            else:
                on_error(PlatformError(NOT_PLATFORM, 200))

        url = self.url(self._translation_path(project, component, language))
        self.get_json(url, reply, on_error)

    def file(self, project, component, language, on_done, on_error):
        """on_done(bytes): the translation's file as Weblate stores it."""
        path = self._translation_path(project, component, language) + 'file/'
        self.get_bytes(self.url(path), on_done, on_error)

    def monolingual_base(self, project, component, on_done, on_error):
        """on_done(bytes): a monolingual component's template."""
        path = 'components/%s/%s/monolingual_base/' % (path_part(project), path_part(component))
        self.get_bytes(self.url(path), on_done, on_error)

    def units(self, project, component, language, on_page, on_done, on_error, query=None):
        """Every unit of the translation, or those a search query
        finds, a page of up to UNITS_PAGE_SIZE at a time."""
        path = self._translation_path(project, component, language) + 'units/'
        params = {'page_size': self.UNITS_PAGE_SIZE}
        if query:
            params['q'] = query
        self.get_list(path, on_page, on_done, on_error, params)

    def unit_count(self, project, component, language, on_done, on_error):
        """on_done(how many units the translation has): one page of one."""

        def reply(data):
            if _is_page(data):
                on_done(data['count'])
            else:
                on_error(PlatformError(NOT_PLATFORM, 200))

        path = self._translation_path(project, component, language) + 'units/'
        self.get_json(self.url(path, {'page_size': 1}), reply, on_error)

    def locked(self, project, component, on_done, on_error):
        """on_done(whether the component is locked)."""

        def reply(data):
            if isinstance(data, dict) and isinstance(data.get('locked'), bool):
                on_done(data['locked'])
            else:
                on_error(PlatformError(NOT_PLATFORM, 200))

        path = 'components/%s/%s/lock/' % (path_part(project), path_part(component))
        self.get_json(self.url(path), reply, on_error)

    def patch_unit(self, unit_id, fields, on_done, on_error):
        """Changes one unit's fields ({"target": [...], "state": 20}):
        on_done(the fields as written)."""
        self.patch_json(self.url('units/%d/' % int(unit_id)), fields, on_done, on_error)


def _is_page(data):
    return (
        isinstance(data, dict)
        and isinstance(data.get('count'), int)
        and isinstance(data.get('results'), list)
    )


def component_slug(component):
    """A component's slug with its category's, as its API URL has it
    ("category/component")."""
    slug = component.get('slug', '')
    url = component.get('url') or ''
    marker = '/components/'
    if marker in url:
        tail = url.split(marker, 1)[1].strip('/').split('/')
        if len(tail) == 2:
            full = parse.unquote(parse.unquote(tail[1]))
            if full.endswith(slug):
                return full
    return slug
