#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Fetch one file from a zip on a web server without downloading the
whole zip: HTTP range requests for the zip's directory at its end, then
for that file's own bytes."""

import logging
import re
import zipfile
import zlib

# The end record plus the longest comment zipfile searches back through.
TAIL_SIZE = (1 << 16) + 22


class MissingRange(Exception):
    """A read reached bytes of the zip not fetched yet."""

    def __init__(self, start, end):
        super().__init__('bytes %d-%d not fetched' % (start, end))
        self.start = start
        self.end = end


class SparseFile:
    """A seekable file of C{size} bytes of which only the ranges added
    are known; reading anything else raises L{MissingRange}."""

    def __init__(self, size):
        self.size = size
        self._chunks = {}
        self._pos = 0

    def add(self, start, data):
        """Add the bytes at C{start}, joined to a range they touch."""
        after = self._chunks.pop(start + len(data), None)
        if after is not None:
            data += after
        for other, chunk in list(self._chunks.items()):
            if other + len(chunk) == start:
                del self._chunks[other]
                start, data = other, chunk + data
                break
        self._chunks[start] = data

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=0):
        position = {0: 0, 1: self._pos, 2: self.size}[whence] + offset
        if position < 0:
            raise OSError('negative seek')
        self._pos = position
        return position

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self._pos
        end = min(self._pos + n, self.size)
        if end <= self._pos:
            return b''
        for start, data in self._chunks.items():
            if start <= self._pos and end <= start + len(data):
                chunk = data[self._pos - start : end - start]
                self._pos = end
                return chunk
        raise MissingRange(self._pos, end)


def _total_size(request):
    """The whole file's size from a 206 reply's Content-Range."""
    match = re.match(r'bytes \d+-\d+/(\d+)', request.response_header('Content-Range') or '')
    return int(match.group(1)) if match else None


class RemoteZipMember:
    """Fetches one member of the zip at C{url} through C{client}, an
    L{virtaal.support.httpclient.HTTPClient}.

    C{choose(names)} picks the member from the zip's names (or None).
    C{on_done(data, etag)} gets the member's bytes and the zip's ETag, or
    C{None} for the bytes when the zip still has C{etag}.
    C{on_error(reason)} gets a short reason for the debug log."""

    def __init__(self, client, url, choose, on_done, on_error, etag=None):
        self.client = client
        self.url = url
        self.choose = choose
        self.on_done = on_done
        self.on_error = on_error
        self.etag = etag
        self._new_etag = None
        self._zip = None

    def start(self):
        headers = ['Range: bytes=-%d' % TAIL_SIZE]
        if self.etag:
            headers.append('If-None-Match: %s' % self.etag)
        self._get(headers, TAIL_SIZE, self._on_tail)

    def _get(self, headers, size, callback):
        def failed(request, reason):
            self.on_error('%s: %s' % (self.url, reason))

        request = self.client.get(
            self.url,
            lambda request, result: callback(request, result),
            error_callback=failed,
            download=True,
            headers=headers,
            max_size=size,
        )
        if request is not None:
            request.connect('http-failed', failed)

    def _on_tail(self, request, result):
        etag = request.response_header('ETag')
        if request.status == 304 or (request.status == 206 and etag and etag == self.etag):
            return self.on_done(None, self.etag)
        if request.status == 206:
            size = _total_size(request)
            if size is None:
                return self.on_error('no Content-Range')
            self._file = SparseFile(size)
            self._file.add(size - len(result), result)
        elif request.status == 200:
            # A server that ignores Range; only a zip smaller than the
            # tail gets through max_size.
            self._file = SparseFile(len(result))
            self._file.add(0, result)
        else:
            return self.on_error('status %s' % request.status)
        self._new_etag = etag
        self._open_zip()

    def _open_zip(self, retried=False):
        try:
            self._zip = zipfile.ZipFile(self._file)
        except MissingRange as missing:
            if retried:
                return self.on_error(str(missing))
            # A directory longer than the tail.
            start, end = missing.start, max(self._file._chunks)
            return self._fetch(
                start, end, lambda request, result: self._on_directory(request, result, start)
            )
        except (zipfile.BadZipFile, OSError, ValueError) as e:
            return self.on_error('not a zip: %s' % e)
        self._fetch_member()

    def _on_directory(self, request, result, start):
        if request.status != 206:
            return self.on_error('status %s' % request.status)
        self._file.add(start, result)
        self._open_zip(retried=True)

    def _fetch(self, start, end, callback):
        """Request bytes C{start} up to C{end}."""
        headers = ['Range: bytes=%d-%d' % (start, end - 1)]
        if self._new_etag:
            headers.append('If-Match: %s' % self._new_etag)
        self._get(headers, end - start, callback)

    def _fetch_member(self):
        name = self.choose(self._zip.namelist())
        if name is None:
            return self.on_error('no matching member')
        info = self._zip.getinfo(name)
        later = [
            i.header_offset for i in self._zip.infolist() if i.header_offset > info.header_offset
        ]
        end = min(later) if later else self._zip.start_dir
        try:
            # Already fetched (a zip smaller than the tail).
            return self._read(name)
        except MissingRange:
            pass
        self._fetch(
            info.header_offset,
            end,
            lambda request, result: self._on_member(request, result, name, info.header_offset),
        )

    def _on_member(self, request, result, name, start):
        if request.status != 206:
            return self.on_error('status %s' % request.status)
        self._file.add(start, result)
        try:
            self._read(name)
        except MissingRange as e:
            self.on_error(str(e))

    def _read(self, name):
        try:
            data = self._zip.read(name)
        except (zipfile.BadZipFile, zlib.error, NotImplementedError) as e:
            logging.debug('Could not unpack %s from %s: %s', name, self.url, e)
            return self.on_error('bad member: %s' % e)
        self.on_done(data, self._new_etag)
