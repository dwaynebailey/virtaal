#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Weblate's check flags (#3337). No GTK here.

Weblate keeps check flags on a component (check_flags) and on each
unit (flags, extra_flags), as a comma-separated text: "max-length:40,
ignore-end-stop, placeholders:"%(name)s":r"\\{\\w+\\}"". parse_flags()
reads one into (name, params).
"""


class Regex(str):
    """A flag's parameter written as a regular expression: r"..."."""


def _split(text, separator):
    """text split at separator, except within double quotes (where a
    backslash escapes the next character)."""
    parts, current, quoted, escaped = [], [], False, False
    for char in text:
        if escaped:
            escaped = False
        elif quoted and char == '\\':
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == separator and not quoted:
            parts.append(''.join(current))
            current = []
            continue
        current.append(char)
    parts.append(''.join(current))
    return parts


def _unquote(param):
    """A flag's parameter without its quotes: a Regex for r"..."."""
    param = param.strip()
    regex = param.startswith('r"')
    if regex:
        param = param[1:]
    if len(param) >= 2 and param[0] == param[-1] == '"':
        param = param[1:-1]
        out, escaped = [], False
        for char in param:
            if escaped or char != '\\':
                out.append(char)
                escaped = False
            else:
                escaped = True
                if regex:
                    out.append(char)
        param = ''.join(out)
    return Regex(param) if regex else param


def parse_flags(text):
    """[(name, [params])] of a check flags text, in order."""
    flags = []
    for part in _split(str(text or ''), ','):
        if not part.strip():
            continue
        name, *params = _split(part.strip(), ':')
        flags.append((name.strip(), [_unquote(p) for p in params]))
    return flags


# Weblate's checks as Virtaal's #

MAX_LENGTH = 'maxlength'
PLACEHOLDERS = 'placeholders'

CHECKS = {
    'same': ('unchanged',),
    'begin-newline': ('newlines',),
    'end-newline': ('newlines',),
    'newline-count': ('newlines',),
    'begin-space': ('startwhitespace',),
    'end-space': ('endwhitespace',),
    'double-space': ('doublespacing',),
    'end-stop': ('endpunc',),
    'end-colon': ('endpunc',),
    'end-question': ('endpunc',),
    'end-exclamation': ('endpunc',),
    'end-ellipsis': ('endpunc',),
    'end-semicolon': ('endpunc',),
    'escaped-newline': ('escapes',),
    'c-format': ('printf',),
    'python-format': ('printf',),
    'php-format': ('printf',),
    'perl-format': ('printf',),
    'python-brace-format': ('pythonbraceformat',),
    'xml-tags': ('xmltags',),
    'xml-invalid': ('xmltags',),
    'duplicate': ('doublewords',),
    'punctuation-spacing': ('puncspacing',),
    'plurals': ('nplurals',),
    'url': ('urls',),
    'max-length': (MAX_LENGTH,),
    'placeholders': (PLACEHOLDERS,),
}
"""{Weblate check id: the names of Virtaal's checks (failure names)
that do its work}: a flag ignore-<id> leaves those out on its unit,
and an enforced check is enforced on them. Ids are as in flags, "-"
between words (Weblate's own ids may have "_"). max-length and
placeholders are Virtaal's from their flags (WeblateCheck); flags such
as python-format, xml-text or strict-same turn on in Weblate what
Virtaal checks anyway, and forbidden is for glossaries."""

SERVER = 'weblate'
"""The failure name of a check failing on the server."""


def virtaal_names(check_id):
    """The names of Virtaal's checks for a Weblate check id."""
    return CHECKS.get(str(check_id).replace('_', '-'), ())


def enforced_names(record):
    """The names of Virtaal's checks the server enforces, for a side
    record."""
    return {name for check in record.get('enforced_checks') or () for name in virtaal_names(check)}


def _length_problem(target, params):
    """What max-length:<n> says of target (its forms), or None."""
    from virtaal.support.extrachecks import length_problem

    try:
        limit = int(params[-1])
    except (IndexError, ValueError):
        return None
    return length_problem(target, limit)


def _placeholder_problem(source, target, params):
    """What placeholders:<a>:<b> says of target (its forms), or None:
    each placeholder (or each match of a Regex one) in the source must
    be in every form of the target."""
    import re

    wanted = []
    for param in params:
        if isinstance(param, Regex):
            try:
                wanted += re.findall(param, source)
            except re.error:
                continue
        elif param in source:
            wanted.append(param)
    missing = sorted({p for p in wanted if any(p not in form for form in target)}, key=wanted.index)
    if not missing:
        return None
    # l10n: A quality check: placeholders a translation server says the translation must keep; %s is the list of them
    return _('Missing placeholders: %s') % ' '.join(missing)


def server_problem(entry, names):
    """What to say of a unit the server says fails a check: the names
    of the checks if known (names, from Virtaal's check names)."""
    labels = []
    for check in entry.get('checks') or ():
        mapped = virtaal_names(check)
        label = names.get(mapped[0], check) if mapped else check
        if label not in labels:
            labels.append(label)
    if labels:
        # l10n: A quality check: the translation server reports failing checks; %s lists them, such as "Unchanged, Maximum length"
        return _('Weblate reports failing checks: %s') % ', '.join(labels)
    # l10n: A quality check: the translation server reports a failing check, not saying which
    return _('Weblate reports a failing check')


class WeblateCheck:
    """The extra check (support/extrachecks.py) of Weblate's work copies,
    from their side records:

    - flags (the component's and the unit's) steer Virtaal's checks:
      ignore-<id> leaves out those for <id> (CHECKS); max-length and
      placeholders check as Weblate does, as "maxlength" and
      "placeholders";
    - a unit Weblate says fails a check shows it as "weblate", while
      its text is still the server's.

    A unit is found in its work copy among those open
    (workcopy.open_copies()): by the file's units attached, else, for a
    file read for the stats cache, by matching that file's units. Its
    config_name holds a digest of what it checks with, so the stats
    cache checks again when that changes."""

    name = SERVER
    in_child = False

    def __init__(self, copies=()):
        from translate.filters.decorators import Category

        self.category = Category.FUNCTIONAL
        self.copies = {copy.path: copy for copy in copies}
        self.config_name = '%s@%s' % (SERVER, _digest(self.copies.values()))
        self._read = {}
        """{id(a store read for the stats cache): (it, {id(unit): its
        index in the side record})}."""
        self._labels = None

    @staticmethod
    def _ours(copies):
        from .backend import WeblatePlatform

        return [copy for copy in copies if copy.record.get('platform') == WeblatePlatform.id]

    @classmethod
    def applies_to(cls, store):
        from virtaal.support.platforms import workcopy

        return bool(cls._ours(workcopy.open_copies()))

    @staticmethod
    def available():
        return True

    @classmethod
    def for_store(cls, store):
        from virtaal.support.platforms import workcopy

        return cls(cls._ours(workcopy.open_copies()))

    # Finding a unit's entry #

    def entry(self, unit):
        """(work copy, side record entry) of unit, or None."""
        for copy in self.copies.values():
            r = copy.index_of(unit)
            if r is not None and copy.matched.get(r) is unit:
                return copy, copy.units[r]
        return self._read_entry(unit)

    def _read_entry(self, unit):
        import os
        import weakref

        from virtaal.support.platforms import workcopy

        store = getattr(unit, '_store', None)
        filename = getattr(store, 'filename', None)
        copy = self.copies.get(os.path.abspath(filename)) if filename else None
        if copy is None:
            return None
        known = self._read.get(id(store))
        if known is None or known[0]() is not store:
            units = [u for u in store.units if u.istranslatable()]
            found = workcopy.match(units, copy.units)
            known = (weakref.ref(store), {id(units[i]): r for r, i in found.items()})
            self._read[id(store)] = known
        r = known[1].get(id(unit))
        return None if r is None else (copy, copy.units[r])

    # Checking #

    def labels(self):
        """Virtaal's names of its checks, to show."""
        if self._labels is None:
            from virtaal.controllers.checkscontroller import check_names

            self._labels = check_names
        return self._labels

    def steer(self, unit):
        """(names to leave out, {name: message}) for unit."""
        found = self.entry(unit)
        if found is None:
            return (), {}
        copy, entry = found
        from virtaal.support.platforms import workcopy

        target = workcopy.strings(unit.target)
        flags = parse_flags(copy.record.get('check_flags')) + parse_flags(entry.get('flags'))
        ignored = set()
        messages = {}
        for name, params in flags:
            if name.startswith('ignore-'):
                ignored.update(virtaal_names(name[len('ignore-') :]))
            elif name == 'max-length' and any(target):
                messages[MAX_LENGTH] = _length_problem(target, params)
            elif name == 'placeholders' and any(target):
                source = str(unit.source)
                messages[PLACEHOLDERS] = _placeholder_problem(source, target, params)
        for name in ignored:
            messages.pop(name, None)
        if entry.get('failing') and workcopy.text_hash(target) == entry.get('target_hash'):
            messages[SERVER] = server_problem(entry, self.labels())
        return ignored, messages


def _digest(copies):
    """A digest of what WeblateCheck checks copies with."""
    import hashlib
    import json

    data = [
        (
            copy.path,
            copy.record.get('check_flags'),
            copy.record.get('enforced_checks'),
            [
                (u.get('id'), u.get('flags'), u.get('failing'), u.get('checks'), u['target_hash'])
                for u in copy.units
            ],
        )
        for copy in sorted(copies, key=lambda c: c.path)
    ]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()[:16]
