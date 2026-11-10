#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Starting Virtaal again once this instance has quit (#3605): the
language-change notice's Restart.

The new instance is a separate, detached process started after the
main loop has ended and the plugins (and so the TM server) are shut
down - not os.execv(), which would replace this process while GTK
and its helper processes are still around."""

import logging
import os
import subprocess
import sys

from virtaal.common import pan_app
from virtaal.common.platform import platform
from virtaal.support import helperprocess

FLATPAK_COMMAND = '/app/bin/virtaal'
# Set by the AppImage runtime for the instance it starts.
_APPIMAGE_VARS = ('APPIMAGE', 'APPDIR', 'ARGV0', 'OWD')


def _lang_chain_args(lang, ui_language):
    """--lang for the new instance: a list's fallbacks after the newly
    chosen C{ui_language} ("zu:af", xh chosen: "xh:af"), or after the
    system's language for ''."""
    if not lang or ':' not in lang or ui_language is None:
        return []
    ui_language = ui_language or pan_app.system_ui_language()
    if pan_app.parse_ui_language_chain(ui_language) == ['en_US']:
        return []  # English needs no fallbacks
    fallbacks = [code for code in pan_app.parse_ui_language_chain(lang)[1:] if code != ui_language]
    if not fallbacks:
        return []
    return ['--lang', ':'.join([ui_language, *fallbacks])]


def relaunch_args(argv, filename=None, ui_language=None):
    """The command-line arguments for the new instance: this one's
    options, with the file open at quit instead of the startup file.
    --lang and the pseudo-translations override the UI language for
    one run, which would hide the new one the restart is for
    (C{ui_language}, '' for the system's); only a --lang list's
    fallbacks are kept, after the new language."""
    args = []
    if len(argv) > 1:
        from virtaal.cli import build_parser

        try:
            options, _unknown = build_parser().parse_known_args(argv[1:])
        except SystemExit:
            options = None
        if options is not None:
            # macOS's open runs the app in /, so paths are absolute.
            if options.log:
                log = options.log
                args += ['--log', log if log.upper() in ('-', 'STDOUT') else os.path.abspath(log)]
            if options.config:
                args += ['--config', os.path.abspath(options.config)]
            if options.debug:
                args.append('--debug')
            args += _lang_chain_args(options.lang, ui_language)
    if filename and os.path.isfile(filename):
        args.append(os.path.abspath(filename))
    return args


def relaunch_command(args):
    """(argv, env) that start Virtaal with C{args}. env is None for a
    Flatpak, whose new sandbox gets its own."""
    environ = os.environ
    if platform.is_flatpak:
        return [FLATPAK_COMMAND, *args], None
    if platform.is_frozen:
        if platform.is_mac:
            # The .app itself, so it's a proper app launch with its own Dock icon.
            bundle = os.path.dirname(os.path.dirname(os.path.dirname(sys.executable)))
            if bundle.endswith('.app'):
                return ['open', '-n', '-a', bundle, *(['--args', *args] if args else [])], dict(
                    environ
                )
        env = helperprocess.host_env(environ)
        # A new top-level instance, not a helper of this one (PyInstaller 6.9+).
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        if platform.is_appimage:
            # The AppImage file, not its mount, which goes away with this instance.
            appimage = environ['APPIMAGE']
            for name in _APPIMAGE_VARS:
                env.pop(name, None)
            return [appimage, *args], env
        return [sys.executable, *args], env
    script = sys.argv[0] if sys.argv else ''
    if script and os.path.isfile(script):
        command = [sys.executable, os.path.abspath(script)]
    else:
        command = [sys.executable, '-c', 'import sys; from virtaal.cli import main; main(sys.argv)']
    return [*command, *args], helperprocess.env()


def _flatpak_spawn(argv):
    """Start C{argv} in a new sandbox of this app through the Flatpak
    portal: a child in this sandbox would be killed with it, and the
    call returns only once the new one is running."""
    from gi.repository import Gio, GLib

    def bytestring(s):
        return os.fsencode(s) + b'\0'

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call_sync(
        'org.freedesktop.portal.Flatpak',
        '/org/freedesktop/portal/Flatpak',
        'org.freedesktop.portal.Flatpak',
        'Spawn',
        GLib.Variant(
            '(ayaaya{uh}a{ss}ua{sv})',
            (bytestring(os.getcwd()), [bytestring(a) for a in argv], {}, {}, 0, {}),
        ),
        GLib.VariantType('(u)'),
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )


def spawn(argv, env):
    """Start C{argv} so that it outlives this process."""
    if platform.is_flatpak:
        _flatpak_spawn(argv)
    elif platform.is_windows:
        # Not killed with this one's console or process group.
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0x8) | getattr(
            subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200
        )
        subprocess.Popen(argv, env=env, creationflags=flags)
    else:
        subprocess.Popen(argv, env=env, start_new_session=True)


def relaunch(filename=None):
    """Start a new instance of Virtaal, reopening C{filename}."""
    ui_language = pan_app.settings.language['uilang']
    argv, env = relaunch_command(relaunch_args(sys.argv, filename, ui_language))
    logging.info('Restarting Virtaal: %s', argv)
    try:
        spawn(argv, env)
    except Exception:
        logging.exception('Could not restart Virtaal')
