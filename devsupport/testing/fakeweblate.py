#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A fake Weblate server on 127.0.0.1 for tests and hand checks (#3337).

It holds projects, components and translations in memory and serves
the API endpoints Virtaal uses, with Weblate's paging (count, next,
previous, results; page and page_size), its token check, rate-limit
headers and error shapes. Every reply it builds is checked against
weblate_schema.py, and any mismatch is kept in schema_problems.

Faults are injected per request: fail() answers the next matching
requests with a status (429 with Retry-After, a 5xx, ...) or drops the
connection, and not_weblate makes it answer like some other web site.

Routes are a table (ROUTES) of method, path pattern and handler, so
later phases add theirs. A translation can hold a file to download and
its units (fill_po(), fill_json(), fill_android()), paged as Weblate
pages units, with ids, states and last_updated, and searched with
q=changed:>=<time>.

For Submit: PATCH /api/units/<id>/ changes a unit's target and state
(checked as Weblate checks them: the states it accepts, all of a
plural's forms), and the component's lock/ says whether it is locked.
edit() changes a unit as another translator would; forbid() answers
403 for a unit, fail_after() fails the PATCHes after the first k,
enforce() stores "needs editing" for "translated" as an enforced check
does, and old_states refuses 11 and 12 as Weblate before 2026.x does.

For Update: a translation's file is written from its units at each
download, as Weblate writes it from its database, so others' edits
show in it. add_unit(), remove_unit() and change_source() change the
set of units, as a change to the source strings in the project would.
offline drops every connection unanswered, as a machine with no
network sees it, until it is set back.

For review: a unit's suggestions (list, add, accept, delete) and
comments (list, add, with their scope: translation, global or report),
suggest() and comment() adding them as others would. permissions are
what the token may do, as Weblate's permission names (revoke() takes
some away): without unit.edit a change is refused (403), without
unit.review, or with the project's reviews off, approving is (400, as
Weblate refuses it). version is the Weblate the fake behaves as, by
what its API has: suggestions from 2026.8, adding a comment from 5.12
and reading them from 5.15 (405 before, 404 before 5.12). A unit's
global and report comments are on its source unit, as in Weblate.

The server itself and run_until() are fakeplatform.py's, shared with
any other platform's fake. Never point Virtaal at a real Weblate server
from a test.

Run by hand:  python -m devsupport.testing.fakeweblate --port 8765
"""

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib import parse

from devsupport.testing import weblate_schema
from devsupport.testing.fakeplatform import FakeServer, run_until

__all__ = ['TOKEN', 'FakeWeblate', 'demo', 'demo_review', 'run_until']

TOKEN = 'wlu_fake0123456789abcdefghijklmnopqrstuv'
"""The personal token the server accepts by default."""

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 1000

PERMISSIONS = frozenset(
    {
        'unit.edit',
        'unit.review',
        'suggestion.add',
        'suggestion.accept',
        'suggestion.delete',
        'comment.add',
    }
)
"""The permissions the fake knows, as Weblate names them."""

NEWEST = (2026, 11)
SUGGESTIONS_SINCE = (2026, 8)
COMMENTS_ADDED_SINCE = (5, 12)
COMMENTS_LISTED_SINCE = (5, 15)


def _quote_slug(slug):
    return parse.quote(parse.quote(slug, safe=''), safe='')


class _Fault:
    def __init__(self, path, status, times, headers, body, method):
        self.path = path
        self.status = status
        self.times = times
        self.headers = headers or {}
        self.body = body
        self.method = method


class FakeWeblate(FakeServer):
    """The server's data and behaviour; start() serves it."""

    def __init__(self, tokens=None, error_style='standard'):
        super().__init__()
        self.tokens = {TOKEN: None} if tokens is None else dict(tokens)
        """Accepted token: the one project slug it may see, or None
        for all (a wlp_ project token sees one)."""
        self.error_style = error_style
        """'standard' (Weblate 5.10+) or 'legacy' ({"detail": ...})."""
        self.not_weblate = False
        """Answers every request with a web page, like another site."""
        self.offline = False
        """Drops every connection unanswered and unrecorded."""
        self.rate_limit = None
        """Requests allowed per window and token, or None."""
        self.rate_window = 3600
        self.projects = {}
        self.units = {}
        """{id: unit} of every translation's units."""
        self.old_states = False
        """Refuses states 11 and 12, as Weblate before 2026.x."""
        self.forbidden = set()
        self.enforced = set()
        self.patched = []
        """(unit id, the PATCH's JSON) of each change made."""
        self.version = NEWEST
        """The Weblate version whose API the fake has, as (year, month)
        or (major, minor)."""
        self.user = 'tester'
        """The user name of the token's user."""
        self.permissions = set(PERMISSIONS)
        """What the token's user may do."""
        self.suggestions = {}
        """{id: suggestion}, each with its unit's id as 'unit_id' and its
        user's name as 'username'."""
        self.comments = {}
        """{id: comment}, each with its unit's id (a source unit's for
        global and report comments) as 'unit_id', and 'scope'."""
        self.source_units = {}
        """{source unit id: the unit}, for the comments on its source."""
        self._source_ids = 100000
        self._fail_after = None
        self._clock = 0
        self.requests = []
        self.schema_problems = []
        self._faults = []
        self._used = {}
        self._ids = 0
        self._lock = threading.Lock()

    @property
    def api_url(self):
        return self.url + 'api/'

    # Data #

    def _next_id(self):
        self._ids += 1
        return self._ids

    def add_project(self, slug, name=None, **fields):
        api = '{api}projects/%s/' % slug
        project = {
            'id': self._next_id(),
            'name': name or slug.title(),
            'slug': slug,
            'web': '',
            'translation_review': False,
            'source_review': False,
            'locked': False,
            'machinery_settings': '{}',
            'url': api,
            'web_url': '{web}projects/%s/' % slug,
            'components_list_url': api + 'components/',
            'repository_url': api + 'repository/',
            'statistics_url': api + 'statistics/',
            'categories_url': api + 'categories/',
            'changes_list_url': api + 'changes/',
            'languages_url': api + 'languages/',
            'labels_url': api + 'labels/',
            'lock_url': api + 'lock/',
            'metrics_url': api + 'metrics/',
            'reports_url': api + 'reports/',
            'announcements_url': api + 'announcements/',
            **_effective(),
            **fields,
        }
        self.projects[slug] = {'project': project, 'components': {}}
        return project

    def add_component(self, project, slug, name=None, category=None, **fields):
        """A component of project; with category, its slug in URLs is
        "category/slug"."""
        full = '%s/%s' % (category, slug) if category else slug
        api = '{api}components/%s/%s/' % (project, _quote_slug(full))
        component = {
            'id': self._next_id(),
            'name': name or slug.title(),
            'slug': slug,
            'project': self.projects[project]['project'],
            'category': '{api}categories/%s/' % category if category else None,
            'file_format': 'po',
            'filemask': 'po/*.po',
            'template': '',
            'is_glossary': False,
            'locked': False,
            'enable_suggestions': True,
            'check_flags': '',
            'repo': 'local:',
            'linked_component': '',
            'license_url': '',
            'addons': [],
            'task_url': None,
            'url': api,
            'web_url': '{web}projects/%s/%s/' % (project, full),
            'translations_url': api + 'translations/',
            'repository_url': api + 'repository/',
            'statistics_url': api + 'statistics/',
            'changes_list_url': api + 'changes/',
            'links_url': api + 'links/',
            'lock_url': api + 'lock/',
            'reports_url': api + 'reports/',
            'announcements_url': api + 'announcements/',
            **_effective(),
            **fields,
        }
        self.projects[project]['components'][full] = {'component': component, 'translations': {}}
        return component

    def add_translation(
        self, project, component, code, name=None, translated=0, total=10, **fields
    ):
        """A language of component, with translated of total strings."""
        parent = self.projects[project]['components'][component]
        api = '{api}translations/%s/%s/%s/' % (project, _quote_slug(component), code)
        translation = {
            'id': self._next_id(),
            'component': parent['component'],
            'language': {
                'id': self._next_id(),
                'code': code,
                'name': name or code,
                'direction': 'ltr',
                'aliases': [],
                'url': '{api}languages/%s/' % code,
                'web_url': '{web}languages/%s/' % code,
                'statistics_url': '{api}languages/%s/statistics/' % code,
            },
            'language_code': code,
            'filename': 'po/%s.po' % code,
            'revision': '',
            'is_template': False,
            'is_source': False,
            'total': total,
            'total_words': total * 3,
            'translated': translated,
            'translated_words': translated * 3,
            'translated_percent': round(100 * translated / total, 1) if total else 100.0,
            'fuzzy': 0,
            'fuzzy_words': 0,
            'fuzzy_percent': 0.0,
            'failing_checks': 0,
            'failing_checks_words': 0,
            'failing_checks_percent': 0.0,
            'have_suggestion': 0,
            'have_comment': 0,
            'last_change': '2026-11-01T12:00:00Z',
            'last_author': None,
            'url': api,
            'web_url': '{web}projects/%s/%s/%s/' % (project, component, code),
            'share_url': '{web}engage/%s/-/%s/' % (project, code),
            'translate_url': '{web}translate/%s/%s/%s/' % (project, component, code),
            'file_url': api + 'file/',
            'units_list_url': api + 'units/',
            'changes_list_url': api + 'changes/',
            'repository_url': api + 'repository/',
            'statistics_url': api + 'statistics/',
            'announcements_url': api + 'announcements/',
            **fields,
        }
        parent['translations'][code] = translation
        return translation

    def _parent(self, project, component):
        return self.projects[project]['components'][component]

    def set_file(self, project, component, code, data, units, content_type='text/plain'):
        """The bytes translation code's file download gives, and its
        units (from unit()), in file order."""
        parent = self._parent(project, component)
        parent.setdefault('files', {})[code] = (data, content_type)
        parent.setdefault('units', {})[code] = units
        self.units.update((unit['id'], unit) for unit in units)

    def set_template(self, project, component, data):
        """A monolingual component's template, its monolingual_base."""
        self._parent(project, component)['template_file'] = data

    def unit(self, project, component, code, source, target='', context='', state=None, **fields):
        """A unit as the API gives it. state is Weblate's code: 0 when
        there's no target, else 20 unless given."""
        source = source if isinstance(source, list) else [source]
        target = target if isinstance(target, list) else [target]
        if state is None:
            state = 20 if any(target) else 0
        number = self._next_id()
        self._source_ids += 1
        source_id = self._source_ids
        translation = '{api}translations/%s/%s/%s/' % (project, _quote_slug(component), code)
        unit = {
            'translation': translation,
            'source': source,
            'previous_source': '',
            'target': target,
            'id_hash': number * 7919,
            'content_hash': number * 104729,
            'location': '',
            'context': context,
            'note': '',
            'flags': 'read-only' if state == 100 else '',
            'labels': [],
            'state': state,
            'fuzzy': state in (10, 11, 12),
            'translated': state >= 20,
            'approved': state == 30,
            'position': 1,
            'has_suggestion': False,
            'has_comment': False,
            'has_failing_check': False,
            'num_words': len(' '.join(source).split()),
            'source_unit': '{api}units/%d/' % source_id,
            'priority': 100,
            'id': number,
            'explanation': '',
            'extra_flags': '',
            'pending': False,
            'timestamp': '2026-11-01T12:00:00Z',
            'last_updated': '2026-11-02T08:%02d:00Z' % (number % 60),
            'url': '{api}units/%d/' % number,
            'web_url': '{web}translate/%s/%s/%s/?checksum=%x' % (project, component, code, number),
            **fields,
        }
        self.source_units[source_id] = unit
        return unit

    def fill_po(self, project, component, code, entries):
        """Gives translation code a PO file of entries, each (context,
        source, target, Weblate state or None), and their units; a
        plural's source and target are lists of its forms. As Weblate
        does, a needs-editing state is written fuzzy, the rest plain."""
        units = [
            self.unit(project, component, code, source, target, context, state, position=position)
            for position, (context, source, target, state) in enumerate(entries, 1)
        ]
        self.set_file(project, component, code, _po_bytes(code, units), units, 'text/x-po')
        self._parent(project, component).setdefault('writers', {})[code] = _po_bytes
        return units

    def fill_json(self, project, component, code, template, translated, states=None):
        """A monolingual JSON component: template and translated are
        {key: text}; the translation's file holds translated, its units
        every key of template, with the key as context, as Weblate's."""
        states = states or {}
        self.set_template(project, component, _json_bytes(template))
        units = [
            self.unit(
                project,
                component,
                code,
                text,
                translated.get(key, ''),
                key,
                states.get(key),
                position=position,
            )
            for position, (key, text) in enumerate(template.items(), 1)
        ]
        self.set_file(project, component, code, _json_bytes(translated), units, 'application/json')
        self._keyed(project, component, code, _json_bytes)
        return units

    def fill_android(self, project, component, code, template, translated, states=None):
        """An Android component, monolingual as fill_json()'s: template
        and translated are {name: text}, written as strings.xml."""
        states = states or {}
        self.set_template(project, component, _android_bytes(template))
        units = [
            self.unit(
                project,
                component,
                code,
                text,
                translated.get(key, ''),
                key,
                states.get(key),
                position=position,
            )
            for position, (key, text) in enumerate(template.items(), 1)
        ]
        self.set_file(project, component, code, _android_bytes(translated), units, 'text/xml')
        self._keyed(project, component, code, _android_bytes)
        return units

    def _keyed(self, project, component, code, writer):
        """Writes a monolingual translation, and its template, from its
        units: the template every key with its source, the translation
        the keys translated."""

        def translation(_code, units):
            return writer({u['context']: u['target'][0] for u in units if any(u['target'])})

        def template(units):
            return writer({u['context']: u['source'][0] for u in units})

        parent = self._parent(project, component)
        parent.setdefault('writers', {})[code] = translation
        parent['template_writer'] = (code, template)

    # The set of units #

    def _units_of(self, unit):
        """(component, language code, its list of units) holding unit."""
        for project in self.projects.values():
            for parent in project['components'].values():
                for code, units in parent.get('units', {}).items():
                    if any(u is unit for u in units):
                        return parent, code, units
        raise KeyError(unit['id'])

    def add_unit(self, project, component, code, source, target='', context='', state=None):
        """A new unit at the end of a translation, as a new source string
        brings."""
        units = self._parent(project, component)['units'][code]
        unit = self.unit(project, component, code, source, target, context, state)
        unit['position'] = len(units) + 1
        unit['last_updated'] = self._tick()
        units.append(unit)
        self.units[unit['id']] = unit
        return unit

    def remove_unit(self, unit_id):
        """Drops a unit, as a source string removed does."""
        unit = self.units.pop(unit_id)
        _parent, _code, units = self._units_of(unit)
        units[:] = [u for u in units if u is not unit]
        for position, other in enumerate(units, 1):
            other['position'] = position

    def change_source(self, unit_id, source):
        """Gives a unit new source text, keeping its id, as a monolingual
        key's changed string does."""
        unit = self.units[unit_id]
        unit['source'] = source if isinstance(source, list) else [source]
        unit['last_updated'] = self._tick()
        return unit

    # Changes #

    def _tick(self):
        """A last_updated after every other: now, a microsecond on from
        the one before."""
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        with self._lock:
            self._clock = max(self._clock + 1, int(now.timestamp() * 1e6))
            stamp = datetime.datetime(1970, 1, 1) + datetime.timedelta(microseconds=self._clock)
        return stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')

    def _store(self, unit, target, state):
        unit['target'] = list(target)
        unit['state'] = state
        unit['fuzzy'] = state in (10, 11, 12)
        unit['translated'] = state >= 20
        unit['approved'] = state == 30
        unit['last_updated'] = self._tick()

    def edit(self, unit_id, target, state=None):
        """Changes a unit as someone else would on the server."""
        unit = self.units[unit_id]
        target = target if isinstance(target, list) else [target]
        self._store(unit, target, state if state is not None else (20 if any(target) else 0))
        return unit

    def lock(self, project, component, locked=True):
        self._parent(project, component)['component']['locked'] = locked

    def forbid(self, unit_id):
        """Answers 403 to a change of the unit, as to a user who may not
        (approve) it."""
        self.forbidden.add(unit_id)

    def enforce(self, unit_id):
        """Stores needs editing (10) when the unit is sent as translated,
        as a failing enforced check does."""
        self.enforced.add(unit_id)

    def fail_after(self, count, status):
        """Answers the PATCHes after the next count with status (None:
        drops the connection)."""
        self._fail_after = [count, status]

    # Review #

    def revoke(self, *permissions):
        """Takes permissions from the token's user."""
        self.permissions -= set(permissions)

    def has(self, version):
        """Whether the fake is as new as version."""
        return tuple(self.version) >= tuple(version)

    def suggest(self, unit_id, target, user='anna'):
        """A suggestion for a unit, as another user makes it."""
        target = target if isinstance(target, list) else [target]
        number = self._next_id()
        self.suggestions[number] = {
            'id': number,
            'unit': '{api}units/%d/' % unit_id,
            'target': target,
            'user': '{api}users/%s/' % user if user else None,
            'timestamp': self._tick(),
            'votes': 0,
            'unit_id': unit_id,
            'username': user,
        }
        self._flag(unit_id)
        return self.suggestions[number]

    def comment(self, unit_id, text, user='anna', scope='translation'):
        """A comment on a unit, as another user writes it: global and
        report comments are on its source unit."""
        unit = self.units[unit_id]
        on = self._source_id(unit) if scope in ('global', 'report') else unit_id
        number = self._next_id()
        self.comments[number] = {
            'id': number,
            'comment': text,
            'user': '{api}users/%s/' % user if user else None,
            'timestamp': self._tick(),
            'unit_id': on,
            'scope': scope,
        }
        self._flag(unit_id)
        return self.comments[number]

    def _source_id(self, unit):
        return int(unit['source_unit'].rstrip('/').rsplit('/', 1)[1])

    def _flag(self, unit_id):
        """Keeps has_suggestion and has_comment of a unit true to what it has."""
        unit = self.units.get(unit_id)
        if unit is None:
            return
        ids = (unit_id, self._source_id(unit))
        unit['has_suggestion'] = any(s['unit_id'] == unit_id for s in self.suggestions.values())
        unit['has_comment'] = any(c['unit_id'] in ids for c in self.comments.values())

    # Faults #

    def fail(self, path, status, times=1, headers=None, body=None, method='GET'):
        """Answers the next times requests whose path starts with path
        with status; status None drops the connection unanswered."""
        with self._lock:
            self._faults.append(_Fault(path, status, times, headers, body, method))

    def _take_fault(self, method, path):
        with self._lock:
            for fault in self._faults:
                if fault.method == method and path.startswith(fault.path):
                    fault.times -= 1
                    if fault.times <= 0:
                        self._faults.remove(fault)
                    return fault
        return None

    def requests_to(self, path):
        """The requests made so far whose path starts with path."""
        return [r for r in self.requests if r['path'].startswith(path)]

    # Replies #

    def error(self, status, code, detail):
        """An error reply body in the server's style."""
        if self.error_style == 'legacy':
            return {'detail': detail}
        kind = 'server_error' if status >= 500 else 'client_error'
        reply = {'type': kind, 'errors': [{'code': code, 'detail': detail, 'attr': None}]}
        self.schema_problems += weblate_schema.check_error(reply)
        return reply

    def authorise(self, headers):
        """(scope, None) for an accepted token, where scope is the one
        project it sees or None; else (None, (status, body))."""
        value = headers.get('Authorization', '')
        scheme, _sep, token = value.partition(' ')
        if not value:
            return None, (
                401,
                self.error(
                    401, 'not_authenticated', 'Authentication credentials were not provided.'
                ),
            )
        if scheme not in ('Token', 'Bearer') or token not in self.tokens:
            return None, (401, self.error(401, 'authentication_failed', 'Invalid token.'))
        return self.tokens[token], None

    def rate_headers(self, token):
        """The X-RateLimit headers for one more request with token, and
        whether it is over the limit."""
        if self.rate_limit is None:
            return {}, False
        with self._lock:
            start, used = self._used.get(token, (time.monotonic(), 0))
            if time.monotonic() - start >= self.rate_window:
                start, used = time.monotonic(), 0
            used += 1
            self._used[token] = (start, used)
        reset = max(1, int(self.rate_window - (time.monotonic() - start)))
        headers = {
            'X-RateLimit-Limit': str(self.rate_limit),
            'X-RateLimit-Remaining': str(max(0, self.rate_limit - used)),
            'X-RateLimit-Reset': str(reset),
        }
        if used > self.rate_limit:
            headers['Retry-After'] = str(reset)
            return headers, True
        return headers, False

    def page(self, request, items):
        """One page of items as Weblate pages a list."""
        query = request['query']
        try:
            size = min(MAX_PAGE_SIZE, max(1, int(query.get('page_size', DEFAULT_PAGE_SIZE))))
            number = int(query.get('page', 1))
        except ValueError:
            return 404, self.error(404, 'not_found', 'Invalid page.')
        pages = max(1, -(-len(items) // size))
        if not 1 <= number <= pages:
            return 404, self.error(404, 'not_found', 'Invalid page.')

        def link(n):
            if n < 1 or n > pages:
                return None
            params = {**query, 'page': n}
            if n == 1:
                params.pop('page')
            url = request['base'] + request['path'].lstrip('/')
            return url + ('?' + parse.urlencode(params) if params else '')

        results = items[(number - 1) * size : number * size]
        body = {
            'count': len(items),
            'next': link(number + 1),
            'previous': link(number - 1),
            'results': results,
        }
        return 200, body

    def handle(self, request):
        """(status, headers, body) for one request."""
        if self.offline:
            return None, {}, None
        self.requests.append(request)
        fault = self._take_fault(request['method'], request['path'])
        if fault is not None:
            return fault.status, fault.headers, fault.body
        if self.not_weblate:
            return 200, {'Content-Type': 'text/html'}, b'<!doctype html><title>Welcome</title>'
        scope, refused = self.authorise(request['headers'])
        if refused:
            return refused[0], {}, refused[1]
        token = request['headers']['Authorization'].partition(' ')[2]
        headers, over = self.rate_headers(token)
        if over:
            detail = (
                'Request was throttled. Expected available in %s seconds.' % headers['Retry-After']
            )
            return 429, headers, self.error(429, 'throttled', detail)
        for method, pattern, name in ROUTES:
            match = re.fullmatch(pattern, request['path'])
            if match and method == request['method']:
                status, body, *more = getattr(self, name)(request, scope, *match.groups())
                return status, {**headers, **(more[0] if more else {})}, body
        return 404, headers, self.error(404, 'not_found', 'Not found.')

    def _check(self, name, status, body):
        if status == 200:
            self.schema_problems += weblate_schema.check_page(name, body)
        return status, body

    # Routes #

    def root(self, request, scope):
        api = request['base'] + 'api/'
        return 200, {
            name: api + name + '/'
            for name in ('projects', 'components', 'translations', 'languages', 'units')
        }

    def list_projects(self, request, scope):
        items = [p['project'] for slug, p in self.projects.items() if scope in (None, slug)]
        return self._check('Project', *self.page(request, items))

    def list_components(self, request, scope, project):
        if project not in self.projects or scope not in (None, project):
            return 404, self.error(404, 'not_found', 'No Project matches the given query.')
        items = [c['component'] for c in self.projects[project]['components'].values()]
        return self._check('Component', *self.page(request, items))

    def list_translations(self, request, scope, project, component):
        component = parse.unquote(parse.unquote(component))
        components = self.projects.get(project, {}).get('components', {})
        if component not in components or scope not in (None, project):
            return 404, self.error(404, 'not_found', 'No Component matches the given query.')
        items = list(components[component]['translations'].values())
        return self._check('Translation', *self.page(request, items))

    def _component(self, scope, project, component):
        """The fake's component (its slug unquoted twice), or None."""
        component = parse.unquote(parse.unquote(component))
        if scope not in (None, project):
            return None
        return self.projects.get(project, {}).get('components', {}).get(component)

    def _translation(self, scope, project, component, code):
        parent = self._component(scope, project, component)
        if parent is None or code not in parent['translations']:
            return None, None
        return parent, parent['translations'][code]

    def _no_translation(self):
        return 404, self.error(404, 'not_found', 'No Translation matches the given query.')

    def get_translation(self, request, scope, project, component, code):
        _parent, translation = self._translation(scope, project, component, code)
        if translation is None:
            return self._no_translation()
        self.schema_problems += weblate_schema.check('Translation', translation)
        return 200, translation

    def translation_file(self, request, scope, project, component, code):
        parent, translation = self._translation(scope, project, component, code)
        if translation is None or code not in parent.get('files', {}):
            return self._no_translation()
        data, content_type = parent['files'][code]
        writer = parent.get('writers', {}).get(code)
        if writer is not None:
            data = writer(code, parent['units'][code])
        name = translation['filename'].rsplit('/', 1)[-1]
        headers = {
            'Content-Type': content_type,
            'Content-Disposition': 'attachment; filename="%s"' % name,
            'Last-Modified': 'Sun, 01 Nov 2026 12:00:00 GMT',
        }
        return 200, data, headers

    def list_units(self, request, scope, project, component, code):
        parent, translation = self._translation(scope, project, component, code)
        if translation is None:
            return self._no_translation()
        items = parent.get('units', {}).get(code, [])
        if request['query'].get('q'):
            items = self._search(request['query']['q'], items)
            if items is None:
                return 400, self.validation('q', 'Unknown search.')
        return self._check('Unit', *self.page(request, items))

    def get_lock(self, request, scope, project, component):
        parent = self._component(scope, project, component)
        if parent is None:
            return 404, self.error(404, 'not_found', 'No Component matches the given query.')
        reply = {'locked': parent['component']['locked']}
        self.schema_problems += weblate_schema.check('Lock', reply)
        return 200, reply

    def _search(self, query, units):
        """The units a search finds: changed:>=<time>, or has:suggestion,
        has:comment or both with OR."""
        import datetime

        flags = [part.strip() for part in query.split(' OR ')]
        if all(f in ('has:suggestion', 'has:comment') for f in flags):
            fields = [f.replace('has:', 'has_') for f in flags]
            return [unit for unit in units if any(unit[field] for field in fields)]
        found = re.fullmatch(r'changed:>=("?)([^"]+)\1', query.strip())
        if not found:
            return None
        try:
            since = datetime.datetime.fromisoformat(found.group(2).replace('Z', '+00:00'))
        except ValueError:
            return None

        def changed(unit):
            when = unit.get('last_updated')
            return when and datetime.datetime.fromisoformat(when.replace('Z', '+00:00')) >= since

        return [unit for unit in units if changed(unit)]

    def _unit_scope(self, scope, unit):
        """Whether a token with scope sees unit."""
        return scope is None or '/translations/%s/' % scope in unit['translation']

    def _written(self, unit_id, data):
        """(status, body) for a PATCH's JSON data, as Weblate checks it."""
        unit = self.units[unit_id]
        if unit_id in self.forbidden or unit['state'] == 100 or 'unit.edit' not in self.permissions:
            return self._denied()
        component = self._component_of(unit)
        if component is not None and component['locked']:
            return 423, self.error(423, 'component-locked', 'Component is locked.')
        states = (0, 10, 20, 30) if self.old_states else (0, 10, 11, 12, 20, 30)
        target, state = data.get('target'), data.get('state')
        if state not in states:
            return 400, self.validation('state', '"%s" is not a valid choice.' % state)
        if not isinstance(target, list) or not all(isinstance(t, str) for t in target):
            return 400, self.validation('target', 'Not a valid list of strings.')
        if len(target) != len(unit['target']):
            return 400, self.validation(
                'target', 'Number of plurals does not match the number provided.'
            )
        if (state == 0) == any(target):
            return 400, self.validation('state', 'Untranslated state requires empty target.')
        if state == 30 and not self._may_review(unit):
            return 400, self.validation('state', 'You do not have permission to approve strings.')
        if unit_id in self.enforced and state == 20:
            state = 10
        self._store(unit, target, state)
        self.patched.append((unit_id, data))
        reply = {
            'target': unit['target'],
            'state': state,
            'explanation': unit['explanation'],
            'extra_flags': unit['extra_flags'],
            'labels': unit['labels'],
        }
        self.schema_problems += weblate_schema.check('UnitWrite', reply)
        return 200, reply

    def validation(self, attr, detail):
        reply = {
            'type': 'validation_error',
            'errors': [{'code': 'invalid', 'detail': detail, 'attr': attr}],
        }
        self.schema_problems += weblate_schema.check_error(reply)
        return reply

    def _component_of(self, unit):
        for project in self.projects.values():
            for component in project['components'].values():
                for units in component.get('units', {}).values():
                    if any(u is unit for u in units):
                        return component['component']
        return None

    def get_unit(self, request, scope, unit_id):
        unit = self.units.get(int(unit_id))
        if unit is None or not self._unit_scope(scope, unit):
            return 404, self.error(404, 'not_found', 'No Unit matches the given query.')
        self.schema_problems += weblate_schema.check('Unit', unit)
        return 200, unit

    def patch_unit(self, request, scope, unit_id):
        unit_id = int(unit_id)
        if unit_id not in self.units or not self._unit_scope(scope, self.units[unit_id]):
            return 404, self.error(404, 'not_found', 'No Unit matches the given query.')
        if self._fail_after is not None:
            if self._fail_after[0] <= 0:
                return self._fail_after[1], None, {}
            self._fail_after[0] -= 1
        try:
            data = json.loads(request['body'].decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            return 400, self.error(400, 'parse_error', 'JSON parse error.')
        if not isinstance(data, dict):
            return 400, self.error(400, 'parse_error', 'JSON parse error.')
        return self._written(unit_id, data)

    # Review routes #

    def _denied(self):
        return 403, self.error(
            403, 'permission_denied', 'You do not have permission to perform this action.'
        )

    def _may_review(self, unit):
        component = self._component_of(unit)
        return 'unit.review' in self.permissions and bool(
            component and component['project'].get('translation_review')
        )

    def _seen_unit(self, scope, unit_id):
        """The unit, or a source unit, with an id a token with scope
        sees, or None."""
        unit_id = int(unit_id)
        unit = self.units.get(unit_id) or self.source_units.get(unit_id)
        if unit is None or not self._unit_scope(scope, unit):
            return None
        return unit

    def _no_unit(self):
        return 404, self.error(404, 'not_found', 'No Unit matches the given query.')

    def _json_body(self, request):
        try:
            data = json.loads(request['body'].decode('utf-8') or 'null')
        except (UnicodeDecodeError, ValueError):
            return None
        return data

    @staticmethod
    def _public(item):
        return {k: v for k, v in item.items() if k not in ('unit_id', 'username', 'scope')}

    def list_suggestions(self, request, scope, unit_id):
        if not self.has(SUGGESTIONS_SINCE):
            return 404, self.error(404, 'not_found', 'Not found.')
        if self._seen_unit(scope, unit_id) is None or int(unit_id) not in self.units:
            return self._no_unit()
        items = [self._public(s) for s in self.suggestions.values() if s['unit_id'] == int(unit_id)]
        return self._check('Suggestion', *self.page(request, items))

    def add_suggestion(self, request, scope, unit_id):
        if not self.has(SUGGESTIONS_SINCE):
            return 404, self.error(404, 'not_found', 'Not found.')
        unit = self.units.get(int(unit_id))
        if unit is None or not self._unit_scope(scope, unit):
            return self._no_unit()
        component = self._component_of(unit)
        if 'suggestion.add' not in self.permissions or unit['state'] == 100:
            return self._denied()
        if component is not None and not component.get('enable_suggestions', True):
            return self._denied()
        data = self._json_body(request)
        target = data.get('target') if isinstance(data, dict) else None
        if (
            not isinstance(target, list)
            or not target
            or not all(isinstance(t, str) for t in target)
        ):
            return 400, self.validation('target', 'This field is required.')
        if not any(target):
            return 400, self.validation('target', 'Please provide a suggestion')
        if len(target) != len(unit['target']):
            return 400, self.validation('target', 'Number of plurals does not match')
        suggestion = self.suggest(int(unit_id), target, self.user)
        reply = self._public(suggestion)
        self.schema_problems += weblate_schema.check('Suggestion', reply)
        return 201, reply

    def _suggestion(self, scope, suggestion_id):
        suggestion = self.suggestions.get(int(suggestion_id))
        if suggestion is None or not self.has(SUGGESTIONS_SINCE):
            return None
        unit = self.units.get(suggestion['unit_id'])
        return suggestion if unit is not None and self._unit_scope(scope, unit) else None

    def delete_suggestion(self, request, scope, suggestion_id):
        suggestion = self._suggestion(scope, suggestion_id)
        if suggestion is None:
            return 404, self.error(404, 'not_found', 'No Suggestion matches the given query.')
        own = suggestion['username'] == self.user
        if not own and 'suggestion.delete' not in self.permissions:
            return self._denied()
        del self.suggestions[suggestion['id']]
        self._flag(suggestion['unit_id'])
        return 204, None

    def accept_suggestion(self, request, scope, suggestion_id):
        suggestion = self._suggestion(scope, suggestion_id)
        if suggestion is None:
            return 404, self.error(404, 'not_found', 'No Suggestion matches the given query.')
        unit = self.units[suggestion['unit_id']]
        data = self._json_body(request)
        approve = bool(data.get('approve')) if isinstance(data, dict) else False
        if 'suggestion.accept' not in self.permissions:
            return self._denied()
        if approve and not self._may_review(unit):
            return self._denied()
        self._store(unit, suggestion['target'], 30 if approve else 20)
        del self.suggestions[suggestion['id']]
        self._flag(unit['id'])
        return 200, {'result': 'accepted'}

    def list_comments(self, request, scope, unit_id):
        if not self.has(COMMENTS_ADDED_SINCE):
            return 404, self.error(404, 'not_found', 'Not found.')
        if not self.has(COMMENTS_LISTED_SINCE):
            return 405, self.error(405, 'method_not_allowed', 'Method "GET" not allowed.')
        if self._seen_unit(scope, unit_id) is None:
            return self._no_unit()
        items = [self._public(c) for c in self.comments.values() if c['unit_id'] == int(unit_id)]
        return self._check('Comment', *self.page(request, items))

    def add_comment(self, request, scope, unit_id):
        if not self.has(COMMENTS_ADDED_SINCE):
            return 404, self.error(404, 'not_found', 'Not found.')
        unit = self.units.get(int(unit_id))
        if unit is None or not self._unit_scope(scope, unit):
            return self._no_unit()
        if 'comment.add' not in self.permissions:
            return self._denied()
        data = self._json_body(request)
        if not isinstance(data, dict) or not isinstance(data.get('comment'), str):
            return 400, self.validation('comment', 'This field is required.')
        kind = data.get('scope')
        if kind not in ('report', 'global', 'translation'):
            return 400, self.validation('scope', '"%s" is not a valid choice.' % kind)
        component = self._component_of(unit)
        if kind == 'report' and not (component and component['project'].get('source_review')):
            return 400, self.validation(
                'scope', '"report" is not a valid choice as source review is disabled.'
            )
        if not data['comment'].strip() or len(data['comment']) > 1000:
            return 400, self.validation(
                'comment', 'Ensure this field has no more than 1000 characters.'
            )
        comment = self.comment(unit['id'], data['comment'], self.user, kind)
        reply = self._public(comment)
        self.schema_problems += weblate_schema.check('Comment', reply)
        return 201, reply

    def monolingual_base(self, request, scope, project, component):
        parent = self._component(scope, project, component)
        if parent is None or 'template_file' not in parent:
            return 404, self.error(404, 'not_found', 'No Component matches the given query.')
        data = parent['template_file']
        if 'template_writer' in parent:
            code, writer = parent['template_writer']
            data = writer(parent['units'][code])
        return 200, data, {'Content-Type': 'application/octet-stream'}


ROUTES = [
    ('GET', r'/api/', 'root'),
    ('GET', r'/api/projects/', 'list_projects'),
    ('GET', r'/api/projects/([^/]+)/components/', 'list_components'),
    ('GET', r'/api/components/([^/]+)/([^/]+)/translations/', 'list_translations'),
    ('GET', r'/api/components/([^/]+)/([^/]+)/monolingual_base/', 'monolingual_base'),
    ('GET', r'/api/translations/([^/]+)/([^/]+)/([^/]+)/', 'get_translation'),
    ('GET', r'/api/translations/([^/]+)/([^/]+)/([^/]+)/file/', 'translation_file'),
    ('GET', r'/api/translations/([^/]+)/([^/]+)/([^/]+)/units/', 'list_units'),
    ('GET', r'/api/components/([^/]+)/([^/]+)/lock/', 'get_lock'),
    ('GET', r'/api/units/(\d+)/', 'get_unit'),
    ('PATCH', r'/api/units/(\d+)/', 'patch_unit'),
    ('GET', r'/api/units/(\d+)/suggestions/', 'list_suggestions'),
    ('POST', r'/api/units/(\d+)/suggestions/', 'add_suggestion'),
    ('DELETE', r'/api/suggestions/(\d+)/', 'delete_suggestion'),
    ('POST', r'/api/suggestions/(\d+)/accept/', 'accept_suggestion'),
    ('GET', r'/api/units/(\d+)/comments/', 'list_comments'),
    ('POST', r'/api/units/(\d+)/comments/', 'add_comment'),
]
"""(method, path pattern, FakeWeblate method name). Each handler gets
the request, the token's project scope and the pattern's groups, and
returns (status, JSON body)."""


def _effective():
    return {
        'effective_add_message': '',
        'effective_addon_message': '',
        'effective_agreement': '',
        'effective_check_flags': '',
        'effective_commit_message': '',
        'effective_delete_message': '',
        'effective_enforced_checks': [],
        'effective_language_code_style': '',
        'effective_license': '',
        'effective_merge_message': '',
        'effective_new_lang': 'contact',
        'effective_pull_message': '',
        'effective_secondary_language': None,
    }


def _po_bytes(code, units):
    """A PO file of units (as the API gives them), as Weblate writes it:
    needs editing (10, 11, 12) fuzzy, the rest plain."""
    from translate.misc.multistring import multistring
    from translate.storage import po

    store = po.pofile()
    store.updateheader(add=True, Language=code)
    if any(len(unit['source']) > 1 for unit in units):
        store.updateheader(Plural_Forms='nplurals=2; plural=(n != 1);')
    for unit in units:
        plural = len(unit['source']) > 1
        source, target = unit['source'], unit['target']
        entry = store.addsourceunit(multistring(source) if plural else source[0])
        if unit['context']:
            entry.setcontext(unit['context'])
        entry.target = multistring(target) if plural else target[0]
        entry.markfuzzy(unit['fuzzy'] and any(target))
    return bytes(store)


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=4) + '\n').encode('utf-8')


def _android_bytes(strings):
    from xml.sax.saxutils import escape, quoteattr

    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<resources>']
    for name, text in strings.items():
        lines.append('    <string name=%s>%s</string>' % (quoteattr(name), escape(text)))
    return ('\n'.join([*lines, '</resources>']) + '\n').encode('utf-8')


def _expand(value, base):
    """value with {api} and {web} links made absolute."""
    if isinstance(value, str):
        return value.replace('{api}', base + 'api/').replace('{web}', base)
    if isinstance(value, list):
        return [_expand(v, base) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, base) for k, v in value.items()}
    return value


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def _serve(self):
        fake = self.server.fake
        parts = parse.urlsplit(self.path)
        length = int(self.headers.get('Content-Length') or 0)
        request = {
            'method': self.command,
            'path': parts.path,
            'query': dict(parse.parse_qsl(parts.query)),
            'headers': dict(self.headers.items()),
            'body': self.rfile.read(length) if length else b'',
            'base': 'http://%s/' % self.headers.get('Host', '127.0.0.1'),
        }
        status, headers, body = fake.handle(request)
        if status is None:
            # A dropped connection: no reply at all.
            self.close_connection = True
            self.connection.shutdown(2)
            return
        if isinstance(body, (dict, list)):
            body = json.dumps(_expand(body, request['base'])).encode('utf-8')
            headers = {'Content-Type': 'application/json', **headers}
        body = body or b''
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _serve


FakeWeblate.handler = _Handler


DEMO_PO = [
    ('', 'Open', 'Maak oop', 30),
    ('', 'Save', 'Stoor', 20),
    ('', 'Close', 'Maak toe', 12),
    ('', 'Quit', 'Verlaat', 11),
    ('', 'Print', 'Druk', 10),
    ('menu', 'File', 'Lêer', 20),
    ('', 'Weblate', 'Weblate', 100),
    ('', 'Help', '', 0),
]
"""(context, source, target, Weblate state) of the demo's PO file:
Approved, Translated, Needs checking, Needs rewriting, Needs editing,
one with a context, Read-only and Empty."""

DEMO_JSON = {'welcome': 'Welcome', 'open': 'Open', 'save': 'Save'}


def demo():
    """A server with a few projects, for hand checks and screenshots."""
    fake = FakeWeblate()
    # Reviews on, as an approved string needs them.
    fake.add_project('hello', 'Hello', translation_review=True, source_review=True)
    fake.add_component('hello', 'program', 'Program')
    for code, name, done in (('af', 'Afrikaans', 98), ('de', 'German', 100), ('zu', 'Zulu', 41)):
        fake.add_translation('hello', 'program', code, name, translated=done, total=100)
    fake.fill_po('hello', 'program', 'af', DEMO_PO)
    fake.add_component('hello', 'glossary', 'Glossary', is_glossary=True, file_format='tbx')
    fake.add_translation('hello', 'glossary', 'af', 'Afrikaans', translated=12, total=20)
    fake.add_component(
        'hello',
        'website',
        'Website',
        file_format='json',
        filemask='locales/*.json',
        template='locales/en.json',
    )
    fake.add_translation('hello', 'website', 'af', 'Afrikaans', filename='locales/af.json')
    fake.fill_json(
        'hello',
        'website',
        'af',
        DEMO_JSON,
        {'welcome': 'Welkom', 'open': 'Maak oop'},
        {'open': 30},
    )
    fake.add_project('docs', 'Documentation')
    fake.add_component('docs', 'manual', 'User manual', category='guides')
    fake.add_translation('docs', 'guides/manual', 'pt_BR', 'Portuguese (Brazil)', 250, 400)
    for n in range(1, 13):
        fake.add_project('app-%02d' % n, 'App %d' % n)
    return fake


DEMO_PO_DE = [
    ('', 'Open', 'Öffnen', 20),
    ('', 'Save', 'Speichern', 20),
    ('', 'Close', '', 0),
    ('', 'Help', 'Hilfe', 20),
]
DEMO_PO_ZU = [
    ('', 'Open', 'Vula', 20),
    ('', 'Save', '', 0),
    ('', 'Close', 'Vala', 10),
    ('', 'Help', '', 0),
]
DEMO_INSTALLER = {
    'af': [('', 'Install', 'Installeer', 20), ('', 'Cancel', '', 0), ('', 'Next', 'Volgende', 20)],
    'de': [
        ('', 'Install', 'Installieren', 20),
        ('', 'Cancel', 'Abbrechen', 20),
        ('', 'Next', '', 0),
    ],
}


def demo_many():
    """demo(), with Program in German and Zulu too, and a second
    component, Installer, in Afrikaans and German: for opening several
    translations at once."""
    fake = demo()
    fake.fill_po('hello', 'program', 'de', DEMO_PO_DE)
    fake.fill_po('hello', 'program', 'zu', DEMO_PO_ZU)
    fake.add_component('hello', 'installer', 'Installer')
    for code, name in (('af', 'Afrikaans'), ('de', 'German')):
        entries = DEMO_INSTALLER[code]
        done = sum(1 for entry in entries if entry[2])
        fake.add_translation('hello', 'installer', code, name, translated=done, total=len(entries))
        fake.fill_po('hello', 'installer', code, entries)
    return fake


def demo_review():
    """demo(), with others' suggestions and comments on Program's
    Afrikaans units: for review."""
    fake = demo()
    units = {
        u['source'][0]: u['id']
        for u in fake.projects['hello']['components']['program']['units']['af']
    }
    fake.suggest(units['Save'], 'Berg', user='anna')
    fake.suggest(units['Save'], 'Bewaar', user='ben')
    fake.comment(units['Save'], 'Most apps say "Stoor" here.', user='anna')
    fake.comment(units['Print'], 'Is this the verb or the noun?', user='ben', scope='global')
    return fake


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--many', action='store_true', help='more languages and components')
    parser.add_argument('--review', action='store_true', help='suggestions and comments')
    args = parser.parse_args()
    print('Fake Weblate at http://127.0.0.1:%d/ with token %s' % (args.port, TOKEN), flush=True)
    fake = demo_many() if args.many else demo_review() if args.review else demo()
    fake.serve(args.port)


if __name__ == '__main__':
    main()
