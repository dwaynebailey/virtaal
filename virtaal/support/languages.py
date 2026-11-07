#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The languages Virtaal knows without the user's own (langs.ini, which
LanguageModel.languages adds): translate-toolkit's, and those
plurals.PLURAL_RULES has that it lacks."""

import functools

from virtaal.support.libi18n import plurals
from virtaal.support.translate_compat import english_name


def known_languages():
    """{code: (English name, nplurals, plural)}, the table's languages
    named as ISO 639 names them (or by their code). translate-toolkit's
    entry wins."""
    from translate.lang.data import languages as toolkit_langs

    extra = {
        code: (english_name(code) or code, nplurals, plural)
        for code, (nplurals, plural) in plurals.PLURAL_RULES.items()
    }
    return {**extra, **toolkit_langs}


@functools.cache
def known_codes():
    """known_languages()' codes, for checking many names quickly."""
    return frozenset(known_languages())
