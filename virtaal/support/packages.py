#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""CAT tools' zipped packages: their bilingual files are taken out into a
working folder to open as one list of units, and written back into the
package on saving. No GTK here.

  - Trados project and return packages (.sdlppx, .sdlrpx): the target
    languages' .sdlxliff files, by the .sdlproj's language directions.
  - WorldServer packages (.wsxz): the XLIFF and SDLXLIFF files in them.
  - TIPP 1.5 and 2.0 (.tipp, or a .zip with a TIPP manifest): the
    bilingual files the manifest lists, in 1.5's nested resources.zip or
    2.0's resources folder.

Writing back copies every other member of the package as it was, byte
for byte (its compressed data too); only the edited files are written
anew. Member names are checked before anything is taken out: no absolute
paths or "..", sizes and counts capped."""

import contextlib
import copy
import datetime
import hashlib
import io
import os
import re
import shutil
import struct
import tempfile
import zipfile

from lxml import etree

TRADOS = "trados"
WORLDSERVER = "worldserver"
TIPP = "tipp"

EXTENSIONS = {
    "sdlppx": TRADOS,
    "sdlrpx": TRADOS,
    "wsxz": WORLDSERVER,
    "tipp": TIPP,
}

BILINGUAL = (".sdlxliff", ".xlf", ".xliff", ".mqxliff", ".mxliff", ".txml")

MAX_MEMBER_SIZE = 512 * 1024 * 1024
MAX_TOTAL_SIZE = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 20000

_TIPP_1 = None
_TIPP_2 = "https://github.com/tipp-specification/2_0/"
_RESOURCES_1 = "resources.zip"
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


class PackageError(ValueError):
    """A package Virtaal can't open or write safely."""


# The Open dialog's name for them, translated there (devsupport/tmp_strings.py).
NAME = "Translation Packages"


def open_formats():
    """(name, extensions) of the packages, for the Open dialog."""
    return [(NAME, sorted(EXTENSIONS))]


# Member names


def parts(name):
    """The path parts of a member name, either slash, or PackageError for
    one that could leave the folder it is taken out into."""
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        raise PackageError("Unsafe file name in the package: %s" % name)
    found = [part for part in re.split(r"[\\/]", name) if part not in ("", ".")]
    if not found or ".." in found:
        raise PackageError("Unsafe file name in the package: %s" % name)
    return found


def _key(name):
    return "/".join(parts(name))


def _check_sizes(infos):
    if len(infos) > MAX_MEMBERS:
        raise PackageError("The package holds too many files.")
    total = 0
    for info in infos:
        if info.file_size > MAX_MEMBER_SIZE:
            raise PackageError("A file in the package is too big: %s" % info.filename)
        total += info.file_size
    if total > MAX_TOTAL_SIZE:
        raise PackageError("The package is too big.")


def _read(archive, info):
    with archive.open(info) as member:
        data = member.read(MAX_MEMBER_SIZE + 1)
    if len(data) > MAX_MEMBER_SIZE:
        raise PackageError("A file in the package is too big: %s" % info.filename)
    return data


# Writing a zip with its members copied as they are


def _raw_length(source, info):
    """How many bytes C{info}'s member takes in the file C{source}: its
    local header, its compressed data and any data descriptor."""
    source.seek(info.header_offset)
    header = source.read(30)
    if len(header) < 30 or header[:4] != b"PK\x03\x04":
        raise PackageError("The package is damaged: %s" % info.filename)
    name_length, extra_length = struct.unpack("<HH", header[26:30])
    length = 30 + name_length + extra_length + info.compress_size
    if info.flag_bits & 0x08:
        source.seek(info.header_offset + length)
        signed = source.read(4) == b"PK\x07\x08"
        zip64 = max(info.file_size, info.compress_size) > zipfile.ZIP64_LIMIT
        length += (4 if signed else 0) + (20 if zip64 else 12)
    return length


def _copy_member(source, info, target):
    """Copy C{info}'s member from the open file C{source} into the zip
    C{target} byte for byte."""
    length = _raw_length(source, info)
    source.seek(info.header_offset)
    blob = source.read(length)
    copied = copy.copy(info)
    # Windows' zipfile turns a backslash into a slash; keep the name.
    copied.filename = info.orig_filename
    copied.header_offset = target.fp.tell()
    target.fp.write(blob)
    target.filelist.append(copied)
    target.NameToInfo[copied.filename] = copied
    target.start_dir = target.fp.tell()
    target._didModify = True


def _new_member(info, data, target):
    member = zipfile.ZipInfo(info.orig_filename, datetime.datetime.now().timetuple()[:6])
    member.filename = info.orig_filename
    member.compress_type = info.compress_type
    member.external_attr = info.external_attr
    member.create_system = info.create_system
    member.comment = info.comment
    target.writestr(member, data)


def rewrite(source, target, replace=None, keep=None, add=()):
    """Write the zip C{source} (a path or binary file) to C{target} (a
    binary file): each member C{keep} (a test of its name, all if None)
    takes, copied byte for byte unless C{replace} gives its new data by
    name; then the (name, data) pairs of C{add}."""
    replace = replace or {}
    with contextlib.ExitStack() as stack:
        if isinstance(source, (str, os.PathLike)):
            source = stack.enter_context(open(source, "rb"))
        archive = stack.enter_context(zipfile.ZipFile(source))
        out = stack.enter_context(zipfile.ZipFile(target, "w"))
        for info in archive.infolist():
            if keep is not None and not keep(info.orig_filename):
                continue
            if info.orig_filename in replace:
                _new_member(info, replace[info.orig_filename], out)
            else:
                _copy_member(source, info, out)
        for name, data in add:
            out.writestr(zipfile.ZipInfo(name, datetime.datetime.now().timetuple()[:6]), data)
        out.comment = archive.comment


def _replace_file(path, write):
    """Write the file C{path} through C{write(file)}, replacing it only
    once the new one is whole."""
    folder = os.path.dirname(os.path.abspath(path))
    handle, temporary = tempfile.mkstemp(prefix=".virtaal-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(handle, "wb") as out:
            write(out)
        if os.path.exists(path):
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


# Which files to open, by package


def _root_members(archive, suffix):
    return [
        i
        for i in archive.infolist()
        if len(parts(i.filename)) == 1 and i.filename.lower().endswith(suffix)
    ]


def _xml(data):
    try:
        return etree.fromstring(data, _PARSER)
    except etree.XMLSyntaxError as exc:
        raise PackageError("The package's description can't be read: %s" % exc) from exc


def _trados(archive):
    """(target language codes, the .sdlproj's member, its type)."""
    projects = _root_members(archive, ".sdlproj")
    if len(projects) != 1:
        raise PackageError("A Trados package needs one .sdlproj file.")
    root = _xml(_read(archive, projects[0]))
    directions = root.findall("LanguageDirections/LanguageDirection")
    targets = {d.get("TargetLanguageCode", "").lower() for d in directions} - {""}
    return targets, projects[0], root.get("PackageType")


def _tipp_manifest(archive):
    manifests = [i for i in archive.infolist() if _key(i.filename) == "manifest.xml"]
    if not manifests:
        return None
    root = _xml(_read(archive, manifests[0]))
    if etree.QName(root).localname != "TIPPManifest":
        return None
    return root


def is_tipp_zip(path):
    """Whether the .zip C{path} is a TIPP package."""
    try:
        with zipfile.ZipFile(path) as archive:
            return _tipp_manifest(archive) is not None
    except (OSError, zipfile.BadZipFile, PackageError):
        return False


def kind_of(path):
    """The kind of package C{path} is, or None."""
    extension = os.path.splitext(path)[1][1:].lower()
    if extension in EXTENSIONS:
        return EXTENSIONS[extension]
    if extension == "zip" and os.path.isfile(path) and is_tipp_zip(path):
        return TIPP
    return None


def _namespaced(root, name):
    namespace = etree.QName(root).namespace
    return "{%s}%s" % (namespace, name) if namespace else name


def _tipp_bilingual(root):
    """The bilingual folder's name and its files' locations."""
    resources = root.find(_namespaced(root, "Resources"))
    bilingual = None if resources is None else resources.find(_namespaced(root, "Bilingual"))
    if bilingual is None:
        return "bilingual", []
    locations = [e.text.strip() for e in bilingual.iter(_namespaced(root, "Location")) if e.text]
    return bilingual.get("name") or "bilingual", locations


class Package:
    """A package opened: where its bilingual files were taken out to,
    and what to write back."""

    def __init__(self, path, work_dir):
        self.path = os.path.abspath(path)
        self.work_dir = work_dir
        self.kind = kind_of(path)
        if self.kind is None:
            raise PackageError("Not a translation package: %s" % path)
        # Path in the working folder -> (inner zip's member or None,
        # member name, sha256 of what was taken out).
        self.members = {}
        self.manifest = None
        self.package_type = None
        self.version = None
        self._extract()

    # Opening

    def _extract(self):
        if os.path.isdir(self.work_dir):
            shutil.rmtree(self.work_dir)
        os.makedirs(self.work_dir)
        with zipfile.ZipFile(self.path) as archive:
            _check_sizes(archive.infolist())
            getattr(self, "_extract_" + self.kind)(archive)
        if not self.members:
            raise PackageError("The package holds no file to translate.")

    def _take(self, archive, info, inner=None, prefix=()):
        data = _read(archive, info)
        relative = os.path.join(*parts(info.filename)[len(prefix) :])
        path = os.path.join(self.work_dir, relative)
        if not os.path.realpath(path).startswith(os.path.realpath(self.work_dir) + os.sep):
            raise PackageError("Unsafe file name in the package: %s" % info.filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as out:
            out.write(data)
        self.members[path] = (inner, info.orig_filename, hashlib.sha256(data).hexdigest())

    def _extract_trados(self, archive):
        targets, project, self.package_type = _trados(archive)
        self.manifest = project.orig_filename
        for info in archive.infolist():
            names = parts(info.filename)
            if names[-1].lower().endswith(".sdlxliff") and names[0].lower() in targets:
                self._take(archive, info)

    def _extract_worldserver(self, archive):
        for info in archive.infolist():
            if not info.is_dir() and info.filename.lower().endswith(BILINGUAL):
                self._take(archive, info)

    def _extract_tipp(self, archive):
        root = _tipp_manifest(archive)
        if root is None:
            raise PackageError("A TIPP package needs its manifest.xml.")
        self.manifest = root
        self.version = root.get("version", "")
        self.package_type = (
            "response" if root.find(_namespaced(root, "TaskResponse")) is not None else "request"
        )
        task = (
            root.findtext(_namespaced(root, "TaskRequest") + "/" + _namespaced(root, "TaskType"))
            or ""
        )
        if self.package_type == "request" and not task.rstrip("/").endswith(
            "translate-strict-bitext"
        ):
            raise PackageError("This TIPP task can't be done in Virtaal: %s" % task)
        folder, locations = _tipp_bilingual(root)
        if self.version.startswith("1."):
            nested = [i for i in archive.infolist() if _key(i.filename) == _RESOURCES_1]
            if not nested:
                raise PackageError("A TIPP 1.5 package needs its resources.zip.")
            inner = zipfile.ZipFile(io.BytesIO(_read(archive, nested[0])))
            _check_sizes(inner.infolist())
            wanted = {"/".join([folder, *parts(location)]) for location in locations}
            for info in inner.infolist():
                if _key(info.filename) in wanted:
                    self._take(inner, info, inner=nested[0].orig_filename)
            return
        wanted = {"/".join(["resources", folder, *parts(location)]) for location in locations}
        for info in archive.infolist():
            if _key(info.filename) in wanted:
                self._take(archive, info, prefix=("resources",))

    def files(self):
        """The bilingual files taken out, in the package's order."""
        return list(self.members)

    # Saving

    def _changes(self):
        """{inner zip or None: {member: new data}} of the files edited."""
        changes = {}
        for path, (inner, member, digest) in self.members.items():
            with open(path, "rb") as f:
                data = f.read()
            if hashlib.sha256(data).hexdigest() != digest:
                changes.setdefault(inner, {})[member] = data
        return changes

    def write_back(self):
        """Write the edited files into the package. @returns: Whether it
        changed."""
        changes = self._changes()
        if not changes:
            return False
        outer = changes.pop(None, {})
        with zipfile.ZipFile(self.path) as archive:
            for inner, replace in changes.items():
                nested = io.BytesIO()
                rewrite(io.BytesIO(archive.read(inner)), nested, replace)
                outer[inner] = nested.getvalue()
        _replace_file(self.path, lambda out: rewrite(self.path, out, outer))
        for path, (inner, member, _digest) in list(self.members.items()):
            with open(path, "rb") as f:
                self.members[path] = (inner, member, hashlib.sha256(f.read()).hexdigest())
        return True


def work_folder(path):
    """Where the package C{path}'s files are taken out to: Virtaal's own
    folder, by the package's path, named as the package."""
    from virtaal.common import pan_app

    key = hashlib.sha256(os.path.abspath(path).encode("utf-8", "surrogateescape")).hexdigest()[:16]
    return os.path.join(pan_app.get_state_dir(), "packages", key, os.path.basename(path))


def open_package(path, work_dir=None):
    """The L{Package} C{path}, its bilingual files taken out."""
    try:
        return Package(path, work_dir or work_folder(path))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError) as exc:
        raise PackageError("The package can't be read: %s" % exc) from exc
