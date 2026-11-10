#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A read-only scan of every file of a set, each in its own language: the
quality checks that compare a translation with its source, or a search.
It runs in a child process that appends one JSON line per file to a
results file, which the main loop reads as it grows."""

import json
import logging
import os
import subprocess
import sys
import tempfile

# Checks that compare the translation with its source, so they mean the
# same in every language.
CHECKS = (
    'printf',
    'pythonbraceformat',
    'variables',
    'xmltags',
    'escapes',
    'newlines',
    'accelerators',
    'brackets',
    'numbers',
    'urls',
    'emails',
    'filepaths',
    'functions',
    'options',
    'tabs',
)
DEFAULT_CHECKS = (
    'printf',
    'pythonbraceformat',
    'variables',
    'xmltags',
    'escapes',
    'newlines',
    'accelerators',
)

# More rows than this would make the list slow to fill and to read.
MAX_RESULTS = 5000

# How long the main loop waits between reads of the results file.
POLL_MS = 150


def checker_for(code, language, checks):
    """A checker of project style C{code} running only C{checks}."""
    from virtaal.support import checkerspec

    classes = checkerspec.checker_classes()
    checker = classes.get(code, classes[None])(limitfilters=list(checks))
    checker.config.updatetargetlanguage(language or None)
    return checker


def _texts(unit):
    return str(unit.source or ''), str(unit.target or '')


def _translatable(units):
    return [(i, u) for i, u in enumerate(units) if u.istranslatable()]


def check_units(units, checker, checks):
    """(unit index, position, check, message, source, target) of each
    failure. The position counts translatable units from 0, as the
    editor lists them; the index counts every unit of the file."""
    checks = set(checks)
    for position, (index, unit) in enumerate(_translatable(units)):
        if not unit.target:
            continue
        try:
            failures = checker.run_filters(unit)
        except Exception:
            logging.debug('Checking unit %d failed', index, exc_info=True)
            continue
        for name, message in sorted(failures.items()):
            if name in checks:
                yield (index, position, name, str(message)) + _texts(unit)


def search_units(units, grep):
    """(unit index, position, '', '', source, target) of each unit
    C{grep} matches, as L{check_units} gives them."""
    translatable = _translatable(units)
    _matches, found = grep.getmatches([u for _i, u in translatable])
    seen = set()
    for position in found:
        if position in seen:
            continue
        seen.add(position)
        index, unit = translatable[position]
        yield (index, position, '', '') + _texts(unit)


def make_grep(search):
    from virtaal.support.pogrep_compat import GrepFilter

    return GrepFilter(
        searchstring=search['text'],
        searchparts=tuple(search.get('parts', ('source', 'target'))),
        ignorecase=not search.get('case', False),
        useregexp=search.get('regex', False),
    )


def scan(job, out):
    """Write one JSON line per file of C{job} to the text file C{out}:
    C{{"file": n, "results": [...]}} or C{{"file": n, "error": "..."}};
    results are lists as L{check_units} or L{search_units} yield them."""
    from translate.storage import factory

    checkers = {}
    grep = make_grep(job['search']) if job['kind'] == 'search' else None
    for number, (path, language) in enumerate(job['files']):
        line = {'file': number}
        try:
            units = factory.getobject(path).units
            if grep is not None:
                line['results'] = [list(r) for r in search_units(units, grep)]
            else:
                if language not in checkers:
                    checkers[language] = checker_for(job['checker'], language, job['checks'])
                rows = check_units(units, checkers[language], job['checks'])
                line['results'] = [list(r) for r in rows]
        except Exception as exc:
            line['error'] = str(exc)
        out.write(json.dumps(line) + '\n')
        out.flush()


class Scan:
    """A scan running in a child process. C{on_file(number, results,
    error)} is called from the main loop for each file as it is done,
    then C{on_done(finished)}: False when it stopped early."""

    def __init__(self, job, on_file, on_done):
        from gi.repository import GLib

        from virtaal.support import helperprocess

        self.total = len(job['files'])
        self.done_files = 0
        self._on_file = on_file
        self._on_done = on_done
        self._partial = ''
        fd, self._jobfile = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(job, f)
        fd, self._outfile = tempfile.mkstemp(suffix='.jsonl')
        os.close(fd)
        self._out = open(self._outfile, encoding='utf-8')  # noqa: SIM115
        self.proc = subprocess.Popen(
            helperprocess.command(__name__) + [self._jobfile, self._outfile],
            env=helperprocess.env(),
        )
        self._source = GLib.timeout_add(POLL_MS, self._poll)

    @property
    def running(self):
        return self.proc is not None

    def stop(self):
        """Stop the child; C{on_done(False)} is called."""
        self._finish(False)

    def _read(self):
        text = self._partial + self._out.read()
        lines = text.split('\n')
        self._partial = lines.pop()
        for line in lines:
            if not line:
                continue
            data = json.loads(line)
            self.done_files += 1
            self._on_file(data['file'], data.get('results', []), data.get('error'))

    def _poll(self):
        if self.proc is None:
            return False
        exited = self.proc.poll() is not None
        self._read()
        if exited:
            if self.proc.returncode:
                logging.debug('The scan stopped (exit status %s)', self.proc.returncode)
            self._source = None
            self._finish(self.done_files == self.total)
            return False
        return True

    def _finish(self, finished):
        from gi.repository import GLib

        if self.proc is None:
            return
        proc, self.proc = self.proc, None
        if self._source is not None:
            GLib.source_remove(self._source)
            self._source = None
        if proc.poll() is None:
            proc.terminate()
            proc.wait()
        self._out.close()
        for path in (self._jobfile, self._outfile):
            try:
                os.remove(path)
            except OSError:
                pass
        self._on_done(finished)


if __name__ == '__main__':
    jobfile, outfile = sys.argv[1:]
    with open(jobfile, encoding='utf-8') as f:
        job = json.load(f)
    with open(outfile, 'a', encoding='utf-8') as out:
        scan(job, out)
