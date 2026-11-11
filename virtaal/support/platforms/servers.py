#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The translation servers of every platform, as one list (#3337).

Each platform keeps its own servers and their credentials, in its own
owner-only file (Weblate: weblate.ini, shaped as wlc's). platforms.ini
in Virtaal's config folder remembers which server was used last, and
which servers are sent finished units as they are saved (trickle.py);
it holds no secret:

  [last]
  platform = weblate
  url = https://hosted.weblate.org/api/

  [send_finished]
  weblate https://hosted.weblate.org/api/ = true
"""

import configparser
import logging
import os

from virtaal.common import pan_app

CONFIG_NAME = 'platforms.ini'
_LAST = 'last'
_SEND_FINISHED = 'send_finished'


def _parser():
    # Keys hold URLs, so ':' is no delimiter.
    parser = configparser.RawConfigParser(delimiters=('=',), interpolation=None)
    parser.optionxform = str
    return parser


def _key(server):
    return '%s %s' % (server.platform.id, server.url)


class Server:
    """One server: its platform, its address and its credentials."""

    def __init__(self, platform, url, credentials, send_finished=False):
        self.platform = platform
        self.url = url
        self.credentials = dict(credentials)
        self.send_finished = bool(send_finished)
        """Units at Translated or above are sent as they are saved."""

    def __repr__(self):
        return '<Server %s %s>' % (self.platform.id, self.url)

    def session(self):
        return self.platform.session(self.url, self.credentials)


class ServerList:
    """The servers of platforms, in platform order, and the one used
    last."""

    def __init__(self, platforms, path=None):
        self.platforms = list(platforms)
        self.path = path or os.path.join(pan_app.get_config_dir(), CONFIG_NAME)
        self.servers = []
        self.last = None
        self.load()

    def load(self):
        self.servers = []
        self.last = None
        self._own_last = {}
        for platform in self.platforms:
            found, last_url = platform.load_servers()
            for url, credentials in found:
                self.servers.append(Server(platform, url, credentials))
            self._own_last[platform] = last_url
        parser = _parser()
        try:
            parser.read(self.path, encoding='utf-8')
        except (OSError, configparser.Error, UnicodeDecodeError) as e:
            logging.warning("%r can't be read: %s", self.path, e)
        for server in self.servers:
            server.send_finished = parser.getboolean(_SEND_FINISHED, _key(server), fallback=False)
        kind = parser.get(_LAST, 'platform', fallback='')
        platform = next((p for p in self.platforms if p.id == kind), None)
        self.last = self.find(platform, parser.get(_LAST, 'url', fallback=''))
        if self.last is None:
            # No platforms.ini yet: a platform's own last server.
            own = (self.find(p, url) for p, url in self._own_last.items())
            self.last = next((s for s in own if s is not None), None)

    def _last_url(self, platform):
        """The server of platform used last, to save in its own file."""
        if self.last is not None and self.last.platform is platform:
            self._own_last[platform] = self.last.url
        url = self._own_last.get(platform, '')
        return url if self.find(platform, url) is not None else ''

    def save(self):
        for platform in self.platforms:
            mine = [(s.url, s.credentials) for s in self.servers if s.platform is platform]
            platform.save_servers(mine, self._last_url(platform))
        parser = _parser()
        if self.last is not None:
            parser.add_section(_LAST)
            parser.set(_LAST, 'platform', self.last.platform.id)
            parser.set(_LAST, 'url', self.last.url)
        sending = [s for s in self.servers if s.send_finished]
        if sending:
            parser.add_section(_SEND_FINISHED)
            for server in sending:
                parser.set(_SEND_FINISHED, _key(server), 'true')
        pan_app._write_ini(parser, self.path)

    def find(self, platform, url):
        return next((s for s in self.servers if s.platform is platform and s.url == url), None)

    def put(self, platform, url, credentials, replace=None, send_finished=None):
        """Adds a server, or changes replace (a Server) in its place. The
        Server, or None if url can't be used. send_finished None keeps
        replace's, else False."""
        url = platform.normalise_url(url)
        if not url:
            return None
        old = [s for s in (replace, self.find(platform, url)) if s is not None]
        if send_finished is None:
            send_finished = any(s.send_finished for s in old)
        server = Server(platform, url, credentials, send_finished)
        at = min((self.servers.index(s) for s in old), default=None)
        if at is None:
            at = max(
                (i + 1 for i, s in enumerate(self.servers) if s.platform is platform),
                default=self._platform_start(platform),
            )
        # at is old's first place, so dropping old leaves it in place.
        kept = [s for s in self.servers if s not in old]
        kept.insert(at, server)
        self.servers = kept
        if self.last in old:
            self.last = server
        return server

    def _platform_start(self, platform):
        """Where platform's first server goes, keeping platform order."""
        before = self.platforms[: self.platforms.index(platform)]
        return sum(1 for s in self.servers if s.platform in before)

    def remove(self, server):
        self.servers = [s for s in self.servers if s is not server]
        if self.last is server:
            self.last = None

    def import_servers(self, platform):
        """Adds the servers platform finds elsewhere, replacing the
        credentials of one already listed. The number added or changed."""
        changed = 0
        for url, credentials in platform.import_servers():
            known = self.find(platform, platform.normalise_url(url))
            if known is not None and known.credentials == credentials:
                continue
            if self.put(platform, url, credentials, replace=known):
                changed += 1
        return changed
