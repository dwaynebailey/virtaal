#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

# Lives outside models/ so the plug-in controller doesn't list it as a
# terminology backend.

import logging
import os
import time

from translate.search.match import terminologymatcher
from translate.storage import factory
from translate.storage.base import TranslationStore
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.support.httpclient import HTTPClient

from .models.basetermmodel import BaseTerminologyModel

THREE_DAYS = 60 * 60 * 24 * 3


class CachedRemoteTerminologyModel(BaseTerminologyModel):
    """Terminology downloaded per language pair from a server and cached
    as a local file in C{TERMDIR}, refreshed when older than C{REFRESH_AGE}."""

    __gtype_name__ = None

    _l10n_URL = None
    """URL template, formatted with C{srclang} and C{tgtlang}."""
    TERMDIR = None
    """Directory holding the cached files."""
    DEFAULT_EXT = 'po'
    """Extension for a language pair with no cached file yet."""
    REFRESH_AGE = THREE_DAYS
    """Seconds before a cached file is checked again."""

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        super().__init__(controller)
        self.internal_name = internal_name
        self.client = HTTPClient()
        self.client.set_virtaal_useragent()

        self.load_config()

        if not os.path.isdir(self.TERMDIR):
            os.mkdir(self.TERMDIR)

        self.main_controller = controller.main_controller
        self.term_controller = controller
        self.matcher = None
        self.init_matcher()

        lang_controller = self.main_controller.lang_controller
        self.source_lang = lang_controller.source_lang.code
        self.target_lang = lang_controller.target_lang.code
        self._signal_tracker.connect(
            lang_controller, 'source-lang-changed', self._on_lang_changed, 'source'
        )
        self._signal_tracker.connect(
            lang_controller, 'target-lang-changed', self._on_lang_changed, 'target'
        )

        self.update_terms()

    def init_matcher(self, filename=''):
        """
        Initialize the matcher to be used by the C{TerminologyPlaceable} parser.
        """
        if self.matcher in TerminologyPlaceable.matchers:
            TerminologyPlaceable.matchers.remove(self.matcher)

        if os.path.isfile(filename):
            logging.debug('Loading terminology from %s' % (filename))
            self.store = self._load_store(filename)
        else:
            logging.debug('Creating empty terminology store')
            self.store = TranslationStore()
        self.store.makeindex()
        self.matcher = terminologymatcher(self.store)
        TerminologyPlaceable.matchers.append(self.matcher)

    # HOOKS #
    def _load_store(self, filename):
        return factory.getobject(filename)

    def _skips_language_pair(self, srclang, tgtlang):
        """Whether the server has nothing for this pair, so an empty
        store is used without fetching."""
        return False

    def _url_lang_code(self, langcode):
        """The language code as the server's URLs spell it."""
        return langcode

    def _etag_key(self, localfile):
        """The config key the cached file's ETag is stored under."""
        return os.path.basename(localfile)

    def _accepts_response(self, content, ext):
        """Whether a 200 response is worth saving as an C{ext} file."""
        return True

    # ACCESSORS #
    def _get_curr_term_filename(self, srclang=None, tgtlang=None, ext=None):
        if srclang is None:
            srclang = self.source_lang
        if tgtlang is None:
            tgtlang = self.target_lang
        if not ext:
            ext = self.DEFAULT_EXT

        base = '%s__%s' % (srclang, tgtlang)
        for filename in os.listdir(self.TERMDIR):
            if filename.startswith(base):
                return filename
        return base + os.extsep + ext

    curr_term_filename = property(_get_curr_term_filename)

    # METHODS #
    def update_terms(self, srclang=None, tgtlang=None):
        """Update the terminology file for the given language or all if none specified."""
        if srclang is None:
            srclang = self.source_lang
        if tgtlang is None:
            tgtlang = self.target_lang

        if srclang is None and tgtlang is None:
            # Update all files
            return

        if srclang is None or tgtlang is None:
            raise ValueError('Both srclang and tgtlang must be specified')

        if self._skips_language_pair(srclang, tgtlang):
            self.init_matcher()
            return

        if not self.is_update_needed(srclang, tgtlang):
            logging.debug('Skipping update for (%s, %s) language pair' % (srclang, tgtlang))
            localfile = self._get_curr_term_filename(srclang, tgtlang)
            localfile = os.path.join(self.TERMDIR, localfile)
            self.init_matcher(localfile)
            return

        self._update_term_file(srclang, tgtlang)

    def is_update_needed(self, srclang, tgtlang):
        localfile = self._get_curr_term_filename(srclang, tgtlang)
        localfile = os.path.join(self.TERMDIR, localfile)
        if not os.path.isfile(localfile):
            return True
        stats = os.stat(localfile)
        from datetime import datetime

        return (time.mktime(datetime.now().timetuple()) - stats.st_mtime) > self.REFRESH_AGE

    def _check_for_update(self, srclang, tgtlang):
        localfile = self._get_curr_term_filename(srclang, tgtlang)
        localfile = os.path.join(self.TERMDIR, localfile)
        etag = None
        etag_key = self._etag_key(localfile)
        if os.path.isfile(localfile) and etag_key in self.config:
            etag = self.config[etag_key]

        url = self._l10n_URL % {
            'srclang': self._url_lang_code(srclang),
            'tgtlang': self._url_lang_code(tgtlang),
        }

        if not os.path.isfile(localfile):
            localfile = None

        def callback(*args):
            return self._process_header(*args, localfile=localfile)

        if logging.root.level != logging.DEBUG:
            self.client.get(url, callback, etag, download=True)
        else:

            def error_log(request, result):
                logging.debug('Could not get %s: status %d' % (url, request.status))

            self.client.get(url, callback, etag, error_callback=error_log, download=True)

    def _get_ext_from_url(self, url):
        from urllib.parse import urlparse

        filename = os.path.basename(urlparse(url).path)
        if not filename or '.' not in filename:
            return None
        ext = filename.split('.')[-1]
        if not ext:
            ext = None
        return ext

    def _get_ext_from_store_guess(self, content):
        # content is real bytes, not str - BytesIO, not StringIO.
        from io import BytesIO

        from translate.storage.factory import _guess_extension

        s = BytesIO(content)
        try:
            return _guess_extension(s)
        except ValueError:
            pass
        return None

    def _process_header(self, request, result, localfile=None):
        if request.status == 304:
            logging.debug('ETag matches for file %s :)' % (localfile))
        elif request.status == 200:
            if not localfile:
                ext = self._get_ext_from_url(request.get_effective_url())
                if ext is None:
                    ext = self._get_ext_from_store_guess(result)
                if ext is None:
                    logging.debug('Unable to determine extension for store. Defaulting to "po".')
                    ext = 'po'
                localfile = self._get_curr_term_filename(ext=ext)
                localfile = os.path.join(self.TERMDIR, localfile)
            else:
                ext = os.path.splitext(localfile)[1].lstrip(os.extsep)

            if not self._accepts_response(result, ext):
                logging.debug(
                    'Response for %s does not parse as %s - discarding' % (localfile, ext)
                )
                self.init_matcher(localfile if os.path.isfile(localfile) else '')
                return

            logging.debug('Saving to %s' % (localfile))
            # result is real bytes - 'wb' preserves it exactly.
            with open(localfile, 'wb') as f:
                f.write(result)

            # Headers are bytes too; only the stored etag value itself
            # is decoded to str (used later as a str header value).
            headers = request.result_headers.getvalue().splitlines()
            etag = ''
            etagline = [line for line in headers if line.lower().startswith(b'etag:')]
            if etagline:
                etag = etagline[0][7:-1].decode('ascii', errors='replace')
            self.config[self._etag_key(localfile)] = etag
        else:
            logging.debug('Unhandled status code: %d' % (request.status))
            localfile = ''

        if os.path.isfile(localfile):
            # Update mtime
            os.utime(localfile, None)
        self.init_matcher(localfile)

    def _update_term_file(self, srclang, tgtlang):
        """Update the terminology file for the given languages."""
        self.init_matcher()  # Make sure that the matcher is empty until we have an update
        self._check_for_update(srclang, tgtlang)

    # SIGNAL HANDLERS #
    def _on_lang_changed(self, lang_controller, lang, which):
        setattr(self, '%s_lang' % (which), lang)
        self.update_terms(self.source_lang, self.target_lang)
