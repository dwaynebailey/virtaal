#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Weblate as a translation platform (virtaal.support.platforms): its
token, its address rules, wlc's servers, its REST replies as Items, and
a translation's download with its units and their states (#3337).

Weblate's states and Virtaal's (translate-toolkit's StateEnum):

  Weblate                      code  Virtaal
  Empty                           0  Untranslated (EMPTY)
  Needs editing                  10  Needs work (NEEDS_WORK)
  Needs rewriting                11  Rejected (REJECTED)
  Needs checking                 12  Needs review (NEEDS_REVIEW)
  Translated, Waiting for review 20  Translated (UNREVIEWED)
  Approved                       30  Reviewed (FINAL)
  Read-only                     100  the file's own state, locked

Submit sends each unit's target and state with PATCH /api/units/<id>/,
{"target": [every form], "state": code}: 0 with no text, never 0 with
some (10 instead). A server older than 2026.x refuses 11 and 12 (400):
those go again as 10, and Virtaal keeps its finer state. Approving (30)
takes the project's reviews and the user's right to review: else 400,
naming the state, which says the user may not review (REVIEW); a 403
is no right to change the unit at all.

Review: a unit's suggestions (GET, POST /api/units/<id>/suggestions/;
DELETE /api/suggestions/<id>/) are Weblate 2026.8's, adding a comment
(POST /api/units/<id>/comments/) 5.12's and reading them 5.15's. There
is no version to ask, so the first reply tells: 404 where there's no
such request (405 for reading comments on 5.12 to 5.14), after which
this server's sessions know without asking (Platform.features). A
comment on the source text is a "global" one, a source report a
"report" (which needs the project's source reviews), and both are on
the source unit, whose comments a unit's are read with.
"""

from translate.storage.workflow import StateEnum

from virtaal.support.platforms import (
    HTTP,
    NOT_PLATFORM,
    ON_SOURCE,
    ON_TRANSLATION,
    REVIEW,
    SOURCE_REPORT,
    UNSUPPORTED,
    Comment,
    Download,
    Item,
    Platform,
    PlatformError,
    RemoteUnit,
    Session,
    Suggestion,
)

from . import client as weblate
from .config import KeyFile, read_wlc, wlc_config_path

READ_ONLY = 100
APPROVED = 30
_NEWER_STATES = (11, 12)

_SCOPES = {ON_TRANSLATION: 'translation', ON_SOURCE: 'global', SOURCE_REPORT: 'report'}
"""Weblate's comment scopes: a report needs the project's source reviews."""

# Platform.features: what a server was found not to have.
SUGGESTIONS = 'suggestions'
COMMENTS_LISTED = 'comments-listed'
COMMENTS_ADDED = 'comments-added'

_TO_VIRTAAL = {
    0: StateEnum.EMPTY,
    10: StateEnum.NEEDS_WORK,
    11: StateEnum.REJECTED,
    12: StateEnum.NEEDS_REVIEW,
    20: StateEnum.UNREVIEWED,
    30: StateEnum.FINAL,
}
_TO_WEBLATE = {state: code for code, state in _TO_VIRTAAL.items()}


def virtaal_state(code):
    """Virtaal's state for a Weblate state code, or None (read-only, or
    a code this Virtaal doesn't know)."""
    return _TO_VIRTAAL.get(code)


def weblate_state(state):
    """Weblate's state code for one of Virtaal's states: the nearest
    below it for a state between them."""
    below = [s for s in _TO_WEBLATE if s <= state]
    return _TO_WEBLATE[max(below)] if below else 0


def submit_code(state, target):
    """The state code to submit for a unit in Virtaal's state with
    target (its forms): Weblate wants 0 for no text, and only for it."""
    if not any(target):
        return 0
    code = 20 if state is None else weblate_state(state)
    return code or 10


def link_id(url):
    """The number at the end of an API link (".../units/42/"), or None."""
    tail = str(url or '').rstrip('/').rpartition('/')[2]
    return int(tail) if tail.isdigit() else None


def user_name(url):
    """The user name in a user's API link (".../users/anna/"), or None."""
    from urllib import parse

    tail = str(url or '').rstrip('/').rpartition('/')[2]
    return parse.unquote(tail) or None


def remote_unit(unit):
    """A RemoteUnit for a unit as the API gives it."""
    code = unit.get('state')
    source_id = link_id(unit.get('source_unit'))
    return RemoteUnit(
        id=unit.get('id'),
        position=unit.get('position') or 0,
        context=unit.get('context') or '',
        source=[str(s) for s in unit.get('source') or ['']],
        target=[str(s) for s in unit.get('target') or ['']],
        state=virtaal_state(code),
        code=code,
        last_updated=unit.get('last_updated'),
        read_only=code == READ_ONLY or bool(unit.get('read_only')),
        has_suggestion=bool(unit.get('has_suggestion')),
        has_comment=bool(unit.get('has_comment')),
        source_id=None if source_id == unit.get('id') else source_id,
    )


def _suggestion(data):
    return Suggestion(
        data.get('id'), [str(t) for t in data.get('target') or ['']], user_name(data.get('user'))
    )


def _comment(data):
    return Comment(data.get('id'), str(data.get('comment') or ''), user_name(data.get('user')))


def _missing(error):
    """Whether an error says the server has no such request: 404, or
    405 for a method it doesn't take there."""
    return (error.kind == NOT_PLATFORM and error.status == 404) or error.status == 405


def file_path(filename, filemask, language):
    """A translation's file path in its component's repository, as
    Weblate has it ("values-af/strings.xml"): its filename, else its
    component's filemask with the language code for the "*"."""
    if filename:
        return filename
    if filemask and '*' in filemask:
        return filemask.replace('*', language, 1)
    return language


class WeblateSession(Session):
    """A Client's replies as the browse dialog wants them."""

    def __init__(self, client, features=None):
        self.client = client
        self.missing = set() if features is None else features
        """What the server was found not to have (SUGGESTIONS, ...),
        shared by the sessions of one server."""

    def test_connection(self, on_done, on_error):
        self.client.test_connection(on_done, on_error)

    def projects(self, query, cursor, on_page, on_error):
        # Weblate's project list can't be searched: query is None.
        def page(results, following):
            on_page([Item(p.get('name', ''), p.get('slug', '')) for p in results], following)

        self.client.projects_page(cursor, page, on_error)

    def components(self, project, on_done, on_error):
        found = []

        def page(results):
            for c in results:
                name = c.get('name', '')
                found.append(
                    Item(name, weblate.component_slug(c), glossary=bool(c.get('is_glossary')))
                )

        self.client.components(project, page, lambda _count: on_done(found), on_error)

    def languages(self, project, component, on_done, on_error):
        found = []

        def page(results):
            for t in results:
                language = t.get('language') or {}
                code = t.get('language_code') or language.get('code', '')
                translated = float(t.get('translated_percent') or 0) / 100
                found.append(Item(language.get('name') or code, code, translated))

        self.client.translations(project, component, page, lambda _count: on_done(found), on_error)

    def download(self, project, component, language, on_done, on_error):
        # The translation (its file name and component), the file, the
        # template of a monolingual component, then the units.
        client = self.client

        def translation(data):
            info = data['component']
            filename = file_path(data.get('filename'), info.get('filemask'), language)
            names = {
                'project': (info.get('project') or {}).get('name') or project,
                'component': info.get('name') or component,
            }
            template = info.get('template') or ''
            about = info.get('project') or {}
            options = {
                'review': about.get('translation_review'),
                'source_review': about.get('source_review'),
            }

            def units(body, base):
                found = []
                client.units(
                    project,
                    component,
                    language,
                    lambda results: found.extend(remote_unit(u) for u in results),
                    lambda _count: on_done(Download(filename, body, base, found, names, options)),
                    on_error,
                )

            def file(body):
                if template:
                    client.monolingual_base(
                        project, component, lambda base: units(body, (template, base)), on_error
                    )
                else:
                    units(body, None)

            client.file(project, component, language, file, on_error)

        client.translation(project, component, language, translation, on_error)

    def locked(self, project, component, on_done, on_error):
        self.client.locked(project, component, on_done, on_error)

    def unit_count(self, project, component, language, on_done, on_error):
        self.client.unit_count(project, component, language, on_done, on_error)

    def changed_since(self, project, component, language, since, on_done, on_error):
        found = []
        self.client.units(
            project,
            component,
            language,
            lambda results: found.extend(remote_unit(u) for u in results),
            lambda _count: on_done(found),
            on_error,
            query='changed:>="%s"' % since,
        )

    def submit_unit(self, unit_id, target, code, on_done, on_error):
        def written(data):
            stored = data.get('state') if isinstance(data, dict) else None
            on_done(stored if isinstance(stored, int) else code)

        def failed(error):
            if error.status == 400 and code in _NEWER_STATES:
                # An older Weblate: needs editing is the nearest it has.
                self.client.patch_unit(unit_id, {'target': target, 'state': 10}, written, on_error)
                return
            if code == APPROVED and _refused_state(error):
                error.refused = REVIEW
            on_error(error)

        self.client.patch_unit(unit_id, {'target': list(target), 'state': code}, written, failed)

    def budget(self):
        return self.client.budget()

    # Review #

    def _ask(self, feature, request, on_error, also=()):
        """Runs request(on_error) unless the server lacks feature; a reply
        saying it has no such request notes that it lacks it (and also)."""
        if feature in self.missing:
            from gi.repository import GLib

            GLib.idle_add(lambda: on_error(PlatformError(UNSUPPORTED)) and False)
            return

        def failed(error):
            if _missing(error):
                self.missing.add(feature)
                if error.status == 404:
                    self.missing.update(also)
                error = PlatformError(UNSUPPORTED, error.status, error.detail)
            on_error(error)

        request(failed)

    def flagged(self, project, component, language, on_done, on_error):
        suggested, commented = set(), set()

        def page(results):
            for unit in results:
                if unit.get('has_suggestion'):
                    suggested.add(unit.get('id'))
                if unit.get('has_comment'):
                    commented.add(unit.get('id'))

        self.client.units(
            project,
            component,
            language,
            page,
            lambda _count: on_done(suggested, commented),
            on_error,
            query='has:suggestion OR has:comment',
        )

    def suggestions(self, unit_id, on_done, on_error):
        found = []

        def request(failed):
            self.client.unit_list(
                unit_id,
                'suggestions',
                lambda results: found.extend(_suggestion(r) for r in results),
                lambda _count: on_done(found),
                failed,
            )

        self._ask(SUGGESTIONS, request, on_error)

    def submit_suggestion(self, unit_id, target, on_done, on_error):
        def request(failed):
            data = {'target': list(target)}
            self.client.add_to_unit(unit_id, 'suggestions', data, lambda _r: on_done(None), failed)

        self._ask(SUGGESTIONS, request, on_error)

    def dismiss_suggestion(self, suggestion_id, on_done, on_error):
        def request(failed):
            self.client.delete_suggestion(suggestion_id, lambda _r: on_done(None), failed)

        self._ask(SUGGESTIONS, request, on_error)

    def comments(self, unit_id, source_id, on_done, on_error):
        found = []
        ids = [unit_id] + ([source_id] if source_id is not None else [])

        def request(failed):
            def next_unit(_count=None):
                if not ids:
                    on_done(found)
                    return
                self.client.unit_list(
                    ids.pop(0),
                    'comments',
                    lambda results: found.extend(_comment(r) for r in results),
                    next_unit,
                    failed,
                )

            next_unit()

        self._ask(COMMENTS_LISTED, request, on_error, also=(COMMENTS_ADDED,))

    def add_comment(self, unit_id, text, scope, on_done, on_error):
        data = {'comment': text, 'scope': _SCOPES[scope]}

        def request(failed):
            self.client.add_to_unit(
                unit_id, 'comments', data, lambda reply: on_done(_comment(reply or {})), failed
            )

        self._ask(COMMENTS_ADDED, request, on_error)

    def cancel(self):
        self.client.cancel()


def _refused_state(error):
    """Whether Weblate refused a unit's state: 400 naming the state, as
    for approving without the right to review (a 403 is no right to
    change the unit at all)."""
    return error.kind == HTTP and error.status == 400 and 'state' in getattr(error, 'fields', ())


class WeblatePlatform(Platform):
    id = 'weblate'
    name = _('Weblate')
    has_components = True
    supports_search = False
    can_submit = True
    can_suggest = True
    can_comment = True
    rate_limit = (5000, 3600)
    """API_RATELIMIT_USER's default; replies say what is left."""
    # l10n: Imports the servers and tokens of wlc, Weblate's command-line client
    import_label = _('_Import from wlc')

    client_factory = weblate.Client

    def __init__(self, path=None):
        self.keys = KeyFile(path)
        self.budgets = {}
        """What each server last said of its budget: one for every
        session of a server and token."""
        self.features = {}
        """{API URL: what the server was found not to have}, for every
        session of a server."""

    def credential_fields(self):
        from virtaal.views.widgets.settingsdialog import SECRET, Field

        # l10n: The API token a Weblate server gives each user, on its profile page
        return (Field('token', _('_Token:'), SECRET, required=True),)

    def normalise_url(self, text):
        return weblate.api_url(text)

    def url_problem(self, text):
        if weblate.unencrypted(text):
            # l10n: Shown for an address starting http:// instead of https://
            return _('Use https: the token would be sent unencrypted.')
        return None

    def not_platform_message(self):
        return _('No Weblate server answered at this address.')

    def load_servers(self):
        self.keys.load()
        servers = [(url, {'token': token}) for url, token in self.keys.servers]
        return servers, self.keys.last_url

    def save_servers(self, servers, last_url):
        self.keys.servers = [(url, credentials.get('token', '')) for url, credentials in servers]
        self.keys.last_url = last_url
        self.keys.save()

    def session(self, url, credentials):
        token = credentials.get('token', '')
        client = self.client_factory(url, token, budgets=self.budgets)
        return WeblateSession(client, self.features.setdefault(client.api, set()))

    def state_code(self, state, target):
        return submit_code(state, target)

    def can_import(self):
        return wlc_config_path() is not None

    def import_servers(self):
        return [(url, {'token': token}) for url, token in read_wlc()]
