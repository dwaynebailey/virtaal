#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Quality check results a translator dismissed as fine on a unit (#3276).

Kept in the file where it can hold a comment, so they go with it: a
translator note per check, a "#" comment in PO (which msgmerge keeps), a
<note from="translator"> in XLIFF, a translator comment in Qt .ts, and a
comment before the key in the monolingual formats that have comments
(see converters.MonolingualConverter.comment):

    (dismissed-check) endpunc 3f2a9c1e

The last word is a fingerprint of the unit's source and translation
when it was dismissed: once either changes, the dismissal no longer
counts. It stays with the unit while the file is open, so undoing the
change brings it back, but a save leaves it out.

Each save also keeps the file's dismissals in a record of this user's
(L{record_path}), for a format without comments and in case another
tool drops them; on opening, the file's own win, and one only in the
record counts if its fingerprint fits.

Not pofilter's "(pofilter) check: message" marker, which makes pofilter
skip the whole unit and the toolkit's isreview check fail."""

import contextlib
import hashlib
import json
import logging
import os
import threading

MARKER = '(dismissed-check)'
RECORD_FORMAT = 1
RECORD_DIRECTORY = 'dismissed-checks'
_ATTRIBUTE = '_dismissed_checks'
"""{check: fingerprint} on a unit whose format has no note for them."""


def _in_notes(unit):
    """Whether unit's format keeps a translator note to remember a
    dismissal in."""
    from translate.storage import pocommon, ts2, xliff

    return isinstance(unit, (pocommon.pounit, xliff.xliffunit, ts2.tsunit))


def unit_key(unit):
    """What identifies unit in its file, or None."""
    try:
        return unit.getid() or None
    except Exception:
        return None


def can_dismiss(unit):
    """Whether a dismissal on unit can be remembered: in the file, or
    in the record by the unit's key."""
    return _in_notes(unit) or unit_key(unit) is not None


def fingerprint(unit):
    """A short hash of unit's source and translation, every plural form."""
    texts = [_strings(unit.source), _strings(unit.target)]
    text = '\x01'.join('\x00'.join(strings) for strings in texts)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]


def _strings(text):
    if text is None:
        return []
    return [str(s) for s in getattr(text, 'strings', [text])]


def _notes(unit):
    try:
        notes = unit.getnotes('translator')
    except ValueError:
        return []
    return notes.splitlines() if notes else []


def parse(line):
    """(check, fingerprint) of a dismissal line, else None."""
    words = line.split()
    if len(words) == 3 and words[0] == MARKER:
        return words[1], words[2]
    return None


def line(check, mark):
    return '%s %s %s' % (MARKER, check, mark)


def _entries(unit):
    """(check, fingerprint) of every dismissal on unit, counting or not."""
    entries = list(getattr(unit, _ATTRIBUTE, {}).items())
    if _in_notes(unit):
        entries += [p for p in map(parse, _notes(unit)) if p]
    return entries


def dismissed(unit):
    """The checks dismissed on unit that still count."""
    entries = _entries(unit)
    if not entries:
        return set()
    current = fingerprint(unit)
    return {check for check, mark in entries if mark == current}


def lines(unit):
    """The dismissal lines that count on unit, for a file to keep."""
    current = fingerprint(unit)
    return [line(check, current) for check in sorted(dismissed(unit))]


def set_dismissed(unit, check, dismiss=True):
    """Dismiss check on unit, or take that back; either way drop the
    dismissals that no longer count.
    @returns: Whether unit's dismissals changed."""
    if not _in_notes(unit):
        return _set_attribute(unit, check, dismiss)
    return _rewrite(unit, check, dismiss)


def _set_attribute(unit, check, dismiss):
    old = getattr(unit, _ATTRIBUTE, {})
    current = fingerprint(unit)
    new = {c: mark for c, mark in old.items() if mark == current and c != check}
    if dismiss:
        new[check] = current
    if new == old:
        return False
    setattr(unit, _ATTRIBUTE, new)
    return True


def _rewrite(unit, check=None, dismiss=False):
    notes = _notes(unit)
    if not dismiss and not any(MARKER in note for note in notes):
        return False
    current = fingerprint(unit)
    kept = [
        note
        for note in notes
        if not (parsed := parse(note)) or (parsed[1] == current and parsed[0] != check)
    ]
    if dismiss:
        kept.append(line(check, current))
    if kept == notes:
        return False
    _set_notes(unit, kept)
    return True


def _set_notes(unit, notes):
    unit.removenotes('translator')
    if notes:
        unit.addnote(getattr(unit, 'newline', '\n').join(notes), 'translator')


@contextlib.contextmanager
def stale_left_out(units):
    """While saving units: without the dismissals that no longer count,
    which come back afterwards for an undo to find."""
    put_back = []
    for unit in units:
        if _in_notes(unit):
            notes = _notes(unit)
            if _rewrite(unit):
                put_back.append((unit, notes))
    try:
        yield
    finally:
        for unit, notes in put_back:
            _set_notes(unit, notes)


def has_stale(units):
    """Whether any of units keeps, in its file, a dismissal that no
    longer counts: a save removes it."""
    for unit in units:
        if not _in_notes(unit):
            continue
        entries = [p for p in map(parse, _notes(unit)) if p]
        if entries and any(mark != fingerprint(unit) for _check, mark in entries):
            return True
    return False


def without_dismissals(notes):
    """notes, without the lines that record dismissals."""
    if not notes or MARKER not in notes:
        return notes
    return '\n'.join(note for note in notes.splitlines() if not parse(note))


def filter_failures(unit, failures):
    """failures, without the checks dismissed on unit."""
    if not failures:
        return failures
    checks = dismissed(unit)
    if not checks:
        return failures
    return {name: value for name, value in failures.items() if name not in checks}


# The record #


def record_path(filename):
    from virtaal.common import pan_app

    key = os.path.normcase(os.path.abspath(filename))
    name = hashlib.sha256(key.encode('utf-8')).hexdigest()[:32] + '.json'
    return os.path.join(pan_app.get_state_dir(), RECORD_DIRECTORY, name)


def load_record(filename):
    """{unit key: {check: fingerprint}} recorded for filename."""
    path = record_path(filename)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logging.warning('Ignoring an unreadable record of dismissed checks: %s', path)
        return {}
    if not isinstance(data, dict) or data.get('format') != RECORD_FORMAT:
        return {}
    units = data.get('units')
    return units if isinstance(units, dict) else {}


def save_record(filename, units):
    """Record the dismissals that count on units, saved as filename."""
    found = {}
    for unit in units:
        key = unit_key(unit)
        checks = dismissed(unit)
        if key is not None and checks:
            current = fingerprint(unit)
            found[key] = {check: current for check in sorted(checks)}
    path = record_path(filename)
    if not found and not os.path.exists(path):
        return
    data = {
        'format': RECORD_FORMAT,
        'file': os.path.normcase(os.path.abspath(filename)),
        'units': found,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        partial = '%s.%d.tmp' % (path, os.getpid())
        with open(partial, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True)
        os.replace(partial, path)
    except OSError:
        logging.warning('Could not record the dismissed checks of %s', filename, exc_info=True)


def apply_record(filename, units):
    """Add to units the recorded dismissals their file lacks that still
    count."""
    record = load_record(filename)
    if not record:
        return
    for unit in units:
        entries = record.get(unit_key(unit))
        if not isinstance(entries, dict):
            continue
        current = fingerprint(unit)
        have = dismissed(unit)
        for check, mark in sorted(entries.items()):
            if mark == current and check not in have:
                set_dismissed(unit, check)


_pruning = None


def prune_in_background():
    """Once a session, drop in a background thread the records of files
    that are gone. A renamed file's record goes with its old path.
    @returns: The thread, or None if it ran already."""
    global _pruning
    if _pruning is not None:
        return None
    _pruning = threading.Thread(target=prune_records, name='prune-dismissed-checks', daemon=True)
    _pruning.start()
    return _pruning


def prune_records():
    """Drop the records of files that no longer exist.
    @returns: How many were dropped."""
    from virtaal.common import pan_app

    folder = os.path.join(pan_app.get_state_dir(), RECORD_DIRECTORY)
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    dropped = 0
    for name in names:
        if not name.endswith('.json'):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, encoding='utf-8') as f:
                target = json.load(f).get('file')
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(target, str) and not os.path.exists(target):
            try:
                os.remove(path)
                dropped += 1
            except OSError:
                pass
    logging.debug('Dropped %d records of dismissed checks for files that are gone', dropped)
    return dropped
