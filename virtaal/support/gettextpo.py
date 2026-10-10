# -*- coding: utf-8 -*-
#
# Copyright 2026 Zuza Software Foundation
#
# This file is part of Virtaal.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

"""Run the checks of C{msgfmt -c} on single units through libgettextpo.

This uses the checking API of GNU gettext's libgettextpo
(C{po_message_check_all()}), which does the same checks as C{msgfmt -c}:
format strings (according to the C{*-format} flags), plural forms against the
header's Plural-Forms, leading/trailing newlines and header validity.

If libgettextpo can't be loaded, L{available} returns C{False} and nothing
else in this module should be used.
"""

from __future__ import absolute_import, print_function, unicode_literals

import ctypes
import ctypes.util
import logging

PO_SEVERITY_WARNING = 0
PO_SEVERITY_ERROR = 1
PO_SEVERITY_FATAL_ERROR = 2

_xerror_prototype = ctypes.CFUNCTYPE(
    None,
    ctypes.c_int, ctypes.c_void_p,
    ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t,
    ctypes.c_int, ctypes.c_char_p,
)
_xerror2_prototype = ctypes.CFUNCTYPE(
    None,
    ctypes.c_int, ctypes.c_void_p,
    ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t,
    ctypes.c_int, ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t,
    ctypes.c_int, ctypes.c_char_p,
)


class _XErrorHandler(ctypes.Structure):
    _fields_ = [
        ('xerror', _xerror_prototype),
        ('xerror2', _xerror2_prototype),
    ]


_lib = None
_load_attempted = False


def _load_library():
    global _lib, _load_attempted
    if _load_attempted:
        return _lib
    _load_attempted = True

    names = [ctypes.util.find_library('gettextpo'), 'libgettextpo.so.0',
             'libgettextpo-0.dll', 'libgettextpo.0.dylib']
    for name in names:
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    else:
        logging.debug('libgettextpo not found; msgfmt checks disabled')
        return None

    vp = ctypes.c_void_p
    handler_p = ctypes.POINTER(_XErrorHandler)
    try:
        signatures = {
            'po_file_create': ([], vp),
            'po_file_free': ([vp], None),
            'po_message_iterator': ([vp, ctypes.c_char_p], vp),
            'po_message_iterator_free': ([vp], None),
            'po_message_insert': ([vp, vp], None),
            'po_message_create': ([], vp),
            'po_message_set_msgctxt': ([vp, ctypes.c_char_p], None),
            'po_message_set_msgid': ([vp, ctypes.c_char_p], None),
            'po_message_set_msgid_plural': ([vp, ctypes.c_char_p], None),
            'po_message_set_msgstr': ([vp, ctypes.c_char_p], None),
            'po_message_set_msgstr_plural': ([vp, ctypes.c_int, ctypes.c_char_p], None),
            'po_message_set_format': ([vp, ctypes.c_char_p, ctypes.c_int], None),
            'po_message_check_all': ([vp, vp, handler_p], None),
        }
        for funcname, (argtypes, restype) in signatures.items():
            func = getattr(lib, funcname)
            func.argtypes = argtypes
            func.restype = restype
    except AttributeError as e:
        logging.debug('libgettextpo is missing a function: %s', e)
        return None

    _lib = lib
    return _lib


def available():
    """Return whether libgettextpo could be loaded."""
    return _load_library() is not None


def _encode(text):
    if text is None:
        return None
    return (u"%s" % text).encode('utf-8')


def _strings(multistring):
    strings = getattr(multistring, 'strings', None)
    if strings is None:
        return [multistring]
    return [u"%s" % s for s in strings]


def _unit_flags(unit):
    flags = []
    for comment in getattr(unit, 'typecomments', []):
        comment = comment.strip()
        if comment.startswith('#,'):
            comment = comment[2:]
        flags.extend(f.strip() for f in comment.split(','))
    return [f for f in flags if f]


class MsgfmtChecker(object):
    """Checks single units like C{msgfmt -c} would.

    A checker holds an in-memory PO file containing only the header of the
    store, so that plural forms can be checked against Plural-Forms."""

    def __init__(self):
        self._lib = _load_library()
        if self._lib is None:
            raise RuntimeError('libgettextpo is not available')
        self._file = None
        self._iterator = None
        self._header = None
        self._message = None
        self._problems = None

        # Keep references to the callbacks so they don't get collected.
        self._xerror = _xerror_prototype(self._on_xerror)
        self._xerror2 = _xerror2_prototype(self._on_xerror2)
        self._handler = _XErrorHandler(self._xerror, self._xerror2)

    def __del__(self):
        self._free()

    def _free(self):
        lib = self._lib
        if self._iterator:
            lib.po_message_iterator_free(self._iterator)
            self._iterator = None
        if self._file:
            lib.po_file_free(self._file)
            self._file = None

    def set_header(self, header):
        """Set the header (msgstr of the header entry) to check against."""
        header = header or u""
        if header == self._header and self._file:
            return
        self._free()
        lib = self._lib
        self._header = header
        self._file = lib.po_file_create()
        self._iterator = lib.po_message_iterator(self._file, None)
        message = lib.po_message_create()
        lib.po_message_set_msgid(message, b"")
        lib.po_message_set_msgstr(message, _encode(header))
        lib.po_message_insert(self._iterator, message)

    def _on_xerror(self, severity, message, filename, lineno, column, multiline, text):
        # po_message_check_all() also checks the header in our file every
        # time, so ignore problems reported against other messages.
        if message and message != self._message:
            return
        self._add_problem(severity, text)

    def _on_xerror2(self, severity, message1, filename1, lineno1, column1, multiline1, text1,
                    message2, filename2, lineno2, column2, multiline2, text2):
        text1 = (text1 or b"").rstrip()
        text2 = (text2 or b"").strip()
        self._add_problem(severity, b" ".join(t for t in (text1, text2) if t))

    def _add_problem(self, severity, text):
        # A fatal error must not return to libgettextpo, but the checking API
        # does not report fatal errors, only errors and warnings.
        if self._problems is None:
            return
        text = (text or b"").decode('utf-8', 'replace').strip()
        if text:
            self._problems.append(text)

    def _make_message(self, unit):
        lib = self._lib
        message = lib.po_message_create()
        sources = _strings(unit.source)
        targets = _strings(unit.target)

        context = unit.getcontext() if hasattr(unit, 'getcontext') else None
        if context:
            lib.po_message_set_msgctxt(message, _encode(context))
        lib.po_message_set_msgid(message, _encode(sources[0]))
        if unit.hasplural() and len(sources) > 1:
            lib.po_message_set_msgid_plural(message, _encode(sources[1]))
            for i, target in enumerate(targets):
                lib.po_message_set_msgstr_plural(message, i, _encode(target))
        else:
            lib.po_message_set_msgstr(message, _encode(targets[0] if targets else u""))

        for flag in _unit_flags(unit):
            # Fuzzy units are not marked as fuzzy, since libgettextpo doesn't
            # check the format strings of fuzzy messages.
            if flag.endswith('-format'):
                is_format = not flag.startswith('no-')
                format_type = flag[3:] if not is_format else flag
                lib.po_message_set_format(message, _encode(format_type), int(is_format))
        return message

    def check_unit(self, unit):
        """Return a list of problems (strings) msgfmt -c reports for C{unit}.

        Untranslated units are not checked, since msgfmt ignores them. Fuzzy
        units are checked, unlike in msgfmt, since they are being edited."""
        if not any(_strings(unit.target)) and not unit.isheader():
            return []
        if self._file is None:
            self.set_header(u"")
        if unit.isheader():
            self.set_header(_strings(unit.target)[0])

        # The message is deliberately not inserted into our file: it would
        # then be freed with the file, and we don't want it to accumulate.
        # libgettextpo has no po_message_free(), so this leaks a few bytes
        # per check.
        self._message = self._make_message(unit)
        self._problems = []
        try:
            self._lib.po_message_check_all(self._message, self._iterator, ctypes.byref(self._handler))
            return self._problems
        finally:
            self._message = None
            self._problems = None
