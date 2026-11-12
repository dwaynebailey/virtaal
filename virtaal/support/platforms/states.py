#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A translation platform's states on units whose file can't hold them
all (#3337). No GTK here.

A platform such as Weblate keeps six states, but a PO file only holds
untranslated, fuzzy and translated. A unit of a platform's file is
given a class of its own (adopt()) with all six in its STATE: the
state the platform gave it, or the one chosen in Virtaal, is shown
while the file still holds the nearest state it can store, and saving
writes that nearest state (Approved becomes translated; Needs review
and Rejected become fuzzy). The side record (workcopy.py) keeps the
finer state. A change made outside Virtaal that the file shows, such
as a unit no longer fuzzy, wins over the side record.

A unit the platform won't let anyone change is locked (locked()):
catxliff_compat.is_locked() reads it, so it shows read-only as a
locked Trados or memoQ segment does.

A unit this user may not approve (a project without reviews, or one
whose server refused an approval) isn't reviewable(): the state button
doesn't offer Reviewed for it, though a unit already approved still
shows so.
"""

from translate.storage.workflow import StateEnum

PLATFORM_STATES = (
    StateEnum.EMPTY,
    StateEnum.NEEDS_WORK,
    StateEnum.REJECTED,
    StateEnum.NEEDS_REVIEW,
    StateEnum.UNREVIEWED,
    StateEnum.FINAL,
)
"""The states a platform's unit can show, in order."""

_STATE = {
    state: (state, end)
    for state, end in zip(PLATFORM_STATES, (*PLATFORM_STATES[1:], StateEnum.MAX), strict=True)
}

_classes = {}


def coarse(state):
    """What every format holds of a state: 0 untranslated, 1 needs some
    work (fuzzy), 2 translated."""
    if state is None or state < StateEnum.NEEDS_WORK:
        return 0
    return 1 if state < StateEnum.UNREVIEWED else 2


def nearest(stored, state, target):
    """Of the states stored (None: all of PLATFORM_STATES), the nearest to
    state for a unit with target (its forms): the state itself, else the
    nearest below it, else above it, but never EMPTY for a unit with
    text. A state such as Needs work, where only EMPTY is below, goes as
    the next state up."""
    if stored is None or state is None or state in stored:
        return state
    choices = [s for s in stored if s != StateEnum.EMPTY] if any(target) else list(stored)
    below = [s for s in choices if s <= state]
    if below:
        return max(below)
    above = [s for s in choices if s > state]
    return min(above) if above else state


def _stored(base, value):
    """The state base's STATE can store nearest to value: the start of
    its range that holds value."""
    for start, end in base.STATE.values():
        if start <= value < end:
            return start
    return value


def _layer(base):
    """base with the platform's six states."""
    found = _classes.get(base)
    if found is not None:
        return found
    STATE = {k: v for k, v in base.STATE.items() if v[0] < 0}
    STATE.update(_STATE)

    def get_state_n(self):
        native = base.get_state_n(self)
        state = self.__dict__.get('_platform_state')
        if state is None or native < 0 or coarse(state) != coarse(native):
            return native
        return state

    def set_state_n(self, value):
        base.set_state_n(self, _stored(base, value))
        self._platform_state = value if value >= 0 else None

    found = type(
        base.__name__,
        (base,),
        {
            'STATE': STATE,
            'get_state_n': get_state_n,
            'set_state_n': set_state_n,
            '__module__': base.__module__,
            '__qualname__': base.__qualname__,
        },
    )
    _classes[base] = found
    return found


def is_adopted(unit):
    return type(unit) in _classes.values()


def adopt(unit, state=None):
    """Gives unit the platform's six states, showing state (one of
    PLATFORM_STATES, or None for the file's own) while the file's state
    agrees with it."""
    if not is_adopted(unit):
        unit.__class__ = _layer(type(unit))
    unit._platform_state = state


def lock(unit, locked=True):
    unit.platform_locked = locked


def locked(unit):
    return bool(getattr(unit, 'platform_locked', False))


def allow_review(unit, allowed=True):
    unit.platform_no_review = not allowed


def reviewable(unit):
    """Whether Reviewed is this user's to choose for unit."""
    return not getattr(unit, 'platform_no_review', False)
