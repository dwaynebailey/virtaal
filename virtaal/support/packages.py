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
    A project package gives a return package (.sdlrpx) as OpenXLIFF
    writes one: the .sdlproj marked ReturnPackage, without its termbase
    settings, automatic tasks and reports, and the source and target
    languages' folders.
  - WorldServer packages (.wsxz): the XLIFF and SDLXLIFF files in them.
    The package itself goes back.
  - TIPP 1.5 and 2.0 (.tipp, or a .zip with a TIPP manifest): the
    bilingual files the manifest lists, in 1.5's nested resources.zip or
    2.0's resources folder. A request gives a response package: a
    manifest answering the request and the bilingual files.

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
import sys
import tempfile
import time
import uuid
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
HOMEPAGE = "https://virtaal.translatehouse.org"
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


def _new_member(info, data, target, name=None):
    name = name or info.orig_filename
    member = zipfile.ZipInfo(name, datetime.datetime.now().timetuple()[:6])
    member.filename = name
    member.compress_type = info.compress_type
    member.external_attr = info.external_attr
    member.create_system = info.create_system
    member.comment = info.comment
    target.writestr(member, data)


def rewrite(source, target, replace=None, keep=None, add=(), names=None):
    """Write the zip C{source} (a path or binary file) to C{target} (a
    binary file): each member C{keep} (a test of its name, all if None)
    takes, copied byte for byte unless C{replace} gives its new data by
    name (and C{names} its new name); then the (name, data) pairs of
    C{add}."""
    replace = replace or {}
    names = names or {}
    with contextlib.ExitStack() as stack:
        if isinstance(source, (str, os.PathLike)):
            source = stack.enter_context(open(source, "rb"))
        archive = stack.enter_context(zipfile.ZipFile(source))
        out = stack.enter_context(zipfile.ZipFile(target, "w"))
        for info in archive.infolist():
            if keep is not None and not keep(info.orig_filename):
                continue
            if info.orig_filename in replace:
                _new_member(info, replace[info.orig_filename], out, names.get(info.orig_filename))
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
    """(source and target language codes, the .sdlproj's member, its
    type)."""
    projects = _root_members(archive, ".sdlproj")
    if len(projects) != 1:
        raise PackageError("A Trados package needs one .sdlproj file.")
    root = _xml(_read(archive, projects[0]))
    directions = root.findall("LanguageDirections/LanguageDirection")
    sources = {d.get("SourceLanguageCode", "").lower() for d in directions} - {""}
    targets = {d.get("TargetLanguageCode", "").lower() for d in directions} - {""}
    return (sources, targets), projects[0], root.get("PackageType")


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
        # A Trados package's (source, target) language codes.
        self.languages = (set(), set())
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
        self.languages, project, self.package_type = _trados(archive)
        targets = self.languages[1]
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

    def discard(self):
        """Remove the work folder, unless it holds edits not yet in the
        package. @returns: Whether it was removed."""
        try:
            if self._changes():
                return False
        except OSError:
            return False
        return remove_work_folder(self.work_dir)

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

    # Return packages

    def can_return(self):
        """Whether the package asks for a package back that isn't itself."""
        if self.kind == TRADOS:
            return self.package_type == "ProjectPackage"
        return self.kind == TIPP and self.package_type == "request"

    def return_name(self):
        """The usual name of the return package, beside this one."""
        stem, extension = os.path.splitext(self.path)
        if self.kind == TRADOS:
            return stem + ".sdlrpx"
        return stem + "-response" + extension

    def write_return(self, path):
        """Write the return package to C{path}, from the package as saved."""
        if not self.can_return():
            raise PackageError("This package needs no return package.")
        if os.path.abspath(path) == self.path:
            raise PackageError("The return package can't replace the package.")
        _replace_file(path, getattr(self, "_return_" + self.kind))

    def _return_trados(self, out):
        with zipfile.ZipFile(self.path) as archive:
            project = archive.read(self.manifest)
        sources, targets = self.languages
        folders = sources | targets
        now = datetime.datetime.now(datetime.timezone.utc)

        def keep(name):
            return name == self.manifest or parts(name)[0].lower() in folders

        rewrite(
            self.path,
            out,
            {self.manifest: _return_project(project, sources, targets, now)},
            keep,
            names={self.manifest: _return_project_name(self.manifest, now)},
        )

    def _return_tipp(self, out):
        manifest = etree.tostring(
            _response_manifest(self.manifest), xml_declaration=True, encoding="UTF-8"
        )
        folder, _locations = _tipp_bilingual(self.manifest)
        if self.version.startswith("1."):
            with zipfile.ZipFile(self.path) as archive:
                nested_name = next(
                    i.orig_filename for i in archive.infolist() if _key(i.filename) == _RESOURCES_1
                )
                nested = io.BytesIO()
                rewrite(io.BytesIO(archive.read(nested_name)), nested, keep=_under(folder))
                manifest_name = next(
                    i.orig_filename
                    for i in archive.infolist()
                    if _key(i.filename) == "manifest.xml"
                )
            replace = {manifest_name: manifest, nested_name: nested.getvalue()}
            rewrite(self.path, out, replace, keep=lambda name: name in replace)
            return

        def keep(name):
            return (
                _key(name) == "manifest.xml"
                or _under("resources/" + folder)(name)
                or _key(name) == "resources"
            )

        with zipfile.ZipFile(self.path) as archive:
            manifest_name = next(
                i.orig_filename for i in archive.infolist() if _key(i.filename) == "manifest.xml"
            )
        rewrite(self.path, out, {manifest_name: manifest}, keep)


def _under(folder):
    prefix = "/".join(parts(folder))

    def keep(name):
        key = "/".join(p for p in re.split(r"[\\/]", name) if p)
        return key == prefix or key.startswith(prefix + "/")

    return keep


# A Trados return package's .sdlproj, edited where OpenXLIFF's
# Xliff2Sdlrpx edits it and left as it was everywhere else.

_TAG = re.compile(
    r"""<!--.*?-->|<!\[CDATA\[.*?\]\]>|<[?!].*?>"""
    r"""|<(/?)([^\s/>]+)((?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*(/?)>""",
    re.S,
)


def _attribute(tag, name):
    found = re.search(r"""\s%s\s*=\s*(["'])(.*?)\1""" % name, tag, re.S)
    return found.group(2) if found else None


def _set_attribute(tag, name, value):
    """The start tag C{tag} with the attribute C{name} set to C{value}."""
    pattern = r"""(\s%s\s*=\s*)(["']).*?\2""" % name
    if re.search(pattern, tag, re.S):
        return re.sub(
            pattern,
            lambda m: m.group(1) + m.group(2) + value + m.group(2),
            tag,
            count=1,
            flags=re.S,
        )
    end = len(tag) - (2 if tag.endswith("/>") else 1)
    return '%s %s="%s"%s' % (tag[:end].rstrip(), name, value, tag[end:])


class _ProjectEdits:
    """The edits a return package makes to a .sdlproj's text, as OpenXLIFF
    makes them: PackageType ReturnPackage; no TermbaseConfiguration or
    Tasks/AutomaticTask; every Reports outside the language files
    emptied; the target languages' FileVersions timestamped; the language
    files of other languages than these removed."""

    def __init__(self, text, sources, targets, stamp):
        self.text = text
        self.sources = sources
        self.targets = targets
        self.stamp = stamp
        self.edits = []
        # (name, start, end of start tag, what to do at its end tag, mode)
        self.stack = []

    def run(self):
        for match in _TAG.finditer(self.text):
            if match.group(2) is None:
                continue
            if match.group(1):
                self._end(match)
            else:
                self._start(match)
        if self.stack:
            raise PackageError("The package's project file can't be read.")
        return self.edits

    def _mode(self):
        return self.stack[-1][4] if self.stack else None

    def _path(self, name):
        return [entry[0] for entry in self.stack] + [name]

    def _start(self, match):
        name, tag = match.group(2), match.group(0)
        mode, action = self._mode(), None
        if mode is None:
            action, mode = self._project_start(name, tag, match)
        elif mode == "target" and self._path(name)[-3:] == [
            "LanguageFile",
            "FileVersions",
            "FileVersion",
        ]:
            self._replace(match, _set_attribute(tag, "FileTimeStamp", self.stamp))
        if action == "remove" and match.group(4):
            self.edits.append((self._line_start(match.start()), match.end(), ""))
        elif not match.group(4):
            self.stack.append((name, match.start(), match.end(), action, mode))

    def _project_start(self, name, tag, match):
        """(what to do at the end tag, the mode inside) of an element
        outside any element whose content is left or removed."""
        path = self._path(name)
        if len(path) == 1:
            self._replace(match, _set_attribute(tag, "PackageType", "ReturnPackage"))
            return None, None
        if path[1:] in (["TermbaseConfiguration"], ["Tasks", "AutomaticTask"]):
            return "remove", "frozen"
        if name == "Reports":
            return "empty", "frozen"
        if name == "LanguageFile":
            return self._language_file(tag)
        return None, None

    def _language_file(self, tag):
        code = (_attribute(tag, "LanguageCode") or "").lower()
        if code in self.targets:
            return None, "target"
        if code in self.sources:
            return None, "frozen"
        return "remove", "frozen"

    def _end(self, match):
        if not self.stack or self.stack[-1][0] != match.group(2):
            raise PackageError("The package's project file can't be read.")
        _name, start, start_end, action, _mode = self.stack.pop()
        if action == "remove":
            self.edits.append((self._line_start(start), match.end(), ""))
        elif action == "empty" and self.text[start_end : match.start()]:
            tag = self.text[start:start_end]
            self.edits.append((start, match.end(), tag[:-1].rstrip() + " />"))

    def _replace(self, match, tag):
        if tag != match.group(0):
            self.edits.append((match.start(), match.end(), tag))

    def _line_start(self, start):
        """C{start}, or the end of the line before it if only white space
        is between."""
        found = re.search(r"\r?\n[ \t]*\Z", self.text[:start])
        return found.start() if found else start


def _decode(data):
    """(text, encoding, byte order mark) of a .sdlproj's bytes."""
    for bom, encoding in (
        (b"\xef\xbb\xbf", "utf-8"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    ):
        if data.startswith(bom):
            return data[len(bom) :].decode(encoding), encoding, bom
    return data.decode("utf-8"), "utf-8", b""


def _return_project(data, sources=(), targets=(), now=None):
    """A .sdlproj's bytes edited for a return package (L{_ProjectEdits}),
    otherwise as they were."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03d0000Z" % (now.microsecond // 1000)
    try:
        text, encoding, bom = _decode(data)
    except UnicodeDecodeError as exc:
        raise PackageError("The package's project file can't be read.") from exc
    edits = _ProjectEdits(text, set(sources), set(targets), stamp).run()
    for start, end, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    return bom + text.encode(encoding)


def _return_project_name(name, now):
    """The .sdlproj's name in the return package: its two last "-" parts
    (Studio's date and time) replaced by the time now, as OpenXLIFF names
    it."""
    stem = name[: -len(".sdlproj")] if name.lower().endswith(".sdlproj") else None
    if stem is None or stem.count("-") < 2:
        return name
    stem = stem.rsplit("-", 2)[0]
    return "%s-%s.sdlproj" % (stem, now.astimezone().strftime("%Y%m%d-%Hh%Mm%Ss"))


def _creator_name():
    """Who the response is from: the translator's name in Virtaal's
    settings, or "Virtaal"."""
    pan_app = sys.modules.get("virtaal.common.pan_app")
    translator = getattr(getattr(pan_app, "settings", None), "translator", None) or {}
    return (translator.get("name") or "").strip() or "Virtaal"


def _response_manifest(request):
    """A TIPP response manifest answering the request manifest
    C{request}, as the specification's samples have it."""
    from virtaal.__version__ import ver

    def name(local):
        return _namespaced(request, local)

    root = etree.Element(request.tag, nsmap=request.nsmap)
    for attribute, value in request.attrib.items():
        root.set(attribute, value)
    descriptor = etree.SubElement(root, name("GlobalDescriptor"))
    etree.SubElement(descriptor, name("UniquePackageID")).text = "urn:uuid:%s" % uuid.uuid4()
    creator = etree.SubElement(descriptor, name("Creator"))
    etree.SubElement(creator, name("Name")).text = _creator_name()
    etree.SubElement(creator, name("ID")).text = HOMEPAGE
    now = datetime.datetime.now(datetime.timezone.utc)
    etree.SubElement(creator, name("Date")).text = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tool = etree.SubElement(creator, name("Tool"))
    etree.SubElement(tool, name("ToolName")).text = "Virtaal"
    etree.SubElement(tool, name("ToolID")).text = HOMEPAGE
    etree.SubElement(tool, name("ToolVersion")).text = ver

    response = etree.SubElement(root, name("TaskResponse"))
    answered = etree.SubElement(response, name("InResponseTo"))
    task = request.find(name("TaskRequest"))
    for child in task if task is not None else ():
        if isinstance(child.tag, str):
            answered.append(_clean(child))
    request_descriptor = request.find(name("GlobalDescriptor"))
    for local in ("UniquePackageID", "Creator"):
        found = request_descriptor.find(name(local)) if request_descriptor is not None else None
        if found is not None:
            answered.append(_clean(found))
    etree.SubElement(response, name("Response")).text = "Success"
    etree.SubElement(response, name("Comment"))

    resources = etree.SubElement(root, name("Resources"))
    bilingual = request.find(name("Resources") + "/" + name("Bilingual"))
    if bilingual is not None:
        resources.append(_clean(bilingual))
    if hasattr(etree, "indent"):
        etree.indent(root, "  ")
    return root


def _clean(element):
    """A copy of C{element} without its comments."""
    copied = copy.deepcopy(element)
    for comment in copied.xpath(".//comment()"):
        parent = comment.getparent()
        if comment.tail:
            previous = comment.getprevious()
            if previous is not None:
                previous.tail = (previous.tail or "") + comment.tail
            else:
                parent.text = (parent.text or "") + comment.tail
        parent.remove(comment)
    copied.tail = None
    return copied


# Work folders

# How old a work folder of no open package must be to go at start-up.
STALE_SECONDS = 24 * 60 * 60


def packages_dir():
    """The folder all packages' work folders are in."""
    from virtaal.common import pan_app

    return os.path.join(pan_app.get_state_dir(), "packages")


def work_folder(path):
    """Where the package C{path}'s files are taken out to: Virtaal's own
    folder, by the package's path, named as the package."""
    key = hashlib.sha256(os.path.abspath(path).encode("utf-8", "surrogateescape")).hexdigest()[:16]
    return os.path.join(packages_dir(), key, os.path.basename(path))


def _key_folder(work_dir):
    """The folder under L{packages_dir()} holding C{work_dir}, resolved,
    or None if C{work_dir} isn't in one."""
    root = os.path.realpath(packages_dir())
    folder = os.path.realpath(os.path.dirname(os.path.abspath(work_dir)))
    if os.path.normcase(os.path.dirname(folder)) != os.path.normcase(root):
        return None
    return folder


def _remove(folder):
    """Remove the work folder C{folder}, if it is one: a real folder
    right inside L{packages_dir()}, never anything else."""
    root = os.path.realpath(packages_dir())
    if os.path.islink(folder) or not os.path.isdir(folder):
        return False
    resolved = os.path.realpath(folder)
    if os.path.normcase(os.path.dirname(resolved)) != os.path.normcase(root):
        return False
    try:
        shutil.rmtree(resolved)
    except OSError:
        return False
    return True


def remove_work_folder(work_dir):
    """Remove the work folder of C{work_dir} under L{packages_dir()}.
    @returns: Whether it was removed."""
    folder = _key_folder(work_dir)
    return folder is not None and _remove(folder)


def _newest(folder):
    """When anything in C{folder} last changed."""
    newest = os.lstat(folder).st_mtime
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in dirnames + filenames:
            try:
                newest = max(newest, os.lstat(os.path.join(dirpath, name)).st_mtime)
            except OSError:
                pass
    return newest


def _holds(folder, paths):
    """Whether any of C{paths} (resolved, normcased) is C{folder} or in it."""
    folder = os.path.normcase(os.path.realpath(folder))
    return any(path == folder or path.startswith(folder + os.sep) for path in paths)


def remove_stale_work_folders(keep=(), now=None):
    """Remove the work folders older than L{STALE_SECONDS} whose package
    isn't open, left by a crash or a package saved elsewhere. C{keep}:
    the paths open now; a work folder holding one stays.
    @returns: The folders removed."""
    root = packages_dir()
    if not os.path.isdir(root):
        return []
    now = time.time() if now is None else now
    kept = [os.path.normcase(os.path.realpath(path)) for path in keep if path]
    removed = []
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if _holds(folder, kept):
            continue
        try:
            stale = now - _newest(folder) > STALE_SECONDS
        except OSError:
            continue
        if stale and _remove(folder):
            removed.append(folder)
    return removed


def open_package(path, work_dir=None):
    """The L{Package} C{path}, its bilingual files taken out."""
    try:
        return Package(path, work_dir or work_folder(path))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError) as exc:
        raise PackageError("The package can't be read: %s" % exc) from exc
