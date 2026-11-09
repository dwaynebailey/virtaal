#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The translation files opened together: several paths, or a directory
scanned for them. No GTK here."""

import os
import re
from collections import Counter

# Bilingual formats a scan picks up. Monolingual ones need a template,
# and templates themselves (.pot) are not translated in place.
SCAN_EXTENSIONS = ('.po', '.xlf', '.xliff', '.sdlxliff', '.mqxliff', '.mxliff', '.txml', '.ts')
TEMPLATE_EXTENSIONS = ('.pot', '.xlt')
SKIP_DIRS = {'node_modules', '__pycache__', 'CVS'}

# How far into a file to look for its language.
_SNIFF_BYTES = 16384
_HEADER_LANGUAGE_RES = (
    re.compile(rb'^"Language:\s*([^\\"]+?)\s*\\n"', re.MULTILINE),
    re.compile(rb'\btarget-language\s*=\s*["\']([^"\']+)["\']'),
    re.compile(rb'<TS\b[^>]*\blanguage\s*=\s*["\']([^"\']+)["\']'),
)
_CODE_RE = re.compile(r'^([a-z]{2,3})(?:[_-]([A-Za-z]{2}|[A-Za-z]{4}|\d{3}))?(?:@\w+)?$')


def known_language(token):
    """The language code token names, or None: one Virtaal knows
    (virtaal.support.languages, or the user's own in langs.ini)."""
    from virtaal.models.langmodel import LanguageModel
    from virtaal.support.languages import known_codes

    match = _CODE_RE.match(token)
    if not match:
        return None
    code = match.group(1)
    if code not in known_codes() and code not in LanguageModel.languages:
        return None
    return token.replace('-', '_')


def header_language(path):
    """The target language a file declares near its start, or None."""
    try:
        with open(path, 'rb') as f:
            head = f.read(_SNIFF_BYTES)
    except OSError:
        return None
    for regex in _HEADER_LANGUAGE_RES:
        match = regex.search(head)
        if match:
            code = match.group(1).decode('utf-8', 'replace').strip()
            if code:
                return code.replace('-', '_')
    return None


_ANDROID_REGION = re.compile(r'^r([A-Z]{2})$')


def folder_language_code(segment):
    """The language code an app's resource folder names, unchecked:
    Android's values-af, values-pt-rBR and values-b+sr+Latn, Apple's
    af.lproj; other folders as they are."""
    if segment.endswith('.lproj'):
        return segment[: -len('.lproj')]
    if not segment.startswith('values-'):
        return segment
    qualifiers = segment[len('values-') :].split('-')
    if qualifiers[0].startswith('b+'):
        return '_'.join(qualifiers[0][2:].split('+'))
    region = len(qualifiers) > 1 and _ANDROID_REGION.match(qualifiers[1])
    return qualifiers[0] + ('_' + region.group(1) if region else '')


def path_language(path, root=None):
    """The language a file's name or directories name: af.po,
    foo.af.po, foo-af.po, af/LC_MESSAGES/foo.po, values-af/strings.xml,
    af.lproj/Localizable.strings."""
    name = os.path.basename(path)
    stem = name.split('.')[0]
    candidates = [stem]
    candidates += name.split('.')[1:-1][::-1]
    candidates += re.split(r'[-.]', stem)[1:][::-1]
    directory = os.path.dirname(os.path.abspath(path))
    stop = os.path.abspath(root) if root else None
    while directory and directory != stop:
        directory, segment = os.path.split(directory)
        if not segment:
            break
        candidates.append(folder_language_code(segment))
    for candidate in candidates:
        code = known_language(candidate)
        if code:
            return code
    return None


def file_language(path, root=None):
    return header_language(path) or path_language(path, root)


def is_template(path):
    return path.lower().endswith(TEMPLATE_EXTENSIONS)


def _walk(directory, wanted):
    found = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.') and d not in SKIP_DIRS)
        for name in filenames:
            if not name.startswith('.') and wanted(name):
                found.append(os.path.join(dirpath, name))
    found.sort(key=lambda path: os.path.relpath(path, directory).split(os.sep))
    return found


def scan_directory(directory):
    """Translation files under directory, sorted by relative path."""
    return _walk(directory, lambda name: name.lower().endswith(SCAN_EXTENSIONS))


def is_monolingual_source(path):
    """Whether C{path} is a monolingual file that others translate, or
    whose place says it's a source-language file (Android's values/)."""
    from virtaal.support import converters, templatefinder

    if not converters.is_monolingual(path):
        return False
    return converters.is_source_path(path) or bool(templatefinder.translations_of(path))


def scan_monolingual_sources(directory):
    """The monolingual source-language files under directory."""
    return scan_monolingual(directory)[1]


def scan_monolingual(directory):
    """([(path, TemplateMatch)], sources, unpaired) of the monolingual
    files under directory: those whose template is known without asking,
    the source-language files, and the others."""
    from virtaal.support import converters, templatefinder

    paired = []
    sources = []
    unpaired = []
    for path in _walk(directory, converters.may_be_monolingual):
        if not converters.in_folder_scan(path, directory):
            continue
        if is_monolingual_source(path):
            sources.append(path)
            continue
        match = templatefinder.known_template(path)
        if match is None:
            unpaired.append(path)
        elif os.path.samefile(match.template, path):
            # The user said it is the source-language file.
            sources.append(path)
        else:
            paired.append((path, match))
    return paired, sources, unpaired


def _scan(path):
    """(files to translate, {monolingual file: TemplateMatch}, monolingual
    source files, monolingual files with no known template) of a file or
    a directory. A monolingual file named on its own whose template isn't
    known opens unpaired."""
    from virtaal.support import converters, templatefinder

    if os.path.isdir(path):
        paired, sources, unpaired = scan_monolingual(path)
        files = scan_directory(path) + [name for name, _match in paired]
        files.sort(key=lambda name: os.path.relpath(name, path).split(os.sep))
        return files, dict(paired), sources, unpaired
    if is_monolingual_source(path):
        return [], {}, [path], []
    match = converters.is_monolingual(path) and templatefinder.known_template(path)
    return [path], ({path: match} if match else {}), [], []


class FileEntry:
    """One file of a set."""

    def __init__(self, path, root=None, template=None):
        self.path = os.path.abspath(path)
        self.root = root
        self.template = template
        """The L{TemplateMatch} a monolingual file is paired with."""
        self._language = template.language if template else None
        self._language_known = bool(self._language)

    @property
    def language(self):
        if not self._language_known:
            self._language = file_language(self.path, self.root)
            self._language_known = True
        return self._language

    @property
    def display_name(self):
        """The path relative to the scanned directory, else the file name."""
        if self.root:
            return os.path.relpath(self.path, self.root)
        return os.path.basename(self.path)

    @property
    def template_name(self):
        """The template's name: beside the file, its own; else its path
        relative to the scanned directory, if any."""
        template = os.path.abspath(self.template.template)
        if os.path.dirname(template) == os.path.dirname(self.path) or not self.root:
            return os.path.basename(template)
        return os.path.relpath(template, self.root)

    def __repr__(self):
        return 'FileEntry(%r)' % self.path


class FileSet:
    """An ordered list of translation files, and the monolingual files
    found with them that aren't translated in place: source-language
    files (C{templates}), and files whose template isn't known without
    asking (C{unpaired})."""

    def __init__(self, entries, root=None, templates=(), unpaired=()):
        self.entries = list(entries)
        self.root = root
        self.templates = list(templates)
        self.unpaired = list(unpaired)

    @classmethod
    def from_paths(cls, paths):
        """A set from files and directories: a directory adds the files a
        scan finds in it. With one directory, paths are shown relative
        to it."""
        paths = [os.path.abspath(p) for p in paths]
        dirs = [p for p in paths if os.path.isdir(p)]
        root = dirs[0] if len(paths) == 1 and dirs else None
        if root is None and len(paths) > 1:
            common = os.path.commonpath(paths)
            root = common if os.path.isdir(common) else os.path.dirname(common)
        entries = []
        templates = []
        unpaired = []
        seen = set()
        for path in paths:
            found, paired, sources, lone = _scan(path)
            for name in found + sources + lone:
                if name in seen:
                    continue
                seen.add(name)
                if name in sources:
                    templates.append(FileEntry(name, root))
                elif name in lone:
                    unpaired.append(FileEntry(name, root))
                else:
                    entries.append(FileEntry(name, root, paired.get(name)))
        return cls(entries, root, templates, unpaired)

    def pair(self, path, match):
        """Take the file C{path} out of L{unpaired}, paired as the
        L{TemplateMatch} C{match} says: a file in its place among
        L{entries}, returned, or one of L{templates} if the user said it
        is the source-language file (C{None} is returned then)."""
        path = os.path.abspath(path)
        # The template too, if it waited: it's a source-language file now.
        done = {path, os.path.abspath(match.template)}
        self.unpaired = [entry for entry in self.unpaired if entry.path not in done]
        if os.path.normcase(os.path.abspath(match.template)) == os.path.normcase(path):
            self.templates.append(FileEntry(path, self.root))
            return None
        entry = FileEntry(path, self.root, match)
        self.entries.insert(self.position(entry), entry)
        return entry

    def position(self, entry):
        """Where C{entry} goes among L{entries}: in path order, as a scan
        lists them."""

        def key(other):
            return os.path.relpath(other.path, self.root or os.path.dirname(other.path)).split(
                os.sep
            )

        wanted = key(entry)
        return sum(1 for other in self.entries if key(other) < wanted)

    def __len__(self):
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __getitem__(self, index):
        return self.entries[index]

    @property
    def paths(self):
        return [entry.path for entry in self.entries]

    def languages(self):
        """How many files have each language; None counts the unknown."""
        return Counter(entry.language for entry in self.entries)

    def for_language(self, code):
        """The files in code's language, plus those of unknown language."""
        from virtaal.models.langmodel import same_language

        def wanted(entries):
            return [
                e
                for e in entries
                # A code Virtaal knows only in part (zh-x-hmn-SDL) is
                # chosen as the files have it.
                if e.language is None or e.language == code or same_language(e.language, code)
            ]

        return FileSet(wanted(self.entries), self.root, self.templates, wanted(self.unpaired))

    def choose_language(self, current=None):
        """The language to open, as the files have it: current's (fr_CA
        for fr) if some file has it, else the most common one. None when
        no file says."""
        from virtaal.models.langmodel import same_language

        counts = self.languages()
        known = [code for code, _n in counts.most_common() if code]
        if not known:
            return None
        if current in known:
            return current
        return next((code for code in known if same_language(code, current)), known[0])
