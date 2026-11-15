#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Submit as a pull request from the user's fork, for a Git project on
GitHub the user can't push to (forge.py). Runs in a git thread.

Each language has a branch of its own on the fork (virtaal/<code>). Its
pull request holds one commit: the language's file as the project's
branch has it now with the pull request's changes and the new ones,
committed onto the project's tip. Each Submit while the pull request is
open makes that commit again on the newest tip and pushes it over the
old one, only while the fork's branch is still what Virtaal pushed last
(a lease; MOVED otherwise). Nothing is ever pushed to the project.

A Submit adds a commit on top instead, keeping the reviewer's view,
when someone other than the user reviewed or commented since the last
push ("Address review comments"), or when the user chose to (the
project's separate option, with a summary). Once commits were added, a
Submit without new review updates the last of them in place.

Until the pull request is merged, its changes count as the project's
for Update and Submit (view()): a unit the pull request changed takes
its text there, unless someone changed it on the branch too. Once it
is merged, the next Submit starts again: a new commit on the tip, a new
pull request.

Each language's pull request is kept in pulls.json beside the clones.
"""

import json
import logging
import os
import threading

from virtaal.support import gitrepo
from virtaal.support.gitrepo import Credential

from .forge import MOVED, ForgeError

FRESH = 'fresh'
"""A new branch and pull request."""
AMEND = 'amend'
"""The pull request's commit (its last) made again."""
REVIEW = 'review'
"""A commit added for the review."""
SEPARATE = 'separate'
"""A commit added because the user asked for one."""
REVIEW_MESSAGE = 'Address review comments'
BRANCH = 'virtaal/*'

_lock = threading.Lock()


class Records:
    """{key: record} in pulls.json in folder; one record a language of a
    project's branch."""

    def __init__(self, folder):
        self.path = os.path.join(folder, 'pulls.json')

    def _all(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                found = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            logging.warning("%r can't be read: %s", self.path, error)
            return {}
        return found if isinstance(found, dict) else {}

    def get(self, key):
        with _lock:
            return dict(self._all().get(key) or {})

    def put(self, key, record):
        with _lock:
            found = self._all()
            found[key] = record
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temporary = self.path + '.new'
            with open(temporary, 'w', encoding='utf-8') as f:
                json.dump(found, f, indent=1, sort_keys=True)
            os.replace(temporary, self.path)


class ForkCredentials(gitrepo.Credentials):
    """The user's login and token, for pushing to the fork over https."""

    def __init__(self, login, token):
        self.credential = Credential(login, token)

    def known(self, url):
        return self.credential

    def candidates(self, url):
        return iter(())


def language_name(code):
    """The language's English name, for commit messages."""
    from translate.lang import data

    found = data.languages.get(code) or data.languages.get(code.split('_')[0])
    return found[0] if found else code


def counts(before, after):
    """(translated, updated): the units of after (stores) given a text
    they had none of in before, and those changed otherwise."""
    from . import backend

    old = _by_id(before)
    translated = updated = 0
    for own, unit in _by_id(after).items():
        was = old.get(own)
        if was is not None and _same(was, unit):
            continue
        if was is None or any(backend.strings(was.target)) or not any(backend.strings(unit.target)):
            updated += 1
        else:
            translated += 1
    return translated, updated


def summary(translated, updated):
    """ "3 strings translated, 2 updated", in English."""
    parts = []
    if translated:
        parts.append('%d %s translated' % (translated, 'string' if translated == 1 else 'strings'))
    if updated:
        noun = '' if parts else (' string' if updated == 1 else ' strings')
        parts.append('%d%s updated' % (updated, noun))
    return ', '.join(parts) or 'No strings changed'


def overlay(tip_data, base_data, head_data, path):
    """The file as the project's tip has it with a pull request's units
    (those its head changed from its base), unless the tip changed them
    too. Bytes."""
    from . import backend

    if tip_data == base_data:
        return head_data
    store = backend.parse_store(path, tip_data)
    base = _by_id(backend.parse_store(path, base_data))
    head = _by_id(backend.parse_store(path, head_data))
    changed = False
    for own, unit in _by_id(store).items():
        theirs, mine = base.get(own), head.get(own)
        if theirs is None or mine is None or _same(theirs, mine) or not _same(unit, theirs):
            continue
        backend._set_target(unit, backend.strings(mine.target))
        if unit.STATE and mine.STATE:
            unit.set_state_n(mine.get_state_n())
        changed = True
    return bytes(store) if changed else tip_data


def _by_id(store):
    from . import backend

    units = backend.translatable(store)
    return dict(zip(backend.unit_ids(units), units, strict=True))


def _same(one, other):
    from . import backend

    return backend.strings(one.target) == backend.strings(other.target) and backend.state_of(
        one
    ) == backend.state_of(other)


class Fork:
    """A project's pull requests from the user's fork, one a language,
    through github (a forge.GitHub) and session (its GitSession)."""

    def __init__(self, session, github, records, pattern=BRANCH):
        self.session = session
        self.github = github
        self.records = records
        self.pattern = pattern or BRANCH
        self.separate = False
        """The user's choice for the next Submit: a commit of its own."""
        self.summary = ''
        """That commit's subject, if the user gave one."""

    @property
    def repo(self):
        return self.session.repo

    def key(self, language):
        return '%s\n%s\n%s' % (self.repo.url, self.repo.branch, language)

    def branch(self, language):
        return self.pattern.replace('*', language)

    # The pull request #

    def refresh(self, record):
        """record with its pull request's state as GitHub has it now."""
        number = record.get('number')
        if not number:
            return record
        found = self.github.pull(number)
        if found is None:
            return record
        record['state'] = 'merged' if found.get('merged') else found.get('state', 'open')
        record['url'] = found.get('html_url') or record.get('url')
        return record

    def state(self, language):
        """(state, number, URL) of the language's pull request, as GitHub
        has it now, or None."""
        record = self.records.get(self.key(language))
        if not record.get('number'):
            return None
        record = self.refresh(record)
        self.records.put(self.key(language), record)
        return record['state'], record['number'], record.get('url') or ''

    def mode(self, record):
        """FRESH, AMEND, REVIEW or SEPARATE, for record (refreshed)."""
        if not record.get('head') or record.get('state') == 'merged':
            return FRESH
        if record.get('state') == 'open':
            activity = self.github.review_activity(record['number'], record.get('author'))
            if activity - set(record.get('reviewed') or ()):
                return REVIEW
            if self.separate:
                return SEPARATE
        return AMEND

    def plan(self, language):
        """What the next Submit of language does: a mode."""
        return self.mode(self.refresh(self.records.get(self.key(language))))

    # Reading #

    def view(self, tip, path, data, language):
        """data, path's file at the project's tip, with the open (or
        closed, not merged) pull request's units."""
        record = self.records.get(self.key(language))
        if not record.get('head') or record.get('state') == 'merged':
            return data
        try:
            base = self.repo.read(record['base'], path)
            head = self.repo.read(record['head'], path)
        except (KeyError, gitrepo.GitError) as error:
            logging.warning('The pull request of %s is not in the clone: %s', path, error)
            return data
        if base is None or head is None:
            return data
        return overlay(data, base, head, path)

    # Submit #

    def submit(self, path, units, language):
        """units into path, pushed to the fork's branch, with a pull
        request open for it. The state each stored."""
        key = self.key(language)
        record = self.refresh(self.records.get(key))
        mode = self.mode(record)
        allowed = {record.get('head')}
        if mode == FRESH:
            # A merged pull request's branch may still be there, or not.
            allowed = {None, record.get('head')}
            record = {'fork': record['fork']} if record.get('fork') else {}
        tip = self.session._tip(fresh=True)
        parent, data, check = self._start(mode, record, tip, path, language)
        store = self.session._parse(path, data)
        stored = self.session._apply(path, store, units, check)
        if bytes(store) == data:
            self.session._note_sent(path, units, stored)
            return stored
        from . import backend

        backend._update_header(store)
        title = self._title(mode, record, language)
        message = self._message(title, parent, path, store)
        commit = self.repo.commit_file(parent, path, bytes(store), message, backend._author())
        self._push(record, language, commit, allowed)
        record.update(self._record(mode, record, tip, commit), title=title)
        self._pull_request(record, language, path, tip, store)
        self.records.put(key, record)
        self.session._note_sent(path, units, stored)
        return stored

    def _start(self, mode, record, tip, path, language):
        """(parent, the file's bytes to put the units into, whether to
        check them against the tip) for mode."""
        head = record.get('head')
        if mode in (REVIEW, SEPARATE):
            return head, self.repo.read(head, path), False
        if mode == AMEND and record.get('commits', 1) > 1:
            parent = self.repo.parents(head)[0]
            return parent, self.repo.read(head, path), False
        data = self.repo.read(tip, path)
        if data is None:
            from .backend import Changed

            raise Changed(path)
        return tip, self.view(tip, path, data, language), True

    def _title(self, mode, record, language):
        """The commit's subject: the last commit's when it is made again
        on top of others."""
        if mode == REVIEW:
            return self.summary or REVIEW_MESSAGE
        if mode == SEPARATE and self.summary:
            return self.summary
        if mode == AMEND and record.get('commits', 1) > 1 and record.get('title'):
            return record['title']
        return 'Update %s (%s) translation' % (language_name(language), language)

    def _message(self, title, parent, path, store):
        before = self.session._parse(path, self.repo.read(parent, path) or b'')
        return '%s\n\n%s in %s.\n' % (title, summary(*counts(before, store)), path)

    def _push(self, record, language, commit, allowed):
        fork = record.get('fork') or self._fork()
        record['fork'] = fork
        try:
            self.repo.push_to(
                fork['push_url'],
                self.branch(language),
                commit,
                allowed,
                ForkCredentials(self.github.login(), self.github._token.password),
            )
        except gitrepo.GitError as error:
            if error.kind == gitrepo.REJECTED:
                raise ForgeError(MOVED, detail=self.branch(language)) from error
            raise

    def _fork(self):
        """The fork's name and the URL to push to."""
        found = self.github.fork()
        ssh = not self.repo.url.lower().startswith(('http://', 'https://'))
        url = found.get('ssh_url') if ssh else found.get('clone_url')
        return {'full_name': found['full_name'], 'push_url': url or found.get('clone_url')}

    def _record(self, mode, record, tip, commit):
        if mode == FRESH:
            return {'base': tip, 'head': commit, 'commits': 1, 'reviewed': []}
        if mode in (REVIEW, SEPARATE):
            return {'head': commit, 'commits': record.get('commits', 1) + 1}
        if record.get('commits', 1) > 1:
            return {'head': commit}
        return {'base': tip, 'head': commit, 'commits': 1}

    def _pull_request(self, record, language, path, tip, store):
        """record's pull request: the open one, else a new one."""
        login = self.github.login()
        if record.get('number') and record.get('state') == 'open':
            found = None
        else:
            head = '%s:%s' % (login, self.branch(language))
            before = self.session._parse(path, self.repo.read(tip, path) or b'')
            title = 'Update %s (%s) translation' % (language_name(language), language)
            body = '%s.\n\nSubmitted from Virtaal.\n' % summary(*counts(before, store))
            found = self.github.create_pull(head, self.repo.branch, title, body)
            record.update(
                number=found['number'],
                url=found.get('html_url', ''),
                state='open',
                author=login,
            )
        if record.get('number'):
            record['reviewed'] = sorted(self.github.review_activity(record['number'], login))
        return found
