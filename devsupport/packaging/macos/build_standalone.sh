#!/bin/bash
# Builds dist/Virtaal.app as a real, self-contained bundle: Python, GTK3/
# PyGObject's dylibs, and every dependency vendored inside Contents/ via
# PyInstaller - unlike build.sh's dist/Virtaal.app, this one runs on a
# machine that never had this checkout, Homebrew, or GTK3 set up at all.
#
# This is deliberately a separate script from build.sh, not a replacement
# for it: build.sh is fast (no build step, just wraps this checkout's
# .venv) and useful for quick local iteration; this one is slow (PyInstaller
# has to trace and copy the whole dependency tree) and is the one that
# actually matters for distribution. See ISSUE_TRIAGE.md's "self-contained
# macOS .app bundle" entry for the fuller story - notably, this also fixes
# the "Python" branding limitation build.sh's bundle still has: PyInstaller
# produces a compiled native bootloader that embeds the interpreter
# directly, never invoking a Framework Python's own launcher, so the
# self-relaunch-into-Resources/Python.app mechanism that blocked the
# launcher-script approach never gets a chance to fire - confirmed via
# `lsappinfo` showing the bundle's own name as the real macOS app identity,
# not "Python".
set -eu
cd "$(git rev-parse --show-toplevel)"

PYTHON="$PWD/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="python3"

# setup.py's mo-compile step runs unconditionally as a side effect of
# *any* setup.py invocation (see setup.py's own module docstring) - this
# is the documented way to trigger it without going through pip/build.
"$PYTHON" setup.py --version >/dev/null

"$PYTHON" -m pip show pyinstaller >/dev/null 2>&1 || "$PYTHON" -m pip install pyinstaller

rm -rf build/virtaal dist/Virtaal.app
"$PYTHON" -m PyInstaller -y devsupport/packaging/macos/virtaal.spec

# PyInstaller's macOS BUNDLE step relocates data files (share/) into
# Contents/Resources/ (proper Apple convention), but translate-toolkit's
# frozen-mode data lookup (file_discovery.py) only checks next to the
# executable (Contents/MacOS/) - a Windows/PyInstaller-flat-layout
# assumption that doesn't hold for a macOS .app's split layout. A symlink
# is the fix, not patching an external dependency - confirmed this is
# needed by a real crash without it ("Could not find virtaal/virtaal.ui").
ln -sf ../Resources/share dist/Virtaal.app/Contents/MacOS/share

echo "Built dist/Virtaal.app (self-contained)"
