#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Translation bundles (.virtaal): documents and their translations in
one zip, a project of source documents translated into any number of
languages. No GTK here.

    mimetype                    application/x-virtaal-bundle, stored first
    manifest.json               what the bundle holds (see docs/bundle_format.rst)
    sources/intro.odt           each source document, as it was added
    trans/zu/intro.odt.xlf      one translatable file per document and language
    targets/zu/intro.odt        the translated documents, made on saving
    baselines/zu/intro.odt.xlf  in a master, each language's file as split
    tm/, terms/                 TMX and TBX files, kept as they are

Opening a bundle takes its translatable files out into a work folder
(packages.work_folder()), where Virtaal edits them as files of a
folder; saving writes them back with the rest. The user's documents are
only ever read."""

import datetime
import hashlib
import io
import json
import os
import shutil
import uuid
import zipfile

from virtaal.support import converters, packages

EXTENSION = 'virtaal'
MIMETYPE = 'application/x-virtaal-bundle'
FORMAT = 'virtaal-bundle'
VERSION = 1
MANIFEST = 'manifest.json'

# The Open dialog's name for them, translated there (devsupport/tmp_strings.py).
NAME = 'Translation Bundles'

TEMPLATES = '_templates'
"""The work folder's folder for the documents' units when the bundle
has no target language."""

INLINE_BYTES = 2 * 1024 * 1024
"""Translated documents are made while saving when their translatable
files come to less than this; larger, they are made in the background
(about a second's work at this size)."""


class BundleError(ValueError):
    """A bundle Virtaal can't open, write or merge."""


def open_formats():
    """(name, extensions) of the bundles, for the Open dialog."""
    return [(NAME, [EXTENSION])]


def is_bundle(path):
    """Whether C{path} is named as a translation bundle."""
    return os.path.splitext(path)[1][1:].lower() == EXTENSION


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _creator():
    from virtaal.__version__ import ver

    try:
        from importlib.metadata import version

        toolkit = version('translate-toolkit')
    except Exception:
        toolkit = ''
    return {'tool': 'Virtaal', 'version': ver, 'toolkit': toolkit}


def new_manifest(source_language, name):
    return {
        'format': FORMAT,
        'version': VERSION,
        'id': 'urn:uuid:%s' % uuid.uuid4(),
        'name': name,
        'created': _now(),
        'creator': _creator(),
        'source_language': source_language,
        'target_languages': [],
        'documents': [],
        'translations': [],
    }


def _store(data, name):
    """The translation store of a translatable file's bytes."""
    from translate.storage import factory

    handle = io.BytesIO(data)
    handle.name = name
    return factory.getobject(handle)


def _with_languages(data, name, source, target):
    store = _store(data, name)
    store.setsourcelanguage(source)
    if target:
        store.settargetlanguage(target)
    return bytes(store)


def updated(template, old, name, source, target):
    """The bytes of the translatable file C{old} brought up to the
    document's new units C{template}: translations of unchanged units
    kept, those of changed units kept as needing review."""
    from translate.convert import pot2po

    store = pot2po.convert_stores(_store(template, name), _store(old, name), fuzzymatching=True)
    store.setsourcelanguage(source)
    store.settargetlanguage(target)
    return bytes(store)


def _replace_file(path, data):
    """Write C{data} to C{path} through a file beside it, replacing it
    only once the new one is whole and on disk."""

    def write(out):
        out.write(data)
        out.flush()
        os.fsync(out.fileno())

    packages._replace_file(path, write)


class DocumentBundle:
    """A translation bundle, open: its manifest and members in memory,
    its translatable files in a work folder."""

    kind = 'virtaal'

    def __init__(self, path=None, work_dir=None, manifest=None):
        self.path = os.path.abspath(path) if path else None
        self.manifest = manifest
        self.contents = {}
        """{member name: bytes} of everything but the manifest and the
        translatable files."""
        self.work_dir = work_dir or packages.work_folder(
            self.path or 'new-%s.%s' % (uuid.uuid4(), EXTENSION)
        )
        self.modified = False
        """Whether the manifest or members changed since the bundle was
        last written."""
        self.failures = {}
        """{(document, language): error} of the translated documents
        that couldn't be made at the last save."""
        if os.path.isdir(self.work_dir):
            shutil.rmtree(self.work_dir)
        os.makedirs(self.work_dir)

    # Opening and creating

    @classmethod
    def create(cls, source_language, name, work_dir=None):
        """A new bundle, not yet written anywhere."""
        return cls(None, work_dir, new_manifest(source_language, name))

    @classmethod
    def open(cls, path, work_dir=None):
        """The bundle C{path}, its translatable files taken out."""
        try:
            return cls._open(path, work_dir)
        except packages.PackageError as exc:
            raise BundleError(str(exc)) from exc

    @classmethod
    def _open(cls, path, work_dir):
        try:
            with zipfile.ZipFile(path) as archive:
                packages._check_sizes(archive.infolist())
                found = {
                    packages._key(i.filename): packages._read(archive, i)
                    for i in archive.infolist()
                    if not i.is_dir()
                }
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, KeyError) as exc:
            raise BundleError("The bundle can't be read: %s" % exc) from exc
        manifest = _manifest(found.pop(MANIFEST, None))
        found.pop('mimetype', None)
        bundle = cls(path, work_dir, manifest)
        for entry in bundle.translations():
            data = found.pop(entry['file'], None)
            if data is None:
                raise BundleError('The bundle has no %s.' % entry['file'])
            bundle._write_work(entry['file'], data)
        bundle.contents = found
        for document in bundle.documents():
            if document['source'] not in found:
                raise BundleError('The bundle has no %s.' % document['source'])
        bundle._write_templates()
        return bundle

    # The manifest

    @property
    def id(self):
        return self.manifest['id']

    @property
    def name(self):
        return self.manifest.get('name') or 'bundle'

    @property
    def source_language(self):
        return self.manifest.get('source_language') or 'en'

    @property
    def languages(self):
        return list(self.manifest['target_languages'])

    def documents(self):
        return list(self.manifest['documents'])

    def document(self, name):
        for document in self.manifest['documents']:
            if document['name'] == name:
                return document
        raise KeyError(name)

    def translations(self, language=None, document=None):
        return [
            entry
            for entry in self.manifest['translations']
            if language in (None, entry['language']) and document in (None, entry['document'])
        ]

    def translation(self, document, language):
        return self.translations(language, document)[0]

    def converter(self, document):
        found = converters.document_converter_by_id(document['converter'])
        if found is None:
            raise BundleError("Virtaal can't convert %s documents." % document['converter'])
        return found

    def split_from(self):
        """The master's record of this bundle as a split one, or None."""
        return self.manifest.get('split_from')

    def suggested_name(self):
        """The bundle's file name: with the language code when it has
        one target language."""
        if len(self.languages) == 1:
            return '%s.%s.%s' % (self.name, self.languages[0], EXTENSION)
        return '%s.%s' % (self.name, EXTENSION)

    # The work folder

    def work_path(self, member):
        """Where the translatable file C{member} (trans/zu/a.odt.xlf) is
        in the work folder (zu/a.odt.xlf)."""
        return os.path.join(self.work_dir, *packages.parts(member)[1:])

    def _write_work(self, member, data):
        path = self.work_path(member)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as out:
            out.write(data)

    def trans_data(self, entry):
        with open(self.work_path(entry['file']), 'rb') as f:
            return f.read()

    def _template_path(self, document):
        return os.path.join(
            self.work_dir, TEMPLATES, self.converter(document).trans_name(document['name'])
        )

    def _template(self, document):
        """The units of a document, untranslated."""
        data = self.contents[document['source']]
        trans = self.converter(document).forward(data, document['name'])
        return _with_languages(trans, document['file_name'], self.source_language, None)

    def _write_templates(self):
        """With no target language, the documents' units to show."""
        folder = os.path.join(self.work_dir, TEMPLATES)
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        if self.languages:
            return
        os.makedirs(folder)
        for document in self.documents():
            with open(self._template_path(document), 'wb') as out:
                out.write(self._template(document))

    def files(self):
        """The files to open: every language's translatable files, or
        with no target language, the documents' units to look at."""
        if not self.languages:
            return [self._template_path(d) for d in self.documents()]
        return [self.work_path(entry['file']) for entry in self.manifest['translations']]

    def read_only(self):
        """Whether the files open are only to look at."""
        return not self.languages

    def label(self, path):
        """The document a work file is the translation of, and its
        language (None for a document's units without one)."""
        path = os.path.abspath(path)
        for entry in self.manifest['translations']:
            if os.path.abspath(self.work_path(entry['file'])) == path:
                return entry['document'], entry['language']
        for document in self.documents():
            if os.path.abspath(self._template_path(document)) == path:
                return document['name'], None
        return None

    def discard(self):
        """Remove the work folder, unless it holds edits not yet in the
        bundle; a bundle never saved has nowhere else to keep them.
        @returns: Whether it was removed."""
        if self.path is not None and (self.modified or self._changed_entries()):
            return False
        return packages.remove_work_folder(self.work_dir)

    @property
    def members(self):
        """{work file: (None, member, its hash as last written)}, as a
        packages.Package has them."""
        return {
            self.work_path(e['file']): (None, e['file'], e.get('sha256'))
            for e in self.manifest['translations']
        }

    def can_return(self):
        return False

    def title(self):
        """The bundle's name for the window title: its file's, or before
        it is saved, the document's."""
        if self.path:
            return os.path.basename(self.path)
        names = [d['name'] for d in self.documents()]
        return names[0] if len(names) == 1 else self.suggested_name()

    def _changed_entries(self):
        changed = []
        for entry in self.manifest['translations']:
            try:
                data = self.trans_data(entry)
            except OSError:
                continue
            if sha256(data) != entry.get('sha256'):
                changed.append(entry)
        return changed

    # Documents and languages

    def _unique_name(self, name):
        taken = {d['name'] for d in self.manifest['documents']}
        stem, extension = os.path.splitext(name)
        number = 1
        while name in taken:
            number += 1
            name = '%s-%d%s' % (stem, number, extension)
        return name

    def add_document(self, path):
        """Add the document C{path}, translated into every language as
        yet untranslated. @returns: Its name in the bundle."""
        converter = converters.document_converter(path)
        if converter is None:
            raise BundleError("Virtaal can't open %s documents." % os.path.splitext(path)[1])
        with open(path, 'rb') as f:
            data = f.read()
        return self.add_document_data(data, os.path.basename(path), os.path.abspath(path))

    def add_document_data(self, data, filename, original=None):
        converter = converters.document_converter(filename)
        if converter is None:
            raise BundleError("Virtaal can't open %s documents." % os.path.splitext(filename)[1])
        name = self._unique_name(filename)
        trans = converter.forward(data, name)
        _check_translatable(trans, converter.trans_name(name))
        document = {
            'name': name,
            'converter': converter.id,
            'source': 'sources/' + name,
            'file_name': converter.trans_name(name),
            'sha256': sha256(data),
        }
        if original:
            document['original'] = original
        self.manifest['documents'].append(document)
        self.contents[document['source']] = data
        for language in self.languages:
            self._add_translation(document, language, trans)
        self._write_templates()
        self.modified = True
        return name

    def _add_translation(self, document, language, trans=None):
        if trans is None:
            trans = self.converter(document).forward(
                self.contents[document['source']], document['name']
            )
        member = 'trans/%s/%s' % (language, document['file_name'])
        data = _with_languages(trans, document['file_name'], self.source_language, language)
        self._write_work(member, data)
        self.manifest['translations'].append(
            {
                'document': document['name'],
                'language': language,
                'file': member,
                'target': 'targets/%s/%s' % (language, document['name']),
            }
        )

    def remove_document(self, name):
        document = self.document(name)
        self.manifest['documents'].remove(document)
        self.contents.pop(document['source'], None)
        for entry in self.translations(document=name):
            self._drop_translation(entry)
        self._write_templates()
        self.modified = True

    def add_language(self, language):
        """Add the target language C{language}: each document's units to
        translate into it."""
        packages.parts(language)
        if language in self.languages:
            raise BundleError('The bundle already has %s.' % language)
        self.manifest['target_languages'].append(language)
        for document in self.documents():
            self._add_translation(document, language)
        self._write_templates()
        self.modified = True

    def translated_count(self, language):
        """How many units are translated into C{language}."""
        count = 0
        for entry in self.translations(language):
            store = _store(self.trans_data(entry), entry['file'])
            count += sum(1 for u in store.units if u.istranslatable() and u.istranslated())
        return count

    def remove_language(self, language):
        self.manifest['target_languages'].remove(language)
        for entry in self.translations(language):
            self._drop_translation(entry)
        self.contents = {
            name: data
            for name, data in self.contents.items()
            if not name.startswith('baselines/%s/' % language)
        }
        self.manifest.get('splits', {}).pop(language, None)
        self._write_templates()
        self.modified = True

    def _drop_translation(self, entry):
        self.manifest['translations'].remove(entry)
        self.contents.pop(entry['target'], None)
        try:
            os.remove(self.work_path(entry['file']))
        except OSError:
            pass

    # Translated documents

    def _fresh(self, entry, data):
        return entry.get('target_of') == sha256(data) and entry['target'] in self.contents

    def stale(self):
        """The translations whose translated document is older than
        their translatable file."""
        return [e for e in self.manifest['translations'] if not self._fresh(e, self.trans_data(e))]

    def make_target(self, entry):
        """Make the translated document of C{entry} now."""
        data = self.trans_data(entry)
        document = self.document(entry['document'])
        try:
            target = self.converter(document).back(data, self.contents[document['source']])
        except Exception as exc:
            self.failures[(entry['document'], entry['language'])] = str(exc) or repr(exc)
            self.contents.pop(entry['target'], None)
            entry.pop('target_of', None)
            return False
        self.set_target(entry, target, sha256(data))
        return True

    def set_target(self, entry, target, made_from):
        """C{target}, made from the translatable file whose hash is
        C{made_from}, as C{entry}'s translated document."""
        self.failures.pop((entry['document'], entry['language']), None)
        self.contents[entry['target']] = target
        entry['target_of'] = made_from

    def regeneration_jobs(self, entries):
        """What a helper process needs to make the translated documents
        of C{entries}: [(entry, converter id, trans bytes, source bytes)]."""
        jobs = []
        for entry in entries:
            document = self.document(entry['document'])
            jobs.append(
                (
                    entry,
                    document['converter'],
                    self.trans_data(entry),
                    self.contents[document['source']],
                )
            )
        return jobs

    # Saving

    def _check_destination(self, path):
        """Refuse to write over a source document."""
        for document in self.documents():
            original = document.get('original')
            if original and _same_file(original, path):
                raise BundleError("A source document can't be replaced by the bundle.")

    def save(self, path=None, background=False):
        """Write the bundle to C{path} (its own path if None): the
        translatable files first, so a translated document that can't be
        made doesn't keep them from being saved.
        @param background: Leave translated documents too large to make
            now (L{INLINE_BYTES}) to the caller.
        @returns: The translations whose documents are left to make."""
        path = os.path.abspath(path or self.path)
        self._check_destination(path)
        stale = self.stale()
        left = []
        if background and sum(len(self.trans_data(e)) for e in stale) >= INLINE_BYTES:
            left = stale
        else:
            for entry in stale:
                self.make_target(entry)
        self._write(path)
        return left

    def _write(self, path):
        trans = {}
        for entry in self.manifest['translations']:
            data = self.trans_data(entry)
            trans[entry['file']] = data
            entry['sha256'] = sha256(data)
        self.manifest['modified'] = _now()
        self.manifest['creator'] = _creator()
        _replace_file(path, _zip(self.manifest, {**self.contents, **trans}))
        self.path = path
        self.modified = False

    def save_targets(self, made):
        """Write the translated documents made in the background:
        C{made} is [(entry, target bytes, hash of the trans file it was
        made from)]; one whose file changed since is left out.
        @returns: Whether the bundle was written."""
        kept = False
        for entry, target, made_from in made:
            if entry in self.manifest['translations'] and made_from == entry.get('sha256'):
                self.set_target(entry, target, made_from)
                kept = True
        if kept and not self.modified and not self._changed_entries():
            self._write(self.path)
            return True
        return False

    def write_back(self):
        """Write the bundle, edits and all (as packages.Package does).
        @returns: Whether it was written."""
        self.save()
        return True

    # Source changes

    def update_document(self, name, data, original=None):
        """Replace the document C{name} by C{data}, a new version of it:
        every language's translatable file is brought up to it
        (L{updated()})."""
        document = self.document(name)
        template = self.converter(document).forward(data, name)
        _check_translatable(template, document['file_name'])
        for entry in self.translations(document=name):
            self._write_work(
                entry['file'],
                updated(
                    template,
                    self.trans_data(entry),
                    document['file_name'],
                    self.source_language,
                    entry['language'],
                ),
            )
        self.contents[document['source']] = data
        document['sha256'] = sha256(data)
        if original:
            document['original'] = original
        self._write_templates()
        self.modified = True

    # Splitting by language, and merging what comes back

    def split(self, folder):
        """Write one bundle for each target language to C{folder}
        (manual.zu.virtaal), for each language's translator. The master
        keeps each language's files as split, to merge what comes back.
        @returns: Their paths."""
        if not self.languages:
            raise BundleError('The bundle has no target language to split.')
        sources = {d['name']: d['sha256'] for d in self.documents()}
        written = []
        for language in self.languages:
            path = os.path.join(folder, '%s.%s.%s' % (self.name, language, EXTENSION))
            self._check_export(path)
            manifest, members = self._split_bundle(language, sources)
            _replace_file(path, _zip(manifest, members))
            for entry in self.translations(language):
                self.contents[_baseline(entry)] = members[entry['file']]
            self.manifest.setdefault('splits', {})[language] = {
                'id': manifest['id'],
                'created': manifest['created'],
                'sources': sources,
            }
            written.append(path)
        self.modified = True
        return written

    def _split_bundle(self, language, sources):
        manifest = json.loads(json.dumps(self.manifest))
        manifest.pop('splits', None)
        manifest.update(
            id='urn:uuid:%s' % uuid.uuid4(),
            created=_now(),
            target_languages=[language],
            translations=[e for e in manifest['translations'] if e['language'] == language],
            split_from={'id': self.id, 'language': language, 'sources': sources},
        )
        members = {d['source']: self.contents[d['source']] for d in self.documents()}
        for entry in manifest['translations']:
            data = self.trans_data(entry)
            members[entry['file']] = data
            entry['sha256'] = sha256(data)
            if entry['target'] in self.contents:
                members[entry['target']] = self.contents[entry['target']]
        for name, data in self.contents.items():
            if name.startswith('tm/') and name.lower().endswith('.tmx'):
                members[name] = tmx_for_language(data, self.source_language, language)
            elif name.startswith(('tm/', 'terms/')):
                members[name] = data
        return manifest, members

    def plan_merge(self, returned):
        """What merging the bundle C{returned}, split from this one and
        sent back, brings into its language: unit by unit, the
        translator's changes to units not changed here since the split,
        and conflicts where both changed. Documents whose source changed
        here since are merged against the split's own copy, then
        brought up to the new source."""
        language = self._split_language(returned)
        record = self.manifest['splits'][language]
        plan = MergePlan(returned, language)
        for document in self.documents():
            name = document['name']
            if name not in record['sources']:
                continue
            entry = self.translation(name, language)
            base = self.contents.get(_baseline(entry))
            incoming = returned.trans_data(returned.translation(name, language))
            if base is None:
                raise BundleError('The bundle has no record of %s as split.' % name)
            if record['sources'][name] != document['sha256']:
                template = self.converter(document).forward(self.contents[document['source']], name)
                args = (document['file_name'], self.source_language, language)
                base = updated(template, base, *args)
                incoming = updated(template, incoming, *args)
            plan.compare(entry, self.trans_data(entry), base, incoming)
        return plan

    def _split_language(self, returned):
        """The language of C{returned} if it was split from this bundle
        and still has the documents it had then."""
        split = returned.split_from() or {}
        language = split.get('language')
        record = self.manifest.get('splits', {}).get(language)
        if split.get('id') != self.id or record is None or language not in self.languages:
            raise BundleError("This bundle wasn't split from the one open.")
        names = {d['name']: d['sha256'] for d in returned.documents()}
        if names != split.get('sources') or set(names) != set(record['sources']):
            raise BundleError("The returned bundle's documents were changed.")
        return language

    def apply_merge(self, plan, choices=None):
        """Write what C{plan} brings in. C{choices}: {conflict index:
        KEEP_MINE, TAKE_THEIRS or SKIP}; a conflict not in it is skipped
        and comes up again at the next merge.
        @returns: How many units changed."""
        from virtaal.support.platforms.submit import KEEP_MINE, TAKE_THEIRS

        choices = choices or {}
        changed = 0
        for part in plan.parts:
            taken = list(part.takes)
            skipped = []
            for number, (index, theirs) in part.conflicts:
                choice = choices.get(number)
                if choice == TAKE_THEIRS:
                    taken.append((index, theirs))
                elif choice != KEEP_MINE:
                    skipped.append(index)
            for index, theirs in taken:
                _take(part.local.units[index], theirs)
            changed += len(taken)
            self._write_work(part.entry['file'], bytes(part.local))
            self.contents[_baseline(part.entry)] = part.baseline(skipped)
        self.modified = True
        return changed

    # TIPP

    def export_tipp(self, language, path):
        """Write a TIPP 2.0 package asking for the translation of the
        documents into C{language}, with this bundle's work so far: the
        translatable files, as XLIFF."""
        self._check_export(path)
        files = []
        for entry in self.translations(language):
            name = entry['file'].split('/')[-1]
            data = self.trans_data(entry)
            if not name.endswith('.xlf'):
                data, name = _as_xliff(data, name), name + '.xlf'
            files.append((name, data))
        manifest = _tipp_manifest(self.source_language, language, [n for n, _d in files])
        members = {'manifest.xml': manifest}
        members.update(('resources/bilingual/' + name, data) for name, data in files)
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        _replace_file(path, out.getvalue())
        return path

    def export_tipp_all(self, folder):
        """One TIPP package for each target language in C{folder}
        (manual.zu.tipp): a TIPP has one language pair."""
        os.makedirs(folder, exist_ok=True)
        return [
            self.export_tipp(language, os.path.join(folder, '%s.%s.tipp' % (self.name, language)))
            for language in self.languages
        ]

    # Export

    def export_document(self, document, language, path):
        """Write the translated document of C{document} into
        C{language} to C{path}."""
        entry = self.translation(document, language)
        self._check_export(path)
        if not self._fresh(entry, self.trans_data(entry)) and not self.make_target(entry):
            raise BundleError(
                "The translated document couldn't be made: %s" % self.failures[(document, language)]
            )
        _replace_file(path, self.contents[entry['target']])
        return path

    def _check_export(self, path):
        if self.path and _same_file(self.path, path):
            raise BundleError("The bundle can't be replaced by a translated document.")
        for document in self.documents():
            original = document.get('original')
            if original and _same_file(original, path):
                raise BundleError("A source document can't be replaced by its translation.")

    def export_language(self, language, folder):
        """Write the translated documents into C{language} to C{folder}.
        @returns: Their paths."""
        os.makedirs(folder, exist_ok=True)
        return [
            self.export_document(d['name'], language, os.path.join(folder, d['name']))
            for d in self.documents()
        ]

    def export_all(self, folder):
        """Write every language's translated documents to
        C{folder}/<language>/."""
        written = []
        for language in self.languages:
            written += self.export_language(language, os.path.join(folder, language))
        return written


def _baseline(entry):
    """The master's copy of a translatable file as split."""
    return 'baselines/' + entry['file'].split('/', 1)[1]


def _value(unit):
    """What a merge compares of a unit: its translation and whether it
    needs work."""
    if unit is None:
        return ('', False)
    return (str(unit.target or ''), bool(unit.isfuzzy()))


def _take(unit, other):
    """Give C{unit} the translation and state of C{other}."""
    if other is None or not other.target:
        unit.target = ''
    else:
        unit.rich_target = other.rich_target
    unit.markfuzzy(bool(other is not None and other.isfuzzy()))


def _keyed(store):
    return {u.getid(): u for u in store.units if u.istranslatable()}


class _MergePart:
    """One translatable file's share of a merge."""

    def __init__(self, entry, local, base, incoming):
        self.entry = entry
        self.local = local
        self.base = base
        self.incoming = incoming
        self.takes = []
        """(index in local, incoming unit) of units changed only there."""
        self.conflicts = []
        """(conflict number, (index in local, incoming unit))."""

    def baseline(self, skipped):
        """The new baseline: the returned file, but with the conflicts
        skipped as they were, so they come up again."""
        baseline = _store(bytes(self.incoming), self.entry['file'])
        units = _keyed(baseline)
        old = _keyed(self.base)
        for index in skipped:
            key = self.local.units[index].getid()
            if key in units:
                _take(units[key], old.get(key))
        return bytes(baseline)


class MergePlan:
    """What a merge of a returned bundle brings in (L{DocumentBundle.plan_merge()})."""

    def __init__(self, returned, language):
        self.returned = returned
        self.language = language
        self.parts = []
        self.conflicts = []
        """Conflict tuples (platforms.submit.Conflict): source, mine,
        theirs and the document's name."""
        self.same = 0
        """Units the translator changed to what this bundle has."""

    @property
    def takes(self):
        return sum(len(part.takes) for part in self.parts)

    def compare(self, entry, local, base, incoming):
        from virtaal.support.platforms.submit import Conflict

        name = entry['file']
        part = _MergePart(entry, _store(local, name), _store(base, name), _store(incoming, name))
        base_units = _keyed(part.base)
        incoming_units = _keyed(part.incoming)
        for index, unit in enumerate(part.local.units):
            theirs = incoming_units.get(unit.getid()) if unit.istranslatable() else None
            if theirs is None:
                continue
            before, mine, now = _value(base_units.get(unit.getid())), _value(unit), _value(theirs)
            if now == before:
                continue
            if now == mine:
                self.same += 1
            elif mine == before:
                part.takes.append((index, theirs))
            else:
                number = len(self.conflicts)
                self.conflicts.append(
                    Conflict(number, str(unit.source), [mine[0]], [now[0]], entry['document'])
                )
                part.conflicts.append((number, (index, theirs)))
        self.parts.append(part)


def tmx_for_language(data, source, language):
    """A TMX file's bytes with only the entries in C{source} and
    C{language}."""
    from lxml import etree

    from virtaal.models.langmodel import same_language

    try:
        root = etree.fromstring(data, packages._PARSER)
    except etree.XMLSyntaxError:
        return data
    lang_attr = '{http://www.w3.org/XML/1998/namespace}lang'
    for tu in list(root.iter('tu')):
        for tuv in list(tu.iter('tuv')):
            code = (tuv.get(lang_attr) or tuv.get('lang') or '').replace('-', '_')
            if not (same_language(code, source) or same_language(code, language)):
                tu.remove(tuv)
        if len(tu.findall('tuv')) < 2:
            tu.getparent().remove(tu)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')


def _as_xliff(data, name):
    from translate.convert import po2xliff

    return po2xliff.po2xliff().convertstore(_store(data, name))


def _bcp47(code):
    return code.replace('_', '-')


def _tipp_manifest(source, target, names):
    """A TIPP 2.0 request manifest for translating the bilingual files
    C{names}, as the specification's samples have it."""
    from lxml import etree

    from virtaal.__version__ import ver

    namespace = packages._TIPP_2

    def add(parent, local, text=None, **attributes):
        element = etree.SubElement(parent, '{%s}%s' % (namespace, local), **attributes)
        element.text = text
        return element

    root = etree.Element('{%s}TIPPManifest' % namespace, nsmap={None: namespace}, version='2.0')
    descriptor = add(root, 'GlobalDescriptor')
    add(descriptor, 'UniquePackageID', 'urn:uuid:%s' % uuid.uuid4())
    creator = add(descriptor, 'Creator')
    add(creator, 'Name', packages._creator_name())
    add(creator, 'ID', packages.HOMEPAGE)
    add(creator, 'Date', _now())
    tool = add(creator, 'Tool')
    add(tool, 'ToolName', 'Virtaal')
    add(tool, 'ToolID', packages.HOMEPAGE)
    add(tool, 'ToolVersion', ver)
    task = add(root, 'TaskRequest')
    add(task, 'TaskType', namespace + 'tasks/translate-strict-bitext')
    add(task, 'SourceLanguage', _bcp47(source))
    add(task, 'TargetLanguage', _bcp47(target))
    bilingual = add(add(root, 'Resources'), 'Bilingual', name='bilingual')
    for sequence, name in enumerate(names, 1):
        resource = add(bilingual, 'File', sequence=str(sequence))
        add(resource, 'Name', name)
        add(resource, 'Location', name)
    if hasattr(etree, 'indent'):
        etree.indent(root, '  ')
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _check_translatable(trans, name):
    store = _store(trans, name)
    if not any(unit.istranslatable() and unit.source for unit in store.units):
        raise BundleError('The file contains nothing to translate.')


def _manifest(data):
    if data is None:
        raise BundleError('The bundle has no %s.' % MANIFEST)
    try:
        manifest = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BundleError("The bundle's manifest can't be read: %s" % exc) from exc
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT:
        raise BundleError('Not a translation bundle.')
    if int(manifest.get('version', 0)) > VERSION:
        raise BundleError('The bundle was made by a newer version of Virtaal.')
    for key in ('target_languages', 'documents', 'translations'):
        if not isinstance(manifest.get(key), list):
            raise BundleError("The bundle's manifest has no %s." % key)
    for entry in manifest['documents'] + manifest['translations']:
        for key in ('source', 'file', 'target'):
            if key in entry:
                packages.parts(entry[key])
    return manifest


def _zip(manifest, members):
    """The bytes of a bundle: its mimetype first and stored, as in an
    OpenDocument file, so it can be told by its first bytes."""
    out = io.BytesIO()
    stamp = datetime.datetime.now().timetuple()[:6]
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo('mimetype', stamp), MIMETYPE, zipfile.ZIP_STORED)
        archive.writestr(
            zipfile.ZipInfo(MANIFEST, stamp),
            json.dumps(manifest, indent=2, ensure_ascii=False) + '\n',
            zipfile.ZIP_DEFLATED,
        )
        for name in sorted(members):
            archive.writestr(zipfile.ZipInfo(name, stamp), members[name], zipfile.ZIP_DEFLATED)
    return out.getvalue()


def from_documents(paths, source_language, target_language=None):
    """A new bundle of the documents C{paths}: into C{target_language}
    if one document, else with no target language yet. Named after the
    document, or the folder of several."""
    if len(paths) == 1:
        name = os.path.splitext(os.path.basename(paths[0]))[0]
    else:
        common = os.path.commonpath([os.path.abspath(p) for p in paths])
        folder = common if os.path.isdir(common) else os.path.dirname(common)
        name = os.path.basename(folder) or 'documents'
    bundle = DocumentBundle.create(source_language, name)
    try:
        if len(paths) == 1 and target_language:
            bundle.add_language(target_language)
        for path in paths:
            bundle.add_document(path)
    except Exception:
        packages.remove_work_folder(bundle.work_dir)
        raise
    return bundle
