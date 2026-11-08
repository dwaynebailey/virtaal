# Run PyInstaller with this spec from the repo root:
#   pyinstaller -y devsupport/packaging/windows/virtaal.spec
# (or via devsupport/packaging/windows/build_standalone.ps1, which also
# handles the mo-compile prerequisite - see that script's header for why
# this exists alongside a plain pip install rather than replacing it.)
#
# The parts shared with the macOS spec are in
# devsupport/packaging/pyinstaller_common.py.
#
# --onedir (not --onefile): translate.misc.file_discovery's frozen-mode
# data lookup (os.path.dirname(sys.executable), confirmed by reading the
# installed package directly) assumes a flat, same-directory-as-
# executable layout - exactly what PyInstaller's default Windows
# --onedir output already is. --onefile self-extracts to a temp
# directory on every launch instead, which would move data files
# somewhere unpredictable and defeat that assumption.
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo,
)

ROOT = Path(os.getcwd())

sys.path.insert(0, str(ROOT))
from virtaal.__version__ import ver as virtaal_version  # noqa: E402

sys.path.insert(0, str(ROOT / "devsupport" / "packaging"))
import pyinstaller_common as common  # noqa: E402

# build_enchant.ps1's enchant, with its hunspell and winspell providers,
# not the one in pyenchant's wheel; pyi_rth_spelling.py points pyenchant
# at it. build_standalone.ps1 fetches the dictionaries.
ENCHANT_BUILD = Path(r"C:\enchant")
ENCHANT_LIBRARY = ENCHANT_BUILD / "bin" / "libenchant.dll"
ENCHANT_PROVIDERS = [ENCHANT_BUILD / "lib" / "enchant-2" / name
                     for name in ("enchant_hunspell.dll", "enchant_winspell.dll")]
DICTIONARIES = ROOT / "build" / "dictionaries" / "bundled"
for _required in [ENCHANT_LIBRARY] + ENCHANT_PROVIDERS:
    if not _required.is_file():
        raise SystemExit("Not found (run build_enchant.ps1): %s" % _required)


def _version_tuple(ver):
    """VSVersionInfo needs a plain 4-int tuple - ver is e.g. "1.0.0-beta1",
        so take the numeric release part only and pad/truncate to 4."""
    nums = [int(n) for n in re.findall(r'\d+', ver.split('-')[0])][:4]
    return tuple(nums + [0] * (4 - len(nums)))


_ver_tuple = _version_tuple(virtaal_version)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_ver_tuple,
        prodvers=_ver_tuple,
        mask=0x3F,
        flags=0x0,
        OS=0x4,       # VOS_NT_WINDOWS32
        fileType=0x1,  # VFT_APP
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable('040904B0', [
                StringStruct('CompanyName', 'Translate'),
                StringStruct('FileDescription', 'Virtaal'),
                StringStruct('FileVersion', virtaal_version),
                StringStruct('InternalName', 'virtaal'),
                StringStruct('LegalCopyright', common.COPYRIGHT),
                StringStruct('OriginalFilename', 'virtaal.exe'),
                StringStruct('ProductName', 'Virtaal'),
                StringStruct('ProductVersion', virtaal_version),
            ]),
        ]),
        # 1033 = LANG_ENGLISH/SUBLANG_ENGLISH_US, 1200 = Unicode codepage -
        # must match the StringTable's own '040904B0' (same two values,
        # hex-encoded) or Explorer's Properties dialog won't show the
        # strings above at all.
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ],
)

LANGUAGES = common.languages(ROOT)
mo_files = common.mo_files(ROOT)

# gvsbuild's own GTK3 install, from ci.yml's "Download prebuilt GTK3
# (gvsbuild)" step (always C:\gtk - same convention the env vars there
# use). PyInstaller's own build log for this spec warned "Could not
# determine Gio modules path! / Bundling Gio modules is not supported on
# your platform." - GIO modules cover things like network-monitor/D-Bus
# backends, and a GTK app failing to find them is a known way for one to
# hang (retrying a backend connection) rather than fail cleanly. Bundle
# gvsbuild's own gio/modules directory explicitly rather than relying on
# PyInstaller's own (apparently absent-on-this-platform) GIO-module
# discovery. Guarded on the directory actually existing rather than a
# hard path reference, in case a future gvsbuild layout changes this -
# better to build without it (and re-hit the same warning) than hard-fail
# the whole build over one optional directory.
GTK_ROOT = Path(r"C:\gtk")
gio_modules_dir = GTK_ROOT / "lib" / "gio" / "modules"
binaries = []
if gio_modules_dir.is_dir():
    binaries.append((str(gio_modules_dir), "lib/gio/modules"))
# libgettextpo, for the msgfmt check (virtaal/support/gettextpo.py). Loaded
# with ctypes, so nothing else pulls it in.
GETTEXTPO = GTK_ROOT / "bin" / "gettextpo.dll"
if not GETTEXTPO.is_file():
    raise SystemExit("libgettextpo not found: %s" % GETTEXTPO)
binaries.append((str(GETTEXTPO), "."))
binaries.append((str(ENCHANT_LIBRARY), "."))
binaries += [(str(p), "libenchant/lib/enchant-2") for p in ENCHANT_PROVIDERS]

datas = common.datas(ROOT) + mo_files
datas += [(str(p), "share/hunspell") for p in sorted(DICTIONARIES.iterdir())]
# hunspell first, then Windows' own spell checker.
datas.append((str(ROOT / "devsupport" / "packaging" / "windows" / "enchant.ordering"), "libenchant/share/enchant-2"))

a = Analysis(  # noqa: F821
    [str(ROOT / "bin" / "virtaal")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    # Overrides pyinstaller-hooks-contrib's own hook-enchant.py, which
    # bundles the wheel's own enchant and dictionaries. And GtkSpell's,
    # build_gtkspell.ps1's build.
    hookspath=[
        str(ROOT / "devsupport" / "packaging" / "windows" / "pyinstaller-hooks"),
        str(ROOT / "devsupport" / "packaging" / "pyinstaller-hooks"),
    ],
    runtime_hooks=[str(ROOT / "devsupport" / "packaging" / "windows" / "pyi_rth_spelling.py")],
    hiddenimports=common.hiddenimports(),
    hooksconfig=common.hooksconfig(LANGUAGES, {"GtkSpell": "3.0"}),
    excludes=common.EXCLUDES,
    noarchive=False,
)
a.datas = common.trim(a.datas, ROOT)

# The Toolkit's spelling check imports enchant.checker.
if "enchant.checker" not in {entry[0] for entry in a.pure}:
    raise SystemExit("enchant.checker isn't bundled")
# The spell checker plugin needs GtkSpell, linking our enchant: one
# enchant, nothing from the wheel.
_names = {Path(entry[0]).name.lower(): Path(entry[0]) for entry in a.datas + a.binaries}
if "gtkspell-3.0.typelib" not in _names or "libgtkspell3-3-0.dll" not in _names:
    raise SystemExit("GtkSpell isn't bundled")
_enchants = sorted({str(Path(entry[0])) for entry in a.datas + a.binaries
                    if "enchant" in Path(entry[0]).name.lower() and Path(entry[0]).suffix.lower() == ".dll"})
_expected = sorted([ENCHANT_LIBRARY.name] + [str(Path("libenchant", "lib", "enchant-2", p.name))
                                            for p in ENCHANT_PROVIDERS])
_wheel = [entry[0] for entry in a.datas + a.binaries if Path(entry[0]).parts[:2] == ("enchant", "data")]
if _enchants != _expected or _wheel:
    raise SystemExit("Bundled the wrong enchant: %r %r" % (_enchants, _wheel[:10]))

common.check_catalogs(a.datas, mo_files, LANGUAGES)

pyz = PYZ(a.pure, a.zipped_data)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    icon=str(ROOT / "share" / "icons" / "virtaal.ico"),
    version=version_info,
    # PyInstaller 6.0 changed --onedir's default layout to collect
    # everything (including `datas`, e.g. share/virtaal/virtaal.ui) into
    # a _internal/ subdirectory instead of flat next to the exe.
    # Confirmed live: translate-toolkit's file_discovery.py (third-party,
    # not ours to edit) has its own frozen-mode data lookup that assumes
    # the pre-6.0 flat layout (os.path.dirname(sys.executable) + "share"
    # directly, no _internal/ level) - crashed with `ValueError: Could
    # not find "virtaal\virtaal.ui"` because the actual bundled location
    # was C:\...\dist\virtaal\_internal\share\virtaal\virtaal.ui, one
    # level off from where it looked. contents_directory="." is
    # PyInstaller's own documented way to opt back into the flat layout.
    contents_directory=".",
    **common.EXE_OPTIONS,
)

coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, **common.COLLECT_OPTIONS)  # noqa: F821
