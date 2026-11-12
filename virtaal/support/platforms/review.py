#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Others' suggestions and comments on the units of work copies, asked
of their server as each unit is shown (#3337). No GTK here.

Only a platform that can_suggest or can_comment is asked. To keep to
the server's budget, a work copy first asks once which of its units
have suggestions or comments (Session.flagged(), one query), and again
only after TTL seconds; then a unit shown asks for its own only when
flagged, and what it was told is kept for TTL seconds. Nothing is asked
while the server's budget is down to RESERVE requests. A server too old
for suggestions or comments, or one that doesn't answer, shows none:
quietly, as a unit without any.

Accepting a suggestion is the editor's (it becomes the unit's text, to
go with the next Submit); dismissing one and adding a comment go to the
server at once. Dismissing is offered until the server refuses it,
which the side record keeps, for every work copy of that project.
"""

import time

from . import DISMISS, ON_SOURCE, ON_TRANSLATION, SOURCE_REPORT, UNSUPPORTED

TTL = 600
"""Seconds what the server said is kept."""
RESERVE = 20
"""Requests of the server's budget left for Submit and the rest."""

NOTHING = ((), ())


class Reviews:
    """What the servers of work copies said of their units' suggestions
    and comments. platform_of(copy) is its Platform (or None), server_of
    (copy) its Server (or None)."""

    clock = staticmethod(time.monotonic)

    def __init__(self, platform_of, server_of):
        self.platform_of = platform_of
        self.server_of = server_of
        self._sessions = {}
        self._units = {}
        """{(work file, unit id): (when, suggestions, comments)}."""
        self._flags = {}
        """{work file: when its flags were asked}."""
        self._asking = {}
        """{(work file, unit id): callbacks waiting for its reply}."""
        self.cannot_comment = set()
        """The servers (platform id, URL) found too old to add comments."""
        self.cannot_dismiss = set()
        """The projects (platform id, URL, project) whose server refused
        this user a rejection."""

    def clear(self):
        """Forgets what servers said, as after an Update or a Submit."""
        self._units = {}
        self._flags = {}

    # Who can do what #

    def _platform(self, copy):
        return self.platform_of(copy) if copy is not None else None

    def can_suggest(self, copy):
        platform = self._platform(copy)
        return bool(platform and platform.can_suggest)

    def can_comment(self, copy):
        """Whether comments can be added to copy's units."""
        platform = self._platform(copy)
        key = (copy.record['platform'], copy.record['server']) if copy is not None else None
        return bool(platform and platform.can_comment) and key not in self.cannot_comment

    def _project(self, copy):
        record = copy.record
        return record['platform'], record['server'], record['project']

    def can_dismiss(self, copy):
        """Rejecting is offered until the server refuses it, on that
        project (kept in the side record too)."""
        refused = DISMISS in copy.refused() or self._project(copy) in self.cannot_dismiss
        return self.can_suggest(copy) and not refused

    def wanted(self, copy):
        platform = self._platform(copy)
        return bool(platform and (platform.can_suggest or platform.can_comment))

    def session(self, copy):
        key = (copy.record['platform'], copy.record['server'])
        if key not in self._sessions:
            server = self.server_of(copy)
            if server is None:
                return None
            self._sessions[key] = server.session()
        return self._sessions[key]

    def _affordable(self, session):
        budget = session.budget()
        return budget is None or budget.left > RESERVE

    # Reading #

    def known(self, copy, r):
        """(suggestions, comments) the server said of units[r] not more
        than TTL seconds ago, or None."""
        found = self._units.get((copy.path, copy.units[r]['id']))
        if found is None or self.clock() - found[0] > TTL:
            return None
        return found[1:]

    def fetch(self, copy, r, on_done):
        """on_done(suggestions, comments) of units[r], from what is known
        or else from the server: empty when it has none, can't say, or
        the budget is low."""
        known = self.known(copy, r)
        if known is not None:
            on_done(*known)
            return
        key = (copy.path, copy.units[r]['id'])
        waiting = self._asking.setdefault(key, [])
        waiting.append(on_done)
        if len(waiting) > 1:
            return
        session = self.session(copy)
        if session is None or not self.wanted(copy) or not self._affordable(session):
            self._answer(key, NOTHING)
            return

        def flagged():
            self._read(copy, r, session, lambda found: self._answer(key, found))

        if self.clock() - self._flags.get(copy.path, -TTL - 1) > TTL:
            self._ask_flags(copy, session, flagged)
        else:
            flagged()

    def _answer(self, key, found):
        self._units[key] = (self.clock(), *found)
        for on_done in self._asking.pop(key, []):
            on_done(*found)

    def _ask_flags(self, copy, session, then):
        record = copy.record

        def got(suggested, commented):
            copy.set_flags(suggested, commented)
            self._flags[copy.path] = self.clock()
            then()

        def failed(_error):
            # The flags as last known.
            self._flags[copy.path] = self.clock()
            then()

        session.flagged(record['project'], record.get('component'), record['language'], got, failed)

    def _read(self, copy, r, session, on_done):
        entry = copy.units[r]
        found = {'suggestions': (), 'comments': ()}

        def comments():
            if not (self._platform(copy).can_comment and entry.get('has_comment')):
                on_done((found['suggestions'], ()))
                return
            session.comments(
                entry['id'],
                entry.get('source_id'),
                lambda listed: on_done((found['suggestions'], tuple(listed))),
                lambda _error: on_done((found['suggestions'], ())),
            )

        def got_suggestions(listed):
            found['suggestions'] = tuple(listed)
            comments()

        if self.can_suggest(copy) and entry.get('has_suggestion'):
            session.suggestions(entry['id'], got_suggestions, lambda _error: comments())
        else:
            comments()

    # Changing #

    def _replace(self, copy, r, suggestions=None, comments=None):
        known = self.known(copy, r) or NOTHING
        key = (copy.path, copy.units[r]['id'])
        self._units[key] = (
            self.clock(),
            known[0] if suggestions is None else tuple(suggestions),
            known[1] if comments is None else tuple(comments),
        )

    def dismiss(self, copy, r, suggestion, on_done, on_error):
        """Rejects a suggestion for units[r] on the server: on_done(), or
        on_error(PlatformError)."""
        session = self.session(copy)

        def done(_reply):
            left = [s for s in (self.known(copy, r) or NOTHING)[0] if s.id != suggestion.id]
            copy.units[r]['has_suggestion'] = bool(left)
            self._replace(copy, r, suggestions=left)
            on_done()

        def failed(error):
            if error.refused == DISMISS:
                self.cannot_dismiss.add(self._project(copy))
                copy.refuse(DISMISS)
            on_error(error)

        session.dismiss_suggestion(suggestion.id, done, failed)

    def scope(self, copy, about_source):
        """The scope of a comment on copy's unit: about this translation,
        or a report of a problem with the source text (a comment on the
        source where the project takes no reports)."""
        if not about_source:
            return ON_TRANSLATION
        return SOURCE_REPORT if copy.record.get('source_review') else ON_SOURCE

    def add_comment(self, copy, r, text, about_source, on_done, on_error):
        """Adds a comment to units[r] on the server: on_done(the Comment),
        or on_error(PlatformError)."""
        session = self.session(copy)

        def done(comment):
            copy.units[r]['has_comment'] = True
            known = self.known(copy, r)
            if known is not None:
                self._replace(copy, r, comments=[*known[1], comment])
            on_done(comment)

        def failed(error):
            if error.kind == UNSUPPORTED:
                self.cannot_comment.add((copy.record['platform'], copy.record['server']))
            on_error(error)

        scope = self.scope(copy, about_source)
        session.add_comment(copy.units[r]['id'], text, scope, done, failed)
