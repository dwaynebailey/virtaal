#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Weblate servers and their tokens, in weblate.ini in Virtaal's
config folder (#3337).

The file has wlc's shape: [keys] maps each server's API URL to its
token, and [weblate]'s url is the server last used. A token can write
shared translations, so the file is always written owner-only (0600).
"""

import configparser
import logging
import os

from virtaal.common import pan_app

from .client import api_url

CONFIG_NAME = 'weblate.ini'
FILE_MODE = 0o600

_KEYS = 'keys'
_DEFAULT = 'weblate'


def _parser():
    # URLs hold ':', and their case is kept as typed.
    parser = configparser.RawConfigParser(delimiters=('=',), interpolation=None)
    parser.optionxform = str
    return parser


def _read(parser, path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            parser.read_file(f)
    except FileNotFoundError:
        return False
    except (OSError, configparser.Error, UnicodeDecodeError) as e:
        logging.warning("%r can't be read: %s", path, e)
        return False
    return True


class ServerList:
    """The servers in file order, each an (API URL, token) pair, and the
    one last used."""

    def __init__(self, path=None):
        self.path = path or os.path.join(pan_app.get_config_dir(), CONFIG_NAME)
        self.servers = []
        self.last_url = ''
        self.load()

    def load(self):
        parser = _parser()
        self.servers = []
        self.last_url = ''
        if not _read(parser, self.path):
            return
        if parser.has_section(_KEYS):
            for url, token in parser.items(_KEYS):
                self.put(url, token)
        if parser.has_section(_DEFAULT):
            self.last_url = api_url(parser.get(_DEFAULT, 'url', fallback='')) or ''

    def save(self):
        parser = _parser()
        parser.add_section(_DEFAULT)
        if self.last_url:
            parser.set(_DEFAULT, 'url', self.last_url)
        parser.add_section(_KEYS)
        for url, token in self.servers:
            parser.set(_KEYS, url, token)
        pan_app._write_ini(parser, self.path, mode=FILE_MODE)

    def urls(self):
        return [url for url, _token in self.servers]

    def token(self, url):
        return dict(self.servers).get(url, '')

    def put(self, url, token, replace=None):
        """Adds url with token, or changes the server at replace, in its
        place. Whether url and token are usable."""
        url = api_url(url)
        token = token.strip()
        if not url or not token:
            return False
        old = [replace, url] if replace else [url]
        at = next((i for i, (u, _t) in enumerate(self.servers) if u in old), len(self.servers))
        self.servers = [s for s in self.servers if s[0] not in old]
        self.servers.insert(at, (url, token))
        if replace and self.last_url == replace:
            self.last_url = url
        return True

    def remove(self, url):
        self.servers = [s for s in self.servers if s[0] != url]
        if self.last_url == url:
            self.last_url = ''

    def import_wlc(self, path=None):
        """Adds wlc's servers and tokens, replacing the token of one
        already listed. The number added or changed."""
        found = read_wlc(path)
        changed = 0
        for url, token in found:
            if self.token(url) != token and self.put(url, token):
                changed += 1
        return changed


def wlc_config_path():
    """wlc's own configuration file, found as wlc finds it, or None."""
    for name in ('APPDATA', 'LOCALAPPDATA'):
        folder = os.environ.get(name)
        if folder and os.path.isfile(os.path.join(folder, 'weblate.ini')):
            return os.path.join(folder, 'weblate.ini')
    home = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    folders = [home] + [d for d in os.environ.get('XDG_CONFIG_DIRS', '/etc/xdg').split(':') if d]
    for folder in folders:
        for name in ('weblate', 'weblate.ini'):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    return None


def read_wlc(path=None):
    """The (API URL, token) pairs in wlc's [keys], read only."""
    path = path or wlc_config_path()
    if not path:
        return []
    parser = _parser()
    if not _read(parser, path) or not parser.has_section(_KEYS):
        return []
    found = []
    for url, token in parser.items(_KEYS):
        url = api_url(url)
        if url and token.strip():
            found.append((url, token.strip()))
    return found
