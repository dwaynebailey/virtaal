#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import functools
import logging

from translate.lang import data
from translate.lang.data import languages as toolkit_langs

from virtaal.common.pan_app import ui_language
from virtaal.support.libi18n import plurals
from virtaal.support.translate_compat import english_name, tr_lang

from .basemodel import BaseModel

gettext_lang = tr_lang(ui_language)


def known_languages():
    """{code: (English name, nplurals, plural)}: translate-toolkit's
    languages, and those plurals.PLURAL_RULES has that it lacks, named
    as ISO 639 names them. translate-toolkit's entry wins."""
    extra = {
        code: (english_name(code) or code, nplurals, plural)
        for code, (nplurals, plural) in plurals.PLURAL_RULES.items()
    }
    return {**extra, **toolkit_langs}


# Every n%10 and n%100 case; Breton's fourth form needs a multiple of a million.
_PLURAL_SAMPLES = (*range(200), *(m * 10**k for k in range(3, 10) for m in range(1, 10)))


@functools.cache
def plural_forms(plural):
    """The forms a plural equation gives, or None if gettext can't read it."""
    import gettext

    try:
        return frozenset(gettext.c2py(plural)(n) for n in _PLURAL_SAMPLES)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def same_language(code, langcode):
    """Whether a file's own language C{code} (e.g. "en-US") is the language
    Virtaal knows as C{langcode} (e.g. "en")."""
    return bool(code) and LanguageModel(code).code == langcode


class LanguageModel(BaseModel):
    """
    A simple container for language information for use by the C{LanguageController}
    and C{LanguageView}.
    """

    __gtype_name__ = 'LanguageModel'

    languages = {}

    # INITIALIZERS #
    def __init__(self, langcode='und'):
        """Constructor.
        Looks up the language information based on the given language code
        (C{langcode})."""
        super().__init__()
        if not self.languages:
            self.languages.update(known_languages())
        self.load(langcode)

    # SPECIAL METHODS #
    def __eq__(self, otherlang):
        """Check that the C{code}, C{nplurals} and C{plural} attributes are the
        same. The C{name} attribute may differ, seeing as it is localised.

        @type  otherlang: LanguageModel
        @param otherlang: The language to compare the current instance to."""
        return (
            isinstance(otherlang, LanguageModel)
            and self.code == otherlang.code
            and self.nplurals == otherlang.nplurals
            and self.plural == otherlang.plural
        )

    # METHODS #
    def load(self, langcode):
        if langcode not in self.languages:
            try:
                langcode = self._match_normalized_langcode(langcode)
            except ValueError:
                langcode = data.simplify_to_common(langcode)
                if langcode not in self.languages:
                    try:
                        langcode = self._match_normalized_langcode(langcode)
                    except ValueError:
                        logging.info("unknown language %s" % langcode)
                        self.name = langcode
                        self.code = langcode
                        self.nplurals = 0
                        self.plural = ""
                        return

        self.name = gettext_lang(self.languages[langcode][0], langcode)
        self.code = langcode
        self.nplurals = self.languages[langcode][1]
        self.plural = self.languages[langcode][2]

    def _match_normalized_langcode(self, langcode):
        languages_keys = list(self.languages.keys())
        normalized_keys = [data.normalize_code(lang) for lang in languages_keys]
        i = normalized_keys.index(data.normalize_code(langcode))
        return languages_keys[i]
