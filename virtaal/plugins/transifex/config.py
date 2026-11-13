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

read_tx() reads, and never writes, the servers and tokens of tx, the
Transifex CLI (github.com/transifex/cli): ~/.transifexrc (HOME, else
the user's home folder), one section a host:

  [https://app.transifex.com]
  rest_hostname = https://rest.api.transifex.com
  token = 1/abc...

An older file (the Python client's, before "tx migrate") has no
rest_hostname, and the token as the password of the user "api". TX_TOKEN
and TX_HOSTNAME, tx's own overrides, come first.
"""

import configparser
import logging
import os

from virtaal.common import pan_app

from .client import API_URL, api_url

CONFIG_NAME = 'transifex.ini'
FILE_MODE = 0o600

_KEYS = 'keys'
_DEFAULT = 'transifex'

TX_CONFIG = '.transifexrc'
TRANSIFEX_HOSTS = ('app.transifex.com', 'www.transifex.com', 'transifex.com')
"""The web addresses of Transifex itself, whose API is API_URL."""


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


# tx #


def tx_config_path():
    """tx's ~/.transifexrc, found as tx finds it, or None."""
    home = os.environ.get('HOME') or os.path.expanduser('~')
    path = os.path.join(home, TX_CONFIG)
    return path if os.path.isfile(path) else None


def _host_api(host, rest):
    """The API URL of a tx host: its rest_hostname, or Transifex's own
    for Transifex's web address. None for another host without one."""
    if rest:
        return api_url(rest)
    name = (host or '').strip().lower()
    name = name.partition('://')[2] or name
    if name.rstrip('/') in TRANSIFEX_HOSTS:
        return API_URL
    return None


def _host_token(section):
    """A tx host's token: token, else the password of the user "api"."""
    token = section.get('token', '').strip()
    if not token and section.get('username', '').strip() == 'api':
        token = section.get('password', '').strip()
    return token


def _environment(parser):
    """(API URL, token) of TX_TOKEN for TX_HOSTNAME (a section's name,
    or a REST address), or None."""
    token = os.environ.get('TX_TOKEN', '').strip()
    if not token:
        return None
    host = os.environ.get('TX_HOSTNAME', '').strip()
    if not host:
        return API_URL, token
    if parser.has_section(host):
        url = _host_api(host, parser.get(host, 'rest_hostname', fallback=''))
    else:
        url = api_url(host)
    return (url, token) if url else None


def read_tx(path=None):
    """The (API URL, token) pairs tx has, read only: TX_TOKEN's first,
    then each host's of ~/.transifexrc, one a URL."""
    path = path or tx_config_path()
    parser = _parser()
    if path:
        _read_into(parser, path)
    found = {}
    environment = _environment(parser)
    if environment:
        found[environment[0]] = environment[1]
    for host in parser.sections():
        section = parser[host]
        url = _host_api(section.get('hostname') or host, section.get('rest_hostname', ''))
        token = _host_token(section)
        if url and token:
            found.setdefault(url, token)
    return list(found.items())


def _read_into(parser, path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            parser.read_file(f)
    except (OSError, configparser.Error, UnicodeDecodeError) as e:
        logging.warning("%r can't be read: %s", path, e)
