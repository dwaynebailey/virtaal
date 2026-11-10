#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os
import sys

import pytest

from virtaal.common.platform import Platform
from virtaal.support import helperprocess, relaunch


def _use_platform(monkeypatch, environ=None, **kwargs):
    environ = {} if environ is None else environ
    p = Platform(environ=environ, **kwargs)
    monkeypatch.setattr(relaunch, 'platform', p)
    monkeypatch.setattr(helperprocess, 'platform', p)
    monkeypatch.setattr(os, 'environ', environ)
    return p


# relaunch_args() #


def test_relaunch_args_reopens_the_file_open_at_quit(tmp_path):
    current = tmp_path / 'current.po'
    current.write_text('')

    args = relaunch.relaunch_args(['virtaal', 'startup.po'], str(current))

    assert args == [str(current)]


def test_relaunch_args_drops_a_file_no_longer_on_disk(tmp_path):
    assert relaunch.relaunch_args(['virtaal'], str(tmp_path / 'gone.po')) == []


def test_relaunch_args_keeps_config_log_and_debug_as_absolute_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    args = relaunch.relaunch_args(['virtaal', '--config', 'v.ini', '-l', 'v.log', '-D'])

    assert args == [
        '--log',
        str(tmp_path / 'v.log'),
        '--config',
        str(tmp_path / 'v.ini'),
        '--debug',
    ]


def test_relaunch_args_keeps_logging_to_stdout():
    assert relaunch.relaunch_args(['virtaal', '--log', '-']) == ['--log', '-']


@pytest.mark.parametrize(
    'option', [['--lang', 'fr'], ['--pseudo-translation'], ['--pseudo-translation-bidi']]
)
def test_relaunch_args_drops_a_one_run_ui_language(option):
    # The restart is for the language just chosen in Preferences.
    assert relaunch.relaunch_args(['virtaal', *option]) == []


@pytest.mark.parametrize(
    ('lang', 'chosen', 'expected'),
    [
        ('zu:af', 'xh', ['--lang', 'xh:af']),
        ('zu:af:de', 'af', ['--lang', 'af:de']),
        ('zu:en', 'xh', ['--lang', 'xh:en_US']),
        ('zu:af', 'af', []),
        ('zu', 'xh', []),
        ('zu:af', None, []),
    ],
)
def test_relaunch_args_keeps_a_language_lists_fallbacks_after_the_new_language(
    lang, chosen, expected
):
    assert relaunch.relaunch_args(['virtaal', '--lang', lang], ui_language=chosen) == expected


@pytest.mark.parametrize(
    ('system', 'expected'),
    [('de_DE.UTF-8', ['--lang', 'de:af']), ('af_ZA.UTF-8', []), ('C', [])],
)
def test_relaunch_args_keeps_the_fallbacks_after_the_system_language(monkeypatch, system, expected):
    from virtaal.common import pan_app

    monkeypatch.setattr(pan_app, 'osx_lang', lambda: None)
    monkeypatch.setattr(
        pan_app, '_ORIGINAL_LOCALE_ENV', {'LANGUAGE': None, 'LANG': system, 'LC_ALL': None}
    )
    # As --lang zu:af leaves it.
    monkeypatch.setenv('LANGUAGE', 'zu:af')

    args = relaunch.relaunch_args(['virtaal', '--lang', 'zu:af'], ui_language='')

    assert args == expected
    assert os.environ['LANGUAGE'] == 'zu:af'


def test_relaunch_passes_the_language_chosen_in_preferences(monkeypatch):
    from virtaal.common import pan_app

    calls = []
    monkeypatch.setattr(sys, 'argv', ['virtaal', '--lang=zu:af'])
    monkeypatch.setitem(pan_app.settings.language, 'uilang', 'xh')
    monkeypatch.setattr(relaunch, 'relaunch_command', lambda args: (args, None))
    monkeypatch.setattr(relaunch, 'spawn', lambda argv, env: calls.append(argv))

    relaunch.relaunch()

    assert calls == [['--lang', 'xh:af']]


# relaunch_command() #


def test_source_run_reruns_the_script_with_this_python(monkeypatch, tmp_path):
    _use_platform(monkeypatch, os_name='posix', sys_platform='linux', frozen=False)
    script = tmp_path / 'virtaal'
    script.write_text('')
    monkeypatch.setattr(sys, 'argv', [str(script), 'x.po'])

    argv, env = relaunch.relaunch_command(['/files/x.po'])

    assert argv == [sys.executable, str(script), '/files/x.po']
    assert 'PYTHONPATH' in env  # helperprocess.env(): Virtaal importable


def test_source_run_without_a_script_runs_the_cli_module(monkeypatch):
    _use_platform(monkeypatch, os_name='posix', sys_platform='linux', frozen=False)
    monkeypatch.setattr(sys, 'argv', ['-c'])

    argv, _env = relaunch.relaunch_command(['--debug'])

    assert argv[:2] == [sys.executable, '-c']
    assert 'virtaal.cli' in argv[2]
    assert argv[3:] == ['--debug']


def test_frozen_linux_reruns_the_bundle_as_a_new_top_level_instance(monkeypatch):
    _use_platform(
        monkeypatch, {'_PYI_ARCHIVE_FILE': 'x'}, os_name='posix', sys_platform='linux', frozen=True
    )
    monkeypatch.setattr(sys, 'executable', '/opt/virtaal/Virtaal')

    argv, env = relaunch.relaunch_command(['/files/x.po'])

    assert argv == ['/opt/virtaal/Virtaal', '/files/x.po']
    assert env['PYINSTALLER_RESET_ENVIRONMENT'] == '1'


def test_appimage_reruns_the_appimage_file_not_its_mount(monkeypatch):
    mount = '/tmp/.mount_VirtaaXYZ'
    environ = {
        'APPIMAGE': '/home/u/Virtaal.AppImage',
        'APPDIR': mount,
        'ARGV0': 'Virtaal.AppImage',
        'OWD': '/home/u',
        'LD_LIBRARY_PATH': mount + '/lib:/usr/lib',
        'PATH': '/usr/bin',
    }
    _use_platform(monkeypatch, environ, os_name='posix', sys_platform='linux', frozen=True)
    monkeypatch.setattr(sys, 'executable', mount + '/Virtaal')
    monkeypatch.setattr(sys, '_MEIPASS', mount, raising=False)

    argv, env = relaunch.relaunch_command(['/files/x.po'])

    assert argv == ['/home/u/Virtaal.AppImage', '/files/x.po']
    assert not {'APPIMAGE', 'APPDIR', 'ARGV0', 'OWD'} & set(env)
    assert env['LD_LIBRARY_PATH'] == '/usr/lib'
    assert env['PATH'] == '/usr/bin'


def test_macos_app_reopens_the_app_bundle(monkeypatch):
    _use_platform(monkeypatch, os_name='posix', sys_platform='darwin', frozen=True)
    monkeypatch.setattr(sys, 'executable', '/Applications/Virtaal.app/Contents/MacOS/Virtaal')

    argv, _env = relaunch.relaunch_command(['/files/x.po'])

    assert argv == ['open', '-n', '-a', '/Applications/Virtaal.app', '--args', '/files/x.po']


def test_macos_app_without_args_passes_no_args_flag(monkeypatch):
    _use_platform(monkeypatch, os_name='posix', sys_platform='darwin', frozen=True)
    monkeypatch.setattr(sys, 'executable', '/Applications/Virtaal.app/Contents/MacOS/Virtaal')

    argv, _env = relaunch.relaunch_command([])

    assert argv == ['open', '-n', '-a', '/Applications/Virtaal.app']


def test_windows_frozen_reruns_the_exe(monkeypatch):
    _use_platform(monkeypatch, os_name='nt', sys_platform='win32', frozen=True)
    monkeypatch.setattr(sys, 'executable', r'C:\Program Files\Virtaal\virtaal.exe')

    argv, env = relaunch.relaunch_command([r'C:\files\x.po'])

    assert argv == [r'C:\Program Files\Virtaal\virtaal.exe', r'C:\files\x.po']
    assert env['PYINSTALLER_RESET_ENVIRONMENT'] == '1'


def test_flatpak_reruns_the_app_command_in_a_new_sandbox(monkeypatch):
    _use_platform(
        monkeypatch,
        {'FLATPAK_ID': 'io.github.translate.Virtaal'},
        os_name='posix',
        sys_platform='linux',
        frozen=False,
    )

    argv, env = relaunch.relaunch_command(['/files/x.po'])

    assert argv == ['/app/bin/virtaal', '/files/x.po']
    assert env is None


# spawn() #


class _Popen:
    calls = []

    def __init__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))


@pytest.fixture
def popen(monkeypatch):
    _Popen.calls = []
    monkeypatch.setattr(relaunch.subprocess, 'Popen', _Popen)
    return _Popen.calls


def test_spawn_on_posix_starts_a_new_session(monkeypatch, popen):
    _use_platform(monkeypatch, os_name='posix', sys_platform='linux', frozen=False)

    relaunch.spawn(['virtaal'], {'A': '1'})

    assert popen == [(['virtaal'], {'env': {'A': '1'}, 'start_new_session': True})]


def test_spawn_on_windows_detaches_from_this_process(monkeypatch, popen):
    _use_platform(monkeypatch, os_name='nt', sys_platform='win32', frozen=True)

    relaunch.spawn(['virtaal.exe'], {})

    flags = popen[0][1]['creationflags']
    assert flags & 0x8  # DETACHED_PROCESS
    assert flags & 0x200  # CREATE_NEW_PROCESS_GROUP


def test_spawn_in_a_flatpak_goes_through_the_portal(monkeypatch, popen):
    _use_platform(monkeypatch, {'FLATPAK_ID': 'x'}, os_name='posix', sys_platform='linux')
    from gi.repository import Gio

    calls = []

    class FakeBus:
        def call_sync(self, name, path, interface, method, params, *rest):
            calls.append((name, method, params.unpack()))

    monkeypatch.setattr(Gio, 'bus_get_sync', lambda *args: FakeBus())

    relaunch.spawn(['/app/bin/virtaal', '/files/x.po'], None)

    assert popen == []
    ((name, method, params),) = calls
    assert (name, method) == ('org.freedesktop.portal.Flatpak', 'Spawn')
    _cwd, argv, fds, envs, flags, options = params
    # NUL-terminated bytestrings, as flatpak-spawn sends them.
    assert [bytes(a) for a in argv] == [b'/app/bin/virtaal\0', b'/files/x.po\0']
    assert (fds, envs, flags, options) == ({}, {}, 0, {})


# relaunch() #


def test_relaunch_starts_the_command_once(monkeypatch):
    calls = []
    monkeypatch.setattr(relaunch, 'relaunch_command', lambda args: (['virtaal', *args], {}))
    monkeypatch.setattr(relaunch, 'spawn', lambda argv, env: calls.append(argv))
    monkeypatch.setattr(sys, 'argv', ['virtaal'])

    relaunch.relaunch(None)

    assert calls == [['virtaal']]


def test_relaunch_logs_a_failed_start_instead_of_raising(monkeypatch, caplog):
    def fail(argv, env):
        raise OSError('nope')

    monkeypatch.setattr(relaunch, 'spawn', fail)
    monkeypatch.setattr(sys, 'argv', ['virtaal'])

    relaunch.relaunch(None)

    assert 'Could not restart Virtaal' in caplog.text
