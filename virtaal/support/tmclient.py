#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

# These two json modules are API compatible
try:
    import simplejson as json  # should be a bit faster; needed for Python < 2.6
except ImportError:
    import json  # available since Python 2.6

import logging

import pycurl

from virtaal.support.httpclient import HTTPClient, RESTRequest


def _json_callback(callback):
    """An http-success handler passing the decoded reply to C{callback}.
    A reply that isn't JSON (an error page, an empty or truncated
    body) is logged and dropped instead of raising (#4437)."""

    def handler(widget, response):
        try:
            decoded = json.loads(response)
        except ValueError as exc:
            logging.warning(
                'Ignoring a reply from %s that is not JSON: %s', getattr(widget, 'url', '?'), exc
            )
            return
        callback(widget, widget.id, decoded)

    return handler


class TMClient(HTTPClient):
    """CRUD operations for TM units and stores"""

    def __init__(self, base_url):
        HTTPClient.__init__(self)
        self.base_url = base_url

    def translate_unit(self, unit_source, source_lang, target_lang, callback=None, params=None):
        """suggest translations from TM
        @returns: The request, or None if the queue was too full to send it."""
        request = RESTRequest(
            self.base_url + "/%s/%s/unit" % (source_lang, target_lang),
            unit_source,
            "GET",
            user_agent=self.user_agent,
            params=params,
        )
        # TM requests have to finish quickly to be useful. This also helps to
        # avoid buildup in case of network failure
        request.curl.setopt(pycurl.TIMEOUT, 30)
        if not self.add(request):
            return None
        if callback:
            request.connect("http-success", _json_callback(callback))
        return request

    def translate_abstract(
        self,
        base_url,
        query_str,
        abstract,
        version,
        source_lang,
        target_lang,
        callback=None,
        params=None,
    ):
        """suggest translations from amaGama's API v2 at base_url, sending only
        the abstract of query_str made by abstraction version
        @returns: The request, or None if the queue was too full to send it."""
        request = RESTRequest(
            base_url + "/%s/%s/unit/" % (source_lang, target_lang),
            None,
            "GET",
            user_agent=self.user_agent,
            params={**(params or {}), 'q': abstract, 'av': version},
        )
        # The callback gets query_str, as from translate_unit().
        request.id = query_str
        request.curl.setopt(pycurl.TIMEOUT, 30)
        if not self.add(request):
            return None
        if callback:
            request.connect("http-success", _json_callback(callback))
        return request

    def get_sources(self, lang):
        """Ask an amaGama server for its sources, titled in lang.
        @returns: The request, or None if the queue was too full to send it."""
        request = RESTRequest(
            self.base_url.rstrip('/') + '/sources/',
            None,
            "GET",
            user_agent=self.user_agent,
            params={'lang': lang},
        )
        request.curl.setopt(pycurl.TIMEOUT, 30)
        if not self.add(request):
            return None
        return request

    def add_unit(self, unit, source_lang, target_lang, callback=None):
        request = RESTRequest(
            self.base_url + "/%s/%s/unit" % (source_lang, target_lang),
            unit['source'],
            "PUT",
            json.dumps(unit),
            user_agent=self.user_agent,
        )
        self.add(request)
        if callback:
            request.connect("http-success", _json_callback(callback))

    def update_unit(self, unit, source_lang, target_lang, callback=None):
        request = RESTRequest(
            self.base_url + "/%s/%s/unit" % (source_lang, target_lang),
            unit['source'],
            "POST",
            json.dumps(unit),
            user_agent=self.user_agent,
        )
        self.add(request)
        if callback:
            request.connect("http-success", _json_callback(callback))

    def forget_unit(
        self, unit_source, source_lang, target_lang, callback=None, target=None, context=None
    ):
        """Remove C{target} as a suggestion for C{unit_source}; in any
        context if C{context} is None."""
        request = RESTRequest(
            self.base_url + "/%s/%s/unit" % (source_lang, target_lang),
            unit_source,
            "DELETE",
            json.dumps({'target': target, 'context': context}),
            user_agent=self.user_agent,
        )
        self.add(request)
        if callback:
            request.connect("http-success", _json_callback(callback))

    def get_store_stats(self, store, source_lang, target_lang, callback=None):
        request = RESTRequest(
            self.base_url + "/%s/%s/store" % (source_lang, target_lang),
            store.filename,
            "GET",
            user_agent=self.user_agent,
        )
        self.add(request)
        if callback:
            request.connect("http-success", _json_callback(callback))

    def upload_store(self, store, source_lang, target_lang, callback=None):
        data = bytes(store)
        request = RESTRequest(
            self.base_url + "/%s/%s/store" % (source_lang, target_lang),
            store.filename,
            "PUT",
            data,
            user_agent=self.user_agent,
        )
        self.add(request)
        if callback:
            request.connect("http-success", _json_callback(callback))

    def import_file(self, path, data, source_lang, target_lang):
        """Send C{data}, the content of the translation file at C{path},
        to be imported as the store at that path; units without
        languages of their own go under the passed ones.
        @returns: The request, or None if the queue was too full to send it."""
        request = RESTRequest(
            self.base_url + "/%s/%s/store" % (source_lang, target_lang),
            path,
            "PUT",
            data,
            user_agent=self.user_agent,
        )
        # A big TMX takes the server a while to read.
        request.curl.setopt(pycurl.TIMEOUT, 600)
        if not self.add(request):
            return None
        return request

    def add_store(self, filename, store, source_lang, target_lang, callback=None):
        request = RESTRequest(
            self.base_url + "/%s/%s/store" % (source_lang, target_lang),
            filename,
            "POST",
            json.dumps(store),
            user_agent=self.user_agent,
        )
        self.add(request)
        if callback:
            request.connect("http-success", _json_callback(callback))

    def forget_store(self, store, source_lang, target_lang, callback=None):
        request = RESTRequest(
            self.base_url + "/%s/%s/store" % (source_lang, target_lang),
            store.filename,
            "DELETE",
            user_agent=self.user_agent,
        )
        self.add(request)
        if callback:
            request.connect("http-success", _json_callback(callback))

    def list_stores(self):
        """Ask for every store in the TM, with its counts per language pair.
        @returns: The request, or None if the queue was too full to send it."""
        request = RESTRequest(self.base_url + "/stores", None, "GET", user_agent=self.user_agent)
        if not self.add(request):
            return None
        return request

    def _store_request(self, path, member, source_lang, target_lang, method, data=None):
        url = self.base_url + "/%s/%s/store" % (source_lang, target_lang)
        if not path:
            # The legacy store: an empty path after the slash.
            url += "/"
        params = {'member': member} if member else None
        return RESTRequest(url, path, method, data, user_agent=self.user_agent, params=params)

    def store_info(self, path, source_lang, target_lang, member=''):
        """Ask for the store at C{path}: its unit count for this language
        pair and whether it is excluded from the TM.
        @returns: The request, or None if the queue was too full to send it."""
        request = self._store_request(path, member, source_lang, target_lang, "GET")
        if not self.add(request):
            return None
        return request

    def set_exclude(self, path, exclude, source_lang, target_lang, member=''):
        """Keep the store at C{path} out of the TM, removing what it
        added, or let it in again.
        @returns: The request, or None if the queue was too full to send it."""
        body = json.dumps({'exclude': bool(exclude), 'member': member})
        request = self._store_request(path, '', source_lang, target_lang, "POST", body)
        if not self.add(request):
            return None
        return request

    def remove_store(self, path, source_lang, target_lang, member=''):
        """Remove what the store at C{path} added for this language pair.
        @returns: The request, or None if the queue was too full to send it."""
        request = self._store_request(path, member, source_lang, target_lang, "DELETE")
        if not self.add(request):
            return None
        return request

    def export_tmx(self, source_lang, target_lang, path=None, member=''):
        """Ask for the TM's entries for this language pair as TMX, only
        those of the store at C{path} if given.
        @returns: The request, or None if the queue was too full to send it."""
        params = None
        if path is not None:
            params = {'store': path}
            if member:
                params['member'] = member
        request = RESTRequest(
            self.base_url + "/%s/%s/export" % (source_lang, target_lang),
            None,
            "GET",
            user_agent=self.user_agent,
            params=params,
        )
        # A big TM takes the server a while to write.
        request.curl.setopt(pycurl.TIMEOUT, 600)
        if not self.add(request):
            return None
        return request
