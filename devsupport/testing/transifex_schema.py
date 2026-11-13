#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The shapes of the Transifex API v3 replies faketransifex.py serves,
to check them against.

Written by hand from Transifex's public API reference
(developers.transifex.com/reference, read 2026-11-12): the JSON:API
envelope (data, included, links; errors with status, code, title,
detail), the id patterns, and the attributes of each object Virtaal
reads, with their types. Not a copy of Transifex's OpenAPI document;
no request was made to Transifex.
"""

import re

_S, _I, _B, _O, _A = 'string', 'integer', 'boolean', 'object', 'array'
_TYPES = {
    _S: (str,),
    _I: (int,),
    _B: (bool,),
    _O: (dict,),
    _A: (list,),
    'null': (type(None),),
}
_S_OR_NULL = (_S, 'null')
_O_OR_NULL = (_O, 'null')

_SLUG = r'[a-zA-Z0-9._-]+'
_LANGUAGE_ID = r'l:[a-zA-Z0-9@_.-]+'
_PROJECT_ID = r'o:%s:p:[a-zA-Z0-9_-]+' % _SLUG
_RESOURCE_ID = _PROJECT_ID + r':r:[a-zA-Z0-9_-]+'

OBJECTS = {
    'organizations': {
        'id': r'o:%s' % _SLUG,
        'attributes': {'name': _S, 'slug': _S, 'private': _B, 'logo_url': _S_OR_NULL},
    },
    'projects': {
        'id': _PROJECT_ID,
        'attributes': {
            'name': _S,
            'slug': _S,
            'description': _S_OR_NULL,
            'private': _B,
            'archived': _B,
            'datetime_created': _S,
            'datetime_modified': _S,
        },
        'relationships': ('organization', 'source_language'),
    },
    'languages': {
        'id': _LANGUAGE_ID,
        'attributes': {
            'code': _S,
            'name': _S,
            'rtl': _B,
            'plural_equation': _S,
            'plural_rules': _O,
        },
    },
    'resources': {
        'id': _RESOURCE_ID,
        'attributes': {
            'name': _S,
            'slug': _S,
            'string_count': _I,
            'word_count': _I,
            'accept_translations': _B,
            'datetime_modified': _S,
        },
        'relationships': ('project', 'i18n_format'),
    },
    'resource_language_stats': {
        'id': _RESOURCE_ID + ':l:[a-zA-Z0-9@_.-]+',
        'attributes': {
            'untranslated_strings': _I,
            'translated_strings': _I,
            'reviewed_strings': _I,
            'proofread_strings': _I,
            'total_strings': _I,
            'last_update': _S_OR_NULL,
        },
        'relationships': ('language', 'resource'),
    },
    'resource_strings': {
        'id': _RESOURCE_ID + r':s:[0-9a-fA-F]{32}',
        'attributes': {
            'key': _S,
            'context': (_S, _A),
            'strings': _O,
            'pluralized': _B,
            'developer_comment': _S_OR_NULL,
            'instructions': _S_OR_NULL,
            'character_limit': (_I, 'null'),
            'occurrences': _S_OR_NULL,
            'tags': _A,
            'string_hash': _S,
            'strings_datetime_modified': _S,
        },
    },
    'resource_translations': {
        'id': _RESOURCE_ID + r':s:[0-9a-fA-F]{32}:l:[a-zA-Z0-9@_-]+',
        'attributes': {
            'strings': _O_OR_NULL,
            'reviewed': _B,
            'proofread': _B,
            'finalized': _B,
            'origin': _S_OR_NULL,
            'datetime_created': _S,
            'datetime_translated': _S_OR_NULL,
            'datetime_reviewed': _S_OR_NULL,
            'datetime_proofread': _S_OR_NULL,
        },
        'relationships': ('resource', 'language', 'resource_string'),
    },
    'resource_translations_async_downloads': {
        'id': r'[0-9a-f-]{36}',
        'attributes': {'status': _S, 'errors': _A, 'date_created': _S, 'date_modified': _S},
    },
    'resource_strings_async_downloads': {
        'id': r'[0-9a-f-]{36}',
        'attributes': {'status': _S, 'errors': _A, 'date_created': _S, 'date_modified': _S},
    },
}

ERROR = {'status': _S, 'code': _S, 'title': _S, 'detail': _S}

JOB_STATUSES = ('pending', 'processing', 'failed', 'succeeded')


def _type_problems(where, value, kinds):
    kinds = kinds if isinstance(kinds, tuple) else (kinds,)
    allowed = tuple(t for kind in kinds for t in _TYPES[kind])
    if isinstance(value, bool) and bool not in allowed:
        return ['%s: %r is not %s' % (where, value, '/'.join(kinds))]
    if not isinstance(value, allowed):
        return ['%s: %r is not %s' % (where, value, '/'.join(kinds))]
    return []


def check_object(item):
    """The problems with one JSON:API resource object, [] if none."""
    kind = item.get('type')
    shape = OBJECTS.get(kind)
    if shape is None:
        return ['unknown type %r' % kind]
    problems = []
    if not re.fullmatch(shape['id'], str(item.get('id'))):
        problems.append('%s: id %r' % (kind, item.get('id')))
    attributes = item.get('attributes')
    if not isinstance(attributes, dict):
        return [*problems, '%s: no attributes' % kind]
    for name, kinds in shape['attributes'].items():
        if name not in attributes:
            problems.append('%s: no %s' % (kind, name))
        else:
            problems += _type_problems('%s.%s' % (kind, name), attributes[name], kinds)
    for name in shape.get('relationships', ()):
        relation = (item.get('relationships') or {}).get(name)
        if not isinstance(relation, dict) or 'data' not in relation and 'links' not in relation:
            problems.append('%s: no relationship %s' % (kind, name))
    if kind.endswith('async_downloads') and attributes.get('status') not in JOB_STATUSES:
        problems.append('%s: status %r' % (kind, attributes.get('status')))
    return problems


def check_document(body):
    """The problems with a reply: one object or a list of them, with the
    included ones and the links of a list."""
    if not isinstance(body, dict) or 'data' not in body:
        return ['no data']
    data = body['data']
    items = data if isinstance(data, list) else [data]
    problems = []
    for item in [*items, *(body.get('included') or [])]:
        problems += check_object(item)
    if isinstance(data, list):
        links = body.get('links')
        if not isinstance(links, dict) or 'next' not in links or 'previous' not in links:
            problems.append('a list without links.next and links.previous')
    return problems


def check_error(body):
    errors = body.get('errors') if isinstance(body, dict) else None
    if not isinstance(errors, list) or not errors:
        return ['no errors']
    problems = []
    for error in errors:
        for name, kinds in ERROR.items():
            if name not in error:
                problems.append('error: no %s' % name)
            else:
                problems += _type_problems('error.' + name, error[name], kinds)
    return problems
