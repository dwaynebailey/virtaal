#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import gzip
import os
from io import BytesIO

import pytest
from translate.storage import factory, po

from virtaal.support import multiterm, sdltm, trados_compat

TRADOS = os.path.join('devsupport', 'testfiles', 'trados')
SDLTM = os.path.join(TRADOS, 'rws-trados-studio-api-samples', 'Software En-De.sdltm.gz')
SDLTB = os.path.join(TRADOS, 'rws-trados-studio-api-samples', 'Sample.sdltb.gz')
MILI = os.path.join(TRADOS, 'kaur2k-termconv', 'mili_1.xml')


def _gunzip(source, path):
    with gzip.open(source) as f, open(path, 'wb') as out:
        out.write(f.read())
    return str(path)


def test_the_factory_opens_an_sdltm_by_its_path_or_bytes(tmp_path):
    path = _gunzip(SDLTM, tmp_path / 'Software En-De.sdltm')

    store = factory.getobject(path)
    with open(path, 'rb') as f:
        upload = BytesIO(f.read())
    upload.name = path
    uploaded = factory.getobject(upload)

    assert isinstance(store, sdltm.SdltmFile)
    assert len(store.units) == len(uploaded.units) == 12


def test_the_factory_opens_a_multiterm_export_but_not_another_xml_file(tmp_path):
    store = factory.getobject(MILI)
    other = tmp_path / 'other.xml'
    other.write_bytes(b'<?xml version="1.0"?><root/>')

    assert isinstance(store, multiterm.MultiTermXmlFile)
    assert store.units
    with pytest.raises(ValueError):
        factory.getobject(str(other))


def test_the_factory_reads_a_multiterm_upload_and_leaves_it_at_the_start():
    with open(MILI, 'rb') as f:
        upload = BytesIO(f.read())
    upload.name = '/home/me/mili_1.xml'

    assert factory.getclass(upload) is multiterm.MultiTermXmlFile
    assert upload.tell() == 0
    assert factory.getobject(upload).units


def test_the_factory_opens_an_sdltb_by_its_extension(tmp_path):
    path = _gunzip(SDLTB, tmp_path / 'Sample.sdltb')

    assert factory.getclass(path) is multiterm.SdltbFile
    pytest.importorskip('access_parser')
    assert len(factory.getobject(path).concepts) == 25


def test_other_formats_and_explicit_types_are_left_to_translate_toolkit(tmp_path):
    path = tmp_path / 'a.po'
    path.write_bytes(b'msgid "a"\nmsgstr "b"\n')

    assert isinstance(factory.getobject(str(path)), po.pofile)
    assert factory.getclass(MILI, localfiletype='po') is po.pofile
    with pytest.raises(ValueError):
        factory.getclass(str(tmp_path / 'a.sdltm'), classes={'po': po.pofile})


def test_the_dialog_formats_name_the_readers():
    assert (sdltm.SdltmFile.Name, ['sdltm']) in trados_compat.TM_IMPORT_FORMATS
    assert (multiterm.MultiTermXmlFile.Name, ['xml']) in trados_compat.TM_IMPORT_FORMATS
    assert [name for name, _exts in trados_compat.TERMINOLOGY_FORMATS] == [
        multiterm.MultiTermXmlFile.Name,
        multiterm.SdltbFile.Name,
    ]
