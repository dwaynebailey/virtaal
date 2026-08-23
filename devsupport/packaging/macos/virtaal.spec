# Run PyInstaller with this spec from the repo root:
#   pyinstaller -y devsupport/packaging/macos/virtaal.spec
# (or via devsupport/packaging/macos/build_standalone.sh, which also
# handles the mo-compile prerequisite - see that script's header for why
# this exists alongside build.sh rather than replacing it.)
#
# Modeled on gaphor/gaphor's _packaging/gaphor.spec (Python/PyGObject/GTK4,
# ships real signed/notarized macOS .dmg releases this way) - confirmed via
# their actual, current spec file, not reconstructed from docs. Adapted for
# this codebase's GTK3 (not GTK4) and its two dynamic-import points
# PyInstaller's static analysis can't see on its own: virtaal's own
# directory-scanning plugin loader (virtaal/controllers/plugincontroller.py
# __import__()s plugins by name at runtime, not via a static import
# anywhere), and virtaal/support/tmserver.py (only ever reached via bin/
# virtaal's own runpy-based "--run-module" dispatch, itself only reached at
# runtime from a subprocess argv check - see localtm.py). collect_submodules
# across the whole virtaal package (not just .plugins) is the simplest way
# to not have to enumerate either by hand, at negligible cost for a
# pure-Python package this size.
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(os.getcwd())
PACKAGING = ROOT / "devsupport" / "packaging" / "macos"

sys.path.insert(0, str(ROOT))
from virtaal.__version__ import ver as virtaal_version  # noqa: E402

sys.path.insert(0, str(PACKAGING))
from generate_info_plist import document_types  # noqa: E402

COPYRIGHT = "Copyright 2007-2026 Translate.org.za. GNU General Public License."

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
                # Optional native macOS menu-bar integration
                # (mainview.py's try/except-gated GtkosxApplication block) -
                # statically imported, so PyInstaller's gi hook would try
                # to bundle it regardless; declared explicitly to match the
                # actual gi.require_version() call rather than relying on
                # whatever the hook guesses.
                "GtkosxApplication": "1.0",
            },
        },
    },
    # devsupport isn't needed at runtime in a frozen build - its one
    # consumer (profiling support) is already `if not packaged:`-gated
    # off in bin/virtaal itself. Also sidesteps a real bug this build hit:
    # devsupport/optparse.py (a vendored Python-2-era "Optik" backport,
    # confirmed to have zero real consumers and since deleted from the
    # repo entirely) was getting resolved by PyInstaller's module graph
    # for bin/virtaal's bare `import optparse` instead of the real stdlib
    # module - confirmed via build/virtaal/xref-virtaal.html, crashed
    # immediately at startup with `AttributeError: 'dict' object has no
    # attribute 'has_key'`. Deleting the file was the actual fix; this
    # exclude is belt-and-suspenders since devsupport is dead weight here
    # regardless.
    excludes=[
        "FixTk", "tcl", "tk", "_tkinter", "tkinter", "Tkinter", "devsupport",
        # pyenchant's _enchant.py loads the SYSTEM libenchant via
        # ctypes.util.find_library() + ctypes.cdll.LoadLibrary() - a
        # runtime call PyInstaller's static analysis can't see at all, so
        # it's never bundled/relinked. On a machine with Homebrew's GTK3
        # stack also installed (like this one), that finds and loads
        # Homebrew's OWN libenchant, which pulls in Homebrew's ENTIRE
        # glib/gobject/gio/gmodule chain as its own (unbundled, absolute-
        # path) dependencies - confirmed directly via `vmmap` on a running
        # process, showing both the bundled and Homebrew copies of all
        # four mapped simultaneously. Two independent, complete GObject
        # runtimes in one process splits the type registry enough to
        # break cairo's foreign-struct-converter registration (`TypeError:
        # Couldn't find foreign struct converter for 'cairo.Context'`) and
        # trips the ObjC runtime's duplicate-class warning
        # (GNotificationCenterDelegate) - both disappeared once this was
        # excluded. Spell checking already degrades gracefully without
        # enchant (an already-established, expected code path - see
        # run-virtaal skill notes) - properly self-contained spell
        # checking would need libenchant + its own dependency chain
        # vendored and relinked too, tracked as a separate follow-up in
        # ISSUE_TRIAGE.md rather than attempted here.
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
    icon=str(ROOT / "devsupport" / "virtaal.icns"),
    codesign_identity=os.getenv("CODESIGN_IDENTITY"),
)

coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="virtaal"
)

app = BUNDLE(  # noqa: F821
    coll,
    name="Virtaal.app",
    icon=str(ROOT / "devsupport" / "virtaal.icns"),
    bundle_identifier="za.org.translate.virtaal",
    version=virtaal_version,
    info_plist={
        "CFBundleDisplayName": "Virtaal",
        "CFBundleName": "Virtaal",
        "CFBundleShortVersionString": virtaal_version,
        "CFBundleVersion": virtaal_version,
        "NSHumanReadableCopyright": COPYRIGHT,
        # Same guess as generate_info_plist.py's dev-convenience bundle -
        # not rigorously tested against a matrix of older macOS versions.
        "LSMinimumSystemVersion": "10.15",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "CFBundleDocumentTypes": document_types(),
    },
)
