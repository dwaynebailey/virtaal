#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Small documents built for tests: an OpenDocument text, an HTML page
and a plain text file, each with the paragraphs given."""

import io
import zipfile
from xml.sax.saxutils import escape

_OFFICE = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
_TEXT = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
_MANIFEST = 'urn:oasis:names:tc:opendocument:xmlns:manifest:1.0'


def _odf_paragraph(text):
    """A paragraph; text in *stars* is a span, as bold would be."""
    parts = text.split('*')
    inner = ''.join(
        '<text:span>%s</text:span>' % escape(part) if i % 2 else escape(part)
        for i, part in enumerate(parts)
    )
    return '<text:p>%s</text:p>' % inner


def odt(paragraphs):
    """The bytes of an OpenDocument text with C{paragraphs}."""
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="%s" xmlns:text="%s" office:version="1.2">'
        '<office:body><office:text>%s</office:text></office:body>'
        '</office:document-content>' % (_OFFICE, _TEXT, ''.join(map(_odf_paragraph, paragraphs)))
    )
    empty = (
        '<?xml version="1.0" encoding="UTF-8"?><office:%s xmlns:office="%s" office:version="1.2"/>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo('mimetype'), 'application/vnd.oasis.opendocument.text')
        archive.writestr('content.xml', content)
        archive.writestr('styles.xml', empty % ('document-styles', _OFFICE))
        archive.writestr('meta.xml', empty % ('document-meta', _OFFICE))
        archive.writestr(
            'META-INF/manifest.xml',
            '<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="%s">'
            '<manifest:file-entry manifest:full-path="/"'
            ' manifest:media-type="application/vnd.oasis.opendocument.text"/>'
            '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
            '</manifest:manifest>' % _MANIFEST,
        )
    return out.getvalue()


def odt_text(data):
    """The paragraphs' text of an OpenDocument text's bytes."""
    from lxml import etree

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('content.xml'))
    return [''.join(p.itertext()) for p in root.iter('{%s}p' % _TEXT)]


def html(paragraphs, title='Page'):
    """The bytes of an HTML page with C{paragraphs}; *stars* are bold."""
    body = ''.join(
        '<p>%s</p>'
        % ''.join(
            '<b>%s</b>' % escape(part) if i % 2 else escape(part)
            for i, part in enumerate(text.split('*'))
        )
        for text in paragraphs
    )
    return (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>%s</title></head>'
        '<body>%s</body></html>\n' % (escape(title), body)
    ).encode('utf-8')


def txt(paragraphs):
    """The bytes of a plain text file, a blank line between paragraphs."""
    return ('\n\n'.join(paragraphs) + '\n').encode('utf-8')
