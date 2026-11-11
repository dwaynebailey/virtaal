#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Weblate as a translation platform (virtaal.support.platforms): its
token, its address rules, wlc's servers, and its REST replies as
Items (#3337)."""

from virtaal.support.platforms import Item, Platform, Session

from . import client as weblate
from .config import KeyFile, read_wlc, wlc_config_path


class WeblateSession(Session):
    """A Client's replies as the browse dialog wants them."""

    def __init__(self, client):
        self.client = client

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

    def cancel(self):
        self.client.cancel()


class WeblatePlatform(Platform):
    id = 'weblate'
    name = _('Weblate')
    has_components = True
    supports_search = False
    # l10n: Imports the servers and tokens of wlc, Weblate's command-line client
    import_label = _('_Import from wlc')

    client_factory = weblate.Client

    def __init__(self, path=None):
        self.keys = KeyFile(path)

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
        return WeblateSession(self.client_factory(url, credentials.get('token', '')))

    def can_import(self):
        return wlc_config_path() is not None

    def import_servers(self):
        return [(url, {'token': token}) for url, token in read_wlc()]
