#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Which terminology files Add Term can save new terms into."""

import os

from translate.storage import base, factory, qm

from virtaal.support import trados_compat  # noqa: F401 - the factory opens Trados files
from virtaal.support.multiterm import MultiTermXmlFile
from virtaal.support.sdltm import SdltmFile

# Formats that are read but never written.
READ_ONLY_CLASSES = (qm.qmfile, MultiTermXmlFile, SdltmFile)


def is_extendable(filename):
    """Whether Add Term can save a new term into C{filename}: a format
    that is written, in a file that can be written to."""
    if not filename:
        return False
    if os.path.exists(filename) and not os.access(filename, os.W_OK):
        return False
    try:
        store_class = factory.getclass(filename)
    except ValueError:  # a format the factory has no class for
        return False
    return not (
        issubclass(store_class, READ_ONLY_CLASSES)
        or store_class.serialize is base.TranslationStore.serialize
    )
