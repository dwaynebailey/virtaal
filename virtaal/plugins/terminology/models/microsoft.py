#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import os

from virtaal.common import pan_app
from virtaal.support.remote_zip import RemoteZipMember

from ..cachedtermmodel import CachedRemoteTerminologyModel
from .pontoon import _is_english

ZIP_URL = (
    'https://download.microsoft.com/download/b/2/d/'
    'b2db7a7c-8d33-47f3-b2c1-ee5e6445cf45/MicrosoftTermCollection.zip'
)

# Microsoft's file in the zip for each language, and the language codes
# it serves; the first code names the cached copy.
MEMBERS = {
    'AFRIKAANS.tbx': ('af',),
    'ALBANIAN.tbx': ('sq',),
    'AMHARIC.tbx': ('am',),
    'ARABIC.tbx': ('ar',),
    'ARMENIAN.tbx': ('hy',),
    'ASSAMESE.tbx': ('as',),
    'AZERBAIJANI (LATIN).tbx': ('az',),
    'BANGLA.tbx': ('bn', 'bn_IN', 'bn_BD'),
    'BASQUE.tbx': ('eu',),
    'BELARUSIAN.tbx': ('be',),
    'BOSNIAN (CYRILLIC).tbx': ('bs@cyrillic', 'bs_Cyrl'),
    'BOSNIAN (LATIN).tbx': ('bs',),
    'BULGARIAN.tbx': ('bg',),
    'BURMESE.tbx': ('my',),
    'CATALAN.tbx': ('ca',),
    'CENTRAL KURDISH.tbx': ('ckb',),
    'CHEROKEE.tbx': ('chr',),
    'CHINESE (SIMPLIFIED).tbx': ('zh_CN', 'zh', 'zh_SG', 'zh_Hans'),
    'CHINESE (TRADITIONAL).tbx': ('zh_TW', 'zh_HK', 'zh_MO', 'zh_Hant'),
    'CROATIAN.tbx': ('hr',),
    'CZECH.tbx': ('cs',),
    'DANISH.tbx': ('da',),
    'DARI.tbx': ('prs', 'fa_AF'),
    'DUTCH.tbx': ('nl',),
    'ESTONIAN.tbx': ('et',),
    'FILIPINO.tbx': ('fil', 'tl'),
    'FINNISH.tbx': ('fi',),
    'FRENCH.tbx': ('fr',),
    'FULAH.tbx': ('ff',),
    'GALICIAN.tbx': ('gl',),
    'GEORGIAN.tbx': ('ka',),
    'GERMAN.tbx': ('de',),
    'GREEK.tbx': ('el',),
    'GUJARATI.tbx': ('gu',),
    'HAUSA (LATIN).tbx': ('ha',),
    'HEBREW.tbx': ('he',),
    'HINDI.tbx': ('hi',),
    'HUNGARIAN.tbx': ('hu',),
    'ICELANDIC.tbx': ('is',),
    'IGBO.tbx': ('ig',),
    'INDONESIAN.tbx': ('id',),
    'INUKTITUT (LATIN).tbx': ('iu',),
    'IRISH.tbx': ('ga',),
    'ISIXHOSA.tbx': ('xh',),
    'ISIZULU.tbx': ('zu',),
    'ITALIAN.tbx': ('it',),
    'JAPANESE.tbx': ('ja',),
    'KANNADA.tbx': ('kn',),
    'KAZAKH.tbx': ('kk',),
    'KHMER.tbx': ('km',),
    "K'ICHE'.tbx": ('quc',),
    'KINYARWANDA.tbx': ('rw',),
    'KISWAHILI.tbx': ('sw',),
    'KONKANI.tbx': ('kok',),
    'KOREAN.tbx': ('ko',),
    'KYRGYZ.tbx': ('ky',),
    'LAO.tbx': ('lo',),
    'LATVIAN.tbx': ('lv',),
    'LITHUANIAN.tbx': ('lt',),
    'LUXEMBOURGISH.tbx': ('lb',),
    'MACEDONIAN.tbx': ('mk',),
    'MALAY.tbx': ('ms',),
    'MALAYALAM.tbx': ('ml',),
    'MALTESE.tbx': ('mt',),
    'MĀORI.tbx': ('mi',),
    'MARATHI.tbx': ('mr',),
    'MONGOLIAN.tbx': ('mn',),
    'NEPALI.tbx': ('ne',),
    'NORWEGIAN BOKMÅL.tbx': ('nb', 'no'),
    'NORWEGIAN NYNORSK.tbx': ('nn',),
    'ODIA.tbx': ('or',),
    'PASHTO.tbx': ('ps',),
    'PERSIAN.tbx': ('fa',),
    'POLISH.tbx': ('pl',),
    'PORTUGUESE (BRAZIL).tbx': ('pt_BR',),
    'PORTUGUESE (PORTUGAL).tbx': ('pt',),
    'PUNJABI (ARABIC).tbx': ('pa_PK', 'pnb', 'pa_Arab'),
    'PUNJABI (GURMUKHI).tbx': ('pa',),
    'QUECHUA.tbx': ('quz', 'qu'),
    'ROMANIAN.tbx': ('ro',),
    'RUSSIAN.tbx': ('ru',),
    'SCOTTISH GAELIC.tbx': ('gd',),
    'SERBIAN (CYRILLIC).tbx': ('sr',),
    'SERBIAN (LATIN).tbx': ('sr@latin', 'sr_Latn'),
    'SESOTHO SA LEBOA.tbx': ('nso',),
    'SETSWANA.tbx': ('tn',),
    'SINDHI.tbx': ('sd',),
    'SINHALA.tbx': ('si',),
    'SLOVAK.tbx': ('sk',),
    'SLOVENIAN.tbx': ('sl',),
    'SPANISH.tbx': ('es',),
    'SWEDISH.tbx': ('sv',),
    'TAJIK (CYRILLIC).tbx': ('tg',),
    'TAMIL.tbx': ('ta',),
    'TATAR (CYRILLIC).tbx': ('tt',),
    'TELUGU.tbx': ('te',),
    'THAI.tbx': ('th',),
    'TIGRINYA.tbx': ('ti',),
    'TURKISH.tbx': ('tr',),
    'TURKMEN.tbx': ('tk',),
    'UKRAINIAN.tbx': ('uk',),
    'URDU.tbx': ('ur',),
    'UYGHUR.tbx': ('ug',),
    'UZBEK (CYRILLIC).tbx': ('uz@cyrillic', 'uz_Cyrl'),
    'UZBEK (LATIN).tbx': ('uz',),
    'VALENCIAN.tbx': ('ca@valencia',),
    'VIETNAMESE.tbx': ('vi',),
    'WAYUU.tbx': ('guc',),
    'WELSH.tbx': ('cy',),
    'WOLOF.tbx': ('wo',),
    'YORUBA.tbx': ('yo',),
}

_BY_CODE = {code.lower(): member for member, codes in MEMBERS.items() for code in codes}

WEEK = 60 * 60 * 24 * 7


def microsoft_member(langcode):
    """The zip member holding C{langcode}'s terms, or None: the code
    itself, else its language without the variant or region."""
    if not langcode:
        return None
    code = langcode.replace('-', '_').lower()
    for candidate in (code, code.split('@')[0], code.split('@')[0].split('_')[0]):
        if candidate in _BY_CODE:
            return _BY_CODE[candidate]
    return None


class TerminologyModel(CachedRemoteTerminologyModel):
    """Microsoft's terminology for the target language, taken from the
    Microsoft Terminology Collection zip with range requests, so only
    that language's file is downloaded (#4074)."""

    __gtype_name__ = 'MicrosoftTerminology'
    # l10n: A terminology source: the terms Microsoft uses in its software
    display_name = _('Microsoft Terminology')
    description = _("The terms used in Microsoft’s software, in your language")
    downloads_from = 'Microsoft'
    terms_url = 'https://learn.microsoft.com/globalization/reference/microsoft-terminology'

    TERMDIR = os.path.join(pan_app.get_config_dir(), 'microsoft')
    DEFAULT_EXT = 'tbx'
    REFRESH_AGE = WEEK

    # HOOKS #
    def _skips_language_pair(self, srclang, tgtlang):
        # Microsoft's terms are all from English.
        return not _is_english(srclang) or microsoft_member(tgtlang) is None

    def _get_curr_term_filename(self, srclang=None, tgtlang=None, ext=None):
        member = microsoft_member(tgtlang or self.target_lang)
        code = MEMBERS[member][0] if member else 'none'
        return code.replace('@', '_') + os.extsep + self.DEFAULT_EXT

    curr_term_filename = property(_get_curr_term_filename)

    # METHODS #
    def _update_term_file(self, srclang, tgtlang):
        localfile = os.path.join(self.TERMDIR, self._get_curr_term_filename(srclang, tgtlang))
        # The cached terms stay in use while Microsoft's file is checked.
        self.init_matcher(localfile)
        member = microsoft_member(tgtlang)
        etag = None
        if os.path.isfile(localfile):
            etag = self.config.get(self._etag_key(localfile)) or None
        RemoteZipMember(
            self.client,
            ZIP_URL,
            lambda names: member if member in names else None,
            lambda data, new_etag: self._on_downloaded(localfile, data, new_etag, tgtlang),
            lambda reason: logging.debug('Microsoft terminology not updated: %s', reason),
            etag,
        ).start()

    def _on_downloaded(self, localfile, data, etag, tgtlang):
        if data is None:
            logging.debug('Microsoft terminology unchanged: %s', localfile)
            if os.path.isfile(localfile):
                os.utime(localfile, None)
            return
        partial = localfile + '.part'
        try:
            with open(partial, 'wb') as f:
                f.write(data)
            os.replace(partial, localfile)
        except OSError as e:
            logging.warning("Can't save Microsoft terminology to %s: %s", localfile, e)
            return
        self.config[self._etag_key(localfile)] = etag or ''
        current = not self._skips_language_pair(self.source_lang, self.target_lang)
        if current and microsoft_member(self.target_lang) == microsoft_member(tgtlang):
            self.init_matcher(localfile)
            self.term_controller.rescan_current_unit()
