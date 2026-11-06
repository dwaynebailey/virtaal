#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import io
import os
import re
import zipfile

import pytest

from virtaal.support import remote_zip
from virtaal.support.remote_zip import MissingRange, RemoteZipMember, SparseFile


class FakeRangeServer:
    """An HTTPClient stand-in serving C{content} with range requests, as
    Microsoft's download server does; no network."""

    def __init__(self, content, etag='"v1"', honours_range=True):
        self.content = content
        self.etag = etag
        self.honours_range = honours_range
        self.requests = []

    def get(self, url, callback, etag=None, error_callback=None, download=False, **kwargs):
        headers = dict(h.split(': ', 1) for h in kwargs.get('headers') or [])
        self.requests.append(headers)
        if headers.get('If-None-Match') == self.etag:
            return callback(FakeReply(304, etag=self.etag), b'')
        if 'If-Match' in headers and headers['If-Match'] != self.etag:
            return error_callback(FakeReply(412), 412)
        size = len(self.content)
        match = re.match(r'bytes=(\d*)-(\d*)', headers.get('Range', ''))
        if not (match and self.honours_range):
            body = self.content
            if kwargs.get('max_size') and len(body) > kwargs['max_size']:
                return None  # curl gives up: http-failed, not modelled
            return callback(FakeReply(200, etag=self.etag), body)
        first, last = match.groups()
        if not first:
            start, end = max(size - int(last), 0), size
        else:
            start, end = int(first), min(int(last) + 1, size)
        content_range = 'bytes %d-%d/%d' % (start, end - 1, size)
        return callback(
            FakeReply(206, etag=self.etag, content_range=content_range), self.content[start:end]
        )


class FakeReply:
    def __init__(self, status, etag=None, content_range=None):
        self.status = status
        self._headers = {'etag': etag, 'content-range': content_range}

    def response_header(self, name):
        return self._headers.get(name.lower())

    def connect(self, *args):
        pass


def make_zip(members, comment=b''):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        zf.comment = comment
    return buf.getvalue()


def fetch(server, member, etag=None):
    result = {}
    RemoteZipMember(
        server,
        'https://example.org/terms.zip',
        lambda names: member if member in names else None,
        lambda data, new_etag: result.update(data=data, etag=new_etag),
        lambda reason: result.update(error=reason),
        etag,
    ).start()
    return result


# Incompressible filler, so the zip is far bigger than the tail.
BIG = os.urandom(300_000)


def test_fetches_only_the_tail_and_the_member():
    content = make_zip({'A.tbx': b'<a/>' * 1000, 'B.tbx': BIG, 'C.tbx': b'<c/>'})
    server = FakeRangeServer(content)

    result = fetch(server, 'A.tbx')

    assert result == {'data': b'<a/>' * 1000, 'etag': '"v1"'}
    assert len(server.requests) == 2
    assert server.requests[0]['Range'] == 'bytes=-%d' % remote_zip.TAIL_SIZE
    start, end = map(int, server.requests[1]['Range'][len('bytes=') :].split('-'))
    assert end - start < 10_000
    assert server.requests[1]['If-Match'] == '"v1"'


def test_a_member_with_a_non_ascii_name():
    content = make_zip({'MĀORI.tbx': b'<m/>', 'B.tbx': BIG})

    assert fetch(FakeRangeServer(content), 'MĀORI.tbx')['data'] == b'<m/>'


def test_an_unchanged_zip_sends_no_member():
    content = make_zip({'A.tbx': b'<a/>', 'B.tbx': BIG})
    server = FakeRangeServer(content)

    result = fetch(server, 'A.tbx', etag='"v1"')

    assert result == {'data': None, 'etag': '"v1"'}
    assert len(server.requests) == 1


def test_a_server_ignoring_if_none_match_still_counts_as_unchanged():
    content = make_zip({'A.tbx': b'<a/>', 'B.tbx': BIG})
    server = FakeRangeServer(content)
    server.get = _ignoring_if_none_match(server.get)

    result = fetch(server, 'A.tbx', etag='"v1"')

    assert result == {'data': None, 'etag': '"v1"'}
    assert len(server.requests) == 1


def _ignoring_if_none_match(get):
    def wrapped(url, callback, **kwargs):
        kwargs['headers'] = [h for h in kwargs['headers'] if not h.startswith('If-None-Match')]
        return get(url, callback, **kwargs)

    return wrapped


def test_a_changed_zip_is_fetched_again():
    content = make_zip({'A.tbx': b'<new/>', 'B.tbx': BIG})

    result = fetch(FakeRangeServer(content, etag='"v2"'), 'A.tbx', etag='"v1"')

    assert result == {'data': b'<new/>', 'etag': '"v2"'}


def test_a_zip_changing_between_the_requests_fails():
    content = make_zip({'A.tbx': b'<a/>', 'B.tbx': BIG})
    server = FakeRangeServer(content)
    get = server.get

    def changing(url, callback, **kwargs):
        if server.requests:
            server.etag = '"v2"'
        return get(url, callback, **kwargs)

    server.get = changing

    assert 'error' in fetch(server, 'A.tbx')


def test_a_missing_member():
    content = make_zip({'A.tbx': b'<a/>', 'B.tbx': BIG})

    assert fetch(FakeRangeServer(content), 'Z.tbx') == {'error': 'no matching member'}


def test_a_directory_longer_than_the_tail_is_fetched_too():
    members = {'%05d.tbx' % i: b'x' for i in range(2000)}
    members['BIG.tbx'] = BIG
    content = make_zip(members)
    server = FakeRangeServer(content)

    assert fetch(server, '00001.tbx')['data'] == b'x'
    assert len(server.requests) == 3


def test_a_small_zip_from_a_server_without_ranges():
    content = make_zip({'A.tbx': b'<a/>'})
    server = FakeRangeServer(content, honours_range=False)

    assert fetch(server, 'A.tbx')['data'] == b'<a/>'
    assert len(server.requests) == 1


def test_not_a_zip():
    result = fetch(FakeRangeServer(b'<html>Sign in</html>' * 10), 'A.tbx')

    assert result['error'].startswith('not a zip')


def test_a_corrupt_member_fails_its_crc():
    content = bytearray(make_zip({'A.tbx': b'<a/>' * 100, 'B.tbx': BIG}, comment=b''))
    info = zipfile.ZipFile(io.BytesIO(bytes(content))).getinfo('A.tbx')
    data_start = info.header_offset + 30 + len('A.tbx')
    content[data_start + 2] ^= 0xFF

    assert 'error' in fetch(FakeRangeServer(bytes(content)), 'A.tbx')


def test_a_404():
    server = FakeRangeServer(b'')
    server.get = lambda url, callback, error_callback=None, **kw: error_callback(
        FakeReply(404), 404
    )

    assert '404' in fetch(server, 'A.tbx')['error']


def test_sparse_file_joins_adjacent_ranges():
    f = SparseFile(10)
    f.add(6, b'6789')
    f.add(2, b'2345')
    f.seek(3)

    assert f.read(5) == b'34567'
    f.seek(0)
    with pytest.raises(MissingRange):
        f.read(3)
