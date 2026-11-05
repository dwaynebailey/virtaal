#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import logging
import os
import socket

from virtaal.common import pan_app
from virtaal.common.platform import platform
from virtaal.controllers.baseplugin import PluginUnsupported

from . import remotetm
from .basetmmodel import BaseTMModel


class TMModel(remotetm.TMModel):
    """This is the translation memory model."""

    __gtype_name__ = 'LocalTMModel'
    display_name = _('Local Translation Memory')
    description = _('Previous translations you have made')
    # l10n: Try to keep this as short as possible.
    shortname = _('Local TM')

    PREFETCH_MAX = 2
    default_config = {
        "prefetch": 2,
        "tmserver_bind": "localhost",
        "tmserver_port": "55555",
        "tmdb": os.path.join(pan_app.get_config_dir(), "tm.db"),
    }
    # Its own server picks a free port: nothing to set (#1647).
    settings = ()

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        self.internal_name = internal_name
        self.load_config()
        self._check_config()

        port = self._pick_port()
        command = self._build_tmserver_command(port, controller)
        self._launch_tmserver(command, port)

        # Do not use super() here, as remotetm.TMModel does a bit more than we
        # want in this case.
        BaseTMModel.__init__(self, controller)
        self._signal_tracker.connect(
            self.controller.main_controller.store_controller, "store-saved", self.push_store
        )

    def _check_config(self):
        """Replace config values the server can't use (#4340)."""
        self.config["tmserver_port"] = pan_app.config_int(
            self.config["tmserver_port"],
            int(self.default_config["tmserver_port"]),
            1,
            65535,
            "tmserver_port",
        )
        # One try here: finding a free port otherwise looks the host up
        # once per port tried, for minutes, and then fails. Resolving
        # isn't enough either: "1.5" is an address, but not this host's.
        host = self.config["tmserver_bind"]
        try:
            if not host.strip():
                raise ValueError("blank")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, 0))
        except (OSError, UnicodeError, ValueError, TypeError) as e:
            default = self.default_config["tmserver_bind"]
            logging.warning("Using %s for tmserver_bind: %r can't be used (%s)", default, host, e)
            self.config["tmserver_bind"] = default
        # Relative to the config folder, not wherever Virtaal was started.
        self.config["tmdb"] = os.path.join(
            pan_app.get_config_dir(), os.path.expanduser(self.config["tmdb"])
        )

    def _pick_port(self):
        # test if port specified in config is free
        if test_port(self.config["tmserver_bind"], self.config["tmserver_port"]):
            return self.config["tmserver_port"]
        port = find_free_port(self.config["tmserver_bind"], 49152, 65535)
        if port is None:
            message = "No free port for the local TM server on %s" % self.config["tmserver_bind"]
            logging.warning(message)
            raise PluginUnsupported(message)
        return port

    def _build_tmserver_command(self, port, controller):
        # translate-toolkit's own "tmserver" console script was removed
        # upstream between releases 3.18.1 and 3.19.0 - see
        # virtaal/support/tmserver.py, which vendors it.
        from virtaal.support import helperprocess

        command = helperprocess.command("virtaal.support.tmserver")
        command += [
            "-b",
            self.config["tmserver_bind"],
            "-p",
            str(port),
            "-d",
            self.config["tmdb"],
            "--min-similarity=%d" % controller.min_quality,
            "--max-candidates=%d" % controller.max_matches,
        ]

        if pan_app.DEBUG:
            command.append("--debug")
        return command

    def _launch_tmserver(self, command, port):
        logging.debug("launching tmserver with command {}".format(" ".join(command)))
        try:
            import subprocess

            from virtaal.support import helperprocess, tmclient

            self.tmserver = subprocess.Popen(command, env=helperprocess.env())
            url = "http://%s:%d/tmserver" % (self.config["tmserver_bind"], port)

            self.tmclient = tmclient.TMClient(url)
        except OSError:
            logging.exception('Failed to start TM server')
            raise

    def push_store(self, store_controller):
        """Sync the saved file into the local TM: a changed unit replaces
        its earlier target and a unit no longer translated drops out. The
        server leaves a file excluded from the TM alone."""
        path, member = store_key(store_controller)
        units = [
            {**remotetm.unit2dict(unit), 'key': unit.getid()}
            for unit in store_controller.store.get_units()
            if unit.istranslated()
        ]
        self.tmclient.add_store(
            path,
            {'units': units, 'replace': True, 'member': member},
            self.source_lang,
            self.target_lang,
        )
        self._forget_cached()

    def import_file(self, path, on_done, on_error):
        """Import the translation file at C{path} into the local TM, as a
        store of its own keyed by its real path, without opening it.

        The server reads the file, filing each unit under the file's own
        languages where the format has them, else under the current pair.
        C{on_done(path, added)} runs with the number of translations the
        TM didn't have yet, C{on_error(path)} if the file can't be read
        or imported."""
        path = os.path.realpath(path)
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError as e:
            logging.warning("Can't import %s into the local TM: %s", path, e)
            on_error(path)
            return
        # An empty body would make curl read the upload from stdin.
        if not data:
            on_error(path)
            return
        request = self.tmclient.import_file(path, data, self.source_lang, self.target_lang)
        if request is None:
            on_error(path)
            return

        def on_success(reply):
            try:
                counts = json.loads(reply)
            except ValueError:
                counts = None
            if not isinstance(counts, dict):
                on_error(path)
                return
            self._forget_cached()
            on_done(path, counts.get('added', 0) + counts.get('replaced', 0))

        _on_reply(request, on_success, lambda: on_error(path), "import %s" % path)

    def list_stores(self, on_done, on_error):
        """C{on_done(stores)} with every store in the local TM, each with
        its path, kind, whether its file still exists and its unit counts
        per language pair; C{on_error()} if the server doesn't say."""
        request = self.tmclient.list_stores()
        if request is None:
            on_error()
            return

        def on_success(reply):
            try:
                stores = json.loads(reply)
            except ValueError:
                stores = None
            if not isinstance(stores, list):
                on_error()
                return
            on_done(stores)

        _on_reply(request, on_success, on_error, "list its stores")

    def remove_store(self, path, member, source_lang, target_lang, on_done, on_error):
        """Remove what the store at C{path} (C{member} of a bundle) added
        for this language pair, keeping translations another store also
        holds. C{on_done(removed)} with how many it held, C{on_error()} if
        it can't be removed."""
        request = self.tmclient.remove_store(path, source_lang, target_lang, member)
        if request is None:
            on_error()
            return

        def on_success(reply):
            try:
                removed = json.loads(reply)['removed']
            except (ValueError, KeyError, TypeError):
                on_error()
                return
            self._forget_cached()
            on_done(removed)

        _on_reply(request, on_success, on_error, "remove %s" % path)

    def export_tmx(self, source_lang, target_lang, on_done, on_error, path=None, member=''):
        """C{on_done(data)} with the TMX bytes of this language pair's
        entries, only the store at C{path}'s if given; C{on_error()} if
        the server can't give them."""
        request = self.tmclient.export_tmx(source_lang, target_lang, path, member)
        if request is None:
            on_error()
            return
        _on_reply(request, on_done, on_error, "export")

    def _forget_cached(self):
        self.cache = {}
        self._generation += 1

    def destroy(self):
        self._signal_tracker.disconnect_all()
        if platform.is_windows:
            import ctypes

            ctypes.windll.kernel32.TerminateProcess(int(self.tmserver._handle), -1)
            logging.debug("killing tmserver with handle %d" % int(self.tmserver._handle))
        else:
            import signal

            os.kill(self.tmserver.pid, signal.SIGTERM)
            logging.debug("killing tmserver with pid %d" % self.tmserver.pid)


def _on_reply(request, on_success, on_error, action):
    """Call C{on_success(reply)} when C{request} succeeds, else
    C{on_error()}."""

    def on_failure(_request, reason):
        logging.warning("The local TM couldn't %s: %s", action, reason)
        on_error()

    request.connect('http-success', lambda _request, reply: on_success(reply))
    for signal in ('http-redirect', 'http-client-error', 'http-server-error', 'http-failed'):
        request.connect(signal, on_failure)


def store_key(store_controller):
    """The open file's store in the local TM: its real path, and for a
    bundle the bundle's with the file's name inside it."""
    bundle = store_controller.get_bundle_filename()
    if bundle:
        # get_store_filename() gives "bundle.zip:inner.xlf".
        member = store_controller.get_store_filename()[len(bundle) + 1 :]
        return os.path.realpath(bundle), member
    return os.path.realpath(store_controller.store.get_filename()), ''


def test_port(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False


def find_free_port(host, min_port, max_port):
    import random

    port_range = list(range(min_port, max_port))
    random.shuffle(port_range)
    for port in port_range:
        if test_port(host, port):
            return port
    return None
