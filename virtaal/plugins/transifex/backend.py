#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Transifex as a translation platform (virtaal.support.platforms): its
token, its organisations folded into the project list, a resource's
download with its strings and their states, and Submit through its bulk
PATCH.

Transifex holds organisation > project > resource > language. Virtaal
lists "Organisation / Project" as one project (slug "org/project"), so
the browser keeps its project > component > language: a resource is a
component.

Transifex's states and Virtaal's (translate-toolkit's StateEnum):

  Transifex                 code          Virtaal
  no translation            untranslated  Untranslated (EMPTY)
  translated                translated    Translated (UNREVIEWED)
  reviewed                  reviewed      Reviewed (FINAL)
  proofread                 proofread     Reviewed (FINAL)

Transifex has no fuzzy, needs-review or rejected string
(Platform.states): such a unit goes as translated, and Virtaal keeps
its state. Submit PATCHes {"strings": {CLDR plural: text}, "reviewed":
bool}, with null strings for none, up to 150 a request (bulk); it never
sets proofread, a proofreader's second review.

Every resource opens (formats.py): one in a format Virtaal reads
downloads as its file; any other, or one whose plurals its file can't
hold as Virtaal reads them, is built as XLIFF 2 from its strings, a
unit a string by its hash. Submit and Update work a string at a time
either way; Update builds the file again when strings come or go.
tx's servers and tokens (config.read_tx()) can be imported.

There is no lock and no change feed for reviews: only the date
translated can be filtered on, so changed_since() reads every string
of the resource (1000 a page), each with the latest of its dates
translated, reviewed and proofread as its last_updated.
"""

import os

from gi.repository import GLib
from translate.storage.workflow import StateEnum

from virtaal.support.platforms import (
    REVIEW,
    Download,
    Item,
    Platform,
    RemoteUnit,
    Session,
    workcopy,
)

from . import client as transifex
from . import formats
from .config import KeyFile, read_tx, tx_config_path

UNTRANSLATED, TRANSLATED, REVIEWED, PROOFREAD = (
    'untranslated',
    'translated',
    'reviewed',
    'proofread',
)

_TO_VIRTAAL = {
    UNTRANSLATED: StateEnum.EMPTY,
    TRANSLATED: StateEnum.UNREVIEWED,
    REVIEWED: StateEnum.FINAL,
    PROOFREAD: StateEnum.FINAL,
}

CLDR = ('zero', 'one', 'two', 'few', 'many', 'other')
"""CLDR's plural categories, in the order of a file's plural forms."""


def virtaal_state(code):
    return _TO_VIRTAAL.get(code)


def code_of(attributes):
    """A translation's state code from its attributes."""
    if not attributes.get('strings'):
        return UNTRANSLATED
    if attributes.get('proofread'):
        return PROOFREAD
    return REVIEWED if attributes.get('reviewed') else TRANSLATED


def last_updated(attributes):
    """The latest of a translation's dates translated, reviewed and
    proofread (the same text format each), or None."""
    dates = [attributes.get('datetime_' + n) for n in ('translated', 'reviewed', 'proofread')]
    return max((d for d in dates if d), default=None)


def categories(rules):
    """The plural categories of a language's plural_rules (or of a
    string's strings), in CLDR's order."""
    return [c for c in CLDR if c in (rules or {})] or ['other']


def forms(strings, names):
    """A string's forms in the order of names (plural categories)."""
    if not strings:
        return [''] * len(names)
    return [str(strings.get(name) or '') for name in names]


def context_text(value):
    """A source string's context: Transifex gives a text or a list."""
    if isinstance(value, list):
        return ''.join(str(v) for v in value)
    return str(value or '')


def project_id(project):
    """Transifex's id of a project as Virtaal lists it ("org/project")."""
    organisation, _sep, slug = project.partition('/')
    return 'o:%s:p:%s' % (organisation, slug)


def resource_id(project, resource):
    return '%s:r:%s' % (project_id(project), resource)


def _related(data, name):
    """The id of an object's relationship name, or ''."""
    relation = ((data.get('relationships') or {}).get(name) or {}).get('data') or {}
    return relation.get('id') or ''


def _language(ident):
    return ident[2:] if ident.startswith('l:') else ident


def file_paths(file_format, slug, language, source, opens=None):
    """(the work file's path, the source file's path or None) of a
    resource that opens as opens (formats.mode(), by default as a
    resource without plurals does)."""
    android = language.replace('_', '-r', 1) if '_' in language else language
    names = {'slug': slug, 'lang': language, 'source': source, 'android': android}
    return formats.paths(file_format, opens or formats.mode(file_format, False), names)


def _string_hash(string):
    """A resource string's hash: its id's last part."""
    about = string.get('attributes') or {}
    return about.get('string_hash') or str(string.get('id') or '').rpartition(':s:')[2]


def _idle(callback, *args):
    GLib.idle_add(lambda: callback(*args) and False)


class TransifexSession(Session):
    """A Client's replies as the platform layer wants them."""

    def __init__(self, client, platform):
        self.client = client
        self.plurals = platform.plurals.setdefault(client.api, {})
        """{language code: its plural categories}, for every session of
        the server."""
        self.formats = platform.formats.setdefault(client.api, {})
        """{resource id: its i18n_format}."""
        self.pluralized = {}
        """{translation id: whether its string has plurals}, as read."""

    def test_connection(self, on_done, on_error):
        def counted(organisations):
            total = [0]
            left = list(organisations)

            def next_one():
                if not left:
                    on_done(total[0])
                    return
                slug, _name = left.pop(0)
                self.client.get_list(
                    'projects',
                    {'filter[organization]': 'o:' + slug},
                    lambda data, _included: total.__setitem__(0, total[0] + len(data)),
                    next_one,
                    on_error,
                )

            next_one()

        self._organisations(counted, on_error)

    def _organisations(self, on_done, on_error):
        found = []

        def page(data, _included):
            for org in data:
                attributes = org.get('attributes') or {}
                slug = attributes.get('slug') or _language(org.get('id', ''))
                found.append((slug, attributes.get('name') or slug))

        self.client.get_list('organizations', None, page, lambda: on_done(found), on_error)

    def projects(self, query, cursor, on_page, on_error):
        # The organisations, then each one's projects a page at a time.
        if cursor is None:
            self._organisations(
                lambda orgs: self._project_page(orgs, 0, None, on_page, on_error), on_error
            )
        else:
            self._project_page(*cursor, on_page, on_error)

    def _project_page(self, organisations, at, url, on_page, on_error):
        if at >= len(organisations):
            on_page([], None)
            return
        slug, name = organisations[at]
        url = url or self.client.url('projects', {'filter[organization]': 'o:' + slug})

        def page(data, following):
            items = []
            for project in data:
                attributes = project.get('attributes') or {}
                own = attributes.get('slug', '')
                label = '%s / %s' % (name, attributes.get('name') or own)
                items.append(Item(label, '%s/%s' % (slug, own)))
            if following:
                cursor = (organisations, at, following)
            elif at + 1 < len(organisations):
                cursor = (organisations, at + 1, None)
            else:
                cursor = None
            if not items and cursor is not None:
                self._project_page(*cursor, on_page, on_error)
                return
            on_page(items, cursor)

        self.client.get_page(url, page, on_error)

    def components(self, project, on_done, on_error):
        found = []

        def page(data, _included):
            for resource in data:
                attributes = resource.get('attributes') or {}
                slug = attributes.get('slug', '')
                file_format = _related(resource, 'i18n_format')
                self.formats[resource.get('id')] = file_format
                found.append(Item(attributes.get('name') or slug, slug, info=file_format))

        params = {'filter[project]': project_id(project)}
        self.client.get_list('resources', params, page, lambda: on_done(found), on_error)

    def _project_languages(self, project, on_done, on_error):
        """on_done({code: name}, the project's source language code)."""
        names = {}

        def page(data, _included):
            for language in data:
                attributes = language.get('attributes') or {}
                code = attributes.get('code') or _language(language.get('id', ''))
                names[code] = attributes.get('name') or code
                if 'plural_rules' in attributes:
                    self.plurals[code] = categories(attributes['plural_rules'])

        def got_project(data):
            source = _language(_related(data, 'source_language'))
            path = 'projects/%s/languages' % project_id(project)
            self.client.get_list(path, None, page, lambda: on_done(names, source), on_error)

        self.client.get_one('projects/' + project_id(project), got_project, on_error)

    def languages(self, project, component, on_done, on_error):
        def stats(names, source):
            found = []

            def page(data, _included):
                for item in data:
                    code = _language(_related(item, 'language'))
                    attributes = item.get('attributes') or {}
                    total = attributes.get('total_strings') or 0
                    done = attributes.get('translated_strings') or 0
                    if code and code != source:
                        found.append(
                            Item(names.get(code, code), code, done / total if total else 0)
                        )

            params = {
                'filter[project]': project_id(project),
                'filter[resource]': resource_id(project, component),
            }
            self.client.get_list(
                'resource_language_stats', params, page, lambda: on_done(found), on_error
            )

        self._project_languages(project, stats, on_error)

    # Downloads #

    def _plural_rules(self, language, on_done, on_error):
        """on_done(): the language's plural categories are known."""
        if language in self.plurals:
            on_done()
            return

        def got(data):
            rules = (data.get('attributes') or {}).get('plural_rules')
            self.plurals[language] = categories(rules)
            on_done()

        self.client.get_one('languages/l:' + language, got, on_error)

    def _format(self, project, component, on_done, on_error):
        """on_done(the resource's attributes, its i18n_format)."""
        rid = resource_id(project, component)

        def got(data):
            self.formats[rid] = _related(data, 'i18n_format')
            on_done(data.get('attributes') or {}, self.formats[rid])

        self.client.get_one('resources/' + rid, got, on_error)

    def download(self, project, component, language, on_done, on_error):
        # The resource and its format, the project, the language's plural
        # rules and the strings; then, for a format Virtaal reads, the
        # file and the source file for a format of one language a file,
        # else a file built from the strings.
        def got_resource(resource, file_format):
            def got_project(data):
                attributes = data.get('attributes') or {}
                where = {
                    'project': project,
                    'component': component,
                    'language': language,
                    'format': file_format,
                    'source': _language(_related(data, 'source_language')) or 'en',
                    'names': {
                        'project': attributes.get('name') or project,
                        'component': resource.get('name') or component,
                    },
                }
                self._plural_rules(
                    language,
                    lambda: self._strings(
                        project,
                        component,
                        language,
                        lambda pairs: self._downloaded(where, pairs, on_done, on_error),
                        on_error,
                    ),
                    on_error,
                )

            self.client.get_one('projects/' + project_id(project), got_project, on_error)

        self._format(project, component, got_resource, on_error)

    def _downloaded(self, where, pairs, on_done, on_error):
        """The strings read: the Download, its file fetched or built."""
        file_format = where['format']
        opens = self._opens(file_format, pairs)
        units = self._remote_units(pairs, where['language'], opens)
        paths = file_paths(
            file_format, where['component'], where['language'], where['source'], opens
        )
        options = {'review': None, 'source_review': None, 'platform_format': file_format}
        if opens == formats.BUILT:
            options.update(
                origin=workcopy.BUILT,
                placeables=formats.placeables(file_format, [str(u.source[-1]) for u in units]),
            )
            data = self._built(where, pairs, units)
            _idle(on_done, Download(paths[0], data, None, units, where['names'], options))
            return

        def fetched(body, template):
            on_done(Download(paths[0], body, template, units, where['names'], options))

        self._fetch(where, paths, fetched, on_error)

    def _fetch(self, where, paths, on_done, on_error):
        """on_done(the file's bytes, (the source file's path, bytes) or
        None)."""
        rid = resource_id(where['project'], where['component'])

        def got_file(body):
            if paths[1] is None:
                on_done(body, None)
                return
            self.client.download_job(
                'resource_strings_async_downloads',
                {'content_encoding': 'text', 'file_type': 'default'},
                {'resource': rid},
                lambda base: on_done(body, (paths[1], base)),
                on_error,
            )

        self.client.download_job(
            'resource_translations_async_downloads',
            {'content_encoding': 'text', 'file_type': 'default', 'mode': 'default'},
            {'language': 'l:' + where['language'], 'resource': rid},
            got_file,
            on_error,
        )

    def _built(self, where, pairs, units):
        """The XLIFF bytes of a resource's strings (formats.py)."""
        entries = []
        for (_translation, string), unit in zip(pairs, units, strict=True):
            about = string.get('attributes') or {}
            entries.append(
                formats.Entry(
                    unit.context,
                    about.get('key') or '',
                    unit.source,
                    unit.target,
                    (self.plurals.get(where['language']) or ['other'])
                    if about.get('pluralized')
                    else None,
                    unit.code,
                    formats.notes_of(about),
                    formats.limit_of(about),
                )
            )
        return formats.build_xliff(entries, where['source'], where['language'], where['component'])

    @staticmethod
    def _opens(file_format, pairs):
        pluralized = any(
            (string.get('attributes') or {}).get('pluralized') for _translation, string in pairs
        )
        return formats.mode(file_format, pluralized)

    def _strings(self, project, component, language, on_done, on_error):
        """on_done([(translation, its resource string)]): every string of
        a resource in language."""
        found = []
        params = {
            'filter[resource]': resource_id(project, component),
            'filter[language]': 'l:' + language,
            'include': 'resource_string',
            'limit': self.client.UNITS_PAGE_SIZE,
        }

        def page(data, included):
            strings = {s.get('id'): s for s in included if s.get('type') == 'resource_strings'}
            for translation in data:
                string = strings.get(_related(translation, 'resource_string')) or {}
                found.append((translation, string))

        self.client.get_list(
            'resource_translations', params, page, lambda: on_done(found), on_error
        )

    def _remote_units(self, pairs, language, opens):
        names = self.plurals.get(language) or ['other']
        return [
            self._remote_unit(translation, string, names, opens, position)
            for position, (translation, string) in enumerate(pairs, 1)
        ]

    def _remote_unit(self, translation, string, names, opens, position):
        attributes = translation.get('attributes') or {}
        about = string.get('attributes') or {}
        pluralized = bool(about.get('pluralized'))
        self.pluralized[translation.get('id')] = pluralized
        source = about.get('strings') or {}
        code = code_of(attributes)
        if opens == formats.BUILT:
            # The file's: its id, and a source form for each category.
            context = _string_hash(string)
            source = forms({c: source.get(c) or source.get('other') for c in names}, names)
        else:
            if opens == formats.NATIVE_KEYED:
                context = str(about.get('key') or '')
            else:
                context = context_text(about.get('context'))
            source = forms(source, categories(source))
        if not pluralized:
            source = source[-1:]
        return RemoteUnit(
            id=translation.get('id'),
            position=position,
            context=context,
            source=source,
            target=forms(attributes.get('strings'), names if pluralized else ['other']),
            state=virtaal_state(code),
            code=code,
            last_updated=last_updated(attributes),
            read_only=False,
        )

    # Submit #

    def locked(self, project, component, on_done, on_error):
        # Transifex has no lock.
        _idle(on_done, False)

    def unit_count(self, project, component, language, on_done, on_error):
        path = 'resource_language_stats/%s:l:%s' % (resource_id(project, component), language)
        self.client.get_one(
            path,
            lambda data: on_done((data.get('attributes') or {}).get('total_strings') or 0),
            on_error,
        )

    def changed_since(self, project, component, language, since, on_done, on_error):
        # No filter finds a review: every string, as it is now.
        rid = resource_id(project, component)

        def read(_resource=None, file_format=None):
            file_format = file_format or self.formats.get(rid)

            def got(pairs):
                opens = self._opens(file_format, pairs)
                on_done(self._remote_units(pairs, language, opens))

            self._strings(project, component, language, got, on_error)

        def with_rules():
            if rid in self.formats:
                read()
            else:
                self._format(project, component, read, on_error)

        self._plural_rules(language, with_rules, on_error)

    def strings(self, unit_id, target):
        """A unit's forms as Transifex's strings: one per plural category
        of its language for a string with plurals, else "other"."""
        if not any(target):
            return None
        language = str(unit_id).rpartition(':l:')[2]
        names = self.plurals.get(language) or []
        plural = self.pluralized.get(unit_id, len(target) > 1)
        if plural and len(names) == len(target):
            return dict(zip(names, target, strict=True))
        return {'other': target[0]}

    def attributes(self, unit_id, target, code):
        """What PATCH sends for a unit's target and state code."""
        strings = self.strings(unit_id, target) if code != UNTRANSLATED else None
        if strings is None:
            return {'strings': None}
        return {'strings': strings, 'reviewed': code in (REVIEWED, PROOFREAD)}

    def _item(self, unit_id, target, code):
        return {
            'type': 'resource_translations',
            'id': unit_id,
            'attributes': self.attributes(unit_id, target, code),
        }

    def submit_unit(self, unit_id, target, code, on_done, on_error):
        def written(reply):
            data = (reply or {}).get('data')
            on_done(code_of(data.get('attributes') or {}) if isinstance(data, dict) else code)

        def failed(error):
            if error.status == 403 and code == REVIEWED:
                # The change with reviewed set: most likely not a reviewer.
                error.refused = REVIEW
            on_error(error)

        url = self.client.url('resource_translations/' + unit_id)
        body = {'data': self._item(unit_id, target, code)}
        self.client.send_json('PATCH', url, body, written, failed)

    def submit_units(self, units, on_each, on_done, on_error):
        """One bulk PATCH; if Transifex refuses it whole (400, 403, 409),
        each unit alone, to learn which it refuses."""

        def written(reply):
            stored = {}
            for data in (reply or {}).get('data') or []:
                if isinstance(data, dict):
                    stored[data.get('id')] = code_of(data.get('attributes') or {})
            for index, (unit_id, _target, code) in enumerate(units):
                on_each(index, stored.get(unit_id, code))
            on_done()

        def failed(error):
            if error.status in (400, 403, 409):
                Session.submit_units(self, units, on_each, on_done, on_error)
            else:
                on_error(error)

        body = {'data': [self._item(*unit) for unit in units]}
        url = self.client.url('resource_translations')
        self.client.send_json('PATCH', url, body, written, failed, transifex.BULK)

    def cancel(self):
        self.client.cancel()


class TransifexPlatform(Platform):
    id = 'transifex'
    name = 'Transifex'
    default_url = transifex.API_URL
    has_components = True
    supports_search = False
    can_submit = True
    rate_limit = (500, 60)
    """500 requests a minute an address; Transifex doesn't say what is
    left."""
    batch_size = 150
    """The bulk PATCH's limit."""
    states = (StateEnum.EMPTY, StateEnum.UNREVIEWED, StateEnum.FINAL)
    # l10n: Imports the servers and tokens of tx, Transifex's command-line client
    import_label = _('Import from Trans_ifex Client')
    import_tooltip = _('Adds the servers and tokens saved by tx, Transifex’s command-line client')

    client_factory = transifex.Client

    def __init__(self, path=None):
        self.keys = KeyFile(path)
        self.plurals = {}
        """{API URL: {language: its plural categories}}."""
        self.formats = {}
        """{API URL: {resource id: its i18n_format}}."""

    def credential_fields(self):
        from virtaal.views.widgets.settingsdialog import SECRET, Field

        # l10n: The API token a translation server gives each user, on their settings page
        return (
            Field(
                'token',
                _('_Token:'),
                SECRET,
                required=True,
                # l10n: Shows the token's characters instead of dots
                reveal=_('S_how token'),
            ),
        )

    def normalise_url(self, text):
        return transifex.api_url(text)

    def url_problem(self, text):
        if transifex.unencrypted(text):
            # l10n: Shown for an address starting http:// instead of https://
            return _('Use https: the token would be sent unencrypted.')
        return None

    def not_platform_message(self):
        # l10n: Transifex's API is at https://rest.api.transifex.com/; something else answered
        return _('No Transifex API answered at this address.')

    def load_servers(self):
        self.keys.load()
        servers = [(url, {'token': token}) for url, token in self.keys.servers]
        return servers, self.keys.last_url

    def save_servers(self, servers, last_url):
        self.keys.servers = [(url, credentials.get('token', '')) for url, credentials in servers]
        self.keys.last_url = last_url
        self.keys.save()

    def session(self, url, credentials):
        client = self.client_factory(url, credentials.get('token', ''))
        return TransifexSession(client, self)

    def can_import(self):
        return tx_config_path() is not None or bool(os.environ.get('TX_TOKEN', '').strip())

    def import_servers(self):
        return [(url, {'token': token}) for url, token in read_tx()]

    def state_code(self, state, target):
        if not any(target):
            return UNTRANSLATED
        if self.storable(state, target) == StateEnum.FINAL:
            return REVIEWED
        return TRANSLATED
