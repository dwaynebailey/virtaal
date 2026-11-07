#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""MultiTerm termbases, read only: MultiTerm XML exports (.xml) and
Trados Studio termbases (.sdltb).

A MultiTerm XML file is <mtf> with one <conceptGrp> per concept. A
concept has a <languageGrp> per language, named by <language lang="EN-US"
type="English"/>, each with a <termGrp> per term (<term>); fields are
<descripGrp><descrip type="Definition">...</descrip></descripGrp> at the
concept, language or term level. GlobalSight writes the same with an
<entries> root and <language name="English" locale="en"/>.

An SDLTB file is a Jet 4.0 (Access) database whose mtConcepts table holds
each concept as the same XML with abbreviated names: cG, lG, l, tG, t, dG,
d. Reading it needs the optional access-parser module.

A termbase can hold many languages, and a terminology store is a pair:
its units are the source-language terms of every concept that has both
languages, each with the first target-language term as its target. The
other target terms and the fields are the unit's notes."""

import logging
import os
import tempfile
from io import BytesIO

from lxml import etree
from translate.storage import base

from virtaal.support.languages import known_languages

logger = logging.getLogger(__name__)

_FULL = {
    "concept": "conceptGrp",
    "language_group": "languageGrp",
    "language": "language",
    "term_group": "termGrp",
    "term": "term",
    "field_group": "descripGrp",
    "field": "descrip",
}
_ABBREVIATED = {
    "concept": "cG",
    "language_group": "lG",
    "language": "l",
    "term_group": "tG",
    "term": "t",
    "field_group": "dG",
    "field": "d",
}

_PARSER_OPTIONS = {"resolve_entities": False, "no_network": True}


class MissingReaderError(ImportError):
    """Reading this termbase needs a module that isn't installed."""


def is_multiterm_xml(head):
    """Whether C{head}, the start of a file, is a MultiTerm XML export."""
    for bom, encoding in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if head.startswith(bom):
            head = head[len(bom) :].decode(encoding, "ignore").encode("utf-8")
            break
    return b"<mtf" in head or b"<conceptGrp" in head


def _language_names():
    return {name.lower(): code for code, (name, _n, _p) in known_languages().items()}


def _fields(element, names):
    """(type, text) of each field directly in C{element}'s field groups."""
    fields = []
    for group in element.findall(names["field_group"]):
        for field in group.findall(names["field"]):
            text = " ".join("".join(field.itertext()).split())
            if text:
                fields.append((field.get("type") or "", text))
    return fields


def read_concept(element, names, language_names):
    """C{element}'s concept: its fields, and per language code that
    language's fields and its (term, fields) list."""
    languages = {}
    for group in element.findall(names["language_group"]):
        language = group.find(names["language"])
        if language is None:
            continue
        code = language.get("lang") or language.get("locale")
        if not code:
            name = language.get("type") or language.get("name") or ""
            code = language_names.get(name.lower(), name)
        if not code:
            continue
        terms = []
        for term_group in group.findall(names["term_group"]):
            term = (term_group.findtext(names["term"]) or "").strip()
            if term:
                terms.append((term, _fields(term_group, names)))
        if terms:
            entry = languages.setdefault(code, {"fields": [], "terms": []})
            entry["fields"] += _fields(group, names)
            entry["terms"] += terms
    return {"fields": _fields(element, names), "languages": languages}


def _language_key(code):
    return code.replace("_", "-").lower()


def _primary(code):
    return _language_key(code).split("-")[0]


def _find_language(codes, wanted):
    """The code in C{codes} for language C{wanted}: the same tag, else
    the first with the same primary language."""
    if not wanted:
        return None
    key = _language_key(wanted)
    for code in codes:
        if _language_key(code) == key:
            return code
    return next((code for code in codes if _primary(code) == _primary(wanted)), None)


def _format_fields(fields):
    return ["%s: %s" % (kind, text) if kind else text for kind, text in fields]


class MultiTermUnit(base.TranslationUnit):
    """A term of a MultiTerm concept with its translation."""


class MultiTermXmlFile(base.TranslationStore):
    """A MultiTerm XML termbase export, read as one language pair."""

    Name = "MultiTerm XML Termbase"
    Mimetypes = []
    Extensions = ["xml"]
    UnitClass = MultiTermUnit

    def __init__(self, inputfile=None, **kwargs):
        super().__init__(**kwargs)
        self.filename = ""
        self.concepts = []
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
        content = storefile.read()
        storefile.close()
        store.parse(content)
        return store

    def _read_path(self, path):
        with open(path, "rb") as f:
            self.parse(f.read())

    def parse(self, input):
        if isinstance(input, (str, os.PathLike)):
            self._read_path(os.fspath(input))
            return
        if hasattr(input, "read"):
            self.filename = getattr(input, "name", self.filename)
            input = input.read()
        root = etree.parse(BytesIO(input), etree.XMLParser(**_PARSER_OPTIONS)).getroot()
        language_names = _language_names()
        self.concepts = [
            read_concept(concept, _FULL, language_names) for concept in root.iter("conceptGrp")
        ]
        self._choose_default_languages()

    def languages(self):
        """The termbase's language codes, the most used first."""
        counts = {}
        for concept in self.concepts:
            for code in concept["languages"]:
                counts[code] = counts.get(code, 0) + 1
        return sorted(counts, key=lambda code: -counts[code])

    def _choose_default_languages(self):
        codes = self.languages()
        source = _find_language(codes, "en") or (codes[0] if codes else None)
        target = next((code for code in codes if _primary(code) != _primary(source)), None)
        self._build_units(source, target)

    def select_languages(self, source_lang, target_lang):
        """Make the store the termbase's terms from C{source_lang} into
        C{target_lang} if it has both; the pair kept otherwise.
        @returns: Whether the units changed."""
        codes = self.languages()
        source = _find_language(codes, source_lang)
        if not source:
            return False
        target = _find_language(
            [code for code in codes if _primary(code) != _primary(source)], target_lang
        )
        if not target or (source, target) == (self.sourcelanguage, self.targetlanguage):
            return False
        self._build_units(source, target)
        return True

    def _build_units(self, source, target):
        self.units = []
        self.sourcelanguage = source
        self.targetlanguage = target
        if not (source and target):
            return
        for concept in self.concepts:
            languages = concept["languages"]
            source_entry = languages.get(_find_language(languages, source))
            target_entry = languages.get(_find_language(languages, target))
            if not (source_entry and target_entry) or source_entry is target_entry:
                continue
            (first_target, first_fields), *others = target_entry["terms"]
            target_notes = _format_fields(target_entry["fields"] + first_fields)
            for term, fields in others:
                if fields:
                    term = "%s (%s)" % (term, "; ".join(_format_fields(fields)))
                target_notes.append(term)
            for term, fields in source_entry["terms"]:
                unit = self.UnitClass(term)
                unit.target = first_target
                for note in _format_fields(concept["fields"] + fields) + target_notes:
                    unit.addnote(note)
                self.addunit(unit)

    def serialize(self, out):
        raise NotImplementedError("MultiTerm termbases are read only")


class SdltbFile(MultiTermXmlFile):
    """A Trados Studio / MultiTerm termbase, read with access-parser."""

    Name = "MultiTerm Termbase"
    Extensions = ["sdltb"]

    def parse(self, input):
        if isinstance(input, (str, os.PathLike)):
            self._read_path(os.fspath(input))
            return
        if hasattr(input, "read"):
            self.filename = getattr(input, "name", self.filename)
            input = input.read()
        # access-parser reads only from a path.
        fd, path = tempfile.mkstemp(suffix=".sdltb")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(input)
            self._read_path(path)
        finally:
            os.remove(path)

    def _read_path(self, path):
        try:
            from access_parser import AccessParser
        except ImportError as e:
            raise MissingReaderError(
                "Reading a .sdltb termbase needs the access-parser module"
            ) from e
        try:
            texts = AccessParser(path).parse_table("mtConcepts").get("text", [])
        except Exception as e:
            raise ValueError("not an SDLTB termbase: %s" % e) from e
        parser = etree.XMLParser(**_PARSER_OPTIONS)
        language_names = _language_names()
        self.concepts = []
        for text in texts:
            if not text:
                continue
            try:
                element = etree.fromstring(text.strip().encode("utf-8"), parser)
            except etree.XMLSyntaxError as e:
                logger.warning("Skipping a concept of %s: %s", path, e)
                continue
            self.concepts.append(read_concept(element, _ABBREVIATED, language_names))
        self._choose_default_languages()
