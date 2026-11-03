#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Shared parts of the background downloaders (autocorrect_downloader,
dictionary_downloader, thesaurus_downloader)."""

import logging
import os

from virtaal.support.dictionary_source import dictionary_file_url_candidates


class BackgroundDownloader:
    """Downloads locale_code's data through the app's async HTTPClient,
    calling on_done(success) once it's either written or given up."""

    def __init__(self, locale_code, on_done=None, client=None, target_dir=None):
        self.locale_code = locale_code.replace('-', '_')
        self.on_done = on_done or (lambda success: None)
        self.target_dir = target_dir
        if client is None:
            from virtaal.support.httpclient import HTTPClient

            client = HTTPClient()
            client.set_virtaal_useragent()
        self._client = client


class FilesDownloader(BackgroundDownloader):
    """Fetches self._files from self._folder of the LibreOffice
    dictionaries repository one at a time, then calls _on_success().
    If any file fails, the files already written are removed and
    _give_up() is called."""

    _log_name = ''
    """What is downloaded, for the debug log."""

    def __init__(self, locale_code, on_done=None, client=None, target_dir=None):
        super().__init__(locale_code, on_done, client, target_dir)
        self._written = []

    def _default_target_dir(self):
        raise NotImplementedError()

    def _local_name(self, filename):
        return filename

    def _fetch_next_file(self):
        if not self._files:
            return self._on_success()
        filename = self._files.pop(0)
        self._fetch_file(filename, dictionary_file_url_candidates(self._folder, filename))

    def _fetch_file(self, filename, urls):
        self._client.get(
            urls[0],
            lambda request, result, fn=filename: self._on_file(fn, result),
            error_callback=lambda request, status, fn=filename, rest=urls[1:]: (
                self._on_file_missing(fn, status, rest)
            ),
            download=True,
        )

    def _on_file_missing(self, filename, status, rest):
        if status == 404 and rest:
            return self._fetch_file(filename, rest)
        self._on_file_error(filename, status)

    def _on_file(self, filename, result):
        try:
            target_dir = self.target_dir or self._default_target_dir()
            os.makedirs(target_dir, exist_ok=True)
            dest = os.path.join(target_dir, self._local_name(filename))
            with open(dest, 'wb') as f:
                f.write(result)
            self._written.append(dest)
        except Exception as e:
            logging.debug('%s download: could not write %s: %s', self._log_name, filename, e)
            return self._on_file_error(filename, None)
        self._fetch_next_file()

    def _on_file_error(self, filename, status):
        logging.debug(
            '%s download: could not get %s/%s: status=%r',
            self._log_name,
            self._folder,
            filename,
            status,
        )
        for path in self._written:
            os.remove(path)
        self._give_up()
