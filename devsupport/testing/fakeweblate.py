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
later phases add theirs (unit PATCH, the lock, changes). A translation
can hold a file to download and its units (fill_po(), fill_json()),
paged as Weblate pages units, with ids, states and last_updated.

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

__all__ = ['TOKEN', 'FakeWeblate', 'demo', 'run_until']

TOKEN = 'wlu_fake0123456789abcdefghijklmnopqrstuv'
"""The personal token the server accepts by default."""

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 1000


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
        self.rate_limit = None
        """Requests allowed per window and token, or None."""
        self.rate_window = 3600
        self.projects = {}
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
        translation = '{api}translations/%s/%s/%s/' % (project, _quote_slug(component), code)
        return {
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
            'source_unit': '{api}units/%d/' % number,
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

    def fill_po(self, project, component, code, entries):
        """Gives translation code a PO file of entries, each (context,
        source, target, Weblate state or None), and their units. As
        Weblate does, a needs-editing state is written fuzzy, the rest
        plain."""
        from translate.storage import po

        store = po.pofile()
        store.updateheader(add=True, Language=code)
        units = []
        for position, (context, source, target, state) in enumerate(entries, 1):
            unit = store.addsourceunit(source)
            if context:
                unit.setcontext(context)
            unit.target = target
            made = self.unit(
                project, component, code, source, target, context, state, position=position
            )
            unit.markfuzzy(made['fuzzy'] and bool(target))
            units.append(made)
        self.set_file(project, component, code, bytes(store), units, 'text/x-po')
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
        return units

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
        return self._check('Unit', *self.page(request, items))

    def monolingual_base(self, request, scope, project, component):
        parent = self._component(scope, project, component)
        if parent is None or 'template_file' not in parent:
            return 404, self.error(404, 'not_found', 'No Component matches the given query.')
        return 200, parent['template_file'], {'Content-Type': 'application/octet-stream'}


ROUTES = [
    ('GET', r'/api/', 'root'),
    ('GET', r'/api/projects/', 'list_projects'),
    ('GET', r'/api/projects/([^/]+)/components/', 'list_components'),
    ('GET', r'/api/components/([^/]+)/([^/]+)/translations/', 'list_translations'),
    ('GET', r'/api/components/([^/]+)/([^/]+)/monolingual_base/', 'monolingual_base'),
    ('GET', r'/api/translations/([^/]+)/([^/]+)/([^/]+)/', 'get_translation'),
    ('GET', r'/api/translations/([^/]+)/([^/]+)/([^/]+)/file/', 'translation_file'),
    ('GET', r'/api/translations/([^/]+)/([^/]+)/([^/]+)/units/', 'list_units'),
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
    fake.add_project('hello', 'Hello')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    print('Fake Weblate at http://127.0.0.1:%d/ with token %s' % (args.port, TOKEN), flush=True)
    demo().serve(args.port)


if __name__ == '__main__':
    main()
