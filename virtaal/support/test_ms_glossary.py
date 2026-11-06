#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Made-up files laid out like Microsoft's UI strings glossaries; never
the real ones, whose terms forbid storing them."""

import io
import zipfile

import pytest
from translate.storage import csvl10n, factory

from virtaal.support import ms_glossary, trados_compat  # noqa: F401 - patches the factory
from virtaal.support.ms_glossary import MicrosoftGlossaryFile, language_of
from virtaal.support.tmdb import TMDB

NOTICE = [
    '"MADE-UP NOTICE FOR TESTS"',
    '',
    '"1. This line, like the real ones, has commas, and ""quotes""."',
] + ['"Notice line %d, with a comma."' % i for i in range(4, 13)]
HEADER = '"Source Term",,"Translation",,"String Category",,"Platform",,"Product",,"Version"'
ROWS = [
    '"Open",,"Oop",,"Button",,"Windows",,"Example",,"1.0"',
    '"Save ""all""",,"Stoor ""alles""",,"Menu",,"Windows",,"Example",,"1.0"',
    # Broken quoting: ""text"" for """text""", with a comma inside.
    '""Cancel, now"",,""Kanselleer, nou"",,"Button",,"Windows",,"Example",,"1.0"',
    # Broken quoting without a comma.
    '""Close"",,""Sluit"",,"Button",,"Windows",,"Example",,"1.0"',
    '',
    '"Two\nlines",,"Twee\nreëls",,"Text",,"Windows",,"Example",,"1.0"',
    '"Untranslated",,"",,"Text",,"Windows",,"Example",,"1.0"',
    '"Open",,"Maak oop",,"Menu",,"Mac",,"Other",,"2.0"',
]


def glossary_csv(rows=ROWS):
    text = '\r\n'.join(NOTICE + [HEADER] + rows) + '\r\n'
    return b'\xef\xbb\xbf' + text.encode('utf-8')


def glossary_zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


def named(content, name):
    f = io.BytesIO(content)
    f.name = name
    return f


def _pairs(store):
    return [(unit.source, unit.target) for unit in store.units]


def test_reads_the_rows_after_the_notice_and_skips_broken_ones():
    store = factory.getobject(named(glossary_csv(), 'af-ZA-csv-Example.csv'))

    assert isinstance(store, MicrosoftGlossaryFile)
    assert _pairs(store) == [
        ('Open', 'Oop'),
        ('Save "all"', 'Stoor "alles"'),
        ('Two\nlines', 'Twee\nreëls'),
        ('Open', 'Maak oop'),
    ]
    assert store.skipped == 2
    assert store.getsourcelanguage() == 'en'
    assert store.gettargetlanguage() == 'af'
    assert len({unit.getid() for unit in store.units}) == 4


def test_a_path_to_a_glossary(tmp_path):
    path = tmp_path / 'de-DE-csv-Example.csv'
    path.write_bytes(glossary_csv())

    store = factory.getobject(str(path))

    assert isinstance(store, MicrosoftGlossaryFile)
    assert store.gettargetlanguage() == 'de'


def test_reads_every_csv_in_a_zip_under_its_own_language():
    content = glossary_zip(
        {
            'af-ZA/af-ZA-csv-Example.csv': glossary_csv(ROWS[:1]),
            'es-LATAM/es-LATAM-csv-Example.csv': glossary_csv(['"Open",,"Abrir",,,,,,,,']),
            'af-ZA/readme.txt': b'not a glossary',
        }
    )

    store = factory.getobject(named(content, 'Glossaries.zip'))

    assert isinstance(store, MicrosoftGlossaryFile)
    assert [(u.source, u.target, u.gettargetlanguage()) for u in store.units] == [
        ('Open', 'Oop', 'af'),
        ('Open', 'Abrir', 'es'),
    ]


def test_other_csv_and_zip_files_are_not_glossaries():
    plain = factory.getobject(named(b'source,target\r\nOpen,Oop\r\n', 'terms.csv'))

    assert isinstance(plain, csvl10n.csvfile)
    assert not ms_glossary.is_glossary_zip(io.BytesIO(glossary_zip({'a.csv': b'a,b\r\n'})))
    assert not ms_glossary.is_glossary_zip(io.BytesIO(b'not a zip'))


def test_a_file_with_the_notice_but_no_header_has_no_units():
    store = MicrosoftGlossaryFile.parsestring('\r\n'.join(NOTICE).encode('utf-8'))

    assert store.units == []


@pytest.mark.parametrize(
    'name, language',
    [
        ('af-ZA-csv-Outlook.csv', 'af'),
        ('glossaries/pt-BR/pt-BR-csv-Outlook.csv', 'pt_BR'),
        ('zh-CN/Outlook.csv', 'zh_CN'),
        ('es-LATAM-csv-Skype.csv', 'es'),
        ('Outlook.csv', None),
        ('Downloads/Outlook.csv', None),
    ],
)
def test_language_of(name, language):
    assert language_of(name) == language


def test_the_glossarys_translations_reach_the_tm(tmp_path):
    db = TMDB(str(tmp_path / 'tm.db'))
    store = factory.getobject(named(glossary_csv(), 'af-ZA-csv-Example.csv'))

    counts = db.import_store(store, '/downloads/af-ZA-csv-Example.csv', 'en', 'af')

    assert counts['added'] == 4
    targets = [match['target'] for match in db.translate_unit('Open', 'en', 'af')]
    assert sorted(targets) == ['Maak oop', 'Oop']


def test_the_import_chooser_offers_glossaries():
    names = [name for name, _extensions in trados_compat.TM_IMPORT_FORMATS]

    assert MicrosoftGlossaryFile.Name in names
