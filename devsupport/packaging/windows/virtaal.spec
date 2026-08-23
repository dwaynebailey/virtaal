# Run PyInstaller with this spec from the repo root:
#   pyinstaller -y devsupport/packaging/windows/virtaal.spec
# (or via devsupport/packaging/windows/build_standalone.ps1, which also
# handles the mo-compile prerequisite - see that script's header for why
# this exists alongside a plain pip install rather than replacing it.)
#
# Adapted from devsupport/packaging/macos/virtaal.spec (that one modeled on
# gaphor/gaphor's real, current, shipping spec) - same two dynamic-import
# points PyInstaller's static analysis can't see on its own regardless of
# platform (virtaal's own directory-scanning plugin loader in
# plugincontroller.py, and virtaal/support/tmserver.py, only ever reached
# via bin/virtaal's own runpy-based "--run-module" dispatch - see
# localtm.py), so collect_submodules across the whole virtaal package is
# just as appropriate here.
#
# Genuinely simpler than the macOS spec in one respect:
# translate.misc.file_discovery's frozen-mode data lookup
# (os.path.dirname(sys.executable), confirmed by reading the installed
# package directly) already assumes a flat, same-directory-as-executable
# layout - that's exactly what PyInstaller's default Windows --onedir
# output already is, so there's no equivalent of the macOS build's
# Contents/MacOS vs Contents/Resources symlink workaround needed here.
# --onedir (not --onefile) deliberately: --onefile self-extracts to a
# temp directory on every launch, which would move data files somewhere
# unpredictable and defeat that same flat-layout assumption.
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo,
)

ROOT = Path(os.getcwd())

sys.path.insert(0, str(ROOT))
from virtaal.__version__ import ver as virtaal_version  # noqa: E402

COPYRIGHT = "Copyright 2007-2026 Translate.org.za. GNU General Public License."


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
                StringStruct('CompanyName', 'Translate.org.za'),
                StringStruct('FileDescription', 'Virtaal'),
                StringStruct('FileVersion', virtaal_version),
                StringStruct('InternalName', 'virtaal'),
                StringStruct('LegalCopyright', COPYRIGHT),
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

mo_files = [
    (str(p), str(Path("share", "locale") / p.relative_to(ROOT / "mo").parent / "LC_MESSAGES"))
    for p in (ROOT / "mo").rglob("*.mo")
]

a = Analysis(  # noqa: F821
    [str(ROOT / "bin" / "virtaal")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "share" / "virtaal"), "share/virtaal"),
        (str(ROOT / "share" / "icons"), "share/icons"),
    ]
    + mo_files,
    hiddenimports=collect_submodules("virtaal"),
    hooksconfig={
        "gi": {
            "module-versions": {
                "Gtk": "3.0",
            },
        },
    },
    # Same reasoning as the macOS spec: devsupport isn't needed at runtime
    # in a frozen build (its one consumer, profiling support, is already
    # `if not packaged:`-gated off in bin/virtaal itself), and excluding it
    # avoids the real bug that build hit - a vendored Python-2-era
    # "Optik" optparse.py shadowing the real stdlib module for bin/
    # virtaal's bare `import optparse` (since deleted from the repo
    # entirely, but excluding the whole directory is belt-and-suspenders
    # regardless, and costs nothing since it's dead weight here either way).
    excludes=[
        "FixTk", "tcl", "tk", "_tkinter", "tkinter", "Tkinter", "devsupport",
        # pyenchant loads the SYSTEM libenchant via ctypes at runtime, not
        # a static import PyInstaller's analysis can see - never bundled
        # regardless of whether it's excluded here. The pinned gvsbuild
        # GTK3 build this project uses doesn't ship enchant/gtkspell at
        # all (confirmed live in the Windows 11 ARM64 VM this session -
        # "GtkSpell not installed"), so the spellchecker plugin already
        # degrades gracefully without it, same established posture as the
        # macOS build.
        "enchant",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="virtaal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "share" / "icons" / "virtaal.ico"),
    version=version_info,
)

coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="virtaal"
)
