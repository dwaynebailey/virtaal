#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Microsoft's Translation and UI Strings Glossaries as a user downloads
them: CSV files, one per product and language, or a zip of them.

Each CSV opens with Microsoft's notice, then a header row of
Source Term, Translation, String Category, Platform, Product and Version
with blank columns between them. Rows with broken quoting are skipped
and counted in C{skipped}."""

import csv
import io
import logging
import os
import re
import zipfile

from translate.lang import data
from translate.storage import base

HEADER = 'Source Term'
# Enough to reach the header after Microsoft's notice.
HEAD_SIZE = 32768
DOWNLOAD_URL = 'https://my.visualstudio.com/downloads?pid=6822'

# Microsoft's language names that aren't language codes.
LANGUAGES = {'es-latam': 'es_419'}

_LANGUAGE_NAME = re.compile(r'^([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*?)-csv-', re.IGNORECASE)
_LOCALE = re.compile(r'^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$')


def language_of(name):
    """The target language a glossary's file or folder name gives, or None:
    "af-ZA-csv-Outlook.csv" and "af-ZA/Outlook.csv" give "af"."""
    name = name.replace('\\', '/')
    match = _LANGUAGE_NAME.match(os.path.basename(name))
    if match:
        code = match.group(1)
    else:
        folder = os.path.basename(os.path.dirname(name))
        if not _LOCALE.match(folder):
            return None
        code = folder
    code = LANGUAGES.get(code.lower(), code)
    return data.simplify_to_common(code.replace('-', '_'))


def _header_line(text):
    """The index of the header row's first character, or -1."""
    for match in re.finditer(r'^"?%s"?,' % HEADER, text, re.MULTILINE):
        return match.start()
    return -1


def is_glossary_csv(head):
    """Whether C{head}, a CSV file's first bytes, is one of Microsoft's."""
    if isinstance(head, bytes):
        head = head.decode('utf-8-sig', 'replace')
    return _header_line(head) >= 0


def _csv_members(zf):
    return [name for name in zf.namelist() if name.lower().endswith('.csv')]


def is_glossary_zip(storefile):
    """Whether the zip C{storefile} (a path or a seekable file) holds
    Microsoft glossary CSVs."""
    position = None if isinstance(storefile, (str, os.PathLike)) else storefile.tell()
    try:
        with zipfile.ZipFile(storefile) as zf:
            for name in _csv_members(zf):
                with zf.open(name) as f:
                    return is_glossary_csv(f.read(HEAD_SIZE))
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
        return False
    finally:
        if position is not None:
            storefile.seek(position)
    return False


class GlossaryUnit(base.TranslationUnit):
    target_language = None
    key = None

    def getid(self):
        return self.key or super().getid()

    def gettargetlanguage(self):
        return self.target_language or super().gettargetlanguage()


class MicrosoftGlossaryFile(base.TranslationStore):
    """One glossary CSV, or every glossary CSV in a zip."""

    Name = 'Microsoft UI Strings Glossary'
    Extensions = ['csv', 'zip']
    UnitClass = GlossaryUnit

    def __init__(self, inputfile=None, **kwargs):
        self.skipped = 0
        super().__init__(**kwargs)
        self.setsourcelanguage('en')
        if inputfile is not None:
            self.parse(inputfile)

    @classmethod
    def parsefile(cls, storefile):
        store = super().parsefile(storefile)
        name = storefile if isinstance(storefile, str) else getattr(storefile, 'name', '')
        if not store.gettargetlanguage() and name:
            language = language_of(name)
            if language:
                store.settargetlanguage(language)
        return store

    def parse(self, input):
        if hasattr(input, 'read'):
            input = input.read()
        if input[:4] == b'PK\x03\x04':
            with zipfile.ZipFile(io.BytesIO(input)) as zf:
                for name in _csv_members(zf):
                    self._parse_csv(zf.read(name), name, language_of(name))
        else:
            self._parse_csv(input, '', None)
        if self.skipped:
            logging.info('Skipped %d unreadable rows of a Microsoft glossary', self.skipped)

    def _parse_csv(self, content, member, language):
        text = content.decode('utf-8-sig', 'replace') if isinstance(content, bytes) else content
        start = _header_line(text)
        if start < 0:
            return
        reader = csv.reader(io.StringIO(text[start:], newline=''), strict=True)
        header = [name.strip() for name in next(reader)]
        try:
            source_col, target_col = header.index(HEADER), header.index('Translation')
        except ValueError:
            return
        blank = [i for i, name in enumerate(header) if not name]
        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error:
                self.skipped += 1
                continue
            if not any(field.strip() for field in row):
                continue
            if len(row) != len(header) or any(row[i] for i in blank):
                self.skipped += 1
                continue
            source, target = row[source_col], row[target_col]
            if not (source.strip() and target.strip()):
                continue
            unit = self.addsourceunit(source)
            unit.target = target
            unit.target_language = language
            unit.key = '%s:%d' % (member, reader.line_num)
