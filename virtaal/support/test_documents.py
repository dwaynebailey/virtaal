#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import os
import zipfile
from pathlib import Path

import pytest
from translate.storage import factory

from virtaal.support import _docsamples, documents
from virtaal.support.documents import BundleError, DocumentBundle


@pytest.fixture(autouse=True)
def _state_dir(tmp_path_factory, monkeypatch):
    from virtaal.common import pan_app

    state = str(tmp_path_factory.mktemp('state'))
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: state)


def _odt(tmp_path, name, paragraphs):
    path = tmp_path / name
    path.write_bytes(_docsamples.odt(paragraphs))
    return str(path)


def _translate(path, translations, fuzzy=()):
    store = factory.getobject(path)
    for unit in store.units:
        if unit.source in translations:
            unit.target = translations[unit.source]
            unit.markfuzzy(unit.source in fuzzy)
    store.save()


def _units(path):
    store = factory.getobject(path)
    return {
        u.source: (str(u.target or ''), u.isfuzzy())
        for u in store.units
        if u.istranslatable() and u.source
    }


def _members(path):
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def _read(path, member):
    with zipfile.ZipFile(path) as archive:
        return archive.read(member)


@pytest.fixture
def report(tmp_path):
    return _odt(tmp_path, 'report.odt', ['Hello world.', 'Second *bold* part.'])


def test_a_document_opens_into_one_language_and_nothing_is_written(tmp_path, report):
    before = sorted(os.listdir(tmp_path))
    bundle = documents.from_documents([report], 'en', 'af')

    assert sorted(os.listdir(tmp_path)) == before
    assert bundle.path is None
    assert bundle.languages == ['af']
    assert bundle.suggested_name() == 'report.af.virtaal'
    [trans] = bundle.files()
    assert os.path.relpath(trans, bundle.work_dir) == os.path.join('af', 'report.odt.xlf')
    assert factory.getobject(trans).gettargetlanguage() == 'af'
    assert bundle.label(trans) == ('report.odt', 'af')


def test_saving_writes_the_translations_and_the_translated_document(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    [trans] = bundle.files()
    _translate(trans, {'Hello world.': 'Hallo wêreld.'})
    path = str(tmp_path / bundle.suggested_name())

    assert bundle.save(path) == []

    members = _members(path)
    assert members[:2] == ['mimetype', 'manifest.json']
    assert _read(path, 'mimetype') == documents.MIMETYPE.encode()
    with zipfile.ZipFile(path) as archive:
        assert archive.getinfo('mimetype').compress_type == zipfile.ZIP_STORED
    assert set(members[2:]) == {
        'sources/report.odt',
        'trans/af/report.odt.xlf',
        'targets/af/report.odt',
    }
    translated = _read(path, 'targets/af/report.odt')
    assert _docsamples.odt_text(translated) == ['Hallo wêreld.', 'Second bold part.']
    manifest = json.loads(_read(path, 'manifest.json'))
    assert manifest['format'] == 'virtaal-bundle'
    assert manifest['source_language'] == 'en'
    assert manifest['target_languages'] == ['af']
    [entry] = manifest['translations']
    assert entry['target_of'] == entry['sha256']
    assert manifest['documents'][0]['original'] == os.path.abspath(report)


def test_the_source_document_is_never_written(tmp_path, report):
    os.chmod(report, 0o444)
    stat = os.stat(report)
    data = Path(report).read_bytes()
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.save(str(tmp_path / 'report.af.virtaal'))
    bundle.export_language('af', str(tmp_path / 'out'))

    with pytest.raises(BundleError):
        bundle.save(report)
    with pytest.raises(BundleError):
        bundle.export_document('report.odt', 'af', report)
    with pytest.raises(BundleError):
        bundle.export_document('report.odt', 'af', bundle.path)
    assert Path(report).read_bytes() == data
    assert os.stat(report).st_mtime == stat.st_mtime


def test_a_saved_bundle_opens_again_with_its_translations(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    _translate(bundle.files()[0], {'Hello world.': 'Hallo wêreld.'})
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)

    again = DocumentBundle.open(path, str(tmp_path / 'work'))

    assert again.languages == ['af']
    assert _units(again.files()[0])['Hello world.'] == ('Hallo wêreld.', False)
    assert again.stale() == []
    assert not again.modified


def test_a_failed_save_leaves_the_old_bundle_as_it_was(tmp_path, report, monkeypatch):
    bundle = documents.from_documents([report], 'en', 'af')
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)
    before = Path(path).read_bytes()
    _translate(bundle.files()[0], {'Hello world.': 'Hallo.'})

    def broken(*_args, **_kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(zipfile.ZipFile, 'writestr', broken)
    with pytest.raises(OSError):
        bundle.save()

    assert Path(path).read_bytes() == before
    assert [n for n in os.listdir(tmp_path) if n.startswith('.virtaal-')] == []


def test_a_translated_document_that_cant_be_made_still_saves_the_translation(
    tmp_path, report, monkeypatch
):
    from virtaal.support import converters

    bundle = documents.from_documents([report], 'en', 'af')
    _translate(bundle.files()[0], {'Hello world.': 'Hallo.'})

    def broken(self, trans, template):
        raise ValueError('no good')

    monkeypatch.setattr(converters.OdfConverter, 'back', broken)
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)

    assert 'targets/af/report.odt' not in _members(path)
    assert bundle.failures == {('report.odt', 'af'): 'no good'}
    again = DocumentBundle.open(path, str(tmp_path / 'work'))
    assert _units(again.files()[0])['Hello world.'] == ('Hallo.', False)
    with pytest.raises(BundleError, match='no good'):
        bundle.export_document('report.odt', 'af', str(tmp_path / 'out.odt'))


def test_large_translated_documents_are_left_for_the_background(tmp_path, report, monkeypatch):
    monkeypatch.setattr(documents, 'INLINE_BYTES', 1)
    bundle = documents.from_documents([report], 'en', 'af')
    path = str(tmp_path / 'report.af.virtaal')

    [left] = bundle.save(path, background=True)

    assert 'targets/af/report.odt' not in _members(path)
    [(entry, converter_id, trans, source)] = bundle.regeneration_jobs([left])
    assert converter_id == 'odf'
    from virtaal.support import converters

    target = converters.document_converter_by_id(converter_id).back(trans, source)
    assert bundle.save_targets([(entry, target, documents.sha256(trans))])
    assert 'targets/af/report.odt' in _members(path)


def test_a_document_made_from_an_older_translation_is_not_kept(tmp_path, report, monkeypatch):
    monkeypatch.setattr(documents, 'INLINE_BYTES', 1)
    bundle = documents.from_documents([report], 'en', 'af')
    [left] = bundle.save(str(tmp_path / 'report.af.virtaal'), background=True)

    assert not bundle.save_targets([(left, b'old', 'another hash')])
    assert bundle.stale() == [left]


def test_several_documents_make_a_master_with_no_target_language(tmp_path):
    folder = tmp_path / 'manual'
    folder.mkdir()
    intro = _odt(folder, 'intro.odt', ['Welcome.'])
    usage = _odt(folder, 'usage.odt', ['Press the button.'])

    bundle = documents.from_documents([intro, usage], 'en', 'af')

    assert bundle.languages == []
    assert bundle.suggested_name() == 'manual.virtaal'
    assert bundle.read_only()
    names = [os.path.relpath(p, bundle.work_dir) for p in bundle.files()]
    assert names == [os.path.join('_templates', n) for n in ('intro.odt.xlf', 'usage.odt.xlf')]
    assert [bundle.label(p) for p in bundle.files()] == [('intro.odt', None), ('usage.odt', None)]


def test_adding_languages_gives_each_document_a_file_per_language(tmp_path):
    intro = _odt(tmp_path, 'intro.odt', ['Welcome.'])
    usage = _odt(tmp_path, 'usage.odt', ['Press the button.'])
    bundle = documents.from_documents([intro, usage], 'en')

    for language in ('zu', 'xh', 'af'):
        bundle.add_language(language)

    assert not bundle.read_only()
    assert bundle.suggested_name() == 'tmp_path.virtaal'.replace('tmp_path', tmp_path.name)
    assert len(bundle.files()) == 6
    assert not os.path.exists(os.path.join(bundle.work_dir, documents.TEMPLATES))
    zu = [p for p in bundle.files() if bundle.label(p)[1] == 'zu']
    assert [factory.getobject(p).gettargetlanguage() for p in zu] == ['zu', 'zu']
    with pytest.raises(BundleError):
        bundle.add_language('zu')


def test_adding_a_document_translates_it_into_every_language(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.add_language('zu')
    other = tmp_path / 'other'
    other.mkdir()
    same_name = _odt(other, 'report.odt', ['Another report.'])

    name = bundle.add_document(same_name)

    assert name == 'report-2.odt'
    assert {(e['document'], e['language']) for e in bundle.translations()} == {
        ('report.odt', 'af'),
        ('report.odt', 'zu'),
        ('report-2.odt', 'af'),
        ('report-2.odt', 'zu'),
    }


def test_a_document_with_nothing_to_translate_is_refused(tmp_path):
    empty = _odt(tmp_path, 'empty.odt', [])
    with pytest.raises(BundleError, match='nothing to translate'):
        documents.from_documents([empty], 'en', 'af')


def test_an_unknown_document_format_is_refused(tmp_path):
    letter = tmp_path / 'letter.docx'
    letter.write_bytes(b'PK')
    bundle = DocumentBundle.create('en', 'letters')
    with pytest.raises(BundleError, match='.docx'):
        bundle.add_document(str(letter))


def test_removing_a_language_and_a_document(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.add_language('zu')
    bundle.add_document(_odt(tmp_path, 'notes.odt', ['A note.']))

    bundle.remove_language('af')
    bundle.remove_document('notes.odt')

    assert bundle.languages == ['zu']
    assert [(e['document'], e['language']) for e in bundle.translations()] == [('report.odt', 'zu')]
    assert not os.path.exists(os.path.join(bundle.work_dir, 'af', 'report.odt.xlf'))


def test_translated_count(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    assert bundle.translated_count('af') == 0
    _translate(bundle.files()[0], {'Hello world.': 'Hallo.'})
    assert bundle.translated_count('af') == 1


def test_export_writes_each_language_into_its_own_folder(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.add_language('zu')
    for path in bundle.files():
        language = bundle.label(path)[1]
        _translate(path, {'Hello world.': 'Hello in %s.' % language})

    written = bundle.export_all(str(tmp_path / 'out'))

    assert sorted(os.path.relpath(p, tmp_path / 'out') for p in written) == [
        os.path.join('af', 'report.odt'),
        os.path.join('zu', 'report.odt'),
    ]
    zu = Path(tmp_path / 'out' / 'zu' / 'report.odt').read_bytes()
    assert _docsamples.odt_text(zu)[0] == 'Hello in zu.'


def test_export_makes_a_stale_translated_document_first(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.save(str(tmp_path / 'report.af.virtaal'))
    _translate(bundle.files()[0], {'Hello world.': 'Hallo.'})

    out = bundle.export_document('report.odt', 'af', str(tmp_path / 'report.af.odt'))

    assert _docsamples.odt_text(Path(out).read_bytes())[0] == 'Hallo.'


def test_needs_work_translations_stay_out_of_the_translated_document(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    _translate(bundle.files()[0], {'Hello world.': 'Hallo.'}, fuzzy=('Hello world.',))

    out = bundle.export_document('report.odt', 'af', str(tmp_path / 'report.af.odt'))

    assert _docsamples.odt_text(Path(out).read_bytes())[0] == 'Hello world.'


def test_discard_keeps_a_work_folder_with_edits_not_saved(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.save(str(tmp_path / 'report.af.virtaal'))
    _translate(bundle.files()[0], {'Hello world.': 'Hallo.'})

    assert not bundle.discard()
    bundle.save()
    assert bundle.discard()
    assert not os.path.exists(bundle.work_dir)


@pytest.mark.parametrize(
    ('change', 'message'),
    [
        (lambda m: m.update(format='other'), 'Not a translation bundle'),
        (lambda m: m.update(version=99), 'newer version'),
        (lambda m: m.pop('documents'), 'documents'),
        (lambda m: m['translations'][0].update(file='../escape.xlf'), 'Unsafe'),
    ],
)
def test_a_bundle_with_a_bad_manifest_is_refused(tmp_path, report, change, message):
    bundle = documents.from_documents([report], 'en', 'af')
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)
    with zipfile.ZipFile(path) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    manifest = json.loads(members['manifest.json'])
    change(manifest)
    members['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)

    with pytest.raises(BundleError, match=message):
        DocumentBundle.open(path, str(tmp_path / 'work'))


def test_a_bundle_missing_a_member_or_not_a_zip_is_refused(tmp_path):
    path = tmp_path / 'broken.virtaal'
    path.write_bytes(b'not a zip')
    with pytest.raises(BundleError, match="can't be read"):
        DocumentBundle.open(str(path))
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', documents.MIMETYPE)
    with pytest.raises(BundleError, match='manifest.json'):
        DocumentBundle.open(str(path))


def test_other_members_are_kept_as_they_are(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.members['tm/shared.tmx'] = b'<tmx/>'
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)

    again = DocumentBundle.open(path, str(tmp_path / 'work'))
    again.save()

    assert _read(path, 'tm/shared.tmx') == b'<tmx/>'


def test_is_bundle_and_open_formats():
    assert documents.is_bundle('/a/manual.zu.VIRTAAL')
    assert not documents.is_bundle('manual.zip')
    assert documents.open_formats() == [('Translation Bundles', ['virtaal'])]


def test_html_and_text_documents(tmp_path):
    page = tmp_path / 'page.html'
    page.write_bytes(_docsamples.html(['One paragraph.']))
    notes = tmp_path / 'notes.txt'
    notes.write_bytes(_docsamples.txt(['A note.']))
    bundle = documents.from_documents([str(page), str(notes)], 'en')
    bundle.add_language('af')
    for path in bundle.files():
        _translate(path, {'One paragraph.': 'Een paragraaf.', 'A note.': "'n Nota."})
        assert factory.getobject(path).gettargetlanguage() == 'af'

    bundle.export_language('af', str(tmp_path / 'af'))

    assert '<p>Een paragraaf.</p>' in (tmp_path / 'af' / 'page.html').read_text('utf-8')
    assert (tmp_path / 'af' / 'notes.txt').read_text('utf-8').startswith("'n Nota.")
