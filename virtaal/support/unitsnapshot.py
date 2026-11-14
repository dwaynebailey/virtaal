#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A unit's translation and state, to put back: what undoing a change
to several units at once (Pre-translate) restores. No GTK here."""

from typing import NamedTuple


class Snapshot(NamedTuple):
    rich: list | None
    """The target's trees, one for each plural form; None if it had none."""
    state: int | None
    """Its state number, if its format has states."""
    fuzzy: bool
    """Whether it was fuzzy: a PO file's empty translation can be."""


def take(unit):
    """unit's translation and state as they are now."""
    rich = None
    if unit.target is not None:
        rich = [elem.copy() for elem in unit.rich_target]
    return Snapshot(rich, unit.get_state_n() if unit.STATE else None, unit.isfuzzy())


def restore(unit, snapshot):
    """Give unit the translation and state of snapshot."""
    if snapshot.rich is None:
        unit.target = None
    else:
        unit.rich_target = [elem.copy() for elem in snapshot.rich]
    if unit.isfuzzy() != snapshot.fuzzy:
        unit.markfuzzy(snapshot.fuzzy)
    # Setting a target can change the state (XLIFF approves it).
    if snapshot.state is not None and unit.get_state_n() != snapshot.state:
        unit.set_state_n(snapshot.state)
