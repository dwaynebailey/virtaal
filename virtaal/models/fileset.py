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
SCAN_EXTENSIONS = ('.po', '.xlf', '.xliff', '.sdlxliff', '.ts')
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
    """The language code token names, or None."""
    from translate.lang import data

    match = _CODE_RE.match(token)
    if not match or match.group(1) not in data.languages:
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


def path_language(path, root=None):
    """The language a file's name or directories name: af.po,
    foo.af.po, foo-af.po, af/LC_MESSAGES/foo.po."""
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
        candidates.append(segment)
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
    """Whether C{path} is a monolingual file that others translate."""
    from virtaal.support import converters, templatefinder

    return converters.is_monolingual(path) and bool(templatefinder.translations_of(path))


def scan_monolingual_sources(directory):
    """The monolingual source-language files under directory."""
    return scan_monolingual(directory)[1]


def scan_monolingual(directory):
    """([(path, TemplateMatch)], sources) of the monolingual files under
    directory: those whose template is known without asking, and the
    source-language files. Others are left out."""
    from virtaal.support import converters, templatefinder

    paired = []
    sources = []
    for path in _walk(directory, converters.is_monolingual):
        if is_monolingual_source(path):
            sources.append(path)
            continue
        match = templatefinder.known_template(path)
        if match is not None:
            paired.append((path, match))
    return paired, sources


def _scan(path):
    """(files to translate, {monolingual file: TemplateMatch}, monolingual
    source files) of a file or a directory. A monolingual file named on
    its own whose template isn't known opens unpaired."""
    from virtaal.support import converters, templatefinder

    if os.path.isdir(path):
        paired, sources = scan_monolingual(path)
        files = scan_directory(path) + [name for name, _match in paired]
        files.sort(key=lambda name: os.path.relpath(name, path).split(os.sep))
        return files, dict(paired), sources
    if is_monolingual_source(path):
        return [], {}, [path]
    match = converters.is_monolingual(path) and templatefinder.known_template(path)
    return [path], ({path: match} if match else {}), []


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
    """An ordered list of translation files, and the monolingual
    source-language files (C{templates}) found with them, which aren't
    translated in place."""

    def __init__(self, entries, root=None, templates=()):
        self.entries = list(entries)
        self.root = root
        self.templates = list(templates)

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
        seen = set()
        for path in paths:
            found, paired, sources = _scan(path)
            for name in found + sources:
                if name in seen:
                    continue
                seen.add(name)
                if name in sources:
                    templates.append(FileEntry(name, root))
                else:
                    entries.append(FileEntry(name, root, paired.get(name)))
        return cls(entries, root, templates)

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

        return FileSet(
            [e for e in self.entries if e.language is None or same_language(e.language, code)],
            self.root,
            self.templates,
        )

    def choose_language(self, current=None):
        """The language to open: current if some file has it, else the
        most common one. None when no file says."""
        from virtaal.models.langmodel import same_language

        counts = self.languages()
        known = [(code, n) for code, n in counts.most_common() if code]
        if not known:
            return None
        if current and any(same_language(code, current) for code, _n in known):
            return current
        return known[0][0]
