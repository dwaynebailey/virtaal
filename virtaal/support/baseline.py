#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""What a monolingual translation file and its template looked like when
last seen in sync, so that outside changes show on the next open.

One JSON file per target, in this user's state directory (never in the
project), named by a hash of the target's absolute path:

  {"format": 1, "target": <path>, "template": <path>, "chosen": <bool>,
   "units": {<key>: {"source": <text>, "source_hash": <hash>,
                     "target_hash": <hash or null>, "state": <state>,
                     "previous_source": <text>, "outside": <bool>}}}

"state" is "translated", "fuzzy" or "untranslated". "previous_source"
(the source the translation was made for) and "outside" (the target
changed outside Virtaal) are kept while the unit needs review."""

import hashlib
import json
import logging
import os

FORMAT = 1
DIRECTORY = 'monolingual'

TRANSLATED = 'translated'
FUZZY = 'fuzzy'
UNTRANSLATED = 'untranslated'


def text_hash(text):
    if text is None:
        return None
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()[:16]


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def sidecar_path(target):
    from virtaal.common import pan_app

    name = hashlib.sha256(_key(target).encode('utf-8')).hexdigest()[:32] + '.json'
    return os.path.join(pan_app.get_state_dir(), DIRECTORY, name)


def load(target):
    """The sidecar for C{target}, or None."""
    path = sidecar_path(target)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logging.warning('Ignoring an unreadable baseline: %s', path, exc_info=True)
        return None
    if (
        not isinstance(data, dict)
        or data.get('format') != FORMAT
        or data.get('target') != _key(target)
        or not isinstance(data.get('units'), dict)
    ):
        return None
    return data


def save(target, data):
    path = sidecar_path(target)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = dict(data, format=FORMAT, target=_key(target))
    temp = path + '.tmp'
    try:
        with open(temp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(temp, path)
    except OSError:
        logging.warning("Couldn't write the baseline %s", path, exc_info=True)


def remembered_template(target):
    """The template the user chose for C{target}, if it's still there."""
    data = load(target)
    template = data and data.get('chosen') and data.get('template')
    return template if template and os.path.isfile(template) else None


def remember_template(target, template):
    data = load(target) or {'units': {}}
    data.update(template=os.path.abspath(template), chosen=True)
    save(target, data)


def entry(source, target, state, previous_source=None, outside=False):
    found = {
        'source': source,
        'source_hash': text_hash(source),
        'target_hash': text_hash(target) if target else None,
        'state': state,
    }
    if state == FUZZY and previous_source is not None:
        found['previous_source'] = previous_source
    if state == FUZZY and outside:
        found['outside'] = True
    return found
