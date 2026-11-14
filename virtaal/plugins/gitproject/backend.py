#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A Git repository as a translation platform (virtaal.support.platforms):
a team's translations in a branch, one file a language.

A project is a repository's URL, a branch and where its language files
are ("files", such as po/*.po, * the language's code). Virtaal keeps a
bare clone of the branch (virtaal.support.gitrepo) in its state folder,
never a working tree, so nothing but a language's file is ever written.
Opening a language reads its file from the branch as just fetched into
a work copy, as for any platform; the file's units are the platform's
units, each by its id in the file (PO: its context and source text).

Submit and Update are the platform layer's, unit by unit against the
work copy's baseline (submit.py, update.py), with the branch's file as
"the server": changed_since() reads every unit of the file at the
fetched tip, so others' changes show as conflicts unit by unit, never
as a Git merge. Submit's units go into the file as the tip has it, as
one commit by the translator (git's user.name and user.email, else
Virtaal's translator), pushed and never forced. A push refused because
the branch moved on is fetched and done again on the new tip, at most
ATTEMPTS times; a unit changed on the branch since Submit compared it
stops Submit before anything goes (CHANGED), for Update to bring it in
first.

There is no lock, no review, and no state but what the file holds.
ssh uses the system's ssh and its agent; https asks credentials.py,
which leaves keeping them to the system's password store.
"""

import hashlib
import io
import os
import re
import threading
import time
from urllib import parse

from gi.repository import GLib

from virtaal.support import gitrepo
from virtaal.support.platforms import (
    AUTH,
    GONE,
    NETWORK,
    NOT_PLATFORM,
    Download,
    Item,
    Platform,
    PlatformError,
    RemoteUnit,
    Session,
)
from virtaal.support.platforms.workcopy import strings, text_hash

from .config import ProjectFile
from .credentials import GitCredentials

GIT_MISSING = 'git-missing'
"""A PlatformError's kind: dulwich isn't installed."""
CHANGED = 'git-changed'
"""A PlatformError's kind: the branch changed a unit Submit was to send."""
FAILED = 'git-failed'
"""A PlatformError's kind: git failed otherwise, or the file can't be read."""

LANGUAGE = '*'
"""Where the language's code goes in a project's files."""
ATTEMPTS = 3
"""How many times Submit commits again after a refused push."""
FRESH = 3
"""Seconds a session's fetch stays fresh enough for its next request."""
BATCH = 100000

_ERRORS = {
    gitrepo.MISSING: GIT_MISSING,
    gitrepo.AUTH: AUTH,
    gitrepo.NETWORK: NETWORK,
    gitrepo.NOT_FOUND: NOT_PLATFORM,
    gitrepo.REJECTED: CHANGED,
    gitrepo.FAILED: FAILED,
}

_BAD_BRANCH = re.compile(r'[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{|//|^[-/.]|[/.]$|\.lock$|^@$')
_SCP = re.compile(r'^[^/\s:]+:[^\s]')
_SCHEME = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*://\S+$')


class Changed(Exception):
    """A unit to send changed on the branch since Submit compared it."""


def valid_branch(name):
    return bool(name) and not _BAD_BRANCH.search(name)


def valid_files(pattern):
    """Whether pattern names one file a language: relative, with one *
    in a name, no .. part."""
    parts = pattern.split('/')
    return (
        pattern.count(LANGUAGE) == 1
        and '\\' not in pattern
        and not pattern.startswith('/')
        and all(part not in ('', '.', '..') for part in parts)
    )


def language_paths(paths, pattern):
    """{language code: path} of the paths that pattern names."""
    head, _star, tail = pattern.partition(LANGUAGE)
    found = re.compile(re.escape(head) + r'([^/]+)' + re.escape(tail) + r'\Z')
    languages = {}
    for path in paths:
        match = found.match(path)
        if match:
            languages[match.group(1)] = path
    return languages


def _where(url):
    """The host and path of a repository's URL ("example.org/team/x"),
    for its scheme, scp-like (git@host:path) or a local path."""
    parts = parse.urlsplit(url)
    if parts.scheme and parts.netloc:
        host = parts.netloc.rpartition('@')[2]
        return host + parts.path
    if _SCP.match(url) and not os.path.isabs(url) and not re.match(r'^[A-Za-z]:[\\/]', url):
        host, _colon, path = url.partition(':')
        return host.rpartition('@')[2] + '/' + path
    return 'local/' + (parts.path if parts.scheme else url)


def project_slug(url, branch):
    """The project's slug: its repository's host and path, and the branch."""
    where = re.sub(r'\.git/*$', '', _where(url).replace('\\', '/'))
    return '%s/%s' % (re.sub(r'/+', '/', where).strip('/'), branch)


def project_name(url):
    """The repository's name, as a project's."""
    where = re.sub(r'\.git/*$', '', _where(url).replace('\\', '/')).rstrip('/')
    return where.rpartition('/')[2] or url


def parse_store(path, data):
    """The translate-toolkit store of a file's bytes."""
    from translate.storage import factory

    stream = io.BytesIO(data)
    stream.name = os.path.basename(path)
    return factory.getobject(stream)


def translatable(store):
    return [
        unit
        for unit in store.units
        if not unit.isheader() and not unit.isobsolete() and unit.istranslatable()
    ]


def unit_ids(units):
    """Each unit's id in its file: its own id, numbered after the first
    when several share it."""
    seen = {}
    ids = []
    for unit in units:
        own = unit.getid() or ''
        count = seen.get(own, 0)
        seen[own] = count + 1
        ids.append(own if not count else '%s\0%d' % (own, count))
    return ids


def state_of(unit):
    return unit.get_state_n() if unit.STATE else None


def remote_units(path, store):
    """The RemoteUnits of a file: each id the file's path and the unit's
    id in it."""
    units = translatable(store)
    return [
        RemoteUnit(
            '%s\n%s' % (path, own),
            position,
            unit.getcontext() or '',
            strings(unit.source),
            strings(unit.target),
            state_of(unit),
            state_of(unit),
            None,
            False,
        )
        for position, (own, unit) in enumerate(zip(unit_ids(units), units, strict=True))
    ]


def _signature(target, code):
    return text_hash(target), code


def _translator():
    from virtaal.common import pan_app

    translator = pan_app.settings.translator
    return (translator.get('name') or '').strip(), (translator.get('email') or '').strip()


def _author():
    """A commit's (name, email): git's user.name and user.email if set,
    else Virtaal's translator's."""
    name, email = gitrepo.identity()
    if name:
        return name, email or _translator()[1]
    name, email = _translator()
    return name or 'Virtaal', email


def _update_header(store):
    from translate.storage.poheader import poheader, tzstring

    from virtaal.common import pan_app

    if not isinstance(store, poheader) or not store.header():
        return
    name, email = _translator()
    updates = {
        'PO_Revision_Date': time.strftime('%Y-%m-%d %H:%M') + tzstring(),
        'X_Generator': pan_app.x_generator,
    }
    if name:
        updates['Last_Translator'] = '%s <%s>' % (name, email) if email else name
    store.updateheader(**updates)


def _set_target(unit, forms):
    from translate.misc.multistring import multistring

    forms = list(forms) or ['']
    if strings(unit.target) != forms:
        unit.target = multistring(forms) if len(forms) > 1 else forms[0]


def _platform_error(error):
    if isinstance(error, gitrepo.GitError):
        return PlatformError(_ERRORS.get(error.kind, FAILED), detail=error.detail)
    if isinstance(error, Changed):
        return PlatformError(CHANGED)
    return PlatformError(FAILED, detail=str(error))


class GitSession(Session):
    """One project's branch, read and written through its clone. Each
    request runs git in a thread of its own, one at a time a clone, and
    answers on the GLib main loop."""

    def __init__(self, platform, url, branch, files):
        self.platform = platform
        self.repo = platform.repository(url, branch)
        self.files = files
        self._generation = 0
        self._fetched_at = None
        self._seen = {}
        """{path: {unit id: (target hash, state)}} as last read: what
        Submit compared its units with."""

    # Running git #

    def _job(self, work, on_done, on_error):
        generation = self._generation

        def deliver(callback, value):
            if generation == self._generation:
                callback(value)
            return False

        def run():
            try:
                with self.repo.lock:
                    result = work()
            except Exception as error:  # Git's, or a file translate-toolkit can't read.
                GLib.idle_add(deliver, on_error, _platform_error(error))
            else:
                GLib.idle_add(deliver, on_done, result)

        threading.Thread(target=run, name='git', daemon=True).start()

    def _later(self, callback, value):
        generation = self._generation
        GLib.idle_add(lambda: generation == self._generation and callback(value) and False)

    def cancel(self):
        self._generation += 1

    def _tip(self, fresh=False):
        """The branch's tip: fetched again, unless this session fetched it
        moments ago (Update's count, then its changes)."""
        recent = self._fetched_at is not None and time.monotonic() - self._fetched_at < FRESH
        tip = None if fresh or not recent else self.repo.tip()
        if tip is None:
            tip = self.repo.fetch()
            self._fetched_at = time.monotonic()
        return tip

    def path_of(self, language):
        return self.files.replace(LANGUAGE, language)

    def _units(self, language):
        """The file's (path, bytes, RemoteUnits) at the tip, noted as seen."""
        path = self.path_of(language)
        data = self.repo.read(self._tip(), path)
        if data is None:
            raise FileNotFoundError(path)
        units = remote_units(path, parse_store(path, data))
        self._seen[path] = {u.id: _signature(u.target, u.code) for u in units}
        return path, data, units

    # Browsing #

    def test_connection(self, on_done, on_error):
        def work():
            if not self.repo.has_branch():
                raise gitrepo.GitError(gitrepo.NOT_FOUND)
            return 1

        self._job(work, on_done, on_error)

    def projects(self, query, cursor, on_page, on_error):
        name = project_name(self.repo.url)
        item = Item(name, project_slug(self.repo.url, self.repo.branch))
        GLib.idle_add(lambda: on_page([item], None) and False)

    def components(self, project, on_done, on_error):
        self._later(on_done, [])

    def languages(self, project, component, on_done, on_error):
        from virtaal.models.langmodel import LanguageModel

        def work():
            found = language_paths(self.repo.files(self._tip(fresh=True)), self.files)
            items = []
            for code in sorted(found):
                name = LanguageModel(code).name
                items.append(Item(name if name and name != code else code, code))
            return items

        self._job(work, on_done, on_error)

    def download(self, project, component, language, on_done, on_error):
        def work():
            try:
                path, data, units = self._units(language)
            except FileNotFoundError as error:
                raise gitrepo.GitError(gitrepo.NOT_FOUND, str(error)) from error
            names = {'project': project_name(self.repo.url), 'component': None}
            options = {'review': None, 'source_review': False}
            return Download(path, data, None, units, names, options)

        def failed(error):
            on_error(
                PlatformError(GONE, detail=error.detail) if error.kind == NOT_PLATFORM else error
            )

        self._job(work, on_done, failed)

    # Submit and Update #

    def locked(self, project, component, on_done, on_error):
        self._later(on_done, False)

    def unit_count(self, project, component, language, on_done, on_error):
        self._job(lambda: len(self._units(language)[2]), on_done, on_error)

    def changed_since(self, project, component, language, since, on_done, on_error):
        self._job(lambda: self._units(language)[2], on_done, on_error)

    def submit_unit(self, unit_id, target, code, on_done, on_error):
        stored = []
        self.submit_units(
            [(unit_id, target, code)],
            lambda _index, outcome: stored.append(outcome),
            lambda: on_done(stored[0]),
            on_error,
        )

    def submit_units(self, units, on_each, on_done, on_error):
        """One commit of every unit (all of one file), pushed."""

        def done(stored):
            for index, code in enumerate(stored):
                on_each(index, code)
            on_done()

        self._job(lambda: self._submit(units), done, on_error)

    def _submit(self, units):
        path = units[0][0].partition('\n')[0]
        for _attempt in range(ATTEMPTS):
            tip = self._tip(fresh=True)
            data = self.repo.read(tip, path)
            if data is None:
                raise Changed(path)
            store = parse_store(path, data)
            stored = self._apply(path, store, units)
            if bytes(store) != data:
                _update_header(store)
                commit = self.repo.commit_file(
                    tip, path, bytes(store), self._message(path, units), _author()
                )
                try:
                    self.repo.push(commit)
                except gitrepo.GitError as error:
                    if error.kind == gitrepo.REJECTED:
                        continue
                    raise
            seen = self._seen.setdefault(path, {})
            for (unit_id, target, _code), code in zip(units, stored, strict=True):
                seen[unit_id] = _signature(target, code)
            return stored
        raise gitrepo.GitError(gitrepo.REJECTED)

    def _apply(self, path, store, units):
        """units into store, the file at the tip. The state each stored."""
        found = translatable(store)
        by_id = {
            '%s\n%s' % (path, own): unit for own, unit in zip(unit_ids(found), found, strict=True)
        }
        seen = self._seen.get(path, {})
        stored = []
        for unit_id, target, code in units:
            unit = by_id.get(unit_id)
            if unit is None:
                raise Changed(unit_id)
            now = _signature(strings(unit.target), state_of(unit))
            before = seen.get(unit_id)
            if before is not None and now not in (before, _signature(target, code)):
                raise Changed(unit_id)
            _set_target(unit, target)
            if unit.STATE and code is not None:
                unit.set_state_n(code)
            stored.append(state_of(unit))
        return stored

    def _message(self, path, units):
        language = self._language(path)
        count = len(units)
        return 'Update %s translation\n\n%d %s changed in Virtaal: %s\n' % (
            language,
            count,
            'unit' if count == 1 else 'units',
            path,
        )

    def _language(self, path):
        for code, found in language_paths([path], self.files).items():
            if found == path:
                return code
        return path


class GitPlatform(Platform):
    id = 'git'
    name = 'Git'
    has_components = False
    supports_search = False
    can_submit = True
    batch_size = BATCH
    """Every change of a file goes as one commit."""
    default_credentials = {'branch': 'main', 'files': 'po/*.po'}

    def __init__(self, path=None, clones=None):
        self.projects = ProjectFile(path)
        self.clones = clones
        """The folder of the bare clones; the state folder's git/ if None."""
        self.credentials = GitCredentials()
        """https credentials, shared by every repository."""
        self._repositories = {}

    def repository(self, url, branch):
        """The Repository of a project's branch, one for every session."""
        key = (url, branch)
        if key not in self._repositories:
            from virtaal.common import pan_app

            folder = self.clones or os.path.join(pan_app.get_state_dir(), 'git')
            digest = hashlib.sha256(('%s\n%s' % key).encode('utf-8')).hexdigest()[:16]
            name = '%s-%s.git' % (re.sub(r'[^A-Za-z0-9_.-]', '_', project_name(url))[:40], digest)
            self._repositories[key] = gitrepo.Repository(
                url, branch, os.path.join(folder, name), self.credentials
            )
        return self._repositories[key]

    def credential_fields(self):
        from virtaal.views.widgets.settingsdialog import Field

        return (
            # l10n: A Git project's branch, such as main
            Field('branch', _('_Branch:'), required=True),
            # l10n: Where a Git project keeps each language's file, such as po/*.po; * stands for the language's code (af, pt_BR)
            Field('files', _('_Language files (* for the code):'), required=True),
        )

    def normalise_url(self, text):
        text = (text or '').strip()
        if not text or text.startswith('-') or re.search(r'\s', text):
            return None
        if _SCHEME.match(text) or _SCP.match(text) or os.path.isabs(text) or text[0] in '/\\':
            return text
        return None

    def url_problem(self, text):
        if (text or '').strip().lower().startswith('http://'):
            # l10n: Shown for an address starting http:// instead of https://
            return _('Use https: the token would be sent unencrypted.')
        return None

    def credentials_problem(self, values):
        branch = values.get('branch', '')
        if branch and not valid_branch(branch):
            # l10n: A Git project's branch name has characters Git doesn't allow, such as spaces
            return _('Git doesn’t allow this branch name.')
        files = values.get('files', '')
        if files and not valid_files(files):
            # l10n: A Git project's language files must have one * where the language's code goes
            return _('Use one * for the language’s code, such as po/*.po.')
        return None

    def not_platform_message(self):
        # l10n: No Git repository, or no such branch, at the address given for a Git project
        return _('No Git repository with this branch answered at this address.')

    def error_message(self, error):
        if error.kind == GIT_MISSING:
            # l10n: dulwich is the name of a Python library, not translated
            return _('Git projects need the Python library dulwich, which isn’t installed.')
        if error.kind == AUTH:
            # l10n: The repository refused the user's SSH key, or the username and password or token given for it, or none was given
            message = _(
                'Virtaal couldn’t sign in to the repository. For an ssh address, add your SSH '
                'key to your SSH agent; for https, give a username and a password or token.'
            )
            return message + ('\n' + error.detail if error.detail else '')
        if error.kind == CHANGED:
            # l10n: Someone pushed changes to the same strings while this user's were being submitted to a Git project
            return _(
                'The repository changed while submitting. Use Update from Server, then Submit again.'
            )
        if error.kind == FAILED:
            # l10n: %(reason)s is Git's own message, in English
            return _('Git failed: %(reason)s') % {'reason': error.detail}
        return None

    def load_servers(self):
        self.projects.load()
        return [(url, dict(settings)) for url, settings in self.projects.projects], (
            self.projects.last_url
        )

    def save_servers(self, servers, last_url):
        self.projects.projects = [
            (url, {'branch': c.get('branch', ''), 'files': c.get('files', '')})
            for url, c in servers
        ]
        self.projects.last_url = last_url
        self.projects.save()

    def session(self, url, credentials):
        return GitSession(
            self, url, credentials.get('branch') or 'main', credentials.get('files') or ''
        )
