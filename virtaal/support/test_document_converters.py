#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from translate.storage import factory

from virtaal.support import _docsamples, converters

PARAGRAPHS = ['Hello world.', 'Second *bold* part.', 'Third one.']


def _store(data, converter):
    import io

    handle = io.BytesIO(data)
    handle.name = converter.trans_name('doc')
    return factory.getobject(handle)


def _translate(converter, data, translations, fuzzy=()):
    """The translatable file's bytes, C{translations} ({source: target})
    filled in and those in C{fuzzy} marked needing work."""
    store = _store(converter.forward(data, 'doc'), converter)
    for unit in store.units:
        if unit.source in translations:
            unit.target = translations[unit.source]
            unit.markfuzzy(unit.source in fuzzy)
    return bytes(store)


def _sources(converter, data):
    store = _store(converter.forward(data, 'doc'), converter)
    return [unit.source for unit in store.units if unit.istranslatable() and unit.source]


def test_odf_round_trip_keeps_untranslated_and_needs_work_units_as_the_document_has_them():
    converter = converters.OdfConverter()
    document = _docsamples.odt(PARAGRAPHS)
    assert _sources(converter, document) == ['Hello world.', 'Second bold part.', 'Third one.']
    trans = _translate(
        converter,
        document,
        {'Hello world.': 'Hallo wêreld.', 'Third one.': 'Derde een.'},
        fuzzy=('Third one.',),
    )

    translated = converter.back(trans, document)

    assert _docsamples.odt_text(translated) == ['Hallo wêreld.', 'Second bold part.', 'Third one.']
    assert b'<text:span>bold</text:span>' in _content(translated)


def _content(data):
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read('content.xml')


def test_odf_translatable_file_names_the_document_not_its_path():
    converter = converters.OdfConverter()
    trans = converter.forward(_docsamples.odt(PARAGRAPHS), 'report.odt')
    assert b'virtaal-doc-' not in trans
    assert b'/tmp' not in trans


def test_html_round_trip_leaves_out_needs_work_translations():
    converter = converters.HtmlConverter()
    document = _docsamples.html(['One paragraph.', 'Two *bold*.'])
    trans = _translate(
        converter,
        document,
        {'One paragraph.': 'Een paragraaf.', 'Two <b>bold</b>.': 'Twee <b>vet</b>.'},
        fuzzy=('Two <b>bold</b>.',),
    )

    translated = converter.back(trans, document).decode('utf-8')

    assert '<p>Een paragraaf.</p>' in translated
    assert '<p>Two <b>bold</b>.</p>' in translated


def test_text_round_trip_one_unit_per_paragraph():
    converter = converters.TextConverter()
    document = _docsamples.txt(['First para\nline two.', 'Second para.'])
    assert _sources(converter, document) == ['First para\nline two.', 'Second para.']
    trans = _translate(converter, document, {'Second para.': 'Tweede paragraaf.'})

    translated = converter.back(trans, document).decode('utf-8')

    assert translated.startswith('First para\nline two.\n\nTweede paragraaf.')


@pytest.mark.parametrize(
    ('name', 'converter_id'),
    [
        ('report.odt', 'odf'),
        ('slides.ODP', 'odf'),
        ('page.htm', 'html'),
        ('notes.txt', 'txt'),
        ('letter.docx', None),
        ('messages.po', None),
    ],
)
def test_document_converter_by_extension(name, converter_id):
    converter = converters.document_converter(name)
    assert (converter.id if converter else None) == converter_id
    assert converters.is_document(name) is (converter_id is not None)


def test_a_format_whose_modules_are_missing_is_not_offered(monkeypatch):
    monkeypatch.setattr(converters.HtmlConverter, 'modules', ('virtaal.no_such_module',))

    assert converters.document_converter('page.html') is None
    assert converters.document_converter_by_id('html') is None
    assert 'html' not in converters.document_extensions()
    assert converters.document_converter_by_id('odf').id == 'odf'


def test_document_formats_name_every_extension_for_the_open_dialog():
    [(name, extensions)] = converters.document_formats()
    assert name == 'Documents'
    assert {'odt', 'html', 'txt'} <= set(extensions)


def test_no_document_formats_without_any_converter(monkeypatch):
    monkeypatch.setattr(converters, 'DOCUMENT_CONVERTERS', [])
    assert converters.document_formats() == []


def test_trans_names():
    assert converters.OdfConverter().trans_name('a.odt') == 'a.odt.xlf'
    assert converters.HtmlConverter().trans_name('a.html') == 'a.html.po'
