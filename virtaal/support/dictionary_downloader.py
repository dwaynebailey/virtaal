#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Downloads a missing spell-check dictionary in the background, via
the app's own async HTTPClient - the non-blocking counterpart to
dictionary_source.py's synchronous download_dictionary() (kept as-is:
simpler, directly testable, still useful standalone).

start_with_known_files() is the normal path - dictionary_download_watcher.py
already knows folder/files from virtaal.support.asset_manifest's cached
manifest, no live discovery needed. start() reimplements
dictionary_source.py's own fetch/match orchestration as an explicit
callback chain (tree -> candidate xcu(s) -> files) instead, kept as the
fallback for when the manifest can't say what to fetch - find_dictionary()/
download_dictionary() are both written as blocking loops, so one
request in flight at a time doesn't fit that shape. The pure logic
(list_dictionary_folders/candidate_folders/parse_dictionaries_xcu) is
reused unchanged either way.
"""

import json
import logging
import os

from virtaal.support.background_downloader import FilesDownloader
from virtaal.support.dictionary_source import (
    RAW_BASE,
    TREE_URL,
    candidate_folders,
    dictionary_write_dir,
    list_dictionary_folders,
    locale_covers,
    parse_dictionaries_xcu,
)
from virtaal.support.enchant_compat import enchant_tag


class DictionaryDownloader(FilesDownloader):
    """Downloads locale_code's hunspell dictionary in the background,
    calling on_done(success) once it's either written or given up.
    Silent on failure, same as UpdateChecker: network problems must
    never surface as an application error to the user - success is
    passed only so a caller can decide whether to record this as
    installed, not to raise anything on failure."""

    _log_name = 'dictionary'

    def start(self):
        """Live discovery - a tree call, then per-candidate-folder xcu
        fetches, same as always. Used as the fallback when
        asset_manifest.py's manifest can't say what to fetch (never
        successfully fetched, or has nothing for this locale)."""
        self._client.get(TREE_URL, self._on_tree, error_callback=self._on_error)

    def start_with_known_files(self, folder, files):
        """Skips discovery entirely - folder/files already known (from
        asset_manifest.py's manifest), straight to fetching them."""
        self._folder = folder
        self._files = list(files)
        self._fetch_next_file()

    def _on_tree(self, _request, result):
        try:
            tree = json.loads(result.decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as e:
            logging.debug('dictionary download: bad tree response: %s', e)
            return self._give_up()
        folders = list_dictionary_folders(tree)
        candidates = candidate_folders(self.locale_code, folders)
        self._remaining_folders = candidates + [f for f in folders if f not in candidates]
        self._try_next_folder()

    def _try_next_folder(self):
        if not self._remaining_folders:
            return self._give_up()
        self._folder = self._remaining_folders.pop(0)
        url = RAW_BASE + self._folder + '/dictionaries.xcu'
        self._client.get(url, self._on_xcu, error_callback=self._on_xcu_error)

    def _on_xcu_error(self, _request, _status):
        self._try_next_folder()

    def _on_xcu(self, _request, result):
        for entry in parse_dictionaries_xcu(result):
            if locale_covers(self.locale_code, entry['locales']):
                self._files = list(entry['files'])
                return self._fetch_next_file()
        self._try_next_folder()

    def _default_target_dir(self):
        return dictionary_write_dir()

    def _local_name(self, filename):
        """LibreOffice's names (af_ZA for af_NA, fa-IR, ca-valencia)
        aren't what enchant looks for, so the .aff/.dic pair is saved
        under the locale's own tag."""
        stem, ext = os.path.splitext(filename)
        if ext in ('.aff', '.dic'):
            return enchant_tag(self.locale_code) + ext
        return filename

    def _on_success(self):
        logging.debug('Downloaded dictionary for %s', self.locale_code)
        self.on_done(True)

    def _on_error(self, _request, status):
        logging.debug('dictionary download: request failed, status=%r', status)
        self._give_up()

    def _give_up(self):
        logging.debug('No LibreOffice dictionary found for %s', self.locale_code)
        self.on_done(False)
