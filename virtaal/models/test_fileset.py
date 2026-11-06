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
    _write(tmp_path, 'i18n/af.properties')
    _write(tmp_path, 'i18n/lone.properties')

    files = FileSet.from_paths([str(tmp_path)])

    assert files.paths == [po]
    assert [entry.path for entry in files.templates] == [en]
    assert [entry.path for entry in files.for_language('af').templates] == [en]
