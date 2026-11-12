#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Opening several translations of a server at once (#3337): the
languages of a component chosen together in the browser, or one
language in every component of a project (components_with()). No GTK
here.

Each downloads into its own work copy with its side record, as one
does (workcopy.create()), one after the other (Downloads); a language
whose work copy holds work not yet submitted opens as it is. Each
download is a file and its units, so a server's budget is kept as
Submit keeps it (submit.Pacer): below its reserve, Downloads waits for
the count to start again when that is near, else stops, the rest not
downloaded. The work files then open together, as one list of units
with the Files Panel (MainController.open_files()); those that failed
are listed, except those the server doesn't have (a component without
the language), left out quietly.
"""

from collections import namedtuple

from . import HTTP, PlatformError, workcopy
from .submit import BUDGET, CANCELLED, Pacer, Submission, _later


class Wanted(namedtuple('Wanted', 'project component language names', defaults=(None,))):
    """A language to open: the slugs of its project and component (None
    for a platform without components), its code, and the names of the
    three as shown (project, component or None, language), to list it
    by if it fails."""

    __slots__ = ()

    def _names(self):
        return self.names or (self.project, self.component, self.language)

    def location(self):
        project, component, _language = self._names()
        return '%s/%s' % (project, component) if component else project

    @property
    def label(self):
        from . import file_label

        return file_label(self.location(), self._names()[2])


def title_of(wanted):
    """The project and component of several Wanted, or their projects
    when the components differ."""
    locations = {w.location() for w in wanted}
    if len(locations) == 1:
        return locations.pop()
    return ', '.join(sorted({w._names()[0] for w in wanted}))


REQUESTS = 2
"""Requests a download takes at least: the file and its units."""


class Opened:
    """What Downloads did."""

    def __init__(self):
        self.paths = []
        """The work files to open, in the order wanted."""
        self.failures = []
        """(Wanted, PlatformError) of those not downloaded."""
        self.missing = []
        """(Wanted, PlatformError) of those the server doesn't have
        (404): a component without the language, left out quietly."""
        self.stop = None
        """None, CANCELLED, or BUDGET when the server's budget ran out."""
        self.reset_in = None
        """With BUDGET, the seconds until the budget starts again, if
        known."""


class Downloads:
    """Downloads each of wanted (Wanted) from server through session,
    one at a time. on_progress(number, total) comes before each;
    on_done(Opened) ends it."""

    schedule = staticmethod(_later)

    def __init__(self, server, session, wanted, on_progress, on_done):
        self.server = server
        self.session = session
        self.wanted = list(wanted)
        self.on_progress = on_progress
        self.on_done = on_done
        platform = server.platform
        self.pacer = Pacer(platform.rate_limit, Submission.RESERVE, Submission.MAX_WAIT)
        self.result = Opened()
        self.cancelled = False
        self.finished = False
        self._next = 0
        self._paths = {}

    def start(self):
        self._go()

    def cancel(self):
        """Stops at once: nothing opens."""
        self.cancelled = True
        self.session.cancel()
        self._finish(CANCELLED)

    def _go(self):
        if self.finished:
            return
        if self._next == len(self.wanted):
            self._finish()
            return
        wanted = self.wanted[self._next]
        server = self.server
        copy = workcopy.existing(
            server.platform.id, server.url, wanted.project, wanted.component, wanted.language
        )
        if copy is not None and copy.has_local_changes():
            self._got(copy.path)
            return
        delay = self.pacer.delay(self.session.budget())
        if delay is None:
            self.result.reset_in = self.pacer.reset_in(self.session.budget())
            self._finish(BUDGET)
            return
        if delay > 0:
            self.schedule(delay, self._go)
            return
        self.on_progress(self._next + 1, len(self.wanted))
        for _request in range(REQUESTS):
            self.pacer.note()
        self.session.download(
            wanted.project, wanted.component, wanted.language, self._downloaded, self._failed
        )

    def _downloaded(self, download):
        wanted = self.wanted[self._next]
        server = self.server
        try:
            copy = workcopy.create(
                server.platform,
                server.url,
                wanted.project,
                wanted.component,
                wanted.language,
                download,
            )
        except OSError as error:
            self._failed(PlatformError(HTTP, detail=str(error)))
            return
        self._got(copy.path)

    def _got(self, path):
        self._paths[self._next] = path
        self._next += 1
        self._go()

    def _failed(self, error):
        if self.finished:
            return
        if error.kind == HTTP and error.status == 429:
            self.result.reset_in = self.pacer.reset_in(self.session.budget())
            self._finish(BUDGET)
            return
        # A 404 comes as NOT_PLATFORM from Weblate's client.
        failed = self.result.missing if error.status == 404 else self.result.failures
        failed.append((self.wanted[self._next], error))
        self._next += 1
        self._go()

    def _finish(self, stop=None):
        if self.finished:
            return
        self.finished = True
        self.result.stop = stop
        if stop == BUDGET:
            left = self.wanted[self._next :]
            self.result.failures += [(wanted, PlatformError(HTTP, 429)) for wanted in left]
        if stop != CANCELLED:
            self.result.paths = [self._paths[n] for n in sorted(self._paths)]
        self.on_done(self.result)


def components_with(session, project, language, on_done, on_error):
    """on_done([(component Item, language Item)]): the components of
    project, glossaries left out, that have language, in order."""
    found = []

    def got_components(components):
        wanted = [c for c in components if not c.glossary]
        ask(wanted, 0)

    def ask(components, n):
        if n == len(components):
            on_done(found)
            return
        component = components[n]

        def got_languages(languages):
            item = next((i for i in languages if i.slug == language), None)
            if item is not None:
                found.append((component, item))
            ask(components, n + 1)

        session.languages(project, component.slug, got_languages, on_error)

    session.components(project, got_components, on_error)
