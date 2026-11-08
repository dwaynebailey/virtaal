#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Phrase's (formerly Memsource's) bilingual XLIFF 1.2 (.mxliff), in the
xmlns:m="http://www.memsource.com/mxlf/2.0" namespace. A segment's
m:confirmed is the workflow step it was confirmed at, 0 if not; the
file's m:level is the step it was exported for. m:locked="true" locks
it, m:score is its match score (1.01 is a context match), and
m:trans-origin where it came from (tm, mt, nt for non-translatable).
Phrase doesn't write XLIFF's state or approved, so translate-toolkit
showed every translation as fuzzy (#4735).

L{virtaal.support.catxliff_compat} puts these into translate-toolkit's
XLIFF units."""

from translate.storage.workflow import StateEnum

NAMESPACE = "http://www.memsource.com/mxlf/2.0"
# Its version is in the URI.
NAMESPACE_PREFIX = "http://www.memsource.com/mxlf/"
M = "{%s}" % NAMESPACE

# Unconfirmed with a translation needs review; confirmed at any step is
# translated.
STATE = {
    StateEnum.EMPTY: (StateEnum.EMPTY, StateEnum.NEEDS_REVIEW),
    StateEnum.NEEDS_REVIEW: (StateEnum.NEEDS_REVIEW, StateEnum.UNREVIEWED),
    StateEnum.UNREVIEWED: (StateEnum.UNREVIEWED, StateEnum.MAX),
}


def _attribute(element, name):
    # The namespace's version is in its URI: mxlf/2.0.
    for key, value in element.attrib.items():
        if key.startswith("{" + NAMESPACE_PREFIX) and key.endswith("}" + name):
            return value
    return None


def _number(text, default):
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def level(unit):
    """The workflow step the file was exported for: 1 is translation."""
    root = unit.xmlelement.getroottree().getroot()
    return max(_number(_attribute(root, "level"), 1), 1)


def state_n(unit, has_target):
    if not has_target:
        return StateEnum.EMPTY
    if _number(_attribute(unit.xmlelement, "confirmed"), 0) > 0:
        return StateEnum.UNREVIEWED
    return StateEnum.NEEDS_REVIEW


def _set_attribute(element, name, value):
    for key in element.attrib:
        if key.startswith("{" + NAMESPACE_PREFIX) and key.endswith("}" + name):
            element.set(key, value)
            return
    element.set(M + name, value)


def set_state_n(unit, value):
    """Confirm the segment at the file's step for Translated and up,
    and unconfirm it below."""
    confirmed = level(unit) if value >= StateEnum.UNREVIEWED else 0
    _set_attribute(unit.xmlelement, "confirmed", str(confirmed))


def is_locked(unit):
    return _attribute(unit.xmlelement, "locked") == "true"


def origin(unit):
    """(origin, score as a percentage), each None when missing: the
    origin is Phrase's own name, such as tm, mt or nt."""
    found = _attribute(unit.xmlelement, "trans-origin")
    if found in (None, "", "null"):
        found = None
    score = _attribute(unit.xmlelement, "score")
    try:
        value = float(score)
    except (TypeError, ValueError):
        return found, None
    if value <= 0:
        return found, None
    # Phrase's own files score 0 to 1.01; some tools write a percentage.
    percent = value * 100 if value <= 1.01 else value
    return found, f"{percent:g}"
