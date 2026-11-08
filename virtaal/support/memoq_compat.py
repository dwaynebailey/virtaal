#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""memoQ's bilingual XLIFF 1.2 (.mqxliff, or memoQ's .xlf), in the
xmlns:mq="MQXliff" namespace: a row's status is mq:status, its lock
mq:locked, its match rate mq:percent, and its comments
<mq:comments>. memoQ doesn't write XLIFF's state or approved, so
translate-toolkit showed every translation as fuzzy (#4734).

L{virtaal.support.catxliff_compat} puts these into translate-toolkit's
XLIFF units."""

from translate.storage.workflow import StateEnum

from virtaal.support import sdlxliff_compat

NAMESPACE = "MQXliff"
MQ = "{%s}" % NAMESPACE

# NotStarted is untranslated, or needs work if it has a target. memoQ
# documents no list; the ones not seen in a real file (Edited,
# AssembledFromFragments, Rejected, MachineTranslated) are from memoQ's
# UI.
STATUS_STATES = {
    "Rejected": StateEnum.REJECTED,
    "PreTranslated": StateEnum.NEEDS_REVIEW,
    "MachineTranslated": StateEnum.NEEDS_REVIEW,
    "AssembledFromFragments": StateEnum.NEEDS_REVIEW,
    "PartiallyEdited": StateEnum.NEEDS_REVIEW,
    "Edited": StateEnum.NEEDS_REVIEW,
    "ManuallyConfirmed": StateEnum.UNREVIEWED,
    "Reviewer1Confirmed": StateEnum.FINAL,
    "Proofread": StateEnum.FINAL + 1,
}
# What a state set in Virtaal writes.
STATE_STATUSES = {
    StateEnum.EMPTY: "NotStarted",
    StateEnum.NEEDS_WORK: "NotStarted",
    StateEnum.REJECTED: "Rejected",
    StateEnum.NEEDS_REVIEW: "PartiallyEdited",
    StateEnum.UNREVIEWED: "ManuallyConfirmed",
    StateEnum.FINAL: "Reviewer1Confirmed",
    StateEnum.FINAL + 1: "Proofread",
}
# Trados's steps: memoQ has the same.
STATE = sdlxliff_compat.STATE


def state_n(unit, has_target):
    state = STATUS_STATES.get(unit.xmlelement.get(MQ + "status"))
    if state is None:
        return StateEnum.NEEDS_WORK if has_target else StateEnum.EMPTY
    return state


def set_state_n(unit, value):
    """Write the status for C{value}, a step of L{STATE} or a number in
    L{STATE_STATUSES}."""
    if value not in STATE_STATUSES:
        value = unit.get_state_id(value)
    unit.xmlelement.set(MQ + "status", STATE_STATUSES[value])


# Statuses of a translation memoQ inserted, which an edit makes the
# translator's.
INSERTED = {"PreTranslated", "MachineTranslated", "AssembledFromFragments"}


def mark_edited(unit):
    """An inserted translation the user changed is PartiallyEdited, as
    memoQ marks it, still to confirm."""
    if unit.xmlelement.get(MQ + "status") in INSERTED:
        unit.xmlelement.set(MQ + "status", "PartiallyEdited")


def is_locked(unit):
    return unit.xmlelement.get(MQ + "locked") in ("locked", "true")


def match_rate(unit):
    """The match rate memoQ inserted the translation with, or None:
    101 is a context match."""
    percent = unit.xmlelement.get(MQ + "percent")
    return percent if percent and percent != "0" else None


def last_user(unit):
    return unit.xmlelement.get(MQ + "lastchanginguser") or None


def comments(unit):
    """(user, text) of the unit's memoQ comments that aren't deleted,
    leaving out those its notes already show."""
    notes = set(unit.getnotes().split("\n"))
    found = []
    for comment in unit.xmlelement.iterfind(f"{MQ}comments/{MQ}comment"):
        text = " ".join((comment.text or "").split())
        if text and comment.get("deleted") != "true" and text not in notes:
            found.append((comment.get("creatoruser"), text))
    return found
