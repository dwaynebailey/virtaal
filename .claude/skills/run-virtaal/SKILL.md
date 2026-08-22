---
name: run-virtaal
description: Build, launch, screenshot, and drive Virtaal (the GTK3 desktop translation editor) on macOS. Use when asked to run Virtaal, start the app, open a .po file in it, take a screenshot of it, or verify a change actually works end-to-end (not just tests).
---

Virtaal is a PyGObject/GTK3 desktop app (not Electron, not a web app) —
it's driven here with a small bash driver
(`.claude/skills/run-virtaal/driver.sh`) that launches it in the
background via `nohup`, then uses macOS's `screencapture` to see it and
`kill` to stop it. All paths below are relative to the repo root.

## Prerequisites

Homebrew's system Python must already have GTK3/PyGObject working:

```bash
python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('ok')"
```

If that fails, `brew install pygobject3 gtk+3` first. (Verified on
Homebrew Python 3.14 on macOS 26.)

Optional: `brew install gtk-mac-integration` for native macOS menu-bar
integration (`GtkosxApplication-1.0` typelib). Without it Virtaal still
runs fine, just with an in-window GTK menu bar instead of the native
top-of-screen one.

Optional: `brew install enchant gtkspell3` for spell checking. Without
them the spellchecker plugin fails to load (safe to ignore for
smoke-testing that doesn't touch spelling) but `driver.sh setup` alone
can't fix it — these are system libraries, not a PyPI package.

## Setup (run once)

```bash
.claude/skills/run-virtaal/driver.sh setup
```

This creates `.venv/` with `--system-site-packages` (so it inherits
the Homebrew GTK3/PyGObject bindings — a plain `venv` without that flag
can't see `gi` at all) and installs the PyPI deps `bin/virtaal` needs
that aren't vendored: `translate-toolkit`, `pycurl`,
`diff-match-patch`, `cheroot` (the WSGI server the vendored local
`tmserver` — `virtaal/support/tmserver.py` — runs on), `pyenchant`
(the Python binding for spell checking — needs the `enchant` and
`gtkspell3` Homebrew formulae from Prerequisites above to actually do
anything; `pip install pyenchant` alone gets you an importable module
that still can't spell-check).

## Run (agent path)

```bash
.claude/skills/run-virtaal/driver.sh launch [path/to/file.po]   # backgrounds it, returns immediately
.claude/skills/run-virtaal/driver.sh alive                      # "yes"/"no"
.claude/skills/run-virtaal/driver.sh screenshot [out.png]        # default /tmp/virtaal-screenshot.png
.claude/skills/run-virtaal/driver.sh log                        # stdout/stderr since launch
.claude/skills/run-virtaal/driver.sh quit                       # stop it
```

Opening a real file is the primary way to drive this app end-to-end —
Virtaal takes a translation file as its one CLI argument and opens
straight into the editing view, which is a real, observable user flow
without needing fragile GUI click automation (see Gotchas). A ready
sample file is `devsupport/testfiles/checks.po`. Verified sequence:

```bash
.claude/skills/run-virtaal/driver.sh launch devsupport/testfiles/checks.po
sleep 6   # first launch is slow: dependency checks + plugin loading
.claude/skills/run-virtaal/driver.sh alive       # -> yes
.claude/skills/run-virtaal/driver.sh screenshot /tmp/shot.png
# read /tmp/shot.png — window titled "checks.po - Virtaal", translation
# units visible, "Untranslated" filter active
.claude/skills/run-virtaal/driver.sh quit
```

## Run (human path)

```bash
PYTHONPATH=. .venv/bin/python3 bin/virtaal [file]
```

Opens a real window; blocks the terminal until closed. Same
prerequisites as above.

## Gotchas

- **Plain `python3 -m venv` is not enough.** Without
  `--system-site-packages`, the venv can't see Homebrew's `gi` module
  at all (`ModuleNotFoundError: No module named 'gi'` at the very
  first import in `bin/virtaal`).
- **Two different missing-dependency failure modes, both silent-ish:**
  missing `translate-toolkit` crashes immediately with a bare Python
  traceback (`ModuleNotFoundError: No module named 'translate'`);
  missing `pycurl`/`diff-match-patch` instead trips `bin/virtaal`'s own
  dependency checker, which prints `DEPENDENCY ERRORS:` and calls
  `sys.exit(1)` *before any window opens* — both look like "nothing
  happened" if you're not reading the log.
- **A background-launched Virtaal that exits within ~1–2s with no
  traceback almost always means `sys.exit(1)` from that same
  dependency checker** — check `driver.sh log` for `DEPENDENCY ERRORS`
  before assuming a crash.
- **`Failed to load plugin "spellchecker"` is non-fatal but no longer
  expected once `enchant`/`gtkspell3` (Homebrew) and `pyenchant`
  (`driver.sh setup`) are all present** — with all three, the plugin
  loads cleanly (verified: the error disappears, `import enchant` and
  `GtkSpell 3.0` both work standalone with real dictionaries). If you
  still see it, check which of the three is missing rather than
  assuming it's expected noise. Two other errors used to be in this
  list too, both since fixed: `Failed to load plugin "migration"` was
  a masked `ImportError` (removed `translate.storage.tmdb`), fixed in
  `d2756bae`/`d6901285`; `Couldn't find OSX_Leopard_theme` was dead
  GTK2 rc-theme-loading code that never did anything on GTK3, removed
  entirely along with the GTK2-only `gtk_osxapplication` menu-bar
  integration it sat next to — replaced with GTK3's
  `GtkosxApplication` (native macOS menu bar) and actual system
  dark/light detection. If you see either again, that's a regression,
  not expected noise.
- **Two new harmless `WARNING` (not `ERROR`) lines appear at startup
  once the spellchecker plugin loads:** `iso_639.xml`/`iso_3166.xml:
  Failed to open file "/usr/share/xml/iso-codes/..."`. That's GtkSpell
  or enchant trying to look up translated language/country display
  names from the freedesktop `iso-codes` package, which doesn't exist
  on macOS/Homebrew. Cosmetic only — doesn't affect spell-checking
  itself.
- **Native macOS menu-bar integration needs `gtk-mac-integration`**
  (`brew install gtk-mac-integration`) for its `GtkosxApplication-1.0`
  typelib. Without it, Virtaal falls back to an in-window GTK menu bar
  (same as Linux/Windows) and logs a debug-level message — not an
  error, and nothing to worry about for smoke-testing.
- **`localtm` (the local, zero-config TM plugin) now actually works.**
  It used to fail with `FileNotFoundError: 'tmserver'` because
  translate-toolkit dropped that console script upstream between
  3.18.1 and 3.19.0; `virtaal/support/tmserver.py` vendors it (plus
  its `selector`/`wsgi`/`tmdb` dependencies) and `localtm.py` now
  launches it via `python -m virtaal.support.tmserver` instead of
  relying on a `tmserver` binary on `PATH`. If you see that error
  again, `driver.sh setup` is probably missing `cheroot` (the WSGI
  server `virtaal/support/wsgi.py` needs). A quiet launch (no
  `ERROR:root:` about TM at all) means it worked - check
  `ps aux | grep virtaal.support.tmserver` to confirm the child is
  actually running.
- **Killing the app hard (`driver.sh quit`, a bare `kill`) leaves the
  `localtm` child process running.** `localtm.py`'s `destroy()` only
  runs on a normal quit through the GTK main loop (File → Quit, or the
  window close button) - a SIGTERM straight to the main process skips
  it entirely, same as it would for any other cleanup code. Not a bug
  introduced by vendoring tmserver, just newly visible now that
  `localtm` actually spawns a live child to leak. `pkill -f
  virtaal.support.tmserver` cleans up a stray one.
- **Don't use System Events / AppleScript UI-scripting to click into
  this app.** Tried it: `tell application "System Events" to tell
  process "Python"` intermittently can't even resolve the process (by
  name or by `unix id`), and even when it can, `count windows` reports
  `0` for a window that's plainly on screen in a screenshot taken at
  the same moment. GTK-on-quartz windows aren't reliably exposed to
  the Accessibility API the way native Cocoa windows are — don't spend
  time on this path. Driving via a CLI file argument + screenshot
  verification is the reliable path here.
- **There is a known, reproducible native segfault in this app** during
  widget-hierarchy teardown (GTK menu/tree teardown racing with
  CPython's cyclic GC — signature: `SIGSEGV` in
  `gtk_widget_propagate_hierarchy_changed_recurse` /
  `gtk_menu_shell_forall`, reached via `pygobject_dealloc` →
  `gc_collect`). Confirmed independently via macOS's own crash
  reporter (`~/Library/Logs/DiagnosticReports/Python-*.ips`). It is
  characterized but unfixed — see project notes, not a driver bug. If
  a launched instance disappears from `alive` unexpectedly, check
  `~/Library/Logs/DiagnosticReports/Python-*.ips` for a fresh `.ips`
  file before assuming the driver killed it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'translate'` | Run `driver.sh setup` (installs `translate-toolkit` into `.venv`). |
| `DEPENDENCY ERRORS:` / `PyCurl is required...` / `diff_match_patch is not installed` then exit, no window | Run `driver.sh setup` (installs `pycurl`, `diff-match-patch`). |
| `driver.sh alive` says `no` moments after `launch` | Run `driver.sh log`; look for `DEPENDENCY ERRORS` first, a Python traceback second. |
| `driver.sh alive` says `no` unexpectedly after it was `yes` | Check `~/Library/Logs/DiagnosticReports/Python-*.ips` for a new crash report — likely the known teardown segfault, not the driver. |
