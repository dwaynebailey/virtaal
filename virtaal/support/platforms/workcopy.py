#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Work copies of a translation platform's files (#3337). No GTK here.

Opening a translation from a platform's server downloads its file, in
the platform's own format, into a work folder in this user's state
directory, and opens that file like any other:

  <state dir>/platforms/<platform>/<host>/<project>/<component>/<language>/<file>

A component's template (a monolingual format's source-language file)
goes beside its languages, in <component>/_template/. A platform with
no components leaves that level out. Each part is the server's slug
made safe as a file name; a category's "/" makes a folder.

Beside the file, .platform.json is its side record:

  {"format": 1, "platform": <id>, "platform_name": <name>,
   "server": <the server's URL>, "project": <slug>,
   "component": <slug or null>, "language": <code>,
   "names": {"project": <name>, "component": <name or null>},
   "file": <file name>, "template": <path from the work folder or null>,
   "downloaded_at": <UTC, ISO 8601>, "sha256": <the file at download>,
   "saved_sha256": <the file as Virtaal last saved it, or null>,
   "units": [{"id": <the platform's unit id>, "position": <int>,
              "context": <text>, "source_hash": <hash>,
              "target_hash": <hash>, "state": <the platform's state>,
              "virtaal_state": <StateEnum or null>,
              "last_updated": <text or null>, "read_only": <bool>,
              "changed": <bool>, "local_state": <StateEnum>}]}

Each unit's hashes, state and last_updated are as the server gave them
at download: the baseline Submit (W3) checks for conflicts against.
"changed" and "local_state" are written on each save: whether the
unit's text or state differ from the baseline, and the state chosen in
Virtaal (a PO file can't hold Approved, Needs review or Rejected; see
states.py).

Matching the file's units to the platform's (match()): by context
(PO's msgctxt; a monolingual file's key), then by source text, then by
position. Units with the same context and source pair in file order.
In a monolingual file, where the context is the key, a unit whose
source changed still matches by its key alone when the key names one
unit on each side. A unit left over matches
none: it shows the file's own state and isn't Submitted.

Work folders are never removed here: they may hold work not yet
submitted.
"""

import datetime
import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from urllib import parse

from translate.storage.workflow import StateEnum

from virtaal.support import baseline

from . import states

FORMAT = 1
DIRECTORY = 'platforms'
RECORD = '.platform.json'
TEMPLATE_FOLDER = '_template'

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def platforms_dir():
    from virtaal.common import pan_app

    return os.path.join(pan_app.get_state_dir(), DIRECTORY)


def safe_part(text):
    """text as one file name on every system: no separators or
    characters Windows refuses, no leading or trailing dots or spaces
    (so never '..'), never empty."""
    text = _UNSAFE.sub('_', str(text or '')).strip(' .')
    return text or '_'


def _slug_parts(slug):
    return [safe_part(part) for part in str(slug).split('/') if part] or ['_']


def host_part(url):
    """The server's host (and port) from its URL, as a folder name."""
    netloc = parse.urlsplit(url).netloc.lower().rpartition('@')[2]
    return safe_part(netloc.replace(':', '_'))


def work_folder(platform_id, server, project, component, language):
    """The folder of one language's work copy."""
    parts = [platforms_dir(), safe_part(platform_id), host_part(server)]
    parts += _slug_parts(project)
    if component:
        parts += _slug_parts(component)
    parts.append(safe_part(language))
    return os.path.join(*parts)


def text_hash(strings):
    """A short hash of a unit's text: its forms, a list of strings."""
    data = json.dumps(list(strings), ensure_ascii=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()[:16]


def strings(value):
    """A unit's source or target as a list of its forms."""
    if value is None:
        return ['']
    forms = getattr(value, 'strings', None)
    if forms is not None:
        return [str(form) for form in forms] or ['']
    return [str(value)]


def _file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 16), b''):
            digest.update(block)
    return digest.hexdigest()


def _write(path, data):
    """Writes the bytes data to path, replacing it only once whole."""
    temp = path + '.tmp'
    with open(temp, 'wb') as f:
        f.write(data)
    os.replace(temp, path)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# Matching #


def unit_context(unit, keyed):
    """What the platform calls a unit's context: a monolingual file's
    key (keyed), else the unit's own context (PO's msgctxt)."""
    return (unit.getid() if keyed else unit.getcontext()) or ''


def match(local, remote, keyed=False):
    """{index in remote: index in local} of the units that are the same.
    local are translate-toolkit units; remote the side record's units
    (context, source_hash), in any order. See the module's comment."""
    by_context = defaultdict(list)
    for i, unit in enumerate(local):
        by_context[unit_context(unit, keyed)].append(i)
    remote_count = defaultdict(int)
    for entry in remote:
        remote_count[entry['context']] += 1
    source_hashes = {}
    taken = set()
    found = {}
    order = sorted(range(len(remote)), key=lambda r: remote[r].get('position') or 0)
    for r in order:
        context = remote[r]['context']
        candidates = [i for i in by_context.get(context, ()) if i not in taken]
        same = []
        for i in candidates:
            if i not in source_hashes:
                source_hashes[i] = text_hash(strings(local[i].source))
            if source_hashes[i] == remote[r]['source_hash']:
                same.append(i)
        if not same and keyed and candidates and remote_count[context] == 1:
            same = candidates if len(by_context[context]) == 1 else []
        if same:
            found[r] = same[0]
            taken.add(same[0])
    return found


def record_units(remote_units):
    """The side record's units for a Download's RemoteUnits."""
    return [
        {
            'id': unit.id,
            'position': unit.position,
            'context': unit.context or '',
            'source_hash': text_hash(unit.source),
            'target_hash': text_hash(unit.target),
            'state': unit.code,
            'virtaal_state': unit.state,
            'last_updated': unit.last_updated,
            'read_only': bool(unit.read_only),
            'changed': False,
        }
        for unit in remote_units
    ]


# Work copies #


class WorkCopy:
    """A downloaded file and its side record: the platform context that
    travels with the store opened from it (StoreController.work_copy)."""

    def __init__(self, path, record):
        self.path = os.path.abspath(path)
        self.record = record
        self.matched = {}
        """{index in record['units']: the store's unit}, once attached."""

    def __repr__(self):
        return '<WorkCopy %s>' % self.path

    @property
    def folder(self):
        return os.path.dirname(self.path)

    @property
    def record_path(self):
        return os.path.join(self.folder, RECORD)

    @property
    def units(self):
        return self.record['units']

    def template_path(self):
        template = self.record.get('template')
        return os.path.normpath(os.path.join(self.folder, template)) if template else None

    def platform_name(self):
        from . import registry

        for platform in registry.platforms:
            if platform.id == self.record['platform']:
                return platform.name
        return self.record.get('platform_name') or self.record['platform']

    def location(self):
        """The project and component (if any) as their names."""
        names = self.record.get('names') or {}
        project = names.get('project') or self.record['project']
        component = names.get('component') or self.record.get('component')
        return '%s/%s' % (project, component) if component else project

    def write_record(self):
        data = json.dumps(self.record, ensure_ascii=False, indent=1, sort_keys=True)
        _write(self.record_path, data.encode('utf-8'))

    def changed_units(self):
        return [u for u in self.units if u.get('changed')]

    def has_local_changes(self):
        """Whether the file holds work the server doesn't have: units
        changed in Virtaal, or the file changed elsewhere."""
        if self.changed_units():
            return True
        try:
            digest = _file_hash(self.path)
        except OSError:
            return False
        return digest not in (self.record.get('sha256'), self.record.get('saved_sha256'))

    def attach(self, store):
        """Matches store's units (a StoreModel opened from this file) to
        the record's, showing the platform's states and locks."""
        units = store.get_units()
        keyed = getattr(store, 'pair', None) is not None
        found = match(units, self.units, keyed)
        self.matched = {r: units[i] for r, i in found.items()}
        for r, unit in self.matched.items():
            entry = self.units[r]
            if entry.get('read_only'):
                states.lock(unit)
            elif entry.get('virtaal_state') is not None:
                states.adopt(unit, entry.get('local_state', entry['virtaal_state']))
        if len(found) < len(self.units):
            logging.info(
                '%s: %d of the server\'s %d units not in the file',
                self.path,
                len(self.units) - len(found),
                len(self.units),
            )

    def saved(self):
        """Notes in the side record which units changed since download,
        and their states, after the file was saved."""
        for r, unit in self.matched.items():
            entry = self.units[r]
            state = unit.get_state_n() if unit.STATE else None
            entry['changed'] = text_hash(strings(unit.target)) != entry['target_hash'] or (
                entry.get('virtaal_state') is not None and state != entry['virtaal_state']
            )
            if states.is_adopted(unit):
                entry['local_state'] = state
        try:
            self.record['saved_sha256'] = _file_hash(self.path)
        except OSError:
            pass
        self.write_record()


def _load_record(folder):
    path = os.path.join(folder, RECORD)
    try:
        with open(path, 'rb') as f:
            data = json.loads(f.read().decode('utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logging.warning('Ignoring an unreadable side record: %s', path, exc_info=True)
        return None
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        return None
    if not isinstance(data.get('units'), list) or not data.get('file'):
        return None
    return data


def _inside(path, folder):
    path = os.path.normcase(os.path.realpath(path))
    folder = os.path.normcase(os.path.realpath(folder))
    return path.startswith(folder + os.sep)


def for_file(path):
    """The WorkCopy of path, if it is a platform's work copy."""
    if not path or not _inside(path, platforms_dir()):
        return None
    record = _load_record(os.path.dirname(os.path.abspath(path)))
    if record is None or record['file'] != os.path.basename(path):
        return None
    return WorkCopy(path, record)


def existing(platform_id, server, project, component, language):
    """The WorkCopy already downloaded of a language, or None."""
    folder = work_folder(platform_id, server, project, component, language)
    record = _load_record(folder)
    if record is None:
        return None
    path = os.path.join(folder, record['file'])
    return WorkCopy(path, record) if os.path.isfile(path) else None


def _baseline_state(unit):
    if unit.state is None:
        return baseline.TRANSLATED if any(unit.target) else baseline.UNTRANSLATED
    if unit.state == StateEnum.NEEDS_REVIEW:
        return baseline.NEEDS_REVIEW
    return (baseline.UNTRANSLATED, baseline.NEEDS_WORK, baseline.TRANSLATED)[
        states.coarse(unit.state)
    ]


def _seed_baseline(path, template_path, language, remote_units):
    """Pairs a monolingual work file with its template, and gives the
    monolingual baseline the server's states: a translation it hasn't
    seen would otherwise open as needing review."""
    baseline.remember_template(path, template_path, language)
    data = baseline.load(path) or {}
    units = {}
    for unit in remote_units:
        if not unit.context or len(unit.source) != 1 or len(unit.target) != 1:
            continue
        state = _baseline_state(unit)
        target = unit.target[0] if state != baseline.UNTRANSLATED else None
        units[unit.context] = baseline.entry(unit.source[0], target, state)
    data['units'] = units
    baseline.save(path, data)


def create(platform, server, project, component, language, download):
    """Writes a Download into its work folder, with its side record and
    template, replacing an earlier download. The WorkCopy."""
    folder = work_folder(platform.id, server, project, component, language)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, safe_part(download.filename))
    _write(path, download.data)
    template = None
    if download.template is not None:
        name, data = download.template
        template_folder = os.path.join(os.path.dirname(folder), TEMPLATE_FOLDER)
        os.makedirs(template_folder, exist_ok=True)
        template_path = os.path.join(template_folder, safe_part(name))
        _write(template_path, data)
        template = os.path.relpath(template_path, folder)
        _seed_baseline(path, template_path, language, download.units)
    record = {
        'format': FORMAT,
        'platform': platform.id,
        'platform_name': platform.name,
        'server': server,
        'project': project,
        'component': component,
        'language': language,
        'names': dict(download.names),
        'file': os.path.basename(path),
        'template': template,
        'downloaded_at': _now(),
        'sha256': hashlib.sha256(download.data).hexdigest(),
        'saved_sha256': None,
        'units': record_units(download.units),
    }
    copy = WorkCopy(path, record)
    copy.write_record()
    return copy
