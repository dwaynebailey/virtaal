#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Trados Studio file-based translation memories (.sdltm), read only.

An SDLTM file is an SQLite database. Each row of translation_units holds
its source and target as a serialised .NET <Segment>: <Elements> with
<Text><Value> runs and <Tag> elements, whose <Type> is Start, End,
Standalone, TextPlaceholder, LockedContent, Undefined, UnmatchedStart or
UnmatchedEnd and whose <Anchor> pairs a Start with its End. The row's
translation_memory_id names its row in translation_memories, which gives
the language pair. A unit's fields (FileName, StructureContext, ...) are
in the string_, numeric_, date_ and picklist_attributes tables, named by
the attributes table."""

import logging
import os
import sqlite3
import tempfile
from pathlib import Path

from lxml import etree
from translate.storage import base
from translate.storage.placeables import StringElem, xliff

logger = logging.getLogger(__name__)

_SQLITE_HEADER = b"SQLite format 3\x00"

_TAG_PLACEABLES = {
    "Start": xliff.Bx,
    "UnmatchedStart": xliff.Bx,
    "End": xliff.Ex,
    "UnmatchedEnd": xliff.Ex,
}

_FIELD_QUERY = (
    "SELECT v.translation_unit_id, a.name, v.value FROM {table} v "
    "JOIN attributes a ON a.id = v.attribute_id"
)
_FIELD_TABLES = ("string_attributes", "numeric_attributes", "date_attributes")
_PICKLIST_QUERY = (
    "SELECT p.translation_unit_id, a.name, v.value FROM picklist_attributes p "
    "JOIN picklist_values v ON v.id = p.picklist_value_id "
    "JOIN attributes a ON a.id = v.attribute_id"
)


def segment_elem(xml):
    """The <Segment> XML C{xml} as a StringElem: its text, with each tag
    as an XLIFF placeable."""
    if not xml:
        return StringElem("")
    root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False))
    parts = []
    elements = root.find("Elements")
    for element in elements if elements is not None else ():
        if element.tag == "Text":
            parts.append(element.findtext("Value") or "")
        elif element.tag == "Tag":
            kind = element.findtext("Type") or ""
            anchor = element.findtext("Anchor") or element.findtext("TagID") or ""
            parts.append(_TAG_PLACEABLES.get(kind, xliff.X)(id=anchor))
    return StringElem(parts)


class SdltmUnit(base.TranslationUnit):
    """A translation unit of an SDLTM file, in its own TM's languages."""

    def __init__(self, source=None, source_lang=None, target_lang=None):
        super().__init__(source)
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._id = ""

    def getsourcelanguage(self):
        return self._source_lang

    def gettargetlanguage(self):
        return self._target_lang

    def getid(self):
        return self._id


class SdltmFile(base.TranslationStore):
    """A Trados Studio translation memory, read only."""

    Name = "Trados Studio Translation Memory"
    Mimetypes = []
    Extensions = ["sdltm"]
    UnitClass = SdltmUnit

    def __init__(self, inputfile=None, **kwargs):
        super().__init__(**kwargs)
        self.filename = ""
        if inputfile is not None:
            self.parse(inputfile)

    @classmethod
    def parsefile(cls, storefile):
        store = cls()
        if isinstance(storefile, (str, os.PathLike)):
            store.filename = os.fspath(storefile)
            store._read_path(store.filename)
            return store
        store.filename = getattr(storefile, "name", "")
        data = storefile.read()
        storefile.close()
        store.parse(data)
        return store

    def parse(self, input):
        if isinstance(input, (str, os.PathLike)):
            self._read_path(os.fspath(input))
            return
        if hasattr(input, "read"):
            self.filename = getattr(input, "name", self.filename)
            input = input.read()
        if not input.startswith(_SQLITE_HEADER):
            raise ValueError("not an SDLTM file")
        connection = sqlite3.connect(":memory:")
        try:
            connection.deserialize(input)
        except AttributeError:
            # Python 3.10, or an SQLite built without serialize support.
            connection.close()
            self._read_bytes_from_temp_file(input)
            return
        try:
            self._read(connection)
        finally:
            connection.close()

    def _read_bytes_from_temp_file(self, data):
        fd, path = tempfile.mkstemp(suffix=".sdltm")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            self._read_path(path)
        finally:
            os.remove(path)

    def _read_path(self, path):
        connection = sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True)
        try:
            self._read(connection)
        finally:
            connection.close()

    def _read(self, connection):
        try:
            memories = {
                tm_id: (source_lang, target_lang)
                for tm_id, source_lang, target_lang in connection.execute(
                    "SELECT id, source_language, target_language FROM translation_memories"
                )
            }
            rows = connection.execute(
                "SELECT id, translation_memory_id, source_segment, target_segment "
                "FROM translation_units ORDER BY id"
            ).fetchall()
        except sqlite3.DatabaseError as e:
            raise ValueError("not an SDLTM file: %s" % e) from e
        if len(set(memories.values())) == 1:
            self.sourcelanguage, self.targetlanguage = next(iter(memories.values()))
        fields = self._read_fields(connection)
        several = len(memories) > 1
        for tu_id, tm_id, source_xml, target_xml in rows:
            if tm_id not in memories:
                continue
            try:
                source = segment_elem(source_xml)
                target = segment_elem(target_xml)
            except etree.XMLSyntaxError as e:
                logger.warning("Skipping SDLTM unit %s: %s", tu_id, e)
                continue
            unit = self.UnitClass(str(source), *memories[tm_id])
            unit._rich_source = [source]
            unit.target = str(target)
            unit._rich_target = [target]
            unit._id = "%s:%s" % (tm_id, tu_id) if several else str(tu_id)
            for name, value in fields.get(tu_id, ()):
                unit.addnote("%s: %s" % (name, value))
            self.addunit(unit)

    @staticmethod
    def _read_fields(connection):
        """Each unit's (field name, value) pairs; none for a file
        without the field tables."""
        fields = {}
        queries = [_FIELD_QUERY.format(table=table) for table in _FIELD_TABLES]
        queries.append(_PICKLIST_QUERY)
        for query in queries:
            try:
                rows = connection.execute(query).fetchall()
            except sqlite3.DatabaseError:
                continue
            for tu_id, name, value in rows:
                fields.setdefault(tu_id, []).append((name, value))
        return fields

    def serialize(self, out):
        raise NotImplementedError("SDLTM files are read only")
