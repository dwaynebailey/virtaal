#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A fake Transifex API v3 on 127.0.0.1 for tests and hand checks.

It holds organisations, projects, resources and their strings in
memory and serves, as JSON:API, the requests Virtaal makes: the lists
(paged by cursor, links.next and links.previous, page_size strings a
page), one object, resource_language_stats, and resource_translations
with their resource strings included (limit 150 to 1000). Every reply is
checked against transifex_schema.py; any mismatch is kept in
schema_problems.

Writes: PATCH /resource_translations (bulk: Content-Type
application/vnd.api+json;profile="bulk", 1 to 150 of one resource and
language) and PATCH /resource_translations/<id>. strings must give each
of the language's plural categories for a string with plurals, "other"
alone for one without, or be null; reviewed needs the right to review
(may_review). A change sets datetime_translated, a review
datetime_reviewed; changing the text clears reviewed and proofread unless
the request sets reviewed.

Downloads: POST /resource_translations_async_downloads (or
resource_strings_async_downloads for the source file) makes a job; the
first GET of it says "pending", the next redirects (303) to the file at
another host name (localhost for 127.0.0.1), which takes no token.

Faults: fail() answers the next matching requests with a status;
rate_limit answers 429 with the wait in its text only, as Transifex does
("Expected available in N seconds."), and no headers; edit() and
review() change a string as someone else would; forbid() refuses
changes to one string (403); offline drops every connection.

Never point Virtaal at a real Transifex from a test.

Run by hand:  python -m devsupport.testing.faketransifex --port 8766
(--hand: POST /_fake/edit/ {"source", "target", "reviewed"} changes a
string as someone else would.)
"""

import argparse
import datetime
import hashlib
import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler
from urllib import parse

from devsupport.testing import transifex_schema
from devsupport.testing.fakeplatform import FakeServer, run_until

__all__ = ['TOKEN', 'FakeTransifex', 'demo', 'run_until']

TOKEN = '1/fake0123456789abcdef0123456789abcdef01234567'
"""The personal token the server accepts."""

BULK = 'application/vnd.api+json;profile="bulk"'
PAGE_SIZE = 150
MAX_BULK = 150

LANGUAGES = {
    'en': ('English', {'one': 'n is 1', 'other': 'everything else'}),
    'af': ('Afrikaans', {'one': 'n is 1', 'other': 'everything else'}),
    'de': ('German', {'one': 'n is 1', 'other': 'everything else'}),
    'ja': ('Japanese', {'other': 'everything'}),
    'ru': (
        'Russian',
        {'one': 'n mod 10 is 1', 'few': 'n mod 10 in 2..4', 'many': 'n mod 10 is 0', 'other': ''},
    ),
}

CLDR = ('zero', 'one', 'two', 'few', 'many', 'other')
START = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)


def categories(code):
    rules = LANGUAGES[code][1]
    return [c for c in CLDR if c in rules]


def _hash(key, context):
    return hashlib.md5(('%s:%s' % (key, context)).encode('utf-8')).hexdigest()


class FakeTransifex(FakeServer):
    """The server's data and behaviour; start() serves it."""

    def __init__(self):
        super().__init__()
        self.tokens = {TOKEN}
        self.organisations = {}
        """{slug: {'name', 'projects': {slug: project}}}."""
        self.requests = []
        self.patched = []
        """[(translation id, attributes)] in the order written."""
        self.schema_problems = []
        self.faults = []
        self.forbidden = set()
        self.may_review = True
        self.rate_limit = None
        """Requests allowed before 429s, or None for no limit."""
        self.throttle_seconds = 1
        self.offline = False
        self.controls = False
        self.page_size = PAGE_SIZE
        self.jobs = {}
        self.files = {}
        self._lock = threading.RLock()
        self._clock = 0
        self._used = 0

    # Data #

    def _now(self):
        self._clock += 1
        return (START + datetime.timedelta(seconds=self._clock)).strftime('%Y-%m-%dT%H:%M:%SZ')

    def add_organisation(self, slug, name):
        self.organisations[slug] = {'name': name, 'projects': {}}

    def add_project(self, organisation, slug, name, languages=('af',), source='en'):
        self.organisations[organisation]['projects'][slug] = {
            'name': name,
            'source': source,
            'languages': list(languages),
            'resources': {},
        }

    def add_resource(self, organisation, project, slug, name, file_format='PO'):
        where = self.organisations[organisation]['projects'][project]
        where['resources'][slug] = {
            'name': name,
            'format': file_format,
            'strings': [],
            'id': 'o:%s:p:%s:r:%s' % (organisation, project, slug),
        }
        return where['resources'][slug]

    def _resource(self, rid):
        found = re.fullmatch(r'o:([^:]+):p:([^:]+):r:([^:]+)', rid)
        if not found:
            return None
        organisation, project, slug = found.groups()
        try:
            return self.organisations[organisation]['projects'][project]['resources'][slug]
        except KeyError:
            return None

    def _project(self, pid):
        found = re.fullmatch(r'o:([^:]+):p:([^:]+)', pid)
        if not found:
            return None
        return self.organisations.get(found.group(1), {}).get('projects', {}).get(found.group(2))

    def add_string(self, resource, key, source, translations=None, context='', plural=False):
        """A string of resource: source is its text (a list of forms for a
        plural, one and other), translations {language: (forms, state)}
        with state 'translated', 'reviewed' or 'proofread'."""
        forms = source if isinstance(source, list) else [source]
        string = {
            'hash': _hash(key, context),
            'key': key,
            'context': context,
            'strings': dict(zip(('one', 'other'), forms, strict=False))
            if plural
            else {'other': forms[0]},
            'pluralized': plural,
            'modified': self._now(),
            'translations': {},
        }
        resource['strings'].append(string)
        for code, (target, state) in (translations or {}).items():
            self._store(resource, string, code, target, state)
        return string

    def _store(self, resource, string, code, target, state):
        target = target if isinstance(target, list) else [target]
        names = categories(code) if string['pluralized'] else ['other']
        when = self._now()
        string['translations'][code] = {
            'strings': dict(zip(names, target, strict=False)) if any(target) else None,
            'reviewed': state in ('reviewed', 'proofread'),
            'proofread': state == 'proofread',
            'created': when,
            'translated': when if any(target) else None,
            'reviewed_at': when if state in ('reviewed', 'proofread') else None,
            'proofread_at': when if state == 'proofread' else None,
        }

    def translation_id(self, resource, string, code):
        return '%s:s:%s:l:%s' % (resource['id'], string['hash'], code)

    def find(self, translation_id):
        """(resource, string, language) of a translation id, or None."""
        found = re.fullmatch(r'(o:[^:]+:p:[^:]+:r:[^:]+):s:([0-9a-f]{32}):l:(.+)', translation_id)
        if not found:
            return None
        resource = self._resource(found.group(1))
        if resource is None:
            return None
        string = next((s for s in resource['strings'] if s['hash'] == found.group(2)), None)
        return (resource, string, found.group(3)) if string else None

    def string(self, resource, source):
        return next(s for s in resource['strings'] if s['strings'].get('other') == source)

    def edit(self, resource, source, target, code='af', reviewed=False):
        """Changes a string's translation as another translator would."""
        string = self.string(resource, source)
        self._store(resource, string, code, target, 'reviewed' if reviewed else 'translated')

    def review(self, resource, source, code='af'):
        """Reviews a string's translation, as a reviewer would."""
        entry = self.string(resource, source)['translations'][code]
        entry.update(reviewed=True, reviewed_at=self._now())

    def forbid(self, resource, source):
        self.forbidden.add(self.string(resource, source)['hash'])

    def _languages_of(self, resource):
        for org in self.organisations.values():
            for project in org['projects'].values():
                if resource in project['resources'].values():
                    return project['languages']
        return []

    # Faults #

    def fail(self, path, status, times=1, body=None, method='GET'):
        """Answers the next times requests to path (a prefix) with
        status; None drops the connection."""
        with self._lock:
            self.faults.append([method, path, status, times, body])

    def _take_fault(self, method, path):
        with self._lock:
            for fault in self.faults:
                if fault[0] == method and path.startswith(fault[1]):
                    fault[3] -= 1
                    if fault[3] <= 0:
                        self.faults.remove(fault)
                    return fault
        return None

    def requests_to(self, path, method=None):
        return [
            r for r in self.requests if r['path'].startswith(path) and method in (None, r['method'])
        ]

    # Replies #

    def error(self, status, code, title, detail):
        body = {'errors': [{'status': str(status), 'code': code, 'title': title, 'detail': detail}]}
        self.schema_problems += transifex_schema.check_error(body)
        return body

    def _document(self, body):
        self.schema_problems += transifex_schema.check_document(body)
        return 200, body

    def page(self, request, items, size=None):
        """A page of items (JSON:API objects) with cursor links."""
        size = size or self.page_size
        cursor = request['query'].get('page[cursor]', 'c0')
        start = int(cursor[1:]) if re.fullmatch(r'c\d+', cursor) else 0

        def link(at):
            if at is None:
                return None
            query = {**request['query'], 'page[cursor]': 'c%d' % at}
            return request['path'] + '?' + parse.urlencode(query, safe=':[]')

        following = start + size if start + size < len(items) else None
        previous = max(0, start - size) if start > 0 else None
        body = {
            'data': items[start : start + size],
            'links': {'self': request['path'], 'next': link(following), 'previous': link(previous)},
        }
        return body

    def handle(self, request):
        """(status, headers, body) for one request."""
        if self.offline:
            return None, {}, None
        self.requests.append(request)
        fault = self._take_fault(request['method'], request['path'])
        if fault is not None:
            return fault[2], {}, fault[4]
        if request['path'].startswith('/_files/'):
            data = self.files.get(request['path'][len('/_files/') :])
            return (
                (200, {'Content-Type': 'text/plain'}, data) if data is not None else (404, {}, b'')
            )
        if self.controls and request['path'] == '/_fake/edit/' and request['method'] == 'POST':
            return self._control_edit(request)
        refused = self._authorise(request['headers'])
        if refused:
            return refused
        throttled = self._throttle()
        if throttled:
            return throttled
        for method, pattern, name in ROUTES:
            match = re.fullmatch(pattern, request['path'])
            if match and method == request['method']:
                status, body, *more = getattr(self, name)(request, *match.groups())
                headers = {'Content-Type': 'application/vnd.api+json', **(more[0] if more else {})}
                return status, headers, body
        return 404, {}, self.error(404, 'not_found', 'Not found', 'Not found.')

    def _authorise(self, headers):
        scheme, _sep, token = headers.get('Authorization', '').partition(' ')
        if scheme != 'Bearer' or token not in self.tokens:
            return 401, {}, self.error(401, 'unauthorized', 'Unauthorized', 'Invalid token.')
        return None

    def _throttle(self):
        if self.rate_limit is None:
            return None
        with self._lock:
            self._used += 1
            over = self._used > self.rate_limit
            if over:
                self._used = 0
        if not over:
            return None
        detail = 'Request was throttled. Expected available in %d seconds.' % self.throttle_seconds
        return 429, {}, self.error(429, 'Throttled', 'Throttled', detail)

    def _control_edit(self, request):
        data = json.loads(request['body'] or b'{}')
        for org in self.organisations.values():
            for project in org['projects'].values():
                for resource in project['resources'].values():
                    for string in resource['strings']:
                        if string['strings'].get('other') == data.get('source'):
                            state = 'reviewed' if data.get('reviewed') else 'translated'
                            self._store(
                                resource, string, data.get('language', 'af'), data['target'], state
                            )
                            return 200, {}, b'{}'
        return 404, {}, b'{}'

    # Objects #

    def _organisation(self, slug):
        org = self.organisations[slug]
        return {
            'type': 'organizations',
            'id': 'o:' + slug,
            'attributes': {'name': org['name'], 'slug': slug, 'private': False, 'logo_url': None},
            'links': {'self': '/organizations/o:' + slug},
        }

    def _project_object(self, organisation, slug):
        project = self.organisations[organisation]['projects'][slug]
        return {
            'type': 'projects',
            'id': 'o:%s:p:%s' % (organisation, slug),
            'attributes': {
                'name': project['name'],
                'slug': slug,
                'description': None,
                'private': False,
                'archived': False,
                'datetime_created': START.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'datetime_modified': START.strftime('%Y-%m-%dT%H:%M:%SZ'),
            },
            'relationships': {
                'organization': {'data': {'type': 'organizations', 'id': 'o:' + organisation}},
                'source_language': {'data': {'type': 'languages', 'id': 'l:' + project['source']}},
            },
        }

    @staticmethod
    def _language(code):
        name, rules = LANGUAGES[code]
        return {
            'type': 'languages',
            'id': 'l:' + code,
            'attributes': {
                'code': code,
                'name': name,
                'rtl': False,
                'plural_equation': '(n != 1)',
                'plural_rules': dict(rules),
            },
        }

    def _resource_object(self, resource):
        return {
            'type': 'resources',
            'id': resource['id'],
            'attributes': {
                'name': resource['name'],
                'slug': resource['id'].rpartition(':r:')[2],
                'string_count': len(resource['strings']),
                'word_count': 0,
                'accept_translations': True,
                'datetime_modified': START.strftime('%Y-%m-%dT%H:%M:%SZ'),
            },
            'relationships': {
                'project': {
                    'data': {'type': 'projects', 'id': resource['id'].rpartition(':r:')[0]}
                },
                'i18n_format': {'data': {'type': 'i18n_formats', 'id': resource['format']}},
            },
        }

    def _stats(self, resource, code):
        done = [s for s in resource['strings'] if s['translations'].get(code, {}).get('strings')]
        reviewed = [s for s in done if s['translations'][code]['reviewed']]
        return {
            'type': 'resource_language_stats',
            'id': '%s:l:%s' % (resource['id'], code),
            'attributes': {
                'untranslated_strings': len(resource['strings']) - len(done),
                'translated_strings': len(done),
                'reviewed_strings': len(reviewed),
                'proofread_strings': 0,
                'total_strings': len(resource['strings']),
                'last_update': None,
            },
            'relationships': {
                'language': {'data': {'type': 'languages', 'id': 'l:' + code}},
                'resource': {'data': {'type': 'resources', 'id': resource['id']}},
            },
        }

    def _string_object(self, resource, string):
        return {
            'type': 'resource_strings',
            'id': '%s:s:%s' % (resource['id'], string['hash']),
            'attributes': {
                'key': string['key'],
                'context': string['context'],
                'strings': dict(string['strings']),
                'pluralized': string['pluralized'],
                'tags': [],
                'string_hash': string['hash'],
                'strings_datetime_modified': string['modified'],
            },
        }

    def _translation_object(self, resource, string, code):
        entry = string['translations'].get(code) or {}
        return {
            'type': 'resource_translations',
            'id': self.translation_id(resource, string, code),
            'attributes': {
                'strings': entry.get('strings'),
                'reviewed': bool(entry.get('reviewed')),
                'proofread': bool(entry.get('proofread')),
                'finalized': False,
                'origin': 'EDITOR' if entry else None,
                'datetime_created': entry.get('created') or string['modified'],
                'datetime_translated': entry.get('translated'),
                'datetime_reviewed': entry.get('reviewed_at'),
                'datetime_proofread': entry.get('proofread_at'),
            },
            'relationships': {
                'resource': {'data': {'type': 'resources', 'id': resource['id']}},
                'language': {'data': {'type': 'languages', 'id': 'l:' + code}},
                'resource_string': {
                    'data': {
                        'type': 'resource_strings',
                        'id': '%s:s:%s' % (resource['id'], string['hash']),
                    }
                },
            },
        }

    # Routes #

    def list_organisations(self, request):
        items = [self._organisation(slug) for slug in self.organisations]
        return self._document(self.page(request, items))

    def list_projects(self, request):
        org = request['query'].get('filter[organization]', '')
        if not org.startswith('o:') or org[2:] not in self.organisations:
            return 400, self.error(
                400, 'invalid', 'Invalid filter', 'filter[organization] is required.'
            )
        slug = org[2:]
        items = [self._project_object(slug, p) for p in self.organisations[slug]['projects']]
        return self._document(self.page(request, items))

    def get_project(self, request, pid):
        if self._project(pid) is None:
            return 404, self.error(404, 'not_found', 'Not found', 'No such project.')
        organisation, project = re.fullmatch(r'o:([^:]+):p:([^:]+)', pid).groups()
        return self._document({'data': self._project_object(organisation, project)})

    def project_languages(self, request, pid):
        project = self._project(pid)
        if project is None:
            return 404, self.error(404, 'not_found', 'Not found', 'No such project.')
        items = [self._language(code) for code in project['languages']]
        return self._document(self.page(request, items))

    def get_language(self, request, code):
        if code not in LANGUAGES:
            return 404, self.error(404, 'not_found', 'Not found', 'No such language.')
        return self._document({'data': self._language(code)})

    def list_resources(self, request):
        project = self._project(request['query'].get('filter[project]', ''))
        if project is None:
            return 400, self.error(400, 'invalid', 'Invalid filter', 'filter[project] is required.')
        items = [self._resource_object(r) for r in project['resources'].values()]
        return self._document(self.page(request, items))

    def get_resource(self, request, rid):
        resource = self._resource(rid)
        if resource is None:
            return 404, self.error(404, 'not_found', 'Not found', 'No such resource.')
        return self._document({'data': self._resource_object(resource)})

    def list_stats(self, request):
        query = request['query']
        project = self._project(query.get('filter[project]', ''))
        if project is None:
            return 400, self.error(400, 'invalid', 'Invalid filter', 'filter[project] is required.')
        resources = project['resources'].values()
        if 'filter[resource]' in query:
            resources = [r for r in resources if r['id'] == query['filter[resource]']]
        items = [self._stats(r, code) for r in resources for code in project['languages']]
        return self._document(self.page(request, items))

    def get_stats(self, request, rid, code):
        resource = self._resource(rid)
        if resource is None or code not in self._languages_of(resource):
            return 404, self.error(404, 'not_found', 'Not found', 'No such stats.')
        return self._document({'data': self._stats(resource, code)})

    def list_translations(self, request):
        query = request['query']
        resource = self._resource(query.get('filter[resource]', ''))
        code = query.get('filter[language]', '')[2:]
        if resource is None or code not in self._languages_of(resource):
            return 400, self.error(
                400, 'invalid', 'Invalid filter', 'Resource and language needed.'
            )
        try:
            limit = int(query.get('limit', PAGE_SIZE))
        except ValueError:
            limit = 0
        if not PAGE_SIZE <= limit <= 1000:
            return 400, self.error(400, 'invalid', 'Invalid limit', 'limit is 150 to 1000.')
        strings = resource['strings']
        body = self.page(
            request, [self._translation_object(resource, s, code) for s in strings], limit
        )
        if query.get('include') == 'resource_string':
            shown = {item['id'] for item in body['data']}
            body['included'] = [
                self._string_object(resource, s)
                for s in strings
                if self.translation_id(resource, s, code) in shown
            ]
        return self._document(body)

    # Writes #

    def _check_item(self, item):
        """The (status, error body) refusing one item, or None."""
        found = self.find(str(item.get('id', '')))
        if item.get('type') != 'resource_translations' or found is None:
            return 404, self.error(404, 'not_found', 'Not found', 'No such translation.')
        _resource, string, code = found
        attributes = item.get('attributes') or {}
        strings = attributes.get('strings')
        names = categories(code) if string['pluralized'] else ['other']
        if strings is not None and sorted(strings) != sorted(names):
            detail = 'strings must have the plural forms %s.' % ', '.join(names)
            return 400, self.error(400, 'invalid', 'Invalid strings', detail)
        if string['hash'] in self.forbidden:
            return 403, self.error(403, 'forbidden', 'Forbidden', 'You may not change this.')
        if attributes.get('reviewed') and not self.may_review:
            return 403, self.error(403, 'forbidden', 'Forbidden', 'You may not review.')
        return None

    def _write(self, item):
        resource, string, code = self.find(item['id'])
        attributes = item['attributes']
        entry = string['translations'].setdefault(
            code,
            {
                'strings': None,
                'reviewed': False,
                'proofread': False,
                'created': self._now(),
                'translated': None,
                'reviewed_at': None,
                'proofread_at': None,
            },
        )
        if 'strings' in attributes and attributes['strings'] != entry['strings']:
            entry.update(strings=attributes['strings'], translated=self._now())
            entry.update(reviewed=False, proofread=False)
        if 'reviewed' in attributes and attributes['reviewed'] != entry['reviewed']:
            entry.update(reviewed=attributes['reviewed'], reviewed_at=self._now())
        if not entry['strings']:
            entry.update(reviewed=False, proofread=False)
        self.patched.append((item['id'], dict(attributes)))
        return self._translation_object(resource, string, code)

    def patch_bulk(self, request):
        if request['headers'].get('Content-Type') != BULK:
            return 415, self.error(415, 'unsupported', 'Unsupported', 'Use the bulk profile.')
        items = (_json(request['body']) or {}).get('data')
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_BULK:
            return 400, self.error(400, 'invalid', 'Invalid', 'data must hold 1 to 150 items.')
        found = [self.find(str(item.get('id', ''))) for item in items]
        if None not in found and len({(f[0]['id'], f[2]) for f in found}) > 1:
            detail = 'All must be of one resource and language.'
            return 400, self.error(400, 'invalid', 'Invalid', detail)
        for item in items:
            refused = self._check_item(item)
            if refused:
                return refused
        body = {'data': [self._write(item) for item in items], 'links': {}}
        body['links'] = {'self': '/resource_translations', 'next': None, 'previous': None}
        return self._document(body)

    def patch_one(self, request, translation_id):
        item = (_json(request['body']) or {}).get('data') or {}
        if item.get('id') != translation_id:
            return 400, self.error(400, 'invalid', 'Invalid', 'The id must match the URL.')
        refused = self._check_item(item)
        if refused:
            return refused
        return self._document({'data': self._write(item)})

    # Downloads #

    def start_job(self, request, kind):
        data = (_json(request['body']) or {}).get('data') or {}
        relationships = data.get('relationships') or {}
        rid = ((relationships.get('resource') or {}).get('data') or {}).get('id', '')
        resource = self._resource(rid)
        language = ((relationships.get('language') or {}).get('data') or {}).get('id', '')[2:]
        if resource is None or (kind.startswith('resource_translations') and not language):
            return 400, self.error(400, 'invalid', 'Invalid', 'Resource and language needed.')
        job = str(uuid.uuid4())
        self.jobs[job] = {'kind': kind, 'resource': resource, 'language': language, 'polls': 0}
        return 202, {'data': self._job(job, 'pending')}

    def _job(self, job, status):
        when = START.strftime('%Y-%m-%dT%H:%M:%SZ')
        return {
            'type': self.jobs[job]['kind'],
            'id': job,
            'attributes': {
                'status': status,
                'errors': [],
                'date_created': when,
                'date_modified': when,
            },
        }

    def poll_job(self, request, kind, job):
        if job not in self.jobs:
            return 404, self.error(404, 'not_found', 'Not found', 'No such download.')
        entry = self.jobs[job]
        entry['polls'] += 1
        if entry['polls'] < 2:
            return self._document({'data': self._job(job, 'processing')})
        resource = entry['resource']
        if kind.startswith('resource_strings'):
            self.files[job] = file_bytes(resource, None)
        else:
            self.files[job] = file_bytes(resource, entry['language'])
        host = request['headers'].get('Host', '127.0.0.1').replace('127.0.0.1', 'localhost')
        return 303, b'', {'Location': 'http://%s/_files/%s' % (host, job)}


ROUTES = [
    ('GET', r'/organizations', 'list_organisations'),
    ('GET', r'/projects', 'list_projects'),
    ('GET', r'/projects/([^/]+)/languages', 'project_languages'),
    ('GET', r'/projects/([^/]+)', 'get_project'),
    ('GET', r'/languages/l:([^/]+)', 'get_language'),
    ('GET', r'/resources', 'list_resources'),
    ('GET', r'/resources/([^/]+)', 'get_resource'),
    ('GET', r'/resource_language_stats', 'list_stats'),
    ('GET', r'/resource_language_stats/(o:[^/]+):l:([^/:]+)', 'get_stats'),
    ('GET', r'/resource_translations', 'list_translations'),
    ('PATCH', r'/resource_translations', 'patch_bulk'),
    ('PATCH', r'/resource_translations/([^/]+)', 'patch_one'),
    (
        'POST',
        r'/(resource_translations_async_downloads|resource_strings_async_downloads)',
        'start_job',
    ),
    (
        'GET',
        r'/(resource_translations_async_downloads|resource_strings_async_downloads)/([^/]+)',
        'poll_job',
    ),
]


def _json(body):
    try:
        return json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None


def file_bytes(resource, code):
    """The resource's file in code (None: the source file), as Transifex
    builds it from its strings."""
    if resource['format'] == 'KEYVALUEJSON':
        data = {}
        for string in resource['strings']:
            if code is None:
                data[string['key']] = string['strings']['other']
            else:
                strings = (string['translations'].get(code) or {}).get('strings') or {}
                data[string['key']] = strings.get('other', '')
        return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return _po_bytes(resource, code or 'en')


def _po_bytes(resource, code):
    from translate.misc.multistring import multistring
    from translate.storage import po

    store = po.pofile()
    store.updateheader(add=True, Language=code)
    if any(s['pluralized'] for s in resource['strings']):
        count = len(categories(code))
        store.updateheader(Plural_Forms='nplurals=%d; plural=(n != 1);' % count)
    for string in resource['strings']:
        names = categories(code) if string['pluralized'] else ['other']
        source = [string['strings'][n] for n in ('one', 'other') if n in string['strings']]
        entry = store.addsourceunit(multistring(source) if string['pluralized'] else source[0])
        if string['context']:
            entry.setcontext(string['context'])
        strings = (string['translations'].get(code) or {}).get('strings') or {}
        target = [strings.get(n, '') for n in names]
        entry.target = multistring(target) if string['pluralized'] else target[0]
    return bytes(store)


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
        }
        status, headers, body = fake.handle(request)
        if status is None:
            self.close_connection = True
            self.connection.shutdown(2)
            return
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        body = body or b''
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = _serve


FakeTransifex.handler = _Handler


DEMO_PO = [
    ('', 'Open', 'Maak oop', 'reviewed'),
    ('', 'Save', 'Stoor', 'translated'),
    ('', 'Close', 'Maak toe', 'proofread'),
    ('menu', 'File', 'Lêer', 'translated'),
    ('', 'Print', '', None),
    ('', 'Help', '', None),
]
"""(context, source, Afrikaans, state) of the demo's PO resource."""


def demo():
    """A server with two organisations, for tests and hand checks: Hello
    Org's Program (a PO resource with a plural, and a JSON one) in
    Afrikaans and German, and Tools' Toolbar."""
    fake = FakeTransifex()
    fake.add_organisation('hello-org', 'Hello Org')
    fake.add_project('hello-org', 'program', 'Program', languages=('af', 'de'))
    messages = fake.add_resource('hello-org', 'program', 'messages', 'Messages')
    for context, source, target, state in DEMO_PO:
        translations = {'af': (target, state)} if target else {}
        fake.add_string(
            messages, (context + ':' if context else '') + source, source, translations, context
        )
    fake.add_string(
        messages,
        '%d file',
        ['%d file', '%d files'],
        {'af': (['%d lêer', '%d lêers'], 'translated')},
        plural=True,
    )
    website = fake.add_resource('hello-org', 'program', 'website', 'Website', 'KEYVALUEJSON')
    fake.add_string(website, 'welcome', 'Welcome', {'af': ('Welkom', 'translated')})
    fake.add_string(website, 'open', 'Open', {'af': ('Maak oop', 'reviewed')})
    fake.add_string(website, 'save', 'Save')
    fake.add_organisation('tools', 'Tools')
    fake.add_project('tools', 'toolbar', 'Toolbar', languages=('af',))
    toolbar = fake.add_resource('tools', 'toolbar', 'buttons', 'Buttons')
    fake.add_string(toolbar, 'Undo', 'Undo', {'af': ('Ontdoen', 'translated')})
    fake.add_string(toolbar, 'Redo', 'Redo')
    return fake


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=8766, help='0 for a free one')
    parser.add_argument('--port-file', help='where to write the port, once it listens')
    parser.add_argument('--hand', action='store_true', help='POST /_fake/edit/ to change a string')
    args = parser.parse_args()
    fake = demo()
    fake.controls = args.hand
    if args.hand:
        handle = fake.handle

        def logged(request):
            status, headers, body = handle(request)
            print(request['method'], request['path'], status, flush=True)
            return status, headers, body

        fake.handle = logged

    def ready(url):
        print('Fake Transifex at %s with token %s' % (url, TOKEN), flush=True)
        if args.port_file:
            with open(args.port_file, 'w', encoding='utf-8') as f:
                f.write('%d\n' % fake._server.server_address[1])

    fake.serve(args.port, ready)


if __name__ == '__main__':
    main()
