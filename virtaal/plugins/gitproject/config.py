#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Git projects, in gitprojects.ini in Virtaal's config folder. The
server list (virtaal.support.platforms) reads and writes it through
GitPlatform. It holds no secret: git keeps the credentials.

  [gitprojects]
  last = https://example.org/team/program.git

  [project 1]
  url = https://example.org/team/program.git
  branch = main
  files = po/*.po

A project on GitHub (forge.py) may also have: fork (yes, no, or empty:
yes when the user can't push), can_push (the last check), separate
(Submit adds a commit to the open pull request, instead of updating its
one), and pull_branch (the fork's branch, * the language's code;
virtaal/* if not given). [github] maps a GitHub Enterprise host to its
API URL:

  [github]
  git.example.org = https://git.example.org/api/v3
"""

import configparser
import logging
import os

from virtaal.common import pan_app

CONFIG_NAME = 'gitprojects.ini'
_MAIN = 'gitprojects'
_PROJECT = 'project %d'
_GITHUB = 'github'
OPTIONS = ('fork', 'can_push', 'separate', 'pull_branch')
"""A project's settings besides its branch and files, kept as written."""


def _parser():
    parser = configparser.RawConfigParser(delimiters=('=',), interpolation=None)
    parser.optionxform = str
    return parser


class ProjectFile:
    """The projects in file order, each (URL, {'branch', 'files'} and
    any OPTIONS set), the one last used, and the GitHub Enterprise hosts
    ({host: API URL})."""

    def __init__(self, path=None):
        self.path = path or os.path.join(pan_app.get_config_dir(), CONFIG_NAME)
        self.projects = []
        self.last_url = ''
        self.github_hosts = {}
        self.load()

    def settings(self, url):
        """The settings of the project at url, or {}."""
        return next((s for u, s in self.projects if u == url), {})

    def load(self):
        self.projects = []
        self.last_url = ''
        self.github_hosts = {}
        parser = _parser()
        try:
            with open(self.path, encoding='utf-8-sig') as f:
                parser.read_file(f)
        except FileNotFoundError:
            return
        except (OSError, configparser.Error, UnicodeDecodeError) as e:
            logging.warning("%r can't be read: %s", self.path, e)
            return
        for section in parser.sections():
            if not section.startswith('project '):
                continue
            url = parser.get(section, 'url', fallback='').strip()
            branch = parser.get(section, 'branch', fallback='').strip()
            files = parser.get(section, 'files', fallback='').strip()
            if url and branch and files:
                settings = {'branch': branch, 'files': files}
                for option in OPTIONS:
                    value = parser.get(section, option, fallback='').strip()
                    if value:
                        settings[option] = value
                self.projects.append((url, settings))
        self.last_url = parser.get(_MAIN, 'last', fallback='').strip()
        if parser.has_section(_GITHUB):
            self.github_hosts = {
                host: api.strip() for host, api in parser.items(_GITHUB) if api.strip()
            }

    def save(self):
        parser = _parser()
        parser.add_section(_MAIN)
        if self.last_url:
            parser.set(_MAIN, 'last', self.last_url)
        for number, (url, settings) in enumerate(self.projects, 1):
            section = _PROJECT % number
            parser.add_section(section)
            parser.set(section, 'url', url)
            parser.set(section, 'branch', settings.get('branch', ''))
            parser.set(section, 'files', settings.get('files', ''))
            for option in OPTIONS:
                if settings.get(option):
                    parser.set(section, option, settings[option])
        if self.github_hosts:
            parser.add_section(_GITHUB)
            for host, api in self.github_hosts.items():
                parser.set(_GITHUB, host, api)
        pan_app._write_ini(parser, self.path)
