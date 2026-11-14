#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A local bare repository standing in for a team's Git remote, and a
maintainer's clone of it to change files and push, for tests and hand
checks of Git projects. Never a real host."""

import os
import shutil
import subprocess

import pytest

needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='needs git on PATH')

_ENV = {
    'GIT_AUTHOR_NAME': 'Maintainer',
    'GIT_AUTHOR_EMAIL': 'maintainer@example.org',
    'GIT_COMMITTER_NAME': 'Maintainer',
    'GIT_COMMITTER_EMAIL': 'maintainer@example.org',
    'GIT_CONFIG_NOSYSTEM': '1',
}


def git(cwd, *args):
    env = dict(os.environ, **_ENV)
    done = subprocess.run(['git', *args], cwd=cwd, env=env, capture_output=True, check=False)
    if done.returncode:
        raise AssertionError('git %s: %s' % (' '.join(args), done.stderr.decode()))
    return done.stdout.decode()


class Remote:
    """A bare repository at folder/remote.git, with branch, and a clone
    at folder/maintainer for changes made "by someone else"."""

    def __init__(self, folder, files, branch='main'):
        self.folder = str(folder)
        self.branch = branch
        self.url = os.path.join(self.folder, 'remote.git')
        self.clone = os.path.join(self.folder, 'maintainer')
        os.makedirs(self.folder, exist_ok=True)
        git(self.folder, 'init', '--bare', '--quiet', '-b', branch, self.url)
        git(self.folder, 'init', '--quiet', '-b', branch, self.clone)
        git(self.clone, 'remote', 'add', 'origin', self.url)
        self.commit(files, 'Start')

    def write(self, path, data):
        full = os.path.join(self.clone, *path.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as f:
            f.write(data.encode('utf-8') if isinstance(data, str) else data)

    def commit(self, files, message='Change'):
        """Writes files ({path: text}) and pushes them as one commit."""
        for path, data in files.items():
            self.write(path, data)
        git(self.clone, 'add', '-A')
        git(self.clone, 'commit', '--quiet', '-m', message)
        git(self.clone, 'push', '--quiet', 'origin', self.branch)

    def pull(self):
        git(self.clone, 'pull', '--quiet', '--ff-only', 'origin', self.branch)

    def read(self, path):
        """path as the remote's branch has it now."""
        return git(self.folder, '--git-dir', self.url, 'show', '%s:%s' % (self.branch, path))

    def log(self, *args):
        """`git log` of the remote's branch."""
        return git(self.folder, '--git-dir', self.url, 'log', *args, self.branch)

    def changed_files(self, commit='HEAD'):
        out = git(self.folder, '--git-dir', self.url, 'show', '--name-only', '--format=', commit)
        return out.split()
