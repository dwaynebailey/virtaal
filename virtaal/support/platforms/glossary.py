#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A translation platform's glossaries, for the terminology pane (#3337).
No GTK here.

A platform that can_glossary gives the glossaries of a project in a
language (Session.glossaries()). They are kept in this user's state
directory, beside the work copies, one folder for each server, project
and language:

  <state dir>/platforms/.glossaries/<platform>/<host>/<project>/<language>/

holding each glossary's file, <its slug>.<extension>, and the record
.glossaries.json:

  {"format": 1, "checked_at": <seconds since the epoch, or null>,
   "glossaries": {<slug>: {"name": <its name>, "file": <file name>,
                           "validator": <its Last-Modified, or null>}}}

A work copy of project X in language L uses X's glossaries in L
(for_copy()): the files kept show at once, and unless they were checked
in the last REFRESH_AGE, refresh() asks the server again, sending each
glossary's validator (its Last-Modified, where the server gives one), so
an unchanged one costs no download. A glossary the server no longer has in L is dropped. With no
reply, the files kept stay as they are.
"""

import json
import logging
import os
import time

from .workcopy import _slug_parts, _write, host_part, platforms_dir, safe_part

FORMAT = 1
FOLDER = '.glossaries'
RECORD = '.glossaries.json'
REFRESH_AGE = 60
"""Seconds before the glossaries kept are checked with the server again:
each work copy opened asks, but several opened together ask once."""


def glossary_folder(platform_id, server, project, language):
    """The folder of a project's glossaries in one language. FOLDER
    can't be a platform's id, so it never meets a work copy."""
    parts = [platforms_dir(), FOLDER, safe_part(platform_id), host_part(server)]
    parts += _slug_parts(project)
    parts.append(safe_part(language))
    return os.path.join(*parts)


class Glossaries:
    """The glossaries kept of one project of a server, in one language."""

    clock = staticmethod(time.time)

    def __init__(self, platform_id, server, project, language):
        self.platform_id = platform_id
        self.server = server
        self.project = project
        self.language = language
        self.folder = glossary_folder(platform_id, server, project, language)
        self.record = self._load()

    def __repr__(self):
        return '<Glossaries %s %s %s %s>' % (
            self.platform_id,
            self.server,
            self.project,
            self.language,
        )

    def _load(self):
        empty = {'format': FORMAT, 'checked_at': None, 'glossaries': {}}
        try:
            with open(os.path.join(self.folder, RECORD), encoding='utf-8') as f:
                record = json.load(f)
        except FileNotFoundError:
            return empty
        except (OSError, ValueError) as e:
            logging.warning("The glossaries in %r can't be read: %s", self.folder, e)
            return empty
        if not isinstance(record, dict) or not isinstance(record.get('glossaries'), dict):
            return empty
        return record

    def _save(self):
        data = json.dumps(self.record, ensure_ascii=False, indent=1).encode('utf-8')
        _write(os.path.join(self.folder, RECORD), data)

    def files(self):
        """The paths of the glossary files kept, in the server's order."""
        paths = []
        for kept in self.record['glossaries'].values():
            path = os.path.join(self.folder, safe_part(kept.get('file') or ''))
            if kept.get('file') and os.path.isfile(path):
                paths.append(path)
        return paths

    def due(self):
        """Whether to ask the server again: never asked, or not lately."""
        checked = self.record.get('checked_at')
        return not isinstance(checked, (int, float)) or self.clock() - checked > REFRESH_AGE

    def known(self):
        """{slug: validator} of the glossaries kept, for the server."""
        return {
            slug: kept.get('validator')
            for slug, kept in self.record['glossaries'].items()
            if kept.get('file') and os.path.isfile(os.path.join(self.folder, kept['file']))
        }

    def keep(self, found):
        """Keeps found (the Glossary()s the server gave): writes those
        with data, keeps those unchanged and drops the rest. Whether the
        files changed."""
        os.makedirs(self.folder, exist_ok=True)
        before = self.record['glossaries']
        after = {}
        changed = False
        for glossary in found:
            old = before.get(glossary.slug)
            name = '%s.%s' % (safe_part(glossary.slug.replace('/', '-')), glossary.extension)
            if glossary.data is None and old is not None:
                after[glossary.slug] = {**old, 'name': glossary.name}
                continue
            if glossary.data is None:
                continue
            _write(os.path.join(self.folder, name), glossary.data)
            if old is not None and old.get('file') not in (None, name):
                self._remove(old['file'])
            after[glossary.slug] = {
                'name': glossary.name,
                'file': name,
                'validator': glossary.validator,
            }
            changed = True
        for slug, old in before.items():
            if slug not in after:
                self._remove(old.get('file'))
                changed = True
        self.record['glossaries'] = after
        self.record['checked_at'] = self.clock()
        self._save()
        return changed

    def _remove(self, name):
        if not name:
            return
        try:
            os.remove(os.path.join(self.folder, safe_part(name)))
        except FileNotFoundError:
            pass
        except OSError as e:
            logging.warning("The old glossary %r can't be removed: %s", name, e)

    def refresh(self, session, on_done, on_error):
        """Asks session's server for the glossaries and keeps them:
        on_done(whether the files changed), or on_error(PlatformError)."""
        session.glossaries(
            self.project,
            self.language,
            self.known(),
            lambda found: on_done(self.keep(found)),
            on_error,
        )


def for_copy(copy):
    """The Glossaries of a work copy's project and language."""
    record = copy.record
    return Glossaries(record['platform'], record['server'], record['project'], record['language'])
