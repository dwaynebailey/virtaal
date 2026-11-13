#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Every Transifex resource opens: one in a format Virtaal can't read
(DOCX, SRT, one Transifex adds later) as an XLIFF 2 work copy built
from its strings, with plurals, notes and character limits, which
Submit, Update (strings added and removed) and reopening treat as any
other work copy; one in a format it reads downloads its file; and the
placeables follow the format."""

import os
from types import SimpleNamespace

import pytest
from translate.storage import factory
from translate.storage.workflow import StateEnum

from devsupport.testing.faketransifex import add_formats, demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.transifex import backend, client, formats
from virtaal.plugins.transifex.test_submit import Copy
from virtaal.support import formatplaceables, xliff2_compat
from virtaal.support.platforms import update, workcopy

FORMATS = 'hello-org/formats'


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(pan_app, 'ui_language', 'en')
    monkeypatch.setattr(client.Client, 'POLL_FIRST', 0.01)


@pytest.fixture
def fake():
    server = add_formats(demo()).start()
    yield server
    server.stop()
    assert server.schema_problems == []


class Built(Copy):
    """A work copy of the Formats project, its file opened as Virtaal
    opens an XLIFF (or other) file."""

    def __init__(self, fake, component='guide', language='af'):
        self.fake = fake
        self.language = language
        self.platform = backend.TransifexPlatform('/nonexistent/tx.ini')
        found = []
        self.session().download(FORMATS, component, language, found.append, found.append)
        run_until(lambda: found, timeout=30)
        self.download = found[0]
        self.copy = workcopy.create(
            self.platform, fake.url, FORMATS, component, language, self.download
        )
        self.resource = fake._resource(backend.resource_id(FORMATS, component))
        self.open()

    def open(self):
        self.copy = workcopy.for_file(self.copy.path)
        self.store = factory.getobject(self.copy.path)
        self.units = [u for u in self.store.units if u.istranslatable()]
        self.model = SimpleNamespace(get_units=lambda: self.units, pair=None)
        self.copy.attach(self.model)

    def by_key(self, key):
        return next(u for u in self.units if u.getunitelement().get('name') == key)

    def string(self, key):
        return next(s for s in self.resource['strings'] if s['key'] == key)

    def server_of(self, key):
        return self.string(key)['translations'].get(self.language) or {}

    def tx_of(self, key):
        return self.fake.translation_id(self.resource, self.string(key), self.language)


def _jobs(fake):
    return fake.requests_to('/resource_translations_async_downloads', 'POST')


def _update(copy):
    done = []
    update.Update(
        copy.copy,
        copy.platform,
        copy.session(),
        lambda found, resolve: resolve({}),
        lambda plan, proceed: proceed({}),
        done.append,
    ).start()
    run_until(lambda: done, timeout=30)
    return done[0]


# Built from the strings #


def test_a_docx_resource_opens_as_xliff_built_from_its_strings(fake):
    copy = Built(fake)
    download = copy.download

    assert download.filename == 'guide/af.xlf' and download.template is None
    assert _jobs(fake) == []
    assert isinstance(copy.store, xliff2_compat.xliff2.Xliff2File)
    record = copy.copy.record
    assert (record['origin'], record['platform_format']) == (workcopy.BUILT, 'DOCX')
    # Every string, both "Save"s each its own.
    assert len(copy.copy.matched) == len(download.units) == 4
    assert str(copy.by_key('p2').target) == 'Stoor'
    assert not copy.by_key('p3').target
    assert copy.copy.units[copy.copy.index_of(copy.by_key('p3'))]['id'] == copy.tx_of('p3')


def test_its_states_notes_and_limits_are_the_strings(fake):
    copy = Built(fake)
    welcome, save, again = copy.by_key('p1'), copy.by_key('p2'), copy.by_key('p3')

    assert welcome.get_state_n() == StateEnum.FINAL
    assert save.get_state_n() == StateEnum.UNREVIEWED
    assert again.get_state_n() == StateEnum.EMPTY
    assert welcome.getnotes('developer') == 'The first paragraph.'
    assert welcome.getnotes('instructions') == 'Keep it friendly.'
    assert again.getnotes('location') == 'guide.docx:12'
    assert copy.by_key('files').getnotes('tags') == 'ui, count'
    assert xliff2_compat.size_limit(save) == 6
    assert xliff2_compat.size_limit(welcome) is None
    check = xliff2_compat.SizeRestrictionCheck()
    assert check.check(save) is None
    save.target = 'Stoor dit'
    assert check.check(save) == 'Longer than 6 characters'


def test_a_plural_is_one_unit_of_the_languages_categories(fake):
    copy = Built(fake)
    files = copy.by_key('files')

    assert isinstance(files, xliff2_compat.VariantUnit)
    assert files.plural_tags == ['one', 'other']
    assert list(files.target.strings) == ['%d lêer', '%d lêers']

    russian = Built(fake, language='ru')
    files = russian.by_key('files')
    assert files.plural_tags == ['one', 'few', 'many', 'other']
    assert list(files.source.strings) == ['%d file'] + ['%d files'] * 3
    assert files.get_state_n() == StateEnum.EMPTY


def test_submit_sends_each_string_by_its_categories(fake):
    copy = Built(fake, language='ru')
    copy.by_key('p3').target = 'Сохранить'
    copy.by_key('files').target = ['%d файл', '%d файла', '%d файлов', '%d файла']
    copy.save()

    result = copy.submit()

    assert (result.sent, result.stop) == (2, None)
    assert dict(fake.patched)[copy.tx_of('files')]['strings'] == {
        'one': '%d файл',
        'few': '%d файла',
        'many': '%d файлов',
        'other': '%d файла',
    }
    assert copy.server_of('p3')['strings'] == {'other': 'Сохранить'}
    assert copy.copy.pending() == []


def test_a_built_work_copy_reopens_with_its_changes(fake):
    copy = Built(fake)
    copy.by_key('p3').target = 'Bewaar'
    copy.by_key('p3').set_state_n(StateEnum.NEEDS_REVIEW)
    copy.save()

    copy.open()

    again = copy.by_key('p3')
    assert str(again.target) == 'Bewaar'
    assert again.get_state_n() == StateEnum.NEEDS_REVIEW
    assert copy.copy.pending() == [copy.copy.index_of(again)]


def test_update_takes_others_edits_into_the_built_file(fake):
    copy = Built(fake)
    fake.edit(copy.resource, 'Welcome to the guide.', 'Welkom!')

    result = _update(copy)

    assert result.stop is None and result.downloaded is None
    assert str(copy.by_key('p1').target) == 'Welkom!'


def test_update_builds_the_file_again_for_strings_added_and_removed(fake):
    copy = Built(fake)
    copy.by_key('p3').target = 'Bewaar'
    copy.save()
    fake.add_string(copy.resource, 'p4', 'Print', {'af': ('Druk', 'translated')})
    copy.resource['strings'].remove(copy.string('p1'))

    result = _update(copy)

    assert result.stop is None and result.downloaded is not None
    assert result.lost == []
    copy.open()
    update.carry(copy.copy, result.carried)
    assert sorted(u.getunitelement().get('name') for u in copy.units) == [
        'files',
        'p2',
        'p3',
        'p4',
    ]
    assert str(copy.by_key('p4').target) == 'Druk'
    # The edit not submitted is kept, by its id, to send.
    assert str(copy.by_key('p3').target) == 'Bewaar'
    assert copy.copy.pending() == [copy.copy.index_of(copy.by_key('p3'))]
    assert _jobs(fake) == []


def test_finished_units_of_a_built_file_go_as_they_are_saved(fake):
    from virtaal.plugins.transifex.test_update import Trickling

    copy = Built(fake)
    trickling = Trickling(copy)
    copy.by_key('p3').target = 'Bewaar'
    copy.by_key('files').target = ['%d lêertjie', '%d lêertjies']

    trickling.save()

    assert sorted(i for i, _a in fake.patched) == sorted([copy.tx_of('p3'), copy.tx_of('files')])
    assert copy.server_of('files')['strings'] == {'one': '%d lêertjie', 'other': '%d lêertjies'}
    assert copy.copy.pending() == []


@pytest.mark.parametrize(
    ('component', 'file_format', 'placeables'),
    [
        ('subtitles', 'SRT', None),
        ('future', 'NEW_FORMAT_2027', None),
        ('rails', 'YML_KEY', 'ruby'),
    ],
)
def test_any_other_format_opens_built(fake, component, file_format, placeables):
    copy = Built(fake, component)

    assert copy.copy.record['platform_format'] == file_format
    assert copy.copy.record['placeables'] == placeables
    assert getattr(copy.model, 'placeable_format', None) == placeables
    assert len(copy.copy.matched) == len(copy.download.units) == len(copy.resource['strings'])
    assert _jobs(fake) == []


def test_a_built_unit_shows_its_key_only_when_it_says_something(fake):
    rails, counts, subtitles = Built(fake, 'rails'), Built(fake, 'counts'), Built(fake, 'subtitles')

    assert [u.getlocations() for u in rails.units] == [['en.greeting'], ['en.bye']]
    assert [u.getlocations() for u in counts.units] == [['title'], ['items']]
    assert [u.getlocations() for u in subtitles.units] == [[], []]
    assert [u.getlocations() for u in Built(fake).units] == [[], [], [], ['files']]


def test_rails_placeables_are_rubys(fake):
    copy = Built(fake, 'rails')
    unit = copy.by_key('en.greeting')

    assert formatplaceables.format_of(copy.model, unit) == 'ruby'
    assert formatplaceables.RubyPlaceable in formatplaceables.placeables_for('ruby')


def test_json_with_a_plural_is_built_with_each_category(fake):
    copy = Built(fake, 'counts')

    assert copy.copy.path.endswith(os.path.join('counts', 'af.xlf'))
    assert list(copy.by_key('items').source.strings) == ['{count} item', '{count} items']
    assert copy.copy.record['placeables'] == 'json-vue'


# Downloaded as their own files #


def test_apple_strings_download_with_their_source_file(fake):
    copy = Copy(fake, 'apple', FORMATS, opened=False)
    download = copy.download

    assert download.filename == 'apple/af.lproj/Localizable.strings'
    assert download.template[0] == 'apple/en.lproj/Localizable.strings'
    assert b'"open" = "Open %@";' in download.template[1]
    assert [u.context for u in download.units] == ['open', 'close']
    assert copy.copy.record['origin'] is None
    assert copy.copy.record['platform_format'] == 'STRINGS'
    assert len(_jobs(fake)) == 1


def test_an_arb_downloads_with_its_source_file(fake):
    copy = Copy(fake, 'flutter', FORMATS, opened=False)

    assert copy.download.filename == 'flutter/flutter_af.arb'
    assert copy.download.template[0] == 'flutter/flutter_en.arb'
    assert copy.copy.record['origin'] is None


class _Controller:
    def compare_stats(self, old, new):
        pass


@pytest.mark.parametrize(('component', 'translated'), [('apple', 'open'), ('flutter', 'title')])
def test_a_native_file_opens_paired_and_matches_by_key(fake, component, translated):
    from virtaal.models.storemodel import StoreModel

    copy = Copy(fake, component, FORMATS, opened=False)
    model = StoreModel(
        copy.copy.path, _Controller(), template=copy.copy.template_path(), language='af'
    )

    copy.copy.attach(model)

    assert len(copy.copy.matched) == 2
    unit = next(u for u in copy.copy.matched.values() if u.getid() == translated)
    assert unit.get_state_n() == StateEnum.UNREVIEWED


def test_a_po_resource_still_downloads_as_po(fake):
    copy = Copy(fake)

    assert copy.download.filename == 'messages/af.po'
    assert copy.copy.record['origin'] is None
    assert len(_jobs(fake)) == 1


# The tables #


@pytest.mark.parametrize(
    ('file_format', 'pluralized', 'opens'),
    [
        ('PO', True, formats.NATIVE_BILINGUAL),
        ('QT', True, formats.NATIVE_BILINGUAL),
        ('XLIFF', False, formats.NATIVE_BILINGUAL),
        ('XLIFF', True, formats.BUILT),
        ('ANDROID', True, formats.NATIVE_KEYED),
        ('KEYVALUEJSON', False, formats.NATIVE_KEYED),
        ('KEYVALUEJSON', True, formats.BUILT),
        ('STRINGS', False, formats.NATIVE_KEYED),
        ('ARB', True, formats.BUILT),
        ('STRINGSDICT', True, formats.BUILT),
        ('DOCX', False, formats.BUILT),
        ('UNICODEPROPERTIES', False, formats.BUILT),
        (None, False, formats.BUILT),
    ],
)
def test_how_each_format_opens(file_format, pluralized, opens):
    assert formats.mode(file_format, pluralized) == opens


def test_utf8_properties_are_built_not_downloaded(tmp_path):
    # UNICODEPROPERTIES is UTF-8 .properties. Virtaal tells it from
    # Java's Latin-1 dialect by the file's bytes, so a download still all
    # ASCII would save as Latin-1 once it has an ê.
    from virtaal.support.converters import PropertiesConverter

    template, target = tmp_path / 'r.properties', tmp_path / 'r_af.properties'
    template.write_bytes(b'save=Save\n')
    target.write_bytes(b'save=Save\n')

    saved = PropertiesConverter().to_monolingual(str(template), str(target), {'save': 'Lêer'})

    assert saved == b'save=L\xeaer\n'
    assert 'UNICODEPROPERTIES' not in formats.NATIVE


@pytest.mark.parametrize(
    ('file_format', 'sources', 'expected'),
    [
        ('STRINGSDICT', ['%#@files@'], 'strings'),
        ('ARB', ['{count}'], 'arb'),
        ('YML', ['Hello %{name}'], 'ruby'),
        ('RESX', ['{0:N2}'], 'dotnet'),
        ('STRUCTURED_JSON', ['{{count}} files'], 'json'),
        ('KEYVALUEJSON', ['{count, plural, one {# file} other {# files}}'], 'json-icu'),
        ('DOCX', ['Hello'], None),
        ('HTML', ['<b>Hi</b>'], None),
    ],
)
def test_the_placeables_follow_the_format(file_format, sources, expected):
    assert formats.placeables(file_format, sources) == expected


def test_each_built_formats_placeables_are_known():
    for key in set(formats.PLACEABLES.values()) - {'android'}:
        assert key in formatplaceables.FORMAT_PARSERS


def test_a_built_files_path(fake):
    assert backend.file_paths('DOCX', 'guide', 'pt_BR', 'en') == ('guide/pt_BR.xlf', None)


def test_text_xml_cant_hold_is_left_out():
    data = formats.build_xliff(
        [formats.Entry('h1', 'k\x01', ['A\x02 b'], [''], None, 'untranslated')], 'en', 'af', 'r'
    )
    store = factory.getobject(_named(data))
    assert str(store.units[0].source) == 'A b'


def test_whitespace_is_kept():
    data = formats.build_xliff(
        [formats.Entry('h1', 'k', ['  Two\n lines '], [' Twee '], None, 'translated')],
        'en',
        'af',
        'r',
    )
    unit = factory.getobject(_named(data)).units[0]
    assert (str(unit.source), str(unit.target)) == ('  Two\n lines ', ' Twee ')


def _named(data):
    import io

    stream = io.BytesIO(data)
    stream.name = 'built.xlf'
    return stream


def test_the_hand_server_adds_and_removes_a_string(fake):
    import json
    import urllib.request

    fake.controls = True
    resource = fake._resource(backend.resource_id(FORMATS, 'guide'))

    def post(data):
        request = urllib.request.Request(
            fake.url + '_fake/string/', json.dumps(data).encode('utf-8'), method='POST'
        )
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status

    assert post({'resource': 'hello-org/formats/guide', 'key': 'p9', 'source': 'Quit'}) == 200
    assert resource['strings'][-1]['key'] == 'p9'
    assert post({'resource': 'hello-org/formats/guide', 'key': 'p1', 'remove': True}) == 200
    assert 'p1' not in [s['key'] for s in resource['strings']]
