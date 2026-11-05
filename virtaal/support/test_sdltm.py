#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import gzip
import os
import sqlite3
from io import BytesIO

import pytest
from translate.storage.placeables import xliff

from virtaal.support import sdltm

SAMPLE = os.path.join(
    'devsupport',
    'testfiles',
    'trados',
    'rws-trados-studio-api-samples',
    'Software En-De.sdltm.gz',
)

SEGMENT = (
    '<Segment xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><Elements>%s</Elements>'
    '<CultureName>en-US</CultureName></Segment>'
)


def _text(value):
    return '<Text><Value>%s</Value></Text>' % value


def _tag(kind, anchor):
    return '<Tag><Type>%s</Type><Anchor>%s</Anchor><TagID>pt%s</TagID></Tag>' % (
        kind,
        anchor,
        anchor,
    )


def _make_sdltm(path, memories, units, fields=True):
    """An SDLTM with the tables and columns Trados Studio writes that the
    reader uses. C{memories}: (id, source, target); C{units}: (id, tm id,
    source XML, target XML)."""
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE translation_memories(id INTEGER PRIMARY KEY, guid BLOB, name TEXT,
            source_language TEXT, target_language TEXT);
        CREATE TABLE translation_units(id INTEGER PRIMARY KEY, guid BLOB,
            translation_memory_id INT, source_hash INTEGER, source_segment TEXT,
            target_hash INTEGER, target_segment TEXT);
        """
    )
    if fields:
        db.executescript(
            """
            CREATE TABLE attributes(id INTEGER PRIMARY KEY, guid BLOB, name TEXT, type INT,
                tm_id INT);
            CREATE TABLE string_attributes(translation_unit_id INT, attribute_id INT,
                value TEXT);
            CREATE TABLE numeric_attributes(translation_unit_id INT, attribute_id INT,
                value INT);
            CREATE TABLE date_attributes(translation_unit_id INT, attribute_id INT,
                value DATETIME);
            CREATE TABLE picklist_attributes(translation_unit_id INT, picklist_value_id INT);
            CREATE TABLE picklist_values(id INTEGER PRIMARY KEY, guid BLOB, attribute_id INT,
                value TEXT);
            """
        )
    db.executemany(
        "INSERT INTO translation_memories VALUES (?, X'00', ?, ?, ?)",
        [(tm_id, 'TM %d' % tm_id, s, t) for tm_id, s, t in memories],
    )
    db.executemany(
        "INSERT INTO translation_units VALUES (?, X'00', ?, 0, ?, 0, ?)",
        units,
    )
    db.commit()
    db.close()


@pytest.fixture
def tagged(tmp_path):
    path = str(tmp_path / 'tagged.sdltm')
    source = SEGMENT % (
        _tag('Start', 1)
        + _text('Open the ')
        + _tag('End', 1)
        + _text('file')
        + _tag('Standalone', 2)
    )
    target = SEGMENT % (_text('Datei ') + _tag('Start', 1) + _text('öffnen') + _tag('End', 1))
    _make_sdltm(path, [(1, 'en-US', 'de-DE')], [(7, 1, source, target)])
    db = sqlite3.connect(path)
    db.executescript(
        """
        INSERT INTO attributes VALUES (1, X'00', 'FileName', 2, 1);
        INSERT INTO attributes VALUES (2, X'00', 'Usage', 3, 1);
        INSERT INTO attributes VALUES (3, X'00', 'Client', 5, 1);
        INSERT INTO string_attributes VALUES (7, 1, 'manual.docx');
        INSERT INTO numeric_attributes VALUES (7, 2, 3);
        INSERT INTO picklist_values VALUES (1, X'00', 3, 'ACME');
        INSERT INTO picklist_attributes VALUES (7, 1);
        """
    )
    db.commit()
    db.close()
    return path


def test_a_unit_keeps_its_text_with_the_tags_as_placeables(tagged):
    unit = sdltm.SdltmFile.parsefile(tagged).units[0]

    assert unit.source == 'Open the file'
    assert unit.target == 'Datei öffnen'
    parts = unit.rich_source[0].sub
    assert [type(p).__name__ for p in parts] == ['Bx', 'StringElem', 'Ex', 'StringElem', 'X']
    assert [p.id for p in parts if isinstance(p, (xliff.Bx, xliff.Ex, xliff.X))] == ['1', '1', '2']
    assert [type(p).__name__ for p in unit.rich_target[0].sub] == [
        'StringElem',
        'Bx',
        'StringElem',
        'Ex',
    ]


def test_a_unit_has_its_tm_languages_and_its_fields_as_notes(tagged):
    store = sdltm.SdltmFile.parsefile(tagged)
    unit = store.units[0]

    assert (store.getsourcelanguage(), store.gettargetlanguage()) == ('en-US', 'de-DE')
    assert (unit.getsourcelanguage(), unit.gettargetlanguage()) == ('en-US', 'de-DE')
    assert unit.getid() == '7'
    assert unit.getnotes().split('\n') == ['FileName: manual.docx', 'Usage: 3', 'Client: ACME']
    assert unit.istranslatable() and unit.istranslated()


def test_the_file_reads_the_same_from_its_bytes(tagged):
    with open(tagged, 'rb') as f:
        data = BytesIO(f.read())
    data.name = tagged

    store = sdltm.SdltmFile.parsefile(data)

    assert [(u.source, u.target) for u in store.units] == [('Open the file', 'Datei öffnen')]


def test_the_bytes_are_read_through_a_temp_file_without_deserialize(tagged, monkeypatch):
    class Connection:
        def __init__(self, connection):
            self._connection = connection

        def deserialize(self, data):
            raise AttributeError('deserialize')

        def close(self):
            self._connection.close()

    connect = sqlite3.connect

    def fake_connect(database, *args, **kwargs):
        connection = connect(database, *args, **kwargs)
        return Connection(connection) if database == ':memory:' else connection

    monkeypatch.setattr(sdltm.sqlite3, 'connect', fake_connect)
    with open(tagged, 'rb') as f:
        store = sdltm.SdltmFile.parsestring(f.read())

    assert [u.source for u in store.units] == ['Open the file']


def test_an_empty_tm_has_no_units_but_its_languages(tmp_path):
    path = str(tmp_path / 'empty.sdltm')
    _make_sdltm(path, [(1, 'en-US', 'id-ID')], [], fields=False)

    store = sdltm.SdltmFile.parsefile(path)

    assert store.units == []
    assert (store.getsourcelanguage(), store.gettargetlanguage()) == ('en-US', 'id-ID')


def test_units_of_several_tms_keep_their_own_languages(tmp_path):
    path = str(tmp_path / 'two.sdltm')
    _make_sdltm(
        path,
        [(1, 'en-US', 'de-DE'), (2, 'en-US', 'fr-FR')],
        [
            (1, 1, SEGMENT % _text('Yes'), SEGMENT % _text('Ja')),
            (2, 2, SEGMENT % _text('Yes'), SEGMENT % _text('Oui')),
        ],
        fields=False,
    )

    store = sdltm.SdltmFile.parsefile(path)

    assert [(u.gettargetlanguage(), u.target, u.getid()) for u in store.units] == [
        ('de-DE', 'Ja', '1:1'),
        ('fr-FR', 'Oui', '2:2'),
    ]
    assert store.getsourcelanguage() is None


def test_a_unit_with_broken_segment_xml_is_skipped(tmp_path):
    path = str(tmp_path / 'broken.sdltm')
    _make_sdltm(
        path,
        [(1, 'en-US', 'de-DE')],
        [
            (1, 1, '<Segment><Elements>', SEGMENT % _text('Ja')),
            (2, 1, SEGMENT % _text('No'), SEGMENT % _text('Nein')),
        ],
        fields=False,
    )

    assert [u.source for u in sdltm.SdltmFile.parsefile(path).units] == ['No']


def test_a_file_that_is_not_an_sdltm_is_refused(tmp_path):
    with pytest.raises(ValueError):
        sdltm.SdltmFile.parsestring(b'<tmx/>')
    path = str(tmp_path / 'other.sdltm')
    sqlite3.connect(path).execute('CREATE TABLE t(x)').connection.close()
    with pytest.raises(ValueError):
        sdltm.SdltmFile.parsefile(path)


def test_the_store_is_read_only(tagged):
    with pytest.raises(NotImplementedError):
        bytes(sdltm.SdltmFile.parsefile(tagged))


def test_the_trados_sample_reads_every_unit_with_its_tags(tmp_path):
    path = str(tmp_path / 'Software En-De.sdltm')
    with gzip.open(SAMPLE) as f, open(path, 'wb') as out:
        out.write(f.read())

    store = sdltm.SdltmFile.parsefile(path)

    assert len(store.units) == 12
    assert (store.getsourcelanguage(), store.gettargetlanguage()) == ('en-US', 'de-DE')
    unit = store.units[1]
    assert unit.source == (
        'In the Spelling group box, choose the options you want for the spelling checker:'
    )
    assert unit.target.startswith('Wählen')
    assert 'Gruppenfeld Rechtschreibung' in unit.target
    assert sum(isinstance(p, xliff.Bx) for p in unit.rich_source[0].sub) == 3
    assert 'StructureContext: x-tm-listitem' in unit.getnotes()
