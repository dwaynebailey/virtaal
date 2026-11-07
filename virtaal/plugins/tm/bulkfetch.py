#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Fetching one service's suggestions for a whole file (#1679): a batch
at a time, each sent only once the one before is answered, waiting
when the service asks to, and stopping at the first failure."""

# Lives outside models/ so the plug-in controller doesn't list it as a
# TM backend.

import logging

from gi.repository import GLib

DONE, CANCELLED, FAILED = 'done', 'cancelled', 'failed'

_ERROR_SIGNALS = ('http-redirect', 'http-client-error', 'http-server-error', 'http-failed')


class BulkFetch:
    """Ask C{model} for the suggestions for C{sources} through its
    bulk_*() methods. C{on_found(fetch, found)} gets each batch's
    {source: [match, ...]}; C{on_done(fetch)} runs once, with
    C{finished} DONE, CANCELLED or FAILED."""

    MAX_WAITS = 3
    """How often in a row the service may ask to wait before the fetch
    stops."""

    def __init__(self, model, sources, on_found, on_done):
        self.model = model
        self.batches = model.bulk_batches(list(sources))
        self.total = sum(len(batch) for batch in self.batches)
        self.fetched = 0
        self.finished = None
        self._on_found = on_found
        self._on_done = on_done
        self._request = None
        self._timer = None
        self._waits = 0

    def start(self):
        self._send()

    def cancel(self):
        if self.finished is None:
            self._finish(CANCELLED)

    def _send(self):
        self._timer = None
        if self.finished is not None:
            return False
        if not self.batches:
            self._finish(DONE)
            return False
        request = self.model.bulk_send(self.batches[0])
        if request is None:
            # The client's queue was full, or the service can't be asked.
            self._finish(FAILED)
            return False
        self._request = request
        request.connect('http-success', self._on_success)
        for signal in _ERROR_SIGNALS:
            request.connect(signal, self._on_error)
        return False

    def _on_success(self, request, reply):
        if request is not self._request or self.finished is not None:
            return
        batch = self.batches.pop(0)
        try:
            found = self.model.bulk_parse(batch, reply)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            logging.debug('%s: unexpected reply to a file fetch: %s', self.model, e)
            self._finish(FAILED)
            return
        self._waits = 0
        self.fetched += len(batch)
        self._on_found(self, found)
        self._send()

    def _on_error(self, request, status):
        if request is not self._request or self.finished is not None:
            return
        seconds = self.model.bulk_wait(request, status)
        if seconds is None or self._waits >= self.MAX_WAITS:
            logging.debug('%s: file fetch stopped (%s)', self.model, status)
            self._finish(FAILED)
            return
        self._waits += 1
        logging.debug('%s: file fetch waits %d s (%s)', self.model, seconds, status)
        self._timer = GLib.timeout_add(int(seconds * 1000), self._send)

    def _finish(self, state):
        self.finished = state
        self._request = None
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        self._on_done(self)


def batches(items, max_units, max_chars, size=len):
    """C{items} in batches of at most C{max_units} and, except for an item
    longer on its own, C{max_chars} by C{size}."""
    found, batch, chars = [], [], 0
    for item in items:
        length = size(item)
        if batch and (len(batch) >= max_units or chars + length > max_chars):
            found.append(batch)
            batch, chars = [], 0
        batch.append(item)
        chars += length
    if batch:
        found.append(batch)
    return found
