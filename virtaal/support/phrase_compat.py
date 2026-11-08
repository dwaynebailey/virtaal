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

Phrase writes inline tags as text in the segment: {1}, {1>bold<1},
{b>bold<b}, with their codes in <m:tunit-metadata>. Phrase rebuilds the
codes from that text, so a tag changed or left out breaks the file:
L{PhraseTagCheck} reports one (#4736), and
L{virtaal.support.formatplaceables} shows them as placeables.

L{virtaal.support.catxliff_compat} puts these into translate-toolkit's
XLIFF units."""

import builtins
from collections import Counter

from translate.filters.decorators import Category
from translate.storage.workflow import StateEnum

from virtaal.support.formatplaceables import PHRASE_NAMESPACE, PhraseTagPlaceable

NAMESPACE = "http://www.memsource.com/mxlf/2.0"
NAMESPACE_PREFIX = PHRASE_NAMESPACE
M = "{%s}" % NAMESPACE

# Unconfirmed with a translation needs review; confirmed at any step is
# translated.
STATE = {
    StateEnum.EMPTY: (StateEnum.EMPTY, StateEnum.NEEDS_REVIEW),
    StateEnum.NEEDS_REVIEW: (StateEnum.NEEDS_REVIEW, StateEnum.UNREVIEWED),
    StateEnum.UNREVIEWED: (StateEnum.UNREVIEWED, StateEnum.MAX),
}

TAG = PhraseTagPlaceable.regex


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


def tags(text):
    return TAG.findall(text or "")


class PhraseTagCheck:
    """The "phrasetags" extra check: the target's Phrase tags, which
    Phrase rebuilds the file's codes from, aren't the source's."""

    name = "phrasetags"
    category = Category.CRITICAL

    @staticmethod
    def applies_to(store):
        from virtaal.support import catxliff_compat

        return catxliff_compat.store_flavour(store) == "phrase"

    @staticmethod
    def available():
        return True

    def check(self, unit):
        if not unit.target:
            return None
        source, target = Counter(tags(unit.source)), Counter(tags(unit.target))
        if source == target:
            return None
        missing = sorted((source - target).elements(), key=tags(unit.source).index)
        extra = sorted((target - source).elements(), key=tags(unit.target).index)
        # Virtaal installs _(); a helper process may not have.
        _ = getattr(builtins, "_", str)
        lines = []
        if missing:
            lines.append(
                _(
                    # l10n: Phrase (Memsource) tags such as {1} or {1>, which
                    # a translation must keep. %s is the list of them.
                    "Missing Phrase tags: %s"
                )
                % " ".join(missing)
            )
        if extra:
            lines.append(
                _(
                    # l10n: Phrase (Memsource) tags such as {1} or {1>, which
                    # the source doesn't have. %s is the list of them.
                    "Phrase tags not in the source: %s"
                )
                % " ".join(extra)
            )
        return "\n".join(lines)
