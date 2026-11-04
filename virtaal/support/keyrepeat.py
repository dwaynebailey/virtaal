#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The desktop's key repeat timing, which GTK3 doesn't expose."""

import logging

from virtaal.common.platform import platform

GNOME_KEYBOARD = 'org.gnome.desktop.peripherals.keyboard'


def repeat_timing():
    """A held key's (first repeat delay, repeat interval) in milliseconds,
    or None where they can't be read (Windows, desktops other than GNOME)."""
    try:
        if platform.is_mac:
            timing = _macos()
        elif not platform.is_windows:
            timing = _gnome()
        else:
            timing = None
    except Exception:
        logging.debug('Unable to read the key repeat settings', exc_info=True)
        return None
    if timing is None or timing[0] <= 0 or timing[1] < 0:
        return None
    return timing


def _macos():
    import ctypes
    import ctypes.util

    objc = ctypes.CDLL(ctypes.util.find_library('objc'))
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    # Its own function object: others set other result types.
    send = objc['objc_msgSend']
    send.restype = ctypes.c_double
    send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    nsevent = objc.objc_getClass(b'NSEvent')
    if not nsevent:
        return None

    def ms(selector):
        return round(send(nsevent, objc.sel_registerName(selector)) * 1000)

    return (ms(b'keyRepeatDelay'), ms(b'keyRepeatInterval'))


def _gnome():
    from gi.repository import Gio

    # Gio.Settings.new() aborts on a missing schema.
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or not source.lookup(GNOME_KEYBOARD, True):
        return None
    settings = Gio.Settings.new(GNOME_KEYBOARD)
    return (settings.get_uint('delay'), settings.get_uint('repeat-interval'))
