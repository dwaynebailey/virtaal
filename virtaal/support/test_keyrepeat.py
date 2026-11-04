#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest

from virtaal.support import keyrepeat


def _on(monkeypatch, mac=False, windows=False):
    monkeypatch.setattr(keyrepeat.platform, 'is_mac', mac)
    monkeypatch.setattr(keyrepeat.platform, 'is_windows', windows)


def _gnome_schema():
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    return source and source.lookup(keyrepeat.GNOME_KEYBOARD, True)


def test_repeat_timing_reads_gnomes_keyboard_settings(monkeypatch):
    if not _gnome_schema():
        pytest.skip("needs GNOME's keyboard settings schema")
    from gi.repository import Gio

    _on(monkeypatch)
    settings = Gio.Settings.new(keyrepeat.GNOME_KEYBOARD)

    assert keyrepeat.repeat_timing() == (
        settings.get_uint('delay'),
        settings.get_uint('repeat-interval'),
    )


def test_repeat_timing_without_gnomes_schema_is_unknown(monkeypatch):
    from gi.repository import Gio

    _on(monkeypatch)
    monkeypatch.setattr(Gio.SettingsSchemaSource, 'get_default', staticmethod(lambda: None))

    assert keyrepeat.repeat_timing() is None


def test_repeat_timing_on_windows_is_unknown(monkeypatch):
    _on(monkeypatch, windows=True)
    monkeypatch.setattr(keyrepeat, '_gnome', lambda: pytest.fail('read GNOME settings'))

    assert keyrepeat.repeat_timing() is None


def test_repeat_timing_on_macos_asks_appkit(monkeypatch):
    _on(monkeypatch, mac=True)
    monkeypatch.setattr(keyrepeat, '_macos', lambda: (500, 83))

    assert keyrepeat.repeat_timing() == (500, 83)


@pytest.mark.parametrize('timing', [None, (0, 30), (500, -1)])
def test_repeat_timing_ignores_unusable_values(monkeypatch, timing):
    _on(monkeypatch)
    monkeypatch.setattr(keyrepeat, '_gnome', lambda: timing)

    assert keyrepeat.repeat_timing() is None


def test_repeat_timing_that_fails_is_unknown(monkeypatch):
    _on(monkeypatch, mac=True)

    def broken():
        raise OSError('no objc')

    monkeypatch.setattr(keyrepeat, '_macos', broken)

    assert keyrepeat.repeat_timing() is None
