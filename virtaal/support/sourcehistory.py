#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The source text a monolingual file's translations were made for,
when there's no baseline to say: from the local TM, which recorded each
saved translation with its source and key, else from git, as the
template was in the commit that last changed each key's translation.
No GTK here."""

import logging
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import time

_COMMIT_ID = re.compile(rb'[0-9a-f]{40}|[0-9a-f]{64}')

GIT_SECONDS = 3
"""How long git's history may take, all told."""


def local_tm_path():
    """The local TM's database, as its settings name it, if it exists."""
    from virtaal.common import pan_app

    config = pan_app.load_config(os.path.join(pan_app.get_config_dir(), 'tm.ini'), 'localtm')
    path = os.path.join(pan_app.get_config_dir(), os.path.expanduser(config.get('tmdb', 'tm.db')))
    return path if os.path.isfile(path) else None


def _same_file(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _stores(cursor, target):
    """The ids of the stores that may be C{target}: its own path first,
    then files of the same name elsewhere (a moved project), the latest
    saved first."""
    rows = cursor.execute(
        "SELECT store_id, path FROM stores WHERE member = '' ORDER BY updated DESC"
    ).fetchall()
    name = os.path.normcase(os.path.basename(target))
    same = [store_id for store_id, path in rows if _same_file(path, target)]
    moved = [
        store_id
        for store_id, path in rows
        if store_id not in same and os.path.normcase(os.path.basename(path)) == name
    ]
    return same + moved


def from_local_tm(db_path, target, targets):
    """{key: source} for those of C{targets} ({key: translation}) the
    local TM at C{db_path} holds with the same translation for
    C{target}, or for a file of its name."""
    if not db_path or not targets:
        return {}
    found = {}
    try:
        # Read-write but never written: a read-only connection would leave
        # its write-ahead log files behind. Never created if missing.
        uri = pathlib.Path(os.path.abspath(db_path)).as_uri() + '?mode=rw'
        connection = sqlite3.connect(uri, uri=True, timeout=1)
    except sqlite3.Error:
        logging.debug("Can't read the local TM %s", db_path, exc_info=True)
        return {}
    try:
        cursor = connection.cursor()
        for store_id in _stores(cursor, target):
            rows = cursor.execute(
                "SELECT o.unit_key, t.text, s.text FROM origins o "
                "JOIN targets t ON t.tid = o.tid JOIN sources s ON s.sid = t.sid "
                "WHERE o.store_id = ?",
                (store_id,),
            )
            for key, translation, source in rows:
                if key not in found and targets.get(key) == translation:
                    found[key] = source
            if len(found) == len(targets):
                break
    except sqlite3.Error:
        # An older file without origins, or a locked one.
        logging.debug("Can't read the local TM %s", db_path, exc_info=True)
    finally:
        connection.close()
    return found


def _git():
    """The git command, if there is one that won't ask to be installed."""
    from virtaal.common.platform import platform

    git = shutil.which('git')
    if git is None:
        return None
    if platform.is_mac and os.path.realpath(git) == '/usr/bin/git':
        # Without the command line tools, Apple's stub offers to install them.
        try:
            tools = subprocess.run(
                ['xcode-select', '-p'],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=GIT_SECONDS,
                check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        if not os.path.isdir(os.fsdecode(tools.strip())):
            return None
    return git


def _git_run(git, args, deadline, data=None):
    """C{git args}'s output, or None if it failed or ran out of time."""
    from virtaal.common.platform import platform

    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    options = {}
    if platform.is_windows:
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        return None
    try:
        result = subprocess.run(
            [git, *args],
            input=data if data is not None else b'',
            capture_output=True,
            env=env,
            timeout=timeout,
            **options,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _repository(git, target, template, deadline):
    """(top folder, the target's and template's paths in it), or None if
    they aren't both in one repository."""
    top = _git_run(git, ['-C', os.path.dirname(target), 'rev-parse', '--show-toplevel'], deadline)
    top = top and os.path.realpath(os.fsdecode(top.strip()))
    if not top:
        return None
    paths = []
    for path in (target, template):
        try:
            relative = os.path.relpath(os.path.realpath(path), top)
        except ValueError:
            # On another drive.
            return None
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            return None
        paths.append(relative.replace(os.sep, '/'))
    return top, paths[0], paths[1]


def _blame(output):
    """{line number: commit} from C{git blame --porcelain}; uncommitted
    lines have no commit."""
    lines = {}
    for line in output.split(b'\n'):
        fields = line.split()
        if len(fields) >= 3 and _COMMIT_ID.fullmatch(fields[0]) and fields[1].isdigit():
            commit = fields[0].decode('ascii')
            lines[int(fields[2])] = None if commit.strip('0') == '' else commit
    return lines


def _translated_in(git, top, path, data, converter, keys, deadline):
    """{key: the commit that last changed each of C{keys} in the file at
    C{path}, which holds C{data}}, of those committed as they are."""
    output = _git_run(git, ['-C', top, 'blame', '--porcelain', '--', path], deadline)
    if output is None:
        return {}
    lines = _blame(output)
    order = None
    found = {}
    for key, (first, last) in converter.spans(data).items():
        commits = {lines.get(number) for number in range(first, last + 1)}
        if key not in keys or not commits or None in commits:
            continue
        if len(commits) == 1:
            found[key] = commits.pop()
            continue
        if order is None:
            # A message over several lines: the newest of their commits.
            log = _git_run(git, ['-C', top, 'log', '--format=%H', '--', path], deadline) or b''
            order = {commit: n for n, commit in enumerate(log.decode('ascii').split())}
        found[key] = min(commits, key=lambda commit: order.get(commit, len(order)))
    return found


def _blobs(git, top, names, deadline):
    """{name: (blob id, bytes)} of the objects C{names} ("commit:path"),
    of those there are."""
    request = ''.join(name + '\n' for name in names).encode('utf-8')
    output = _git_run(git, ['-C', top, 'cat-file', '--batch'], deadline, request)
    if output is None:
        return {}
    found = {}
    position = 0
    for name in names:
        end = output.index(b'\n', position)
        header = output[position:end].split()
        position = end + 1
        if len(header) == 3 and header[1] == b'blob':
            size = int(header[2])
            found[name] = (header[0], output[position : position + size])
            position += size + 1
    return found


def from_git(target, template, converter, targets, seconds=GIT_SECONDS):
    """{key: source} for those of C{targets} ({key: translation}) that
    git has committed as they are: the template's text in the commit
    that last changed each. Nothing if git isn't there or takes over
    C{seconds}."""
    deadline = time.monotonic() + seconds
    git = targets and _git()
    repository = git and _repository(git, target, template, deadline)
    if not repository:
        return {}
    top, target_path, template_path = repository
    try:
        with open(target, 'rb') as f:
            data = f.read()
        commits = _translated_in(git, top, target_path, data, converter, targets, deadline)
        names = {key: '%s:%s' % (commit, template_path) for key, commit in commits.items()}
        blobs = _blobs(git, top, sorted(set(names.values())), deadline)
        sources = {}
        found = {}
        for key, name in names.items():
            if name not in blobs:
                continue
            blob, text = blobs[name]
            if blob not in sources:
                if time.monotonic() > deadline:
                    break
                sources[blob] = converter.values_of(text)
            if key in sources[blob]:
                found[key] = sources[blob][key]
    except (OSError, ValueError):
        logging.debug("Couldn't read the git history of %s", target, exc_info=True)
        return {}
    return found


def recall(pair, targets, seconds=GIT_SECONDS):
    """{key: source} of the translations in C{targets} ({key: text})
    that C{pair}'s history knows: the local TM's, else git's."""
    found = from_local_tm(local_tm_path(), pair.target, targets)
    rest = {key: text for key, text in targets.items() if key not in found}
    if rest and seconds > 0:
        found.update(from_git(pair.target, pair.template, pair.converter, rest, seconds))
    return found


def budgeted(seconds):
    """A L{recall} for several files whose git history may take
    C{seconds} all told."""
    deadline = time.monotonic() + seconds

    def recall_within(pair, targets):
        remaining = min(GIT_SECONDS, deadline - time.monotonic())
        return recall(pair, targets, remaining)

    return recall_within
