#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Transifex's file formats (a resource's i18n_format) in Virtaal. No
GTK here.

A resource in a format Virtaal reads (NATIVE) downloads as its file;
any other, including one Transifex adds later, is built here from its
strings as XLIFF 2 (build_xliff()), so every resource opens. So is a
native one whose format can't hold its plurals as Virtaal reads them
(holds_plurals false), once it has a string with plurals: Transifex's
JSON and ARB keep a plural as one ICU message, and its XLIFF as
gettext's plural groups, which Virtaal reads a form at a time, while
Transifex stores and takes each plural category's text.

XLIFF 2, not 1.2: its plural, gender and select module (pgs) holds a
string's plural forms as one unit, a segment for each CLDR category
(virtaal.support.xliff2_compat), as Transifex keeps them; XLIFF 1.2's
plural groups open as a unit a form. Each string is a <unit>:

  id                  the string's hash (Transifex's string_hash)
  name                its key
  pgs:switch          "plural:count" for a string with plurals, each
                      <segment>'s pgs:case one of the language's
                      categories, its source the source string's form
                      for it, else its "other"
  slr:sizeRestriction its character_limit, in xliff:codepoints
  <notes>             its developer_comment (category "developer"),
                      instructions, context, occurrences ("location")
                      and tags
  segment state       translated, reviewed, or final for proofread;
                      none (initial) without a translation

PLACEABLES names the placeables (formatplaceables.FORMAT_PARSERS) of a
built file's text by its format: Apple's printf with %@, ICU for ARB,
Ruby's %{name} for Rails YAML, .NET's {0:N2} for .resx; a JSON
resource's by what its strings hold. Printf, Qt and XML tags are found
in every file.
"""

import re

from lxml import etree

from virtaal.support import formatplaceables

NATIVE = {
    'PO': ('{slug}/{lang}.po', None, True),
    'QT': ('{slug}/{lang}.ts', None, True),
    'XLIFF': ('{slug}/{lang}.xlf', None, False),
    'ANDROID': (
        '{slug}/res/values-{android}/strings.xml',
        '{slug}/res/values/strings.xml',
        True,
    ),
    'KEYVALUEJSON': ('{slug}/{lang}.json', '{slug}/{source}.json', False),
    'PROPERTIES': ('{slug}/{slug}_{lang}.properties', '{slug}/{slug}.properties', False),
    'STRINGS': (
        '{slug}/{lang}.lproj/Localizable.strings',
        '{slug}/{source}.lproj/Localizable.strings',
        False,
    ),
    'ARB': ('{slug}/{slug}_{lang}.arb', '{slug}/{slug}_{source}.arb', False),
}
"""i18n_format: (the translation's path, the source-language file's for
a format of one language a file, whether Virtaal reads its plurals as
Transifex stores them)."""

BUILT_PATH = '{slug}/{lang}.xlf'

PLACEABLES = {
    'STRINGS': 'strings',
    'STRINGSDICT': 'strings',
    'PLIST': 'strings',
    'ANDROID': 'android',
    'ARB': 'arb',
    'YML': 'ruby',
    'YML_KEY': 'ruby',
    'RESX': 'dotnet',
    'RESW': 'dotnet',
    'RESJSON': 'dotnet',
}
"""i18n_format: the placeables of a file built for it."""

JSON = ('KEYVALUEJSON', 'STRUCTURED_JSON', 'JSON')
"""Formats whose placeables are judged from their strings."""

NATIVE_KEYED, NATIVE_BILINGUAL, BUILT = 'keyed', 'bilingual', 'built'
"""How a resource opens: its file, of one language a file and matched
by key; its file, with source and target; or built here."""

XLIFF = 'urn:oasis:names:tc:xliff:document:2.0'
PGS = 'urn:oasis:names:tc:xliff:pgs:1.0'
SLR = 'urn:oasis:names:tc:xliff:sizerestriction:2.0'
XML = 'http://www.w3.org/XML/1998/namespace'

_STATES = {'translated': 'translated', 'reviewed': 'reviewed', 'proofread': 'final'}
_NOT_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')


def mode(file_format, pluralized):
    """How a resource in file_format opens, with or without a string
    with plurals."""
    native = NATIVE.get(file_format)
    if native is None or (pluralized and not native[2]):
        return BUILT
    return NATIVE_KEYED if native[1] else NATIVE_BILINGUAL


def paths(file_format, opens, names):
    """(the work file's path, the source file's or None) for a resource
    that opens as opens; names fill them in (slug, lang, source,
    android)."""
    if opens == BUILT:
        return BUILT_PATH.format(**names), None
    translation, template, _plurals = NATIVE[file_format]
    return translation.format(**names), template and template.format(**names)


def placeables(file_format, sources):
    """The placeables' format of a file built for file_format, whose
    source strings are sources, or None."""
    if file_format in JSON:
        return formatplaceables.json_flavour((sources, False))
    return PLACEABLES.get(file_format)


class Entry:
    """One string of a built file: its unit id and key, its source and
    target forms, the plural categories of a string with plurals (else
    None), its Transifex state code, its notes ([(category, text)]) and
    its character limit (or None)."""

    def __init__(self, ident, key, source, target, cases, code, notes=(), limit=None):
        self.ident = ident
        self.key = key
        self.source = list(source)
        self.target = list(target)
        self.cases = cases
        self.code = code
        self.notes = list(notes)
        self.limit = limit


def notes_of(about):
    """[(category, text)] of a resource string's attributes."""
    tags = ', '.join(str(tag) for tag in about.get('tags') or () if tag)
    found = [
        ('developer', about.get('developer_comment')),
        ('instructions', about.get('instructions')),
        ('context', _context(about.get('context'))),
        ('location', about.get('occurrences')),
        ('tags', tags),
    ]
    return [(category, str(text)) for category, text in found if text]


def _context(value):
    if isinstance(value, list):
        return ', '.join(str(v) for v in value if v)
    return value


def limit_of(about):
    try:
        limit = int(about.get('character_limit') or 0)
    except (TypeError, ValueError):
        return None
    return limit if limit > 0 else None


def _text(text):
    return _NOT_XML.sub('', str(text or ''))


def _element(parent, tag, text=None, **attributes):
    element = etree.SubElement(parent, tag, {k: v for k, v in attributes.items() if v})
    if text is not None:
        element.text = _text(text)
    return element


def _segment(unit, source, target, code, case=None):
    segment = _element(unit, '{%s}segment' % XLIFF)
    if case is not None:
        segment.set('{%s}case' % PGS, case)
    _element(segment, '{%s}source' % XLIFF, source)
    if target:
        segment.set('state', _STATES.get(code, 'translated'))
        _element(segment, '{%s}target' % XLIFF, target)


def _unit(parent, entry):
    unit = _element(parent, '{%s}unit' % XLIFF, id=entry.ident, name=_text(entry.key))
    if entry.cases is not None:
        unit.set('{%s}switch' % PGS, 'plural:count')
    if entry.limit:
        unit.set('{%s}sizeRestriction' % SLR, str(entry.limit))
    if entry.notes:
        notes = _element(unit, '{%s}notes' % XLIFF)
        for category, text in entry.notes:
            _element(notes, '{%s}note' % XLIFF, text, category=category)
    if entry.cases is None:
        _segment(unit, entry.source[0], entry.target[0], entry.code)
        return
    for case, source, target in zip(entry.cases, entry.source, entry.target, strict=True):
        _segment(unit, source, target, entry.code, case)


def build_xliff(entries, source_language, language, original):
    """The XLIFF 2 bytes of entries (Entry), from source_language into
    language; original names the resource."""
    nsmap = {None: XLIFF, 'pgs': PGS, 'slr': SLR}
    root = etree.Element(
        '{%s}xliff' % XLIFF, nsmap=nsmap, version='2.2', srcLang=source_language, trgLang=language
    )
    file = _element(root, '{%s}file' % XLIFF, id='f1', original=_text(original))
    file.set('{%s}space' % XML, 'preserve')
    if any(entry.limit for entry in entries):
        _element(file, '{%s}profiles' % SLR, generalProfile='xliff:codepoints')
    for entry in entries:
        _unit(file, entry)
    etree.indent(root, space=' ')
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8') + b'\n'
