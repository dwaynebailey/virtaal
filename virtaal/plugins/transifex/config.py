#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Transifex servers and their tokens, in transifex.ini in Virtaal's
config folder. The server list (virtaal.support.platforms) reads and
writes it through TransifexPlatform.

[keys] maps each API URL (Transifex's own, or a fake one) to its token,
and [transifex]'s url is the one last used. A token can change shared
translations, so the file is always written owner-only (0600).
"""

import configparser
import logging
import os

from virtaal.common import pan_app

from .client import api_url

CONFIG_NAME = 'transifex.ini'
FILE_MODE = 0o600

_KEYS = 'keys'
_DEFAULT = 'transifex'


def _parser():
    # URLs hold ':', and their case is kept as typed.
    parser = configparser.RawConfigParser(delimiters=('=',), interpolation=None)
    parser.optionxform = str
    return parser


class KeyFile:
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
        try:
            with open(self.path, encoding='utf-8-sig') as f:
                parser.read_file(f)
        except FileNotFoundError:
            return
        except (OSError, configparser.Error, UnicodeDecodeError) as e:
            logging.warning("%r can't be read: %s", self.path, e)
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

    def put(self, url, token):
        """Adds url with token, or changes the token of url, in its place.
        Whether url and token are usable."""
        url = api_url(url)
        token = token.strip()
        if not url or not token:
            return False
        at = next((i for i, (u, _t) in enumerate(self.servers) if u == url), None)
        if at is None:
            self.servers.append((url, token))
        else:
            self.servers[at] = (url, token)
        return True
