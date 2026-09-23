#!/usr/bin/env python3
#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Generate (or check) Virtaal's AppData/website screenshots.

See issue #3625. Drives a real Virtaal window through a handful of
states and captures each with Gdk.pixbuf_get_from_window() - deliberately
not an external screenshot tool, since Xvfb has no window manager to make
those reliable.

    --check   regenerate into a temp dir and compare against the
              committed images; exits non-zero on any mismatch, writes
              nothing to the repo.
    --write   regenerate straight into --out-dir (default:
              docs/_static/appdata).

Both modes run the exact same generation code, so "does it match" and
"here is the refreshed image" can never drift apart from each other.
"""

import argparse
import atexit
import filecmp
import os
import shutil
import sys
import tempfile
from pathlib import Path

# This must happen before any virtaal import: pan_app.get_config_dir()
# resolves against $HOME ("~/.virtaal" on Linux, "~/Library/Application
# Support/Virtaal" on macOS), and MainView.quit() would otherwise persist
# this script's capture window size into a real user's Virtaal config.
# Redirecting HOME to a throwaway directory keeps this script from ever
# touching that file - the driver below calls Gtk.main_quit() directly
# instead of main_controller.quit() for the same reason (no save-prompt,
# no settings write).
_fake_home = tempfile.mkdtemp(prefix="virtaal-screenshot-home-")
atexit.register(shutil.rmtree, _fake_home, ignore_errors=True)
os.environ["HOME"] = _fake_home

REPO_ROOT = Path(__file__).resolve().parents[2]
APPDATA_DIR = REPO_ROOT / "docs" / "_static" / "appdata"
TESTFILES = REPO_ROOT / "devsupport" / "testfiles"

# Real Flathub screenshots for apps in this category (GTranslator, Lokalize,
# Parlatype, Bottles) cluster around a ~1.3-1.5 aspect ratio, not the 16:9
# AppStream suggests as a fallback - a multi-pane editor wants a less-wide
# shape. Comfortably under Flathub's 1000x700 cap either way.
WINDOW_WIDTH = 900
WINDOW_HEIGHT = 650

# (output filename, source file to open, unit index to navigate to).
# Unit indices are picked by hand by inspecting each fixture once and
# pinned to its current content - Cursor.force_index() then jumps there
# directly, no runtime search needed.
#
# TM suggestions, autocomplete, and spellchecking are further states worth
# adding once a fixture/setup exists for each (spellchecking also needs
# enchant/gtkspell3 installed wherever this runs).
STATES = [
    ("welcome.png", REPO_ROOT / "po" / "af.po", 0),
    # "See https://virtaal.org for details." - a URL placeable.
    ("placeable.png", TESTFILES / "placeables.po", 1),
    # "A variable can be printed with printf: %s" -> translation drops
    # the %s - a single, clearly-visible printf-variable check failure.
    ("window.png", TESTFILES / "checks.po", 12),
]


def _run(out_dir):
    """Drive a real Virtaal window through STATES, capturing each to
    out_dir. Runs Gtk.main() - blocks until the driver below quits it."""
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    from virtaal.main import Virtaal

    _first_name, first_source, _first_index = STATES[0]
    app = Virtaal(str(first_source))
    main_controller = app.main_controller
    window = main_controller.view.main_window

    def capture(out_path):
        window.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        gdk_window = window.get_window()
        width, height = window.get_size()
        pixbuf = Gdk.pixbuf_get_from_window(gdk_window, 0, 0, width, height)
        pixbuf.savev(str(out_path), "png", [], [])

    def driver():
        # Give the controllers main.py defers via GLib.idle_add (checks,
        # undo, plugins, ...) a chance to actually construct before the
        # first capture - they only run once Gtk.main() is pumping.
        for _ in range(10):
            yield
        for i, (name, source, index) in enumerate(STATES):
            if i > 0:
                main_controller.open_file(str(source))
                for _ in range(10):
                    yield
            if index is not None:
                main_controller.store_controller.cursor.force_index(index)
            window.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
            window.queue_draw()
            for _ in range(6):
                yield
            capture(out_dir / name)
        # Not main_controller.quit(): that also persists this capture
        # window's size into settings and prompts to save. Plugins do need
        # an explicit shutdown though - the local-TM plugin's `tmserver`
        # subprocess otherwise outlives us as an orphan holding stdout open.
        if main_controller.plugin_controller:
            main_controller.plugin_controller.shutdown()
        Gtk.main_quit()

    gen = driver()

    def step():
        try:
            next(gen)
        except StopIteration:
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(150, step)
    app.run()


def _compare(generated_dir, committed_dir):
    mismatches = []
    for name, _source, _index in STATES:
        committed_file = committed_dir / name
        if not committed_file.exists() or not filecmp.cmp(
            generated_dir / name, committed_file, shallow=False
        ):
            mismatches.append(name)
    return mismatches


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="regenerate and compare against the committed images; write nothing",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help="regenerate and write into --out-dir",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=APPDATA_DIR,
        help="where to write images in --write mode (default: docs/_static/appdata)",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=REPO_ROOT / "screenshot-artifacts",
        help="in --check mode, where to leave freshly generated images if "
        "they differ, for CI to upload as a build artifact",
    )
    args = parser.parse_args(argv)

    if args.check:
        with tempfile.TemporaryDirectory(prefix="virtaal-screenshots-") as tmp:
            tmp_path = Path(tmp)
            _run(tmp_path)
            mismatches = _compare(tmp_path, APPDATA_DIR)
            if mismatches:
                if args.artifact_dir.exists():
                    shutil.rmtree(args.artifact_dir)
                shutil.copytree(tmp_path, args.artifact_dir)
                print("Screenshots differ from what's committed:", ", ".join(mismatches))
                print(f"Freshly generated images copied to {args.artifact_dir}")
                return 1
            print("Screenshots match what's committed.")
            return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    _run(args.out_dir)
    print(f"Wrote {len(STATES)} screenshots to {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
