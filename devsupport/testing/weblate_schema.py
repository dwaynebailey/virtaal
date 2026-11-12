#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The shapes of the Weblate API replies fakeweblate.py serves, to check
them against.

Field names and types only, written down from Weblate's OpenAPI 3.1
schema (/api/schema/, read 2026-11-10, Weblate 2026.11) and its API
docs: the "required" fields of each object, plus the optional ones
Virtaal reads. They are facts about the API, not a copy of the schema
(Weblate is GPL-3.0-or-later). W2-W4 add Unit, Change and the rest
here as they use them; W6 Suggestion and Comment, from docs/api.rst and
weblate/api/serializers.py (SuggestionSerializer, CommentSerializer).
"""

_TYPES = {
    'string': (str,),
    'integer': (int,),
    'number': (int, float),
    'boolean': (bool,),
    'array': (list,),
    'object': (dict,),
    'null': (type(None),),
}

_S, _I, _N, _B, _A = 'string', 'integer', 'number', 'boolean', 'array'
_S_OR_NULL = (_S, 'null')
_I_OR_NULL = (_I, 'null')

_EFFECTIVE = {
    'effective_add_message': _S,
    'effective_addon_message': _S,
    'effective_agreement': _S,
    'effective_check_flags': _S,
    'effective_commit_message': _S,
    'effective_delete_message': _S,
    'effective_enforced_checks': _A,
    'effective_language_code_style': _S,
    'effective_license': _S,
    'effective_merge_message': _S,
    'effective_new_lang': _S,
    'effective_pull_message': _S,
    'effective_secondary_language': _I_OR_NULL,
}

SCHEMAS = {
    'Project': {
        'required': {
            **_EFFECTIVE,
            'announcements_url': _S,
            'categories_url': _S,
            'changes_list_url': _S,
            'components_list_url': _S,
            'id': _I,
            'labels_url': _S,
            'languages_url': _S,
            'lock_url': _S,
            'locked': _B,
            'machinery_settings': _S,
            'metrics_url': _S,
            'name': _S,
            'reports_url': _S,
            'repository_url': _S,
            'slug': _S,
            'statistics_url': _S,
            'url': _S,
            'web_url': _S,
        },
        'optional': {'translation_review': _B, 'source_review': _B, 'web': _S},
    },
    'Component': {
        'required': {
            **_EFFECTIVE,
            'addons': _A,
            'announcements_url': _S,
            'changes_list_url': _S,
            'file_format': _S,
            'filemask': _S,
            'id': _I,
            'license_url': _S,
            'linked_component': _S,
            'links_url': _S,
            'lock_url': _S,
            'locked': _B,
            'name': _S,
            'project': 'Project',
            'repo': _S,
            'reports_url': _S,
            'repository_url': _S,
            'slug': _S,
            'statistics_url': _S,
            'task_url': _S_OR_NULL,
            'translations_url': _S,
            'url': _S,
            'web_url': _S,
        },
        'optional': {
            'is_glossary': _B,
            'template': _S,
            'category': _S_OR_NULL,
            'enable_suggestions': _B,
            'check_flags': _S,
        },
    },
    'Language': {
        'required': {
            'aliases': _A,
            'code': _S,
            'id': _I,
            'statistics_url': _S,
            'url': _S,
            'web_url': _S,
        },
        'optional': {'name': _S, 'direction': _S},
    },
    'Translation': {
        'required': {
            'announcements_url': _S,
            'changes_list_url': _S,
            'component': 'Component',
            'failing_checks': _I,
            'failing_checks_percent': _N,
            'failing_checks_words': _I,
            'file_url': _S,
            'filename': _S,
            'fuzzy': _I,
            'fuzzy_percent': _N,
            'fuzzy_words': _I,
            'have_comment': _I,
            'have_suggestion': _I,
            'id': _I,
            'is_source': _B,
            'is_template': _B,
            'language': 'Language',
            'last_author': _S_OR_NULL,
            'last_change': _S,
            'repository_url': _S,
            'share_url': _S,
            'statistics_url': _S,
            'total': _I,
            'total_words': _I,
            'translate_url': _S,
            'translated': _I,
            'translated_percent': _N,
            'translated_words': _I,
            'units_list_url': _S,
            'url': _S,
            'web_url': _S,
        },
        'optional': {'language_code': _S, 'revision': _S},
    },
    # GET /api/translations/<p>/<c>/<l>/units/ (PaginatedUnitList) and
    # /api/units/<id>/. state: 0 empty, 10 needs editing, 11 needs
    # rewriting, 12 needs checking, 20 translated, 30 approved, 100
    # read-only; last_updated since 5.6.
    'Unit': {
        'required': {
            'approved': _B,
            'content_hash': _I,
            'context': _S,
            'explanation': _S,
            'extra_flags': _S,
            'flags': _S,
            'fuzzy': _B,
            'has_comment': _B,
            'has_failing_check': _B,
            'has_suggestion': _B,
            'id': _I,
            'id_hash': _I,
            'labels': _A,
            'location': _S,
            'note': _S,
            'num_words': _I,
            'pending': _B,
            'position': _I,
            'previous_source': _S,
            'priority': _I,
            'source': _A,
            'source_unit': _S,
            'state': _I,
            'target': _A,
            'timestamp': _S,
            'translated': _B,
            'translation': _S,
            'url': _S,
            'web_url': _S,
        },
        'optional': {'last_updated': _S_OR_NULL},
    },
    # The 200 reply to PATCH /api/units/<id>/: the fields written. A
    # PATCH takes target (every form) and state, one of 0, 10, 11, 12,
    # 20, 30 (StringStateEnum; 11 and 12 since 2026.x).
    'UnitWrite': {
        'required': {
            'explanation': _S,
            'extra_flags': _S,
            'labels': _A,
            'state': _I,
            'target': _A,
        },
        'optional': {},
    },
    # GET /api/components/<p>/<c>/lock/
    'Lock': {'required': {'locked': _B}, 'optional': {}},
    # GET (paged) and POST /api/units/<id>/suggestions/ (2026.8), and
    # /api/suggestions/<id>/: user is the suggester's URL, or null when
    # anonymous or hidden.
    'Suggestion': {
        'required': {
            'id': _I,
            'unit': _S,
            'target': _A,
            'user': _S_OR_NULL,
            'timestamp': _S,
            'votes': _I,
        },
        'optional': {},
    },
    # GET (paged, 5.15) and POST (5.12) /api/units/<id>/comments/; a
    # POST also takes scope (global, translation or report), which no
    # reply gives back.
    'Comment': {
        'required': {'id': _I, 'comment': _S, 'user': _S_OR_NULL, 'timestamp': _S},
        'optional': {},
    },
}


def _type_ok(value, expected):
    names = expected if isinstance(expected, tuple) else (expected,)
    for name in names:
        types = _TYPES[name]
        # bool is an int in Python, never in JSON.
        if isinstance(value, bool) and bool not in types:
            continue
        if isinstance(value, types):
            return True
    return False


def check(name, obj, where=''):
    """The ways obj differs from SCHEMAS[name], as messages."""
    where = where or name
    if not isinstance(obj, dict):
        return ['%s: not an object' % where]
    schema = SCHEMAS[name]
    problems = []
    fields = {**schema['optional'], **schema['required']}
    for field, expected in fields.items():
        if field not in obj:
            if field in schema['required']:
                problems.append('%s.%s: missing' % (where, field))
        elif expected in SCHEMAS:
            problems += check(expected, obj[field], '%s.%s' % (where, field))
        elif not _type_ok(obj[field], expected):
            problems.append('%s.%s: %r is not %s' % (where, field, obj[field], expected))
    return problems


def check_page(name, page):
    """The ways a Paginated<name>List reply differs from its schema."""
    if not isinstance(page, dict):
        return ['page: not an object']
    problems = []
    if not _type_ok(page.get('count'), _I):
        problems.append('page.count: %r is not integer' % page.get('count'))
    for link in ('next', 'previous'):
        if link in page and not _type_ok(page[link], _S_OR_NULL):
            problems.append('page.%s: %r is not a URL' % (link, page[link]))
    results = page.get('results')
    if not isinstance(results, list):
        return [*problems, 'page.results: not an array']
    for i, obj in enumerate(results):
        problems += check(name, obj, 'results[%d]' % i)
    return problems


def check_error(reply):
    """The ways an error reply differs from drf-standardized-errors'
    shape (Weblate 5.10 and later)."""
    if not isinstance(reply, dict) or not isinstance(reply.get('errors'), list):
        return ['error: no errors list']
    problems = []
    if reply.get('type') not in ('client_error', 'server_error', 'validation_error'):
        problems.append('error.type: %r' % reply.get('type'))
    for i, error in enumerate(reply['errors']):
        if not isinstance(error, dict) or not isinstance(error.get('detail'), str):
            problems.append('errors[%d]: no detail' % i)
        elif not isinstance(error.get('code'), str):
            problems.append('errors[%d]: no code' % i)
    return problems
