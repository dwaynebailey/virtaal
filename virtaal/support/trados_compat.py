#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""translate-toolkit's factory opens Virtaal's read-only Trados readers:
.sdltm translation memories, .sdltb termbases and MultiTerm XML (.xml
whose content is a MultiTerm export). The factory can only name classes
inside translate.storage, so its getclass() is wrapped.

translate-toolkit reads a Trados Workbench .txt export but keeps its RTF
codes ({\\cs6\\f1 ...}, \\{, \\'e9) in the text and gives the file no
languages; here the codes are stripped and the store takes the
languages of its first unit's segments."""

import os
import re

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


_RTF_TOKEN = re.compile(
    r"\\'([0-9a-fA-F]{2})|\\u(-?\d+) ?\??|\\([{}\\])|\\([a-zA-Z]+)(-?\d+)? ?|[{}]"
)


def rtf_to_text(text, escapes=None):
    """C{text} from a Workbench segment with its RTF codes stripped:
    groups and control words dropped, escaped characters kept."""
    escapes = escapes or {}

    def replace(match):
        hex_byte, unicode_char, escaped, word, _parameter = match.groups()
        if hex_byte:
            return bytes([int(hex_byte, 16)]).decode("cp1252", "replace")
        if unicode_char:
            return chr(int(unicode_char) % 0x10000)
        if escaped:
            return escaped
        if word:
            return escapes.get("\\" + word, "")
        return ""

    for escape in ("\\~", "\\-", "\\_"):
        if escape in escapes:
            text = text.replace(escape, escapes[escape])
    return _RTF_TOKEN.sub(replace, text)


try:
    from translate.storage import trados
except ImportError:
    # translate-toolkit 3.17 reads Workbench files with BeautifulSoup.
    trados = None

if trados is not None:
    _rtf_escapes = trados.RTF_ESCAPES

    def unescape(text):
        # translate-toolkit 3.17 keeps the line end.
        return rtf_to_text(text, _rtf_escapes).strip()

    trados.unescape = unescape

    def _segment_languages(unit):
        element = getattr(unit, "_element", None)
        if element is not None:
            return [seg.get("l") for seg in element.xpath(".//seg")]
        soup = getattr(unit, "_soup", None)
        if soup is not None:
            return [seg.get("l") for seg in soup.findAll("seg")]
        return []

    _parse = trados.TradosTxtTmFile.parse

    def parse(self, input):
        _parse(self, input)
        if self.units:
            languages = _segment_languages(self.units[0])
            if len(languages) >= 2 and all(languages[:2]):
                self.sourcelanguage, self.targetlanguage = languages[:2]

    trados.TradosTxtTmFile.parse = parse
    TM_IMPORT_FORMATS.append((trados.TradosTxtTmFile.Name, trados.TradosTxtTmFile.Extensions))
