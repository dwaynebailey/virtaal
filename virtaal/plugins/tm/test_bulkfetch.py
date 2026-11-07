#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import io

import pytest

from virtaal.plugins.tm import bulkfetch
from virtaal.plugins.tm.bulkfetch import CANCELLED, DONE, FAILED, BulkFetch, batches


class _Request:
    def __init__(self, batch):
        self.batch = batch
        self.result_headers = io.BytesIO()
        self.handlers = []

    def connect(self, signal, handler, *args):
        self.handlers.append((signal, handler, args))

    def fire(self, signal, result):
        for name, handler, args in self.handlers:
            if name == signal:
                handler(self, result, *args)


class _Model:
    """A service answering each batch with its sources upper-cased."""

    def __init__(self, per_request=2, wait=None):
        self.per_request = per_request
        self.wait = wait
        self.sent = []
        self.full = False

    def bulk_batches(self, sources):
        return batches(sources, self.per_request, 1000)

    def bulk_send(self, batch):
        if self.full:
            return None
        self.sent.append(_Request(batch))
        return self.sent[-1]

    def bulk_parse(self, batch, reply):
        if reply == b'bad':
            raise ValueError('bad reply')
        return {source: [{'source': source, 'target': source.upper()}] for source in batch}

    def bulk_wait(self, request, status):
        return self.wait


@pytest.fixture
def timers(monkeypatch):
    """GLib timeouts a test runs itself."""
    pending = []

    def timeout_add(ms, func):
        pending.append((ms, func))
        return len(pending)

    monkeypatch.setattr(bulkfetch.GLib, 'timeout_add', timeout_add)
    monkeypatch.setattr(bulkfetch.GLib, 'source_remove', lambda source_id: None)
    return pending


def _fetch(model, sources):
    found, done = [], []
    fetch = BulkFetch(model, sources, lambda f, batch: found.append(batch), done.append)
    fetch.start()
    return fetch, found, done


def test_one_batch_is_sent_at_a_time():
    model = _Model()
    fetch, found, done = _fetch(model, ['a', 'b', 'c'])

    assert [r.batch for r in model.sent] == [['a', 'b']]
    model.sent[0].fire('http-success', b'')
    assert [r.batch for r in model.sent] == [['a', 'b'], ['c']]
    assert fetch.fetched == 2
    model.sent[1].fire('http-success', b'')

    assert found == [
        {'a': [{'source': 'a', 'target': 'A'}], 'b': [{'source': 'b', 'target': 'B'}]},
        {'c': [{'source': 'c', 'target': 'C'}]},
    ]
    assert done == [fetch]
    assert fetch.finished == DONE
    assert (fetch.fetched, fetch.total) == (3, 3)


def test_nothing_to_fetch_is_done_at_once():
    model = _Model()
    fetch, _found, done = _fetch(model, [])

    assert model.sent == []
    assert done == [fetch] and fetch.finished == DONE


def test_cancelling_drops_the_reply_and_sends_no_more():
    model = _Model()
    fetch, found, done = _fetch(model, ['a', 'b', 'c'])

    fetch.cancel()
    model.sent[0].fire('http-success', b'')
    fetch.cancel()

    assert found == []
    assert len(model.sent) == 1
    assert done == [fetch] and fetch.finished == CANCELLED


@pytest.mark.parametrize('signal', ['http-client-error', 'http-server-error', 'http-failed'])
def test_a_failure_stops_the_fetch(signal):
    model = _Model()
    fetch, found, done = _fetch(model, ['a', 'b', 'c'])

    model.sent[0].fire(signal, 400)

    assert found == [] and len(model.sent) == 1
    assert done == [fetch] and fetch.finished == FAILED


def test_an_unusable_reply_stops_the_fetch():
    model = _Model()
    fetch, found, _done = _fetch(model, ['a', 'b', 'c'])

    model.sent[0].fire('http-success', b'bad')

    assert found == [] and fetch.finished == FAILED


def test_a_full_queue_stops_the_fetch():
    model = _Model()
    model.full = True
    fetch, _found, _done = _fetch(model, ['a'])

    assert fetch.finished == FAILED


def test_waits_as_long_as_the_service_asks_then_sends_the_batch_again(timers):
    model = _Model(wait=7)
    fetch, found, _done = _fetch(model, ['a', 'b', 'c'])

    model.sent[0].fire('http-client-error', 429)

    assert [ms for ms, _func in timers] == [7000]
    assert len(model.sent) == 1 and fetch.finished is None
    timers[0][1]()
    assert [r.batch for r in model.sent] == [['a', 'b'], ['a', 'b']]
    model.sent[1].fire('http-success', b'')
    assert len(found) == 1


def test_stops_after_waiting_too_often_in_a_row(timers):
    model = _Model(wait=1)
    fetch, _found, _done = _fetch(model, ['a'])

    for n in range(BulkFetch.MAX_WAITS):
        model.sent[n].fire('http-client-error', 429)
        timers[n][1]()
    model.sent[-1].fire('http-client-error', 429)

    assert fetch.finished == FAILED
    assert len(model.sent) == BulkFetch.MAX_WAITS + 1


def test_cancelling_while_waiting_sends_nothing_more(timers):
    model = _Model(wait=1)
    fetch, _found, _done = _fetch(model, ['a'])
    model.sent[0].fire('http-client-error', 429)

    fetch.cancel()
    timers[0][1]()

    assert len(model.sent) == 1 and fetch.finished == CANCELLED


@pytest.mark.parametrize(
    'items, expected',
    [
        ([], []),
        (['ab', 'cd', 'ef'], [['ab', 'cd'], ['ef']]),
        # By length: a long one on its own.
        (['abcdef', 'a'], [['abcdef'], ['a']]),
        (['a', 'abcdef', 'b'], [['a'], ['abcdef'], ['b']]),
        (['a', 'b', 'c', 'd', 'e'], [['a', 'b'], ['c', 'd'], ['e']]),
    ],
)
def test_batches(items, expected):
    assert batches(items, 2, 4) == expected
