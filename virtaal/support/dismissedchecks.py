#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Quality check results a translator dismissed as fine on a unit (#3276).

Kept in the file, so they go with it: a translator note per check, a
"#" comment in PO (which msgmerge keeps), a <note from="translator"> in
XLIFF and a translator comment in Qt .ts:

    (dismissed-check) endpunc 3f2a9c1e

The last word is a fingerprint of the unit's source and translation
when it was dismissed: once either changes, the dismissal no longer
counts, and Virtaal drops it when the unit is next edited.

Not pofilter's "(pofilter) check: message" marker, which makes pofilter
skip the whole unit and the toolkit's isreview check fail."""

import hashlib

MARKER = '(dismissed-check)'


def can_dismiss(unit):
    """Whether unit's format keeps a translator note to remember a
    dismissal in."""
    from translate.storage import pocommon, ts2, xliff

    return isinstance(unit, (pocommon.pounit, xliff.xliffunit, ts2.tsunit))


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


def _parse(line):
    """(check, fingerprint) of a dismissal line, else None."""
    words = line.split()
    if len(words) == 3 and words[0] == MARKER:
        return words[1], words[2]
    return None


def dismissed(unit):
    """The checks dismissed on unit that still count."""
    if not can_dismiss(unit):
        return set()
    lines = [_parse(line) for line in _notes(unit) if MARKER in line]
    if not any(lines):
        return set()
    current = fingerprint(unit)
    return {check for check, mark in filter(None, lines) if mark == current}


def set_dismissed(unit, check, dismiss=True):
    """Dismiss check on unit, or take that back; either way drop the
    dismissals that no longer count.
    @returns: Whether unit's notes changed."""
    return _rewrite(unit, check, dismiss)


def drop_stale(unit):
    """Drop the dismissals made for another source or translation.
    @returns: Whether unit's notes changed."""
    return _rewrite(unit)


def _rewrite(unit, check=None, dismiss=False):
    lines = _notes(unit)
    if not any(MARKER in line for line in lines) and not dismiss:
        return False
    current = fingerprint(unit)
    kept = [
        line
        for line in lines
        if not (parsed := _parse(line)) or (parsed[1] == current and parsed[0] != check)
    ]
    if dismiss:
        kept.append('%s %s %s' % (MARKER, check, current))
    if kept == lines:
        return False
    unit.removenotes('translator')
    if kept:
        unit.addnote(getattr(unit, 'newline', '\n').join(kept), 'translator')
    return True


def without_dismissals(notes):
    """notes, without the lines that record dismissals."""
    if not notes or MARKER not in notes:
        return notes
    return '\n'.join(line for line in notes.splitlines() if not _parse(line))


def filter_failures(unit, failures):
    """failures, without the checks dismissed on unit."""
    if not failures:
        return failures
    checks = dismissed(unit)
    if not checks:
        return failures
    return {name: value for name, value in failures.items() if name not in checks}
