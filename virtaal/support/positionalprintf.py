#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Numbering a translation's printf placeholders (%s to %1$s) so that
it can put them in another order than the source's.

printf takes %s, %d... in turn, so a translation that moves them hands
each the wrong argument. Numbered, %2$d takes the second argument
wherever it is, and gettext accepts a numbered translation of a source
that isn't. Only for formats whose printf has %n$; never Python's
%(name)s, brace fields, Qt's %1 or Java's {0}, which are numbered or
named already.
"""

import re
from typing import NamedTuple

FLAGS = frozenset(
    (
        'c-format',
        'objc-format',
        'java-printf-format',
        'php-format',
        'perl-format',
        'ruby-format',
        'tcl-format',
        'awk-format',
        'gcc-internal-format',
        'gfc-internal-format',
        'javascript-format',
        'sh-printf-format',
        'boost-format',
    )
)
"""gettext flags of formats whose printf takes %n$ (msgfmt -c accepts a
numbered translation of an unnumbered source for each)."""

FORMATS = frozenset(('android', 'strings'))
"""Monolingual formats (see L{formatplaceables.format_of}) whose
placeholders are printf's: Android resources and Apple .strings."""

_DIRECTIVE = re.compile(
    r"""%%|%(?P<number>[1-9]\d*\$)?
    (?P<spec>[-+ 0#']*(?:\d+|(?P<star>\*))?(?:\.(?:\d+|(?P<star2>\*)))?
    (?:hh|ll|I64|I32|[hlLqjztI])?[diouxXeEfFgGaAcCsSpn@])""",
    re.VERBOSE,
)


class Directive(NamedTuple):
    start: int
    number: int | None
    """Its argument number (%2$d), or None."""
    spec: str
    """What follows the % and number: s, 5.2f, @."""
    star: bool
    """Whether a width or precision is an argument (%*d)."""


def applies(fmt, flags):
    """Whether numbering suits a unit of format fmt with gettext flags."""
    if fmt in FORMATS:
        return True
    return bool(FLAGS & set(flags))


def directives(text):
    """The printf directives of text, %% left out."""
    found = []
    for match in _DIRECTIVE.finditer(text):
        if match.group('spec') is None:
            continue
        number = match.group('number')
        found.append(
            Directive(
                match.start(),
                int(number[:-1]) if number else None,
                match.group('spec'),
                bool(match.group('star') or match.group('star2')),
            )
        )
    return found


def _free_positions(source_directives, target_directives):
    """{spec: source positions} that target's numbered directives leave
    for its unnumbered ones, or None if a numbered one doesn't fit the
    source."""
    free = {}
    for position, directive in enumerate(source_directives, 1):
        free.setdefault(directive.spec, []).append(position)
    for directive in target_directives:
        if directive.number is None:
            continue
        if directive.number > len(source_directives):
            return None
        if source_directives[directive.number - 1].spec != directive.spec:
            return None
        if directive.number in free[directive.spec]:
            free[directive.spec].remove(directive.number)
    return free


def _numbers(source, target):
    """The source position of each of target's directives, or None if
    they can't be matched to the source's: a source with numbered
    directives, or %*d, or a target directive the source lacks."""
    source_directives = directives(source)
    target_directives = directives(target)
    if any(d.number or d.star for d in source_directives) or any(d.star for d in target_directives):
        return None
    free = _free_positions(source_directives, target_directives)
    if free is None:
        return None
    numbers = []
    for directive in target_directives:
        if directive.number is not None:
            numbers.append(directive.number)
            continue
        positions = free.get(directive.spec)
        if not positions:
            return None
        numbers.append(positions.pop(0))
    return numbers


def needs_numbers(source, target):
    """Whether target has unnumbered directives that are out of the
    source's order, or mixed with numbered ones, and can be numbered."""
    numbers = _numbers(source, target)
    if not numbers:
        return False
    unnumbered = [n for n, d in zip(numbers, directives(target), strict=True) if d.number is None]
    if not unnumbered:
        return False
    if len(unnumbered) < len(numbers):
        return True
    return unnumbered != sorted(unnumbered)


def numbered(source, target):
    """target with each unnumbered directive numbered by its source
    position, or None if L{needs_numbers} is false."""
    if not needs_numbers(source, target):
        return None
    numbers = _numbers(source, target)
    pieces = []
    last = 0
    for number, directive in zip(numbers, directives(target), strict=True):
        if directive.number is None:
            cut = directive.start + 1
            pieces.append(target[last:cut] + '%d$' % number)
            last = cut
    pieces.append(target[last:])
    return ''.join(pieces)
