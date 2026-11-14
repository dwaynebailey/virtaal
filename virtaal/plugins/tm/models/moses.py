#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


from .basetmmodel import BaseTMModel


class TMModel(BaseTMModel):
    """This is the Moses translation memory model.

    The plugin uses the Moses Statistical Machine Translation software's server to
    query over RPC for MT suggestions."""

    __gtype_name__ = 'MosesTMModel'
    display_name = _('Moses')
    description = _('Unreviewed machine translations from a Moses server')
    machine_translation = True

    PREFETCH_MAX = 2
    BULK_MAX = None
    default_config = {
        "fr->en": "http://localhost:8080",
        "prefetch": 2,
    }

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        self.internal_name = internal_name
        super().__init__(controller)

        self.load_config()
        self.clients = {}
        self._init_plugin()

    def _init_plugin(self):
        from virtaal.support.mosesclient import MosesClient

        # let's map servers to clients to detect duplicates
        client_map = {}
        for lang_pair, server in self.config.items():
            pair = lang_pair.split("->")
            if len(pair) != 2:
                continue
            if self.clients.get(pair[0]) is None:
                self.clients[pair[0]] = {}
            if server in client_map:
                client = client_map[server]
                client.set_multilang()
            else:
                client = MosesClient(server)
                client_map[server] = client
            self.clients[pair[0]].update({pair[1]: client})

    # METHODS #
    def query(self, tmcontroller, unit):
        if not self.bulk_ready():
            return
        query_str = str(unit.source)  # cast in case of multistrings
        if query_str in self.cache:
            self.emit('match-found', query_str, [self.cache[query_str]])
            return
        self._client().translate_unit(query_str, self._handle_response, self.target_lang)

    def requests_in_flight(self):
        clients = {id(c): c for targets in self.clients.values() for c in targets.values()}
        return sum(len(c.requests) for c in clients.values())

    def _handle_response(self, id, response):
        if not response:
            return
        result = _match(id, response)
        self.cache[id] = result
        self.emit('match-found', id, [result])

    # A file's suggestions (#1679), a unit per request #
    def _client(self):
        return self.clients.get(self.source_lang, {}).get(self.target_lang)

    def bulk_ready(self):
        client = self._client()
        return client is not None and client.serves(self.target_lang)

    def bulk_send(self, batch):
        (query_str,) = batch
        return self._client().translate_unit(query_str, None, self.target_lang)

    def bulk_parse(self, batch, reply):
        (query_str,) = batch
        response = self._client()._handle_response(query_str, reply, self.target_lang)
        return {query_str: [_match(query_str, response)] if response else []}


def _match(source, target):
    return {
        'source': source,
        'target': target,
        # l10n: Try to keep this as short as possible. Feel free to transliterate in CJK languages for vertical display optimization.
        'tmsource': _('Moses'),
    }
