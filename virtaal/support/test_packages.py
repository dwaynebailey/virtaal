#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import io
import os
import shutil
import zipfile

import pytest

from virtaal.support import packages

OKAPI = os.path.join('devsupport', 'testfiles', 'packages', 'okapi')

XLIFF = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<xliff version="1.2" '
    b'xmlns="urn:oasis:names:tc:xliff:document:1.2"><file original="a" '
    b'source-language="en-US" target-language="fr-FR" datatype="plaintext"><body>'
    b'<trans-unit id="1"><source>Hello</source></trans-unit></body></file></xliff>\n'
)

# Our own (CC0) TIPP manifests, after the specifications' samples.
TIPP_1 = b'''<?xml version="1.0" encoding="UTF-8"?>
<TIPPManifest version="1.5">
  <GlobalDescriptor>
    <UniquePackageID>urn:uuid:request-1</UniquePackageID>
    <Creator><Name>Agency</Name><ID>http://example.org</ID><Date>2026-01-01T00:00:00Z</Date>
      <Tool><ToolName>Tool</ToolName><ToolID>http://example.org</ToolID><ToolVersion>1</ToolVersion></Tool>
    </Creator>
  </GlobalDescriptor>
  <TaskRequest>
    <TaskType>http://schema.interoperability-now.org/tipp/v1.5/tasks/translate-strict-bitext</TaskType>
    <SourceLanguage>en-US</SourceLanguage>
    <TargetLanguage>fr-FR</TargetLanguage>
  </TaskRequest>
  <Resources>
    <Bilingual name="bilingual">
      <FileResource sequence="1"><Name>a.xlf</Name><Location>a.xlf</Location></FileResource>
    </Bilingual>
    <Preview name="preview"><FileResource sequence="1"><Location>a.html</Location></FileResource></Preview>
  </Resources>
</TIPPManifest>
'''

TIPP_2 = (
    TIPP_1.replace(
        b'<TIPPManifest version="1.5">',
        b'<TIPPManifest xmlns="https://github.com/tipp-specification/2_0/" version="2.0">',
    )
    .replace(
        b'http://schema.interoperability-now.org/tipp/v1.5/tasks/',
        b'https://github.com/tipp-specification/2_0/tasks/',
    )
    .replace(b'FileResource', b'File')
)


def _zip(path, members):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return path


def _zip_bytes(members):
    out = io.BytesIO()
    _zip(out, members)
    return out.getvalue()


def _raw(path):
    """Each member's bytes as stored: header, compressed data."""
    found = {}
    with open(path, 'rb') as f, zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            length = packages._raw_length(f, info)
            f.seek(info.header_offset)
            found[info.orig_filename] = f.read(length)
    return found


def _open(tmp_path, source, name=None):
    path = tmp_path / (name or os.path.basename(source))
    shutil.copy(source, path)
    return path, packages.open_package(str(path), str(tmp_path / 'work' / path.name))


def _relative(package):
    return [os.path.relpath(p, package.work_dir).replace(os.sep, '/') for p in package.files()]


def _edit(path):
    with open(path, 'ab') as f:
        f.write(b'<!-- edited -->\n')


def test_a_trados_package_opens_its_target_languages_files(tmp_path):
    _path, package = _open(tmp_path, os.path.join(OKAPI, 'ts2017-test01.sdlppx'))

    assert (package.kind, package.package_type) == (packages.TRADOS, 'ProjectPackage')
    assert _relative(package) == ['fr-CA/Test.docx.sdlxliff', 'pl-PL/Test.docx.sdlxliff']


def test_saving_writes_only_the_edited_file_back_byte_for_byte_otherwise(tmp_path):
    path, package = _open(tmp_path, os.path.join(OKAPI, 'test-packages.sdlppx'))
    before = _raw(str(path))

    assert not package.write_back()
    assert _raw(str(path)) == before
    _edit(package.files()[1])
    assert package.write_back()

    after = _raw(str(path))
    assert list(after) == list(before)
    changed = [name for name in before if before[name] != after[name]]
    assert changed == ['zh-x-hmn-SDL/dir1/test-in-dir.txt.sdlxliff']
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert archive.read(changed[0]).endswith(b'<!-- edited -->\n')
    assert not package.write_back()


def test_a_worldserver_package_keeps_its_backslashed_names(tmp_path):
    _path, returned = _open(tmp_path, os.path.join(OKAPI, 'test-packages.sdlrpx'))
    path, package = _open(tmp_path, os.path.join(OKAPI, 'test1.wsxz'))

    assert returned.package_type == 'ReturnPackage' and len(returned.files()) == 2
    assert package.kind == packages.WORLDSERVER
    assert _relative(package) == ['okapi_intro_test15977.sdlxliff']
    before = _raw(str(path))
    _edit(package.files()[0])
    package.write_back()
    after = _raw(str(path))
    # Backslashes in member names are kept.
    assert list(after) == list(before)
    assert 'file-types\\Java_Resources_v_2.0.0.0/URI.txt' in after
    assert sum(before[n] != after[n] for n in before) == 1


def test_a_tipp_1_5_package_writes_into_its_nested_resources(tmp_path):
    resources = _zip_bytes([('bilingual/a.xlf', XLIFF), ('preview/a.html', b'<p>Hello</p>')])
    path = _zip(tmp_path / 'job.tipp', [('manifest.xml', TIPP_1), ('resources.zip', resources)])
    package = packages.open_package(str(path), str(tmp_path / 'work'))

    assert (package.kind, package.version, package.package_type) == (
        packages.TIPP,
        '1.5',
        'request',
    )
    assert _relative(package) == ['bilingual/a.xlf']
    _edit(package.files()[0])
    package.write_back()
    with zipfile.ZipFile(path) as archive:
        assert archive.read('manifest.xml') == TIPP_1
        inner = zipfile.ZipFile(io.BytesIO(archive.read('resources.zip')))
        assert inner.read('bilingual/a.xlf') == XLIFF + b'<!-- edited -->\n'
        assert inner.read('preview/a.html') == b'<p>Hello</p>'


def test_a_tipp_2_0_zip_opens(tmp_path):
    path = _zip(
        tmp_path / 'job.zip',
        [
            ('manifest.xml', TIPP_2),
            ('resources/bilingual/a.xlf', XLIFF),
            ('resources/preview/a.html', b'<p>Hello</p>'),
        ],
    )
    assert packages.kind_of(str(path)) == packages.TIPP
    package = packages.open_package(str(path), str(tmp_path / 'work'))

    assert (package.version, _relative(package)) == ('2.0', ['bilingual/a.xlf'])


def test_a_tipp_task_virtaal_cant_do_is_refused(tmp_path):
    manifest = TIPP_1.replace(b'translate-strict-bitext', b'prepare-specifications')
    path = _zip(
        tmp_path / 'job.tipp', [('manifest.xml', manifest), ('resources.zip', _zip_bytes([]))]
    )

    with pytest.raises(packages.PackageError, match='prepare-specifications'):
        packages.open_package(str(path), str(tmp_path / 'work'))


@pytest.mark.parametrize(
    'name', ['../evil.sdlxliff', '/etc/evil.sdlxliff', 'C:\\evil.sdlxliff', 'a\\..\\..\\evil.xlf']
)
def test_a_member_that_would_leave_the_folder_is_refused(tmp_path, name):
    path = _zip(tmp_path / 'evil.wsxz', [(name, XLIFF)])

    with pytest.raises(packages.PackageError, match='Unsafe'):
        packages.open_package(str(path), str(tmp_path / 'work'))
    assert not (tmp_path / 'evil.sdlxliff').exists()


def test_too_many_or_too_big_files_are_refused(tmp_path, monkeypatch):
    path = _zip(tmp_path / 'big.wsxz', [('a.xlf', XLIFF), ('b.xlf', XLIFF)])

    monkeypatch.setattr(packages, 'MAX_MEMBERS', 1)
    with pytest.raises(packages.PackageError, match='too many'):
        packages.open_package(str(path), str(tmp_path / 'work'))
    monkeypatch.setattr(packages, 'MAX_MEMBERS', 10)
    monkeypatch.setattr(packages, 'MAX_MEMBER_SIZE', 10)
    with pytest.raises(packages.PackageError, match='too big'):
        packages.open_package(str(path), str(tmp_path / 'work'))


def test_what_is_a_package(tmp_path):
    plain = _zip(tmp_path / 'bundle.zip', [('a.po', b'')])

    assert packages.kind_of('x.SDLPPX') == packages.TRADOS
    assert packages.kind_of(str(plain)) is None
    assert packages.kind_of('x.xlf') is None
    with pytest.raises(packages.PackageError):
        packages.open_package(str(tmp_path / 'x.mqxlz'), str(tmp_path / 'work'))


def test_a_package_with_nothing_to_translate_is_refused(tmp_path):
    path = _zip(tmp_path / 'empty.wsxz', [('settings.xml', b'<a/>')])

    with pytest.raises(packages.PackageError, match='no file to translate'):
        packages.open_package(str(path), str(tmp_path / 'work'))
