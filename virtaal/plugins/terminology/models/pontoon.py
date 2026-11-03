#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import os

from translate.lang.data import normalize_code
from translate.storage import factory
from translate.storage.base import TranslationStore

from virtaal.common import pan_app

from ..cachedtermmodel import CachedRemoteTerminologyModel


class TerminologyModel(CachedRemoteTerminologyModel):
    """A terminology back-end to access community-maintained localization terminology.

    Was originally Translate.org.za's own terminology.locamotion.org,
    a set of static per-language-pair files - now decommissioned.
    Replaced with Mozilla's Pontoon terminology
    (pontoon.mozilla.org/terminology/<locale>.tbx), a live,
    actively-maintained source. Two structural differences from the
    old source that matter here: (1) English-source only - Pontoon's
    terminology has no concept of an arbitrary source language,
    every file is English-to-<locale>, so this model only fetches
    anything when the project's own source language is English;
    other source languages get an empty store, same as before this
    file existed at all. (2) TBX-Basic format (ISO 30042:ed-2, root
    <tbx style="dca" type="TBX-Basic">, <conceptEntry> entries), not
    the older MARTIF-rooted TBX (root <martif>, <termEntry>)
    translate-toolkit's own tbx.py module supports - parsed directly
    in this file (_parse_tbx_basic()) rather than waiting on/
    extending translate-toolkit's own TBX support.
    """

    __gtype_name__ = 'PontoonTerminology'
    display_name = _('Mozilla Pontoon')
    description = _('Community localization terminology from Mozilla Pontoon')

    # Locale-only (no source language segment) - see class docstring.
    _l10n_URL = 'https://pontoon.mozilla.org/terminology/%(tgtlang)s.tbx'

    TERMDIR = os.path.join(pan_app.get_config_dir(), 'pontoon')

    # Pontoon only ever serves .tbx - see class docstring.
    DEFAULT_EXT = 'tbx'

    # METHODS #
    def _load_store(self, filename):
        if filename.lower().endswith('.tbx'):
            # Pontoon's export is the TBX-Basic dialect, which
            # translate-toolkit's own tbx.py doesn't parse (it
            # targets the older MARTIF-rooted TBX) - see this
            # class's own docstring. Parsed directly here instead.
            with open(filename, 'rb') as f:
                return _parse_tbx_basic(f.read())
        return factory.getobject(filename)

    def _skips_language_pair(self, srclang, tgtlang):
        if _is_english(srclang):
            return False
        # Pontoon's terminology is English-source only (see class
        # docstring) - nothing to fetch for any other source
        # language. Same end state as before this source existed:
        # an empty store, no placeable matches, no error.
        logging.debug(
            'Source language %r is not English - Pontoon terminology has nothing to offer, using an empty store'
            % (srclang,)
        )
        return True

    def _url_lang_code(self, langcode):
        return _pontoon_locale_code(langcode)


def _is_english(langcode):
    """Whether the given language code is some variety of English
    (en, en_US, en-GB, ...) - Pontoon's terminology is English-source
    only, see TerminologyModel's own docstring."""
    normalized = normalize_code(langcode)
    return bool(normalized) and normalized.split('-')[0] == 'en'


def _pontoon_locale_code(langcode):
    """Convert a Virtaal-style language code (e.g. "en_GB") to the exact
    form Pontoon's own static file naming expects. Two things
    normalize_code() alone doesn't get right for this specific server:
    it lowercases the whole code (Pontoon's server is case-sensitive
    and serves "en-GB.tbx", not "en-gb.tbx" - the lowercase form
    404s), and Virtaal's own codes use an underscore
    separator where Pontoon's file names use a hyphen. Language
    subtag lowercase, region subtag uppercase - the standard BCP 47
    casing convention, not something Pontoon invented."""
    normalized = normalize_code(langcode)
    parts = normalized.split('-')
    if len(parts) > 1:
        parts[1] = parts[1].upper()
    return '-'.join(parts)


# TBX-Basic dialect (ISO 30042:ed-2) namespace - what Pontoon actually
# exports. Distinct from, and structurally incompatible with, the older
# MARTIF-rooted TBX translate-toolkit's own tbx.py module supports (see
# TerminologyModel's own docstring).
_TBX_BASIC_NS = 'urn:iso:std:iso:30042:ed-2'
_XML_NS = 'http://www.w3.org/XML/1998/namespace'


def _parse_tbx_basic(content):
    """Parse a TBX-Basic (ISO 30042:ed-2) document's bytes into a plain
    C{TranslationStore}, one unit per concept entry that has both an
    English source term and a target-language term. Malformed or
    genuinely empty input (e.g. a locale Pontoon has no terminology
    for - a valid header with a completely empty <body>) both just
    produce an empty store, matching this model's existing "nothing
    to show" behaviour rather than raising."""
    store = TranslationStore()
    from lxml import etree

    try:
        root = etree.fromstring(content)
    except etree.Error as e:
        logging.debug('Could not parse TBX-Basic content: %s' % (e,))
        return store

    ns = {'t': _TBX_BASIC_NS, 'xml': _XML_NS}
    for entry in root.findall('.//t:conceptEntry', ns):
        source_term = None
        target_term = None
        for langsec in entry.findall('t:langSec', ns):
            lang = langsec.get('{%s}lang' % (_XML_NS,)) or ''
            term_el = langsec.find('t:termSec/t:term', ns)
            if term_el is None or not term_el.text:
                continue
            if _is_english(lang):
                source_term = term_el.text
            else:
                # First non-English langSec found is the target - a
                # concept entry only ever carries the one target locale
                # that was requested (Pontoon builds the file per
                # locale), so there's nothing to disambiguate between
                # multiple targets here.
                target_term = term_el.text
        if source_term and target_term:
            unit = store.addsourceunit(source_term)
            unit.target = target_term

    return store
