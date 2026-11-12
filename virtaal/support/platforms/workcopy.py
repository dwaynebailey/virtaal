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

  <state dir>/platforms/<platform>/<host>/<project>/<component>/<language>/<path>

<path> is the file's path as the platform has it, such as po/af.po or
values-af/strings.xml, so a format that takes its language from its
folder (Android's values-af) opens as it would from the project's own
files. A component's template (a monolingual format's source-language
file) goes into the same folder at its own path, such as
values/strings.xml (under _template/ if the two paths are one). A platform with no components leaves that level
out. Each part is the server's slug, or a part of the path, made safe
as a file name (safe_part()); a category's "/" makes a folder.

In the language's folder, .platform.json is the side record:

  {"format": 1, "platform": <id>, "platform_name": <name>,
   "server": <the server's URL>, "project": <slug>,
   "component": <slug or null>, "language": <code>,
   "names": {"project": <name>, "component": <name or null>},
   "file": <path from the work folder, "/" between parts>,
   "template": <path from the work folder or null>,
   "review": <whether the project reviews translations, or null>,
   "source_review": <whether it takes source reports, or null>,
   "downloaded_at": <UTC, ISO 8601>, "sha256": <the file at download>,
   "saved_sha256": <the file as Virtaal last saved it, or null>,
   "units": [{"id": <the platform's unit id>, "position": <int>,
              "context": <text>, "source_hash": <hash>,
              "target_hash": <hash>, "state": <the platform's state>,
              "virtaal_state": <StateEnum or null>,
              "last_updated": <text or null>, "read_only": <bool>,
              "has_suggestion": <bool>, "has_comment": <bool>,
              "source_id": <its source text's unit id, or null>,
              "flags": <its check flags>, "failing": <bool or null>,
              "checks": [<ids of the checks it fails, if known>],
              "changed": <bool>, "local_state": <StateEnum>}],
   "check_flags": <the component's check flags>,
   "enforced_checks": [<ids of the checks the server enforces>]}

Each unit's hashes, state and last_updated are as the server gave them
at download: the baseline Submit (submit.py) checks for conflicts
against. "changed" and "local_state" are written on each save: whether
the unit's text or state differ from the baseline, and the state chosen
in Virtaal (a PO file can't hold Approved, Needs review or Rejected; see
states.py).

Submit writes the record after each unit it sends, so a Submit cut
short (cancelled, out of budget, a crash) sends only the rest next
time: a unit sent has the sent text and state as its baseline,
"changed" false and "last_updated" null until Submit reads it back
from the server. A unit the server refused keeps "changed" and gets
"error", the server's reason. "submitted_at" is when a Submit last
left no change unsent.

A suggestion accepted in Virtaal is "accepted": {"id": its id,
"target_hash": its text's hash}. Submit accepts it on the server
(Session.accept_suggestion()) while the unit's text is still the
suggestion's, so the suggester keeps the credit; else it sends the
edit as any other.

Submitted as suggestions (a server's option), a unit keeps the
server's text as its baseline, and "suggested" is the hash of the text
suggested: it isn't sent again until it changes.

For a platform that can_check, "flags" and "check_flags" are the
platform's own check flags of the unit and its component, "failing"
whether the server says the unit fails a check (null when it doesn't
say), "checks" the ids of the enforced checks it fails, and
"enforced_checks" the checks the server enforces: Update and Submit
read them again (set_checks(), adopt()).

"refused" lists what the server refused this user on the project
(platforms.REVIEW: Reviewed is no longer offered; DISMISS: nor is
rejecting others' suggestions); "has_suggestion"
and "has_comment" are as the server last said (review.py asks again
when a unit is shown).

With "Send finished units as you go" on for the server (trickle.py),
"queue" lists the ids of the units saved at Translated or above and not
sent yet; a unit that changed on the server too gets "conflict" and
waits for Submit or Update, which clear it. "updated_at" is when Update
last asked the server for changes (update.py).

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
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {
    '%s%s' % (name, n) for name in ('COM', 'LPT') for n in '123456789\u00b9\u00b2\u00b3'
}
"""Names Windows keeps for devices, with any extension (nul.po)."""


def platforms_dir():
    from virtaal.common import pan_app

    return os.path.join(pan_app.get_state_dir(), DIRECTORY)


def safe_part(text):
    """text as one file name on every system: no separators or
    characters Windows refuses, no leading or trailing dots or spaces
    (so never '..'), never a name Windows keeps for a device, never
    empty."""
    text = _UNSAFE.sub('_', str(text or '')).strip(' .') or '_'
    if text.split('.', 1)[0].rstrip(' ').upper() in _RESERVED:
        text = '_' + text
    return text


def safe_path(path):
    """A platform's relative file path ("po/af.po") as safe parts, for
    os.path.join(): split at "/" and "\\", with empty, "." and ".."
    parts dropped, so it is never absolute and never leaves its folder."""
    parts = re.split(r'[/\\]', str(path or ''))
    return [safe_part(part) for part in parts if part not in ('', '.', '..')] or ['_']


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
            'has_suggestion': bool(unit.has_suggestion),
            'has_comment': bool(unit.has_comment),
            'source_id': unit.source_id,
            'flags': unit.flags or '',
            'failing': unit.failing,
            'checks': list(unit.checks),
            'changed': False,
        }
        for unit in remote_units
    ]


# Work copies #

_open = {}
"""{path: WorkCopy} of the files open (StoreController.work_copies)."""


def set_open(copies):
    """The work copies of the files open now: {path: WorkCopy}."""
    global _open
    _open = dict(copies)


def open_copies():
    """The work copies of the files open, attached or not."""
    return list(_open.values())


class WorkCopy:
    """A downloaded file and its side record: the platform context that
    travels with the store opened from it (StoreController.work_copy)."""

    def __init__(self, path, record, folder=None):
        self.path = os.path.abspath(path)
        self.record = record
        self._folder = os.path.abspath(folder) if folder else os.path.dirname(self.path)
        self.matched = {}
        """{index in record['units']: the store's unit}, once attached."""
        self._indexes = {}
        """{id(the store's unit): its index in record['units']}."""
        self.store = None
        """The StoreModel attached, if any."""

    def __repr__(self):
        return '<WorkCopy %s>' % self.path

    @property
    def folder(self):
        """The language's work folder, which holds the side record."""
        return self._folder

    @property
    def record_path(self):
        return os.path.join(self.folder, RECORD)

    @property
    def units(self):
        return self.record['units']

    def template_path(self):
        template = self.record.get('template')
        return os.path.join(self.folder, *safe_path(template)) if template else None

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

    def language_name(self):
        from virtaal.models.langmodel import LanguageModel

        code = self.record['language']
        known = LanguageModel(code).name
        return known if known and known != code else code

    def label(self):
        """The project and component and the language, to tell one work
        copy of several from another."""
        from . import file_label

        return file_label(self.location(), self.language_name())

    def write_record(self):
        data = json.dumps(self.record, ensure_ascii=False, indent=1, sort_keys=True)
        _write(self.record_path, data.encode('utf-8'))

    def changed_units(self):
        return [u for u in self.units if u.get('changed')]

    def pending(self):
        """The indexes in units of those Submit would send: changed in
        the file, there, and not read-only."""
        return [
            r
            for r in sorted(self.matched)
            if self.units[r].get('changed') and not self.units[r].get('read_only')
        ]

    def unsubmitted(self):
        """How many saved changes Submit would send, from the side record
        alone (pending() needs the file's units)."""
        return sum(1 for u in self.units if u.get('changed') and not u.get('read_only'))

    def submitted(self):
        """Whether the last Submit sent every change, and none came
        since."""
        return bool(self.record.get('submitted_at')) and not self.changed_units()

    def mine(self, r):
        """(target forms, state) of the file's unit for units[r]."""
        unit = self.matched[r]
        return strings(unit.target), unit.get_state_n() if unit.STATE else None

    def mark_sent(self, r, target, code, state):
        """Notes units[r] as sent with target and state (Virtaal's, and
        code as the server stored it), at once."""
        entry = self.units[r]
        entry.update(
            target_hash=text_hash(target),
            state=code,
            virtaal_state=state,
            last_updated=None,
            changed=False,
        )
        entry.pop('error', None)
        entry.pop('conflict', None)
        entry.pop('accepted', None)
        self.write_record()

    def mark_failed(self, r, message):
        self.units[r]['error'] = message
        self.write_record()

    def mark_accepted(self, r, suggestion_id, target):
        """Notes that units[r] took the suggestion suggestion_id, whose
        text is target, for Submit to accept it on the server."""
        self.units[r]['accepted'] = {'id': suggestion_id, 'target_hash': text_hash(target)}
        self.write_record()

    def accepted(self, r, target):
        """The id of the suggestion units[r] took, while target (the
        file's text) is still its, else None."""
        accepted = self.units[r].get('accepted')
        if accepted and accepted.get('target_hash') == text_hash(target):
            return accepted.get('id')
        return None

    def forget_accepted(self, r):
        self.units[r].pop('accepted', None)
        self.write_record()

    def mark_suggested(self, r, target):
        """Notes units[r] as suggested with target: the server's text
        stays its baseline."""
        entry = self.units[r]
        entry.update(suggested=text_hash(target), changed=False)
        entry.pop('error', None)
        entry.pop('conflict', None)
        self.write_record()

    def index_of(self, unit):
        """The index in units of the file's unit, or None."""
        return self._indexes.get(id(unit))

    def may_review(self):
        """Whether Reviewed is this user's to choose: the project reviews
        translations (or isn't known not to) and the server hasn't
        refused an approval."""
        from . import REVIEW

        return self.record.get('review') is not False and REVIEW not in self.refused()

    def refused(self):
        return self.record.get('refused') or []

    def refuse(self, what):
        """Notes that the server refused what (REVIEW, DISMISS) to this
        user."""
        if what not in self.refused():
            self.record['refused'] = [*self.refused(), what]
            self.write_record()
        self._allow_review()

    def _allow_review(self):
        allowed = self.may_review()
        for unit in self.matched.values():
            states.allow_review(unit, allowed)

    def set_flags(self, suggested, commented):
        """Which units have suggestions and comments now: their ids."""
        for entry in self.units:
            entry['has_suggestion'] = entry['id'] in suggested
            entry['has_comment'] = entry['id'] in commented

    def note_checks(self, r, remote):
        """Whether units[r] fails a check on the server, as remote (a
        RemoteUnit) says, if it does."""
        if remote.failing is not None:
            self.units[r].update(failing=remote.failing, checks=list(remote.checks))

    def set_checks(self, failing):
        """Which units fail a check on the server now: {unit id: (ids of
        the enforced checks it fails)}."""
        for entry in self.units:
            entry['failing'] = entry['id'] in failing
            entry['checks'] = list(failing.get(entry['id'], ()))

    def take(self, r, remote):
        """The server's text and state (a RemoteUnit) into the file's
        unit for units[r], and as its baseline. Whether the unit
        changed."""
        unit = self.matched[r]
        entry = self.units[r]
        forms = list(remote.target) or ['']
        changed = strings(unit.target) != forms
        if changed:
            from translate.misc.multistring import multistring

            unit.target = multistring(forms) if len(forms) > 1 else forms[0]
        if remote.state is not None and unit.STATE and unit.get_state_n() != remote.state:
            unit.set_state_n(remote.state)
            changed = True
        if states.is_adopted(unit):
            entry['local_state'] = remote.state
        self.adopt(r, remote)
        return changed

    def adopt(self, r, remote):
        """The server's unit (a RemoteUnit) as the baseline of units[r],
        which the file already agrees with."""
        self.units[r].update(
            target_hash=text_hash(remote.target),
            state=remote.code,
            virtaal_state=remote.state,
            last_updated=remote.last_updated,
            has_suggestion=bool(remote.has_suggestion),
            has_comment=bool(remote.has_comment),
            changed=False,
        )
        self.note_checks(r, remote)
        self.units[r].pop('error', None)
        self.units[r].pop('conflict', None)
        self.units[r].pop('suggested', None)
        self.units[r].pop('accepted', None)

    def mark_submitted(self):
        self.record['submitted_at'] = _now()
        self.write_record()

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
        self.store = store
        units = store.get_units()
        keyed = getattr(store, 'pair', None) is not None
        found = match(units, self.units, keyed)
        self.matched = {r: units[i] for r, i in found.items()}
        self._indexes = {id(unit): r for r, unit in self.matched.items()}
        for r, unit in self.matched.items():
            entry = self.units[r]
            if entry.get('read_only'):
                states.lock(unit)
            elif entry.get('virtaal_state') is not None:
                states.adopt(unit, entry.get('local_state', entry['virtaal_state']))
        self._allow_review()
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
            mine = text_hash(strings(unit.target))
            entry['changed'] = mine != entry['target_hash'] or (
                entry.get('virtaal_state') is not None and state != entry['virtaal_state']
            )
            if entry.get('suggested') is not None:
                # Suggested: a suggestion goes again once its text changes.
                entry['changed'] = entry['changed'] and mine != entry['suggested']
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


def _record_file(folder, record):
    """The work file a side record in folder names, kept inside it."""
    return os.path.join(folder, *safe_path(record['file']))


def for_file(path):
    """The WorkCopy of path, if it is a platform's work copy: the side
    record is in its folder or one above, inside the platforms' folder."""
    root = platforms_dir()
    if not path or not _inside(path, root):
        return None
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    while _inside(folder, root):
        record = _load_record(folder)
        if record is not None:
            if os.path.normcase(_record_file(folder, record)) != os.path.normcase(path):
                return None
            return WorkCopy(path, record, folder)
        folder = os.path.dirname(folder)
    return None


def unsubmitted_copies(platform_ids):
    """The work copies of platforms (their ids) holding saved changes
    not submitted, the one saved last first."""
    found = []
    for folder, _dirs, files in os.walk(platforms_dir()):
        if RECORD not in files:
            continue
        record = _load_record(folder)
        if record is None or record.get('platform') not in platform_ids:
            continue
        copy = WorkCopy(_record_file(folder, record), record, folder)
        if copy.unsubmitted() and os.path.isfile(copy.path):
            found.append((os.path.getmtime(copy.record_path), copy))
    return [copy for _when, copy in sorted(found, key=lambda pair: -pair[0])]


def existing(platform_id, server, project, component, language):
    """The WorkCopy already downloaded of a language, or None."""
    folder = work_folder(platform_id, server, project, component, language)
    record = _load_record(folder)
    if record is None:
        return None
    path = _record_file(folder, record)
    return WorkCopy(path, record, folder) if os.path.isfile(path) else None


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


def _write_into(folder, parts, data):
    path = os.path.join(folder, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write(path, data)
    return path


def create(platform, server, project, component, language, download):
    """Writes a Download into its work folder, with its side record and
    template, at their paths as the platform has them, replacing an
    earlier download. The WorkCopy."""
    folder = work_folder(platform.id, server, project, component, language)
    parts = safe_path(download.filename)
    path = _write_into(folder, parts, download.data)
    template = None
    if download.template is not None:
        name, data = download.template
        template_parts = safe_path(name)
        if [p.lower() for p in template_parts] == [p.lower() for p in parts]:
            template_parts = [TEMPLATE_FOLDER, *template_parts]
        template_path = _write_into(folder, template_parts, data)
        template = '/'.join(template_parts)
        _seed_baseline(path, template_path, language, download.units)
    options = download.options or {}
    record = {
        'format': FORMAT,
        'platform': platform.id,
        'platform_name': platform.name,
        'server': server,
        'project': project,
        'component': component,
        'language': language,
        'names': dict(download.names),
        'file': '/'.join(parts),
        'template': template,
        'review': options.get('review'),
        'source_review': options.get('source_review'),
        'check_flags': options.get('check_flags') or '',
        'enforced_checks': list(options.get('enforced_checks') or ()),
        'downloaded_at': _now(),
        'sha256': hashlib.sha256(download.data).hexdigest(),
        'saved_sha256': None,
        'units': record_units(download.units),
    }
    copy = WorkCopy(path, record, folder)
    copy.write_record()
    return copy
