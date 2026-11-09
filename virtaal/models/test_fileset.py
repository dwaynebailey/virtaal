#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

import pytest

from virtaal.models.fileset import (
    FileSet,
    header_language,
    path_language,
    scan_directory,
)
from virtaal.support.converters import FluentConverter


def _po(path, language=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = '"Language: %s\\n"\n' % language if language else ''
    path.write_text('msgid ""\nmsgstr ""\n%s\nmsgid "Open"\nmsgstr ""\n' % header)
    return str(path)


def test_scan_finds_translation_files_in_path_order_without_templates_or_hidden_dirs(tmp_path):
    _po(tmp_path / 'po' / 'zu.po')
    _po(tmp_path / 'po' / 'af.po')
    _po(tmp_path / 'po' / 'virtaal.pot')
    _po(tmp_path / 'help' / 'af.xlf')
    _po(tmp_path / '.git' / 'af.po')
    _po(tmp_path / 'node_modules' / 'x' / 'af.po')
    (tmp_path / 'README').write_text('not a translation')

    found = [os.path.relpath(p, tmp_path) for p in scan_directory(str(tmp_path))]

    assert found == [
        os.path.join('help', 'af.xlf'),
        os.path.join('po', 'af.po'),
        os.path.join('po', 'zu.po'),
    ]


@pytest.mark.parametrize(
    'relpath, expected',
    [
        ('po/af.po', 'af'),
        ('po/pt_BR.po', 'pt_BR'),
        ('gnome-shell.master.de.po', 'de'),
        ('messages-fr.po', 'fr'),
        ('locale/zu/LC_MESSAGES/messages.po', 'zu'),
        ('locale/pt-BR/LC_MESSAGES/messages.po', 'pt_BR'),
        ('strings/messages.po', None),
        # Luganda: from the plural table, not translate-toolkit.
        ('virtaal.lg.po', 'lg'),
        ('lg/LC_MESSAGES/messages.po', 'lg'),
        ('po/lg.po', 'lg'),
        ('res/values-af/strings.xml', 'af'),
        ('res/values-pt-rBR/strings.xml', 'pt_BR'),
        ('res/values-b+sr+Latn/strings.xml', 'sr_Latn'),
        ('res/values/strings.xml', None),
        ('res/values-night/strings.xml', None),
        ('af.lproj/Localizable.strings', 'af'),
        ('pt-BR.lproj/Localizable.strings', 'pt_BR'),
    ],
)
def test_path_language_reads_the_file_name_then_its_directories(tmp_path, relpath, expected):
    assert path_language(str(tmp_path / relpath), str(tmp_path)) == expected


def test_header_language_comes_before_the_path(tmp_path):
    path = _po(tmp_path / 'de' / 'messages.po', language='nl')

    assert header_language(path) == 'nl'
    assert FileSet.from_paths([str(tmp_path)]).entries[0].language == 'nl'


def test_header_language_of_an_xliff_file(tmp_path):
    path = tmp_path / 'doc.xlf'
    path.write_text(
        '<xliff version="1.2"><file source-language="en" target-language="pt-BR" '
        'datatype="plaintext" original="x"><body/></file></xliff>'
    )

    assert header_language(str(path)) == 'pt_BR'


def test_a_directory_is_relative_to_itself_and_several_files_to_their_folder(tmp_path):
    a = _po(tmp_path / 'po' / 'af.po')
    b = _po(tmp_path / 'help' / 'af.po')

    assert [e.display_name for e in FileSet.from_paths([str(tmp_path / 'po')])] == ['af.po']
    names = [e.display_name for e in FileSet.from_paths([a, b])]
    assert names == [os.path.join('po', 'af.po'), os.path.join('help', 'af.po')]


def test_a_file_given_twice_is_listed_once(tmp_path):
    a = _po(tmp_path / 'af.po')

    assert FileSet.from_paths([a, str(tmp_path)]).paths == [a]


def test_the_current_language_is_chosen_when_a_file_has_it(tmp_path):
    _po(tmp_path / 'af' / 'a.po')
    _po(tmp_path / 'zu' / 'a.po')
    _po(tmp_path / 'zu' / 'b.po')
    fileset = FileSet.from_paths([str(tmp_path)])

    assert fileset.choose_language('af') == 'af'
    assert fileset.choose_language('de') == 'zu'


def test_for_language_keeps_unknown_languages(tmp_path):
    _po(tmp_path / 'af.po')
    _po(tmp_path / 'zu.po')
    _po(tmp_path / 'notes.po')
    fileset = FileSet.from_paths([str(tmp_path)])

    names = [e.display_name for e in fileset.for_language('af')]

    assert names == ['af.po', 'notes.po']


def _write(root, relpath, text='a=b\n'):
    path = root.joinpath(*relpath.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return str(path)


def test_a_folder_scan_keeps_monolingual_sources_out_of_the_files(tmp_path):
    po = _write(tmp_path, 'po/af.po', 'msgid "a"\nmsgstr "b"\n')
    en = _write(tmp_path, 'i18n/en.properties')
    af = _write(tmp_path, 'i18n/af.properties')
    _write(tmp_path, 'i18n/lone.properties')

    files = FileSet.from_paths([str(tmp_path)])

    assert files.paths == [af, po]
    assert [entry.path for entry in files.templates] == [en]
    assert [entry.path for entry in files.for_language('af').templates] == [en]


def test_a_folder_scan_pairs_monolingual_files_with_their_templates(tmp_path):
    en = _write(tmp_path, 'app/Messages.properties')
    af = _write(tmp_path, 'app/Messages_af.properties')
    _write(tmp_path, 'app/Messages_de.properties')
    _write(tmp_path, 'app/lone.properties')

    files = FileSet.from_paths([str(tmp_path)])

    assert [e.display_name for e in files] == [
        os.path.join('app', 'Messages_af.properties'),
        os.path.join('app', 'Messages_de.properties'),
    ]
    assert [(e.template.template, e.language) for e in files] == [(en, 'af'), (en, 'de')]
    assert [e.path for e in files.for_language('af')] == [af]
    assert files[0].template_name == 'Messages.properties'


@pytest.mark.skipif(
    not FluentConverter().available(), reason='translate-toolkit before 3.18 has no fluent2po'
)
def test_a_template_in_another_folder_is_named_from_the_scanned_one(tmp_path):
    _write(tmp_path, 'locales/en-US/main.ftl', 'hello = Hello\n')
    _write(tmp_path, 'locales/af/main.ftl', 'hello = Hallo\n')

    entry = FileSet.from_paths([str(tmp_path)])[0]

    assert entry.template_name == os.path.join('locales', 'en-US', 'main.ftl')


def test_a_chosen_template_pairs_a_file_in_a_folder_scan(tmp_path):
    from virtaal.support import baseline

    en = _write(tmp_path, 'strings.properties')
    target = _write(tmp_path, 'vertaal/strings.properties')
    baseline.remember_template(target, en)

    entry = FileSet.from_paths([str(tmp_path)])[0]

    assert (entry.path, entry.template.template, entry.template.how) == (
        target,
        en,
        'remembered',
    )


def test_a_folder_scan_lists_monolingual_files_with_no_known_template_apart(tmp_path):
    _write(tmp_path, 'app/Messages.properties')
    _write(tmp_path, 'app/Messages_af.properties')
    lone = _write(tmp_path, 'app/lone.properties')
    de = _write(tmp_path, 'de/extra.properties')

    files = FileSet.from_paths([str(tmp_path)])

    assert [e.display_name for e in files] == [os.path.join('app', 'Messages_af.properties')]
    assert [e.path for e in files.unpaired] == [lone, de]
    assert all(e.template is None for e in files.unpaired)
    # Like the files, those in another language are left out.
    assert [e.path for e in files.for_language('af').unpaired] == [lone]


def test_a_file_the_user_called_its_own_source_is_a_source_in_a_folder_scan(tmp_path):
    from virtaal.support import baseline

    _write(tmp_path, 'app/Messages.properties')
    _write(tmp_path, 'app/Messages_af.properties')
    own = _write(tmp_path, 'app/lone.properties')
    baseline.remember_template(own, own)

    files = FileSet.from_paths([str(tmp_path)])

    assert own in [e.path for e in files.templates]
    assert files.unpaired == []


def test_a_monolingual_file_named_on_its_own_without_a_template_stays_unpaired(tmp_path):
    lone = _write(tmp_path, 'lone.properties')
    po = _write(tmp_path, 'af.po', 'msgid "a"\nmsgstr "b"\n')

    files = FileSet.from_paths([lone, po])

    assert [(e.path, e.template) for e in files] == [(lone, None), (po, None)]


def test_path_language_knows_a_language_the_user_added(tmp_path, monkeypatch):
    from virtaal.models.langmodel import LanguageModel

    assert path_language(str(tmp_path / 'qqy' / 'messages.po'), str(tmp_path)) is None
    monkeypatch.setitem(LanguageModel.languages, 'qqy', ('Mine', 2, '(n != 1)'))

    assert path_language(str(tmp_path / 'qqy' / 'messages.po'), str(tmp_path)) == 'qqy'


def test_a_folder_scan_counts_luganda_files(tmp_path):
    _po(tmp_path / 'po' / 'lg.po')
    _po(tmp_path / 'lg' / 'help.po')

    _po(tmp_path / 'zu' / 'help.po')

    files = FileSet.from_paths([str(tmp_path)])

    assert files.languages() == {'lg': 2, 'zu': 1}
    assert len(files.for_language('lg')) == 2


ANDROID = '<resources>\n    <string name="a">%s</string>\n</resources>\n'


def test_a_folder_scan_pairs_an_android_project(tmp_path):
    values = _write(tmp_path, 'app/src/main/res/values/strings.xml', ANDROID % 'Open')
    af = _write(tmp_path, 'app/src/main/res/values-af/strings.xml', ANDROID % 'Maak oop')
    _write(
        tmp_path,
        'app/src/main/res/values/colors.xml',
        '<resources><color name="c">#000</color></resources>',
    )
    _write(tmp_path, 'app/src/main/res/values-night/strings.xml', ANDROID % 'Open')
    _write(tmp_path, 'app/src/main/AndroidManifest.xml', '<manifest/>')

    files = FileSet.from_paths([str(tmp_path)])

    assert [(e.path, e.template.template, e.language) for e in files] == [(af, values, 'af')]
    assert [e.path for e in files.templates] == [values]
    assert files.unpaired == []


def test_an_android_default_strings_file_alone_is_a_source(tmp_path):
    values = _write(tmp_path, 'res/values/strings.xml', ANDROID % 'Open')
    po = _write(tmp_path, 'po/af.po', 'msgid "a"\nmsgstr "b"\n')

    files = FileSet.from_paths([str(tmp_path)])

    assert files.paths == [po]
    assert [e.path for e in files.templates] == [values]


def test_a_folder_scan_takes_in_json_only_where_the_path_names_a_language(tmp_path):
    en = _write(tmp_path, 'public/locales/en/translation.json', '{"a": "Open"}')
    af = _write(tmp_path, 'public/locales/af/translation.json', '{"a": "Maak oop"}')
    _write(tmp_path, 'package.json', '{"name": "app"}')
    _write(tmp_path, 'src/config/settings.json', '{"a": "b"}')

    files = FileSet.from_paths([str(tmp_path)])

    assert [(e.path, e.template.template) for e in files] == [(af, en)]
    assert [e.path for e in files.templates] == [en]
    assert files.unpaired == []


def test_pairing_a_waiting_file_puts_it_in_path_order(tmp_path):
    from virtaal.support.templatefinder import TemplateMatch

    po_a = _write(tmp_path, 'a/af.po', 'msgid "a"\nmsgstr "b"\n')
    lone = _write(tmp_path, 'b/lone.properties')
    base = _write(tmp_path, 'b/base.properties')
    po_c = _write(tmp_path, 'c/af.po', 'msgid "a"\nmsgstr "b"\n')
    files = FileSet.from_paths([str(tmp_path)])
    assert {e.path for e in files.unpaired} == {lone, base}

    entry = files.pair(lone, TemplateMatch(base, None, 'chosen'))

    assert files.paths == [po_a, lone, po_c]
    assert entry.template.template == base
    # Its template no longer waits: it's a source-language file.
    assert files.unpaired == []


def test_a_waiting_file_called_its_own_source_becomes_a_template(tmp_path):
    from virtaal.support.templatefinder import TemplateMatch

    lone = _write(tmp_path, 'lone.properties')
    files = FileSet.from_paths([str(tmp_path)])

    assert files.pair(lone, TemplateMatch(lone, None, 'chosen')) is None
    assert [e.path for e in files.templates] == [lone] and files.unpaired == []


def test_a_language_virtaal_knows_only_in_part_keeps_its_files(tmp_path):
    for name in ('a', 'b'):
        path = tmp_path / ('%s.xlf' % name)
        path.write_bytes(
            b'<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">'
            b'<file original="a" source-language="en-US" target-language="zh-x-hmn-SDL"'
            b' datatype="plaintext"><body/></file></xliff>'
        )
    fileset = FileSet.from_paths([str(tmp_path)])

    language = fileset.choose_language('af')

    assert len(fileset.for_language(language)) == 2
