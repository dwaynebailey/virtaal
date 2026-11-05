#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""translate-toolkit's factory opens Virtaal's read-only Trados readers:
.sdltm translation memories, .sdltb termbases and MultiTerm XML (.xml
whose content is a MultiTerm export). The factory can only name classes
inside translate.storage, so its getclass() is wrapped."""

import os

from translate.storage import factory

from virtaal.support.multiterm import MultiTermXmlFile, SdltbFile, is_multiterm_xml
from virtaal.support.sdltm import SdltmFile

_CLASSES = {"sdltm": SdltmFile, "sdltb": SdltbFile}

# (name, extensions) of the files each chooser offers beyond
# translate-toolkit's supported_files(). The names are in
# devsupport/tmp_strings.py.
TM_IMPORT_FORMATS = [
    (SdltmFile.Name, SdltmFile.Extensions),
    (MultiTermXmlFile.Name, MultiTermXmlFile.Extensions),
]
TERMINOLOGY_FORMATS = [
    (MultiTermXmlFile.Name, MultiTermXmlFile.Extensions),
    (SdltbFile.Name, SdltbFile.Extensions),
]


def _head(storefile, size=2048):
    if isinstance(storefile, (str, os.PathLike)):
        try:
            with open(storefile, "rb") as f:
                return f.read(size)
        except OSError:
            return b""
    try:
        position = storefile.tell()
        head = storefile.read(size)
        storefile.seek(position)
    except (AttributeError, OSError, ValueError):
        return b""
    return head if isinstance(head, bytes) else b""


def _trados_class(storefile, ignore):
    if isinstance(storefile, (str, os.PathLike)):
        name = os.fspath(storefile)
    else:
        name = getattr(storefile, "name", None)
        if not isinstance(name, str):
            return None
    if ignore and name.endswith(ignore):
        name = name[: -len(ignore)]
    ext = os.path.splitext(name)[1][1:].lower()
    if ext in _CLASSES:
        return _CLASSES[ext]
    if ext == "xml" and is_multiterm_xml(_head(storefile)):
        return MultiTermXmlFile
    return None


_getclass = factory.getclass


def getclass(
    storefile,
    localfiletype=None,
    ignore=None,
    classes=None,
    classes_str=None,
    hiddenclasses=None,
):
    # getobject() passes the factory's own table on.
    if localfiletype is None and not classes and classes_str in (None, factory._classes_str):
        storeclass = _trados_class(storefile, ignore)
        if storeclass is not None:
            return storeclass
    return _getclass(storefile, localfiletype, ignore, classes, classes_str, hiddenclasses)


factory.getclass = getclass
