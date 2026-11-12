#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Weblate's check flags (#3337). No GTK here.

Weblate keeps check flags on a component (check_flags) and on each
unit (flags, extra_flags), as a comma-separated text: "max-length:40,
ignore-end-stop, placeholders:"%(name)s":r"\\{\\w+\\}"". parse_flags()
reads one into (name, params).
"""


class Regex(str):
    """A flag's parameter written as a regular expression: r"..."."""


def _split(text, separator):
    """text split at separator, except within double quotes (where a
    backslash escapes the next character)."""
    parts, current, quoted, escaped = [], [], False, False
    for char in text:
        if escaped:
            escaped = False
        elif quoted and char == '\\':
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == separator and not quoted:
            parts.append(''.join(current))
            current = []
            continue
        current.append(char)
    parts.append(''.join(current))
    return parts


def _unquote(param):
    """A flag's parameter without its quotes: a Regex for r"..."."""
    param = param.strip()
    regex = param.startswith('r"')
    if regex:
        param = param[1:]
    if len(param) >= 2 and param[0] == param[-1] == '"':
        param = param[1:-1]
        out, escaped = [], False
        for char in param:
            if escaped or char != '\\':
                out.append(char)
                escaped = False
            else:
                escaped = True
                if regex:
                    out.append(char)
        param = ''.join(out)
    return Regex(param) if regex else param


def parse_flags(text):
    """[(name, [params])] of a check flags text, in order."""
    flags = []
    for part in _split(str(text or ''), ','):
        if not part.strip():
            continue
        name, *params = _split(part.strip(), ':')
        flags.append((name.strip(), [_unquote(p) for p in params]))
    return flags
