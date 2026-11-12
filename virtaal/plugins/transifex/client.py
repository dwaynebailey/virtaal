#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Transifex's API v3 (JSON:API) over httpclient (curl), on the GLib main
loop and never blocking it. No GTK here.

Every request sends the token as "Authorization: Bearer <token>", which
nothing logs, and only to URLs under the API URL; a file the API
redirects to elsewhere (a download's 303) is fetched without it. Lists
are paged by cursor: each page's links.next is followed. A 429 says how
long to wait only in its text ("Expected available in 60 seconds"), and
a busy server (502-504) is asked again after a wait that doubles; both
are waited out up to MAX_WAIT. Transifex sends no budget headers, so
budget() is None and Submit paces itself by the platform's rate_limit.

An asynchronous download (a translation's file, or a resource's source
file) is a job: POST it, then poll it until it redirects to the file.
"""

import json
import logging
import re
import time
from urllib import parse

from gi.repository import GLib

from virtaal.plugins.tm.models.remotetm import retry_after
from virtaal.support.httpclient import HTTPClient, HTTPRequest, loggable_url
from virtaal.support.platforms import AUTH, HTTP, NETWORK, NOT_PLATFORM, PlatformError

API_URL = 'https://rest.api.transifex.com/'
"""The API every Transifex user has."""
JSONAPI = 'application/vnd.api+json'
BULK = 'application/vnd.api+json;profile="bulk"'

_LOOPBACK = ('localhost', '127.0.0.1', '::1')
_BUSY = (502, 503, 504)
_WAIT = re.compile(r'available in (\d+) seconds?', re.IGNORECASE)

# What a request wants back.
JSON, BYTES, POLL = 'json', 'bytes', 'poll'


def api_url(text):
    """The API URL ("https://rest.api.transifex.com/") for what a user
    typed, or None. A token only travels over https, or plain http to
    this machine (a fake server)."""
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
    path = parts.path.rstrip('/') + '/'
    return parse.urlunsplit((parts.scheme, parts.netloc.lower(), path, '', ''))


def unencrypted(text):
    """Whether text is a plain http address of another machine."""
    try:
        parts = parse.urlsplit((text or '').strip())
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme == 'http' and bool(host) and host not in _LOOPBACK


def _errors(body):
    try:
        reply = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return []
    errors = reply.get('errors') if isinstance(reply, dict) else None
    return [e for e in errors if isinstance(e, dict)] if isinstance(errors, list) else []


def error_detail(body):
    """The message in a JSON:API error reply: {"errors": [{"detail": ...}]}."""
    details = [e.get('detail') or e.get('title') for e in _errors(body)]
    return ' '.join(str(d) for d in details if d)


def throttle_wait(body, default):
    """The seconds a 429's text says to wait ("Expected available in 60
    seconds."), or default."""
    found = _WAIT.search(error_detail(body))
    return int(found.group(1)) if found else default


def _later(seconds, callback):
    GLib.timeout_add(int(seconds * 1000), lambda: callback() and False)


class Client:
    """Transifex's API, as its URL and a token."""

    MAX_TRIES = 4
    """Tries of one request, when the server asks to wait."""
    RETRY_AFTER = 60
    """Seconds to wait after a 429 that doesn't say."""
    MAX_WAIT = 120
    """The longest wait before a request: beyond it, the request fails."""
    UNITS_PAGE_SIZE = 1000
    """resource_translations' largest page (150 to 1000)."""
    POLL_FIRST = 0.5
    POLL_MAX = 8
    POLL_TRIES = 40
    """How often an asynchronous download is asked whether it's ready."""

    schedule = staticmethod(_later)
    """Calls callback() after seconds: GLib's timer, or a test's."""

    def __init__(self, url, token, http=None):
        self.api = api_url(url) or API_URL
        self.token = token
        if http is None:
            http = HTTPClient()
            http.set_virtaal_useragent()
        self.http = http
        self._resume_at = 0.0
        self._generation = 0

    def __repr__(self):
        return '<Transifex %s>' % self.api

    def cancel(self):
        """Drops the replies to every request made so far."""
        self._generation += 1

    def url(self, path, params=None):
        url = self.api + path.lstrip('/')
        if params:
            url += '?' + parse.urlencode(params, safe=':[]')
        return url

    def own(self, link):
        """A link of the API's (absolute, or a path such as
        "/projects?page[cursor]=..."), as a URL under the API URL, or
        None if it points elsewhere."""
        if not link:
            return None
        url = parse.urljoin(self.api, link)
        if url.startswith(self.api):
            return url
        parts = parse.urlsplit(url)
        api_path = parse.urlsplit(self.api).path
        if not parts.path.startswith(api_path):
            return None
        tail = parts.path[len(api_path) :] + ('?' + parts.query if parts.query else '')
        return self.api + tail

    # One request #

    def get_json(self, url, on_reply, on_error):
        self.request(url, on_reply, on_error, JSON)

    def get_bytes(self, url, on_reply, on_error):
        """A file: with the token only when it is under the API URL."""
        self.request(url, on_reply, on_error, BYTES)

    def send_json(self, method, url, data, on_reply, on_error, content_type=JSONAPI):
        """Sends data as JSON (POST, PATCH): on_reply(the JSON reply, or
        None for none)."""
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.request(url, on_reply, on_error, JSON, (method, body, content_type))

    def request(self, url, on_reply, on_error, want, write=None):
        generation = self._generation

        def current(handler):
            return lambda *args: generation == self._generation and handler(*args)

        self._send(url, current(on_reply), current(on_error), want, write, 1)

    def _headers(self, url, want, write):
        headers = ['Accept: ' + ('*/*' if want == BYTES else JSONAPI)]
        if url.startswith(self.api):
            headers.append('Authorization: Bearer ' + self.token)
        if write is not None:
            headers.append('Content-Type: ' + write[2])
        return headers

    def _send(self, url, on_reply, on_error, want, write, attempt, waited=False):
        wait = 0 if waited else self._resume_at - time.monotonic()
        if wait > self.MAX_WAIT:
            on_error(PlatformError(HTTP, 429))
            return
        if wait > 0:
            self.schedule(
                wait, lambda: self._send(url, on_reply, on_error, want, write, attempt, True)
            )
            return
        method, body = (write or ('GET', None, None))[:2]
        request = HTTPRequest(
            url,
            method=method,
            data=body,
            headers=self._headers(url, want, write),
            user_agent=self.http.user_agent,
            download=want == BYTES,
        )
        if self.http.add(request) is False:
            on_error(PlatformError(NETWORK))
            return

        def done(request, _value):
            self._replied(request, url, on_reply, on_error, want, write, attempt)

        for signal in ('http-success', 'http-redirect', 'http-client-error', 'http-server-error'):
            request.connect(signal, done)
        request.connect(
            'http-failed', lambda _r, reason: on_error(PlatformError(NETWORK, detail=str(reason)))
        )

    def _replied(self, request, url, on_reply, on_error, want, write, attempt):
        status = request.status
        body = request.result.getvalue()
        if status == 429 or status in _BUSY:
            if not self._retry(request, url, on_reply, on_error, want, write, attempt):
                on_error(PlatformError(HTTP, status, error_detail(body)))
        elif status == 401:
            on_error(PlatformError(AUTH, status, error_detail(body)))
        elif want == POLL and status in (200, 202, 303):
            on_reply(status, request.response_header('Location'), _json(body))
        elif want == BYTES and status == 200:
            on_reply(body)
        elif status == 204 or (status in (200, 201, 202) and not body):
            on_reply(None)
        elif status in (200, 201, 202):
            data = _json(body)
            if data is None:
                on_error(PlatformError(NOT_PLATFORM, status))
            else:
                on_reply(data)
        elif status == 404 or status < 400:
            on_error(PlatformError(NOT_PLATFORM, status, error_detail(body)))
        else:
            on_error(PlatformError(HTTP, status, error_detail(body)))

    def _retry(self, request, url, on_reply, on_error, want, write, attempt):
        """Sends the request again after the wait the server asks for (or
        one that doubles), if that is soon enough. A PATCH and a job's
        POST are safe to send again."""
        if attempt >= self.MAX_TRIES:
            return False
        if request.status == 429:
            default = throttle_wait(request.result.getvalue(), self.RETRY_AFTER)
        else:
            default = 2 ** (attempt - 1)
        headers = request.result_headers.getvalue()
        seconds = retry_after(headers, default, self.MAX_WAIT + 1)
        if seconds > self.MAX_WAIT:
            return False
        logging.debug(
            '%s: HTTP %s, asking again in %d s', loggable_url(url), request.status, seconds
        )
        self._resume_at = max(self._resume_at, time.monotonic() + seconds)
        self._send(url, on_reply, on_error, want, write, attempt + 1)
        return True

    # Lists #

    def get_list(self, path, params, on_page, on_done, on_error):
        """Follows a list from path by its links.next: on_page(data,
        included) for each page, then on_done(), or on_error."""

        def page(reply):
            if not _is_document(reply):
                on_error(PlatformError(NOT_PLATFORM, 200))
                return
            on_page(reply['data'], reply.get('included') or [])
            following = self.own((reply.get('links') or {}).get('next'))
            if following:
                self.get_json(following, page, on_error)
            else:
                on_done()

        self.get_json(self.url(path, params), page, on_error)

    def get_page(self, url, on_page, on_error):
        """One page: on_page(data, the next page's URL or None)."""

        def page(reply):
            if not _is_document(reply):
                on_error(PlatformError(NOT_PLATFORM, 200))
                return
            on_page(reply['data'], self.own((reply.get('links') or {}).get('next')))

        self.get_json(url, page, on_error)

    def get_one(self, path, on_done, on_error):
        """on_done(the object's data) at path, such as "projects/o:a:p:b"."""

        def reply(data):
            if isinstance(data, dict) and isinstance(data.get('data'), dict):
                on_done(data['data'])
            else:
                on_error(PlatformError(NOT_PLATFORM, 200))

        self.get_json(self.url(path), reply, on_error)

    # Asynchronous downloads #

    def download_job(self, kind, attributes, relationships, on_done, on_error):
        """Starts an asynchronous download (kind: resource_translations_
        async_downloads, resource_strings_async_downloads) and polls it
        until it gives the file: on_done(bytes)."""
        data = {
            'data': {
                'type': kind,
                'attributes': attributes,
                'relationships': {
                    name: {'data': {'type': name + 's', 'id': ident}}
                    for name, ident in relationships.items()
                },
            }
        }

        def started(reply):
            job = (reply or {}).get('data') or {}
            if not job.get('id'):
                on_error(PlatformError(NOT_PLATFORM, 202))
                return
            self._poll(self.url('%s/%s' % (kind, job['id'])), 1, on_done, on_error)

        self.send_json('POST', self.url(kind), data, started, on_error)

    def _poll(self, url, tries, on_done, on_error):
        def reply(status, location, data):
            if status == 303 and location:
                self.get_bytes(parse.urljoin(url, location), on_done, on_error)
                return
            attributes = ((data or {}).get('data') or {}).get('attributes') or {}
            if attributes.get('status') == 'failed' or tries >= self.POLL_TRIES:
                detail = ' '.join(str(e.get('detail', e)) for e in attributes.get('errors') or [])
                on_error(PlatformError(HTTP, status, detail))
                return
            wait = min(self.POLL_FIRST * 2 ** (tries - 1), self.POLL_MAX)
            self.schedule(wait, lambda: self._poll(url, tries + 1, on_done, on_error))

        self.request(url, reply, on_error, POLL)


def _json(body):
    try:
        return json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None


def _is_document(reply):
    return isinstance(reply, dict) and isinstance(reply.get('data'), list)
