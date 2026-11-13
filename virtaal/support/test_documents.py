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
    bundle.contents['tm/shared.tmx'] = b'<tmx/>'
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)

    again = DocumentBundle.open(path, str(tmp_path / 'work'))
    again.save()

    assert _read(path, 'tm/shared.tmx') == b'<tmx/>'


def test_is_bundle_and_open_formats():
    assert documents.is_bundle('/a/manual.zu.VIRTAAL')
    assert not documents.is_bundle('manual.zip')
    assert documents.open_formats() == [('Translation Bundles', ['virtaal', 'zip'])]


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


# Source changes, splitting and merging


@pytest.fixture
def master(tmp_path):
    """A master of two documents in three languages, saved."""
    folder = tmp_path / 'manual'
    folder.mkdir()
    intro = _odt(folder, 'intro.odt', ['Welcome.', 'Read this first.'])
    usage = _odt(folder, 'usage.odt', ['Press the button.'])
    bundle = documents.from_documents([intro, usage], 'en')
    for language in ('zu', 'xh', 'st'):
        bundle.add_language(language)
    bundle.save(str(tmp_path / 'manual.virtaal'))
    return bundle


def _file(bundle, document, language):
    return bundle.work_path(bundle.translation(document, language)['file'])


def test_a_changed_source_updates_every_language(tmp_path, master):
    for language in master.languages:
        _translate(
            _file(master, 'intro.odt', language),
            {'Welcome.': 'Welcome in %s.' % language, 'Read this first.': 'First, %s.' % language},
        )
    new = _docsamples.odt(['Welcome.', 'Read this first, please.', 'A new paragraph.'])

    master.update_document('intro.odt', new)

    for language in master.languages:
        units = _units(_file(master, 'intro.odt', language))
        assert units['Welcome.'] == ('Welcome in %s.' % language, False)
        assert units['Read this first, please.'] == ('First, %s.' % language, True)
        assert units['A new paragraph.'] == ('', False)
    assert master.contents['sources/intro.odt'] == new
    assert master.document('intro.odt')['sha256'] == documents.sha256(new)
    assert master.modified


def test_split_writes_a_bundle_per_language(tmp_path, master):
    out = tmp_path / 'out'
    out.mkdir()

    written = master.split(str(out))

    assert [os.path.basename(p) for p in written] == [
        'manual.zu.virtaal',
        'manual.xh.virtaal',
        'manual.st.virtaal',
    ]
    zu = DocumentBundle.open(written[0], str(tmp_path / 'work-zu'))
    assert zu.languages == ['zu']
    assert zu.suggested_name() == 'manual.zu.virtaal'
    assert {e['language'] for e in zu.translations()} == {'zu'}
    assert zu.split_from()['id'] == master.id
    assert 'baselines/zu/intro.odt.xlf' in master.contents
    assert master.manifest['splits']['zu']['id'] == zu.id
    assert master.modified


def _returned(tmp_path, master, language, translations):
    out = tmp_path / 'out'
    out.mkdir(exist_ok=True)
    path = [p for p in master.split(str(out)) if '.%s.' % language in p][0]
    returned = DocumentBundle.open(path, str(tmp_path / ('work-' + language)))
    for entry in returned.translations():
        _translate(returned.work_path(entry['file']), translations)
    returned.save()
    return DocumentBundle.open(path, str(tmp_path / ('again-' + language)))


def test_merge_brings_back_one_languages_translations(tmp_path, master):
    returned = _returned(tmp_path, master, 'zu', {'Welcome.': 'Wamukelekile.'})

    plan = master.plan_merge(returned)

    assert (plan.language, plan.takes, plan.conflicts) == ('zu', 1, [])
    assert master.apply_merge(plan) == 1
    assert _units(_file(master, 'intro.odt', 'zu'))['Welcome.'] == ('Wamukelekile.', False)
    for language in ('xh', 'st'):
        assert _units(_file(master, 'intro.odt', language))['Welcome.'] == ('', False)


def test_merging_the_same_bundle_again_brings_nothing(tmp_path, master):
    returned = _returned(tmp_path, master, 'zu', {'Welcome.': 'Wamukelekile.'})
    master.apply_merge(master.plan_merge(returned))

    plan = master.plan_merge(returned)

    assert (plan.takes, plan.conflicts, plan.same) == (0, [], 0)


def test_merge_conflicts_keep_mine_take_theirs_or_skip(tmp_path, master):
    from virtaal.support.platforms.submit import KEEP_MINE, SKIP, TAKE_THEIRS

    returned = _returned(
        tmp_path,
        master,
        'zu',
        {
            'Welcome.': 'Theirs one.',
            'Read this first.': 'Theirs two.',
            'Press the button.': 'Same.',
        },
    )
    _translate(
        _file(master, 'intro.odt', 'zu'), {'Welcome.': 'Mine one.', 'Read this first.': 'Mine two.'}
    )
    _translate(_file(master, 'usage.odt', 'zu'), {'Press the button.': 'Same.'})

    plan = master.plan_merge(returned)

    assert [(c.source, c.mine, c.theirs, c.where) for c in plan.conflicts] == [
        ('Welcome.', ['Mine one.'], ['Theirs one.'], 'intro.odt'),
        ('Read this first.', ['Mine two.'], ['Theirs two.'], 'intro.odt'),
    ]
    assert plan.same == 1
    master.apply_merge(plan, {0: TAKE_THEIRS, 1: SKIP})
    units = _units(_file(master, 'intro.odt', 'zu'))
    assert units['Welcome.'] == ('Theirs one.', False)
    assert units['Read this first.'] == ('Mine two.', False)
    # The conflict skipped comes up again; the one decided doesn't.
    again = master.plan_merge(returned)
    assert [c.source for c in again.conflicts] == ['Read this first.']
    master.apply_merge(again, {0: KEEP_MINE})
    assert master.plan_merge(returned).conflicts == []
    assert _units(_file(master, 'intro.odt', 'zu'))['Read this first.'] == ('Mine two.', False)


def test_merge_refuses_a_bundle_from_another_master(tmp_path, master, report):
    other = documents.from_documents([report], 'en', 'zu')
    other.save(str(tmp_path / 'report.zu.virtaal'))

    with pytest.raises(BundleError, match="wasn't split"):
        master.plan_merge(other)


def test_merge_refuses_a_bundle_whose_documents_changed(tmp_path, master):
    returned = _returned(tmp_path, master, 'zu', {'Welcome.': 'Wamukelekile.'})
    returned.add_document(_odt(tmp_path, 'extra.odt', ['Extra.']))

    with pytest.raises(BundleError, match='documents were changed'):
        master.plan_merge(returned)


def test_merge_after_the_source_changed_marks_changed_units_for_review(tmp_path, master):
    returned = _returned(
        tmp_path,
        master,
        'zu',
        {'Welcome.': 'Wamukelekile.', 'Read this first.': 'Funda lokhu kuqala.'},
    )
    master.update_document('intro.odt', _docsamples.odt(['Welcome.', 'Read this first, please.']))

    plan = master.plan_merge(returned)
    master.apply_merge(plan)

    units = _units(_file(master, 'intro.odt', 'zu'))
    assert units['Welcome.'] == ('Wamukelekile.', False)
    assert units['Read this first, please.'] == ('Funda lokhu kuqala.', True)


def test_split_needs_a_language(tmp_path):
    bundle = documents.from_documents(
        [_odt(tmp_path, 'a.odt', ['A.']), _odt(tmp_path, 'b.odt', ['B.'])], 'en'
    )
    with pytest.raises(BundleError, match='no target language'):
        bundle.split(str(tmp_path))


def test_split_copies_only_its_languages_tm_entries(tmp_path, master):
    master.contents['tm/shared.tmx'] = (
        b'<?xml version="1.0"?><tmx version="1.4"><header srclang="en"/><body>'
        b'<tu><tuv xml:lang="en"><seg>Hi</seg></tuv><tuv xml:lang="zu"><seg>Sawubona</seg></tuv>'
        b'<tuv xml:lang="xh"><seg>Molo</seg></tuv></tu>'
        b'<tu><tuv xml:lang="en"><seg>Bye</seg></tuv><tuv xml:lang="xh"><seg>Hamba</seg></tuv></tu>'
        b'</body></tmx>'
    )
    out = tmp_path / 'out'
    out.mkdir()
    zu = master.split(str(out))[0]

    tmx = _read(zu, 'tm/shared.tmx')
    assert b'Sawubona' in tmx
    assert b'Molo' not in tmx
    assert b'Bye' not in tmx


def test_splitting_one_language_again_replaces_only_its_record(tmp_path, master):
    out = tmp_path / 'out'
    out.mkdir()
    master.split(str(out))
    before = json.loads(json.dumps(master.manifest['splits']))
    master.remove_document('usage.odt')
    _translate(_file(master, 'intro.odt', 'zu'), {'Welcome.': 'Wamukelekile.'})

    [path] = master.split(str(tmp_path / 'out'), ['zu', 'fr'])

    assert os.path.basename(path) == 'manual.zu.virtaal'
    splits = master.manifest['splits']
    assert (splits['xh'], splits['st']) == (before['xh'], before['st'])
    assert splits['zu']['id'] != before['zu']['id']
    assert (before['zu']['number'], splits['zu']['number']) == (1, 2)
    assert splits['zu']['sources'] == {'intro.odt': master.document('intro.odt')['sha256']}
    assert DocumentBundle.open(path, str(tmp_path / 'w')).split_from()['number'] == 2
    baselines = sorted(n for n in master.contents if n.startswith('baselines/zu/'))
    assert baselines == ['baselines/zu/intro.odt.xlf']
    assert b'Wamukelekile.' in master.contents['baselines/zu/intro.odt.xlf']
    assert master.split(str(out), ['fr']) == []


# TIPP


def test_export_as_tipp_writes_a_package_per_language_that_virtaal_opens(tmp_path, master):
    from virtaal.support import packages

    page = tmp_path / 'page.html'
    page.write_bytes(_docsamples.html(['One paragraph.']))
    master.add_document(str(page))
    _translate(_file(master, 'intro.odt', 'zu'), {'Welcome.': 'Wamukelekile.'})

    written = master.export_tipp_all(str(tmp_path / 'tipp'))

    assert [os.path.basename(p) for p in written] == [
        'manual.zu.tipp',
        'manual.xh.tipp',
        'manual.st.tipp',
    ]
    package = packages.open_package(written[0], str(tmp_path / 'tipp-work'))
    assert (package.kind, package.package_type, package.version) == ('tipp', 'request', '2.0')
    names = [os.path.basename(p) for p in package.files()]
    assert names == ['intro.odt.xlf', 'usage.odt.xlf', 'page.html.po.xlf']
    intro = factory.getobject(package.files()[0])
    assert {u.source: str(u.target or '') for u in intro.units}['Welcome.'] == 'Wamukelekile.'
    page_units = factory.getobject(package.files()[2])
    assert 'One paragraph.' in [u.source for u in page_units.units]
    manifest = _read(written[0], 'manifest.xml')
    assert b'<TargetLanguage>zu</TargetLanguage>' in manifest


def test_a_bundle_never_saved_has_its_work_folder_removed(report):
    bundle = documents.from_documents([report], 'en', 'af')
    _translate(bundle.files()[0], {'Hello world.': 'Hallo.'})

    assert bundle.discard()
    assert not os.path.exists(bundle.work_dir)


def test_members_and_title(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    assert bundle.title() == 'report.odt'
    assert not bundle.can_return()
    [(path, (inner, member, _digest))] = bundle.members.items()
    assert (path, inner, member) == (bundle.files()[0], None, 'trans/af/report.odt.xlf')
    bundle.save(str(tmp_path / 'report.af.virtaal'))
    assert bundle.title() == 'report.af.virtaal'


def test_a_bundle_is_told_by_its_first_bytes(tmp_path, report):
    # The shared-mime-info magic (share/mime/packages): a zip whose first
    # member, named mimetype and stored, holds the type at offset 38.
    bundle = documents.from_documents([report], 'en', 'af')
    path = tmp_path / 'report.af.virtaal'
    bundle.save(str(path))

    data = path.read_bytes()

    assert data[:4] == b'PK\x03\x04'
    assert data[38 : 38 + len(documents.MIMETYPE)] == documents.MIMETYPE.encode()


# Keeping a bundle tied to its documents


def test_a_changed_source_document_is_noticed(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)
    assert bundle.changed_documents() == []

    Path(report).write_bytes(_docsamples.odt(['Hello world.', 'New.']))

    again = DocumentBundle.open(path, str(tmp_path / 'work'))
    assert again.changed_documents() == [('report.odt', report)]
    again.update_from(report, 'report.odt')
    assert again.changed_documents() == []
    assert 'New.' in _units(again.files()[0])


def test_moving_the_bundle_and_document_together_keeps_them_tied(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.save(str(tmp_path / 'report.af.virtaal'))
    moved = tmp_path / 'moved'
    moved.mkdir()
    os.rename(report, moved / 'report.odt')
    os.rename(tmp_path / 'report.af.virtaal', moved / 'report.af.virtaal')
    (moved / 'report.odt').write_bytes(_docsamples.odt(['Changed.']))

    again = DocumentBundle.open(str(moved / 'report.af.virtaal'), str(tmp_path / 'work'))

    assert again.changed_documents() == [('report.odt', str(moved / 'report.odt'))]


def test_a_deleted_source_document_turns_off_change_detection_only(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)
    os.remove(report)

    again = DocumentBundle.open(path, str(tmp_path / 'work'))

    assert again.changed_documents() == []
    again.export_document('report.odt', 'af', str(tmp_path / 'out.odt'))


def test_update_from_finds_the_document_by_name_or_as_the_only_one(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    other = tmp_path / 'renamed.odt'
    other.write_bytes(_docsamples.odt(['Renamed.']))

    assert bundle.update_from(str(other)) == 'report.odt'
    bundle.add_document(_odt(tmp_path, 'notes.odt', ['A note.']))
    with pytest.raises(BundleError, match='no document renamed.odt'):
        bundle.update_from(str(other))


def test_a_bundle_changed_on_disk_is_not_saved_over(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)
    assert not bundle.changed_on_disk()
    other = DocumentBundle.open(path, str(tmp_path / 'other'))
    os.utime(path, ns=(0, 0))
    other._opened = None

    assert bundle.changed_on_disk()
    with pytest.raises(documents.BundleChangedError):
        bundle.save()
    bundle.save(str(tmp_path / 'elsewhere.virtaal'))
    assert not bundle.changed_on_disk()


def test_an_export_left_as_written_is_replaced_silently(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    out = str(tmp_path / 'report.af.odt')
    assert bundle.edited_exports([out]) == []
    bundle.export_document('report.odt', 'af', out)

    assert bundle.edited_exports([out]) == []
    Path(out).write_bytes(b'edited by hand')
    assert bundle.edited_exports([out]) == [out]
    other = tmp_path / 'other.odt'
    other.write_bytes(b'someone else')
    assert bundle.edited_exports([str(other)]) == [str(other)]


def test_bundles_for_a_document(tmp_path, report):
    assert documents.bundles_for(report) == []
    bundle = documents.from_documents([report], 'en', 'af')
    path = str(tmp_path / 'report.af.virtaal')
    bundle.save(path)
    (tmp_path / 'broken.virtaal').write_bytes(b'not a zip')

    assert documents.bundles_for(report) == [path]
    copy = tmp_path / 'copy.odt'
    copy.write_bytes(Path(report).read_bytes())
    assert documents.bundles_for(str(copy)) == [path]


def test_export_paths(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.add_language('zu')
    assert bundle.export_all_paths('/out') == [
        os.path.join('/out', 'af', 'report.odt'),
        os.path.join('/out', 'zu', 'report.odt'),
    ]


def test_saving_under_a_name_renames_the_project(tmp_path, report):
    bundle = documents.from_documents([report], 'en', 'af')
    bundle.save(str(tmp_path / 'annual.af.virtaal'))
    assert (bundle.name, bundle.suggested_name()) == ('annual', 'annual.af.virtaal')
    bundle.add_language('zu')
    bundle.save(str(tmp_path / 'Annual Report.virtaal'))
    assert bundle.name == 'Annual Report'
