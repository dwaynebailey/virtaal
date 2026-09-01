# Contributing to Virtaal

## Set up pre-commit

This repo uses [pre-commit](https://pre-commit.com) for basic hygiene
checks (trailing whitespace, end-of-file newlines, valid YAML/TOML, no
leftover merge-conflict markers, no accidental large files, valid Python
syntax, no stray debugger breakpoints) — see `.pre-commit-config.yaml`.
It's deliberately minimal for now: no formatter/linter is enforced yet.

Install it once per checkout so the checks run automatically before each
commit, rather than only finding out in CI after pushing:

```bash
pip install pre-commit
pre-commit install
```

CI also runs these same hooks (the `pre-commit` job in
`.github/workflows/ci.yml`), but only against files that changed in that
push/PR — not the whole tree, since a lot of pre-existing files don't
pass yet (see `ISSUE_TRIAGE.md`'s dev-tooling section). Running
`pre-commit install` locally means you find out before pushing, not
after.

## Testing a macOS build without building it yourself

Every push builds a real, self-contained `Virtaal.app` in CI (the
`build-macos-app` job) and uploads it as a build artifact — useful for
testing a specific commit's behaviour on macOS without needing a local
Homebrew/GTK3 setup, or for grabbing a colleague's branch to try out.

```bash
devsupport/packaging/macos/download-latest-app.sh          # current branch
devsupport/packaging/macos/download-latest-app.sh py3      # or any branch by name
```

Downloads the newest such artifact for that branch (via `gh`, so you
need it authenticated against this repo) and extracts it to
`dist/downloaded/Virtaal.app`. Deliberately doesn't just look at "the
latest CI run" — `test-windows` is a known, separately-tracked failure
that keeps the *whole run* red even on commits where the macOS build
succeeded and uploaded fine, so it queries the artifacts API directly
instead of trusting the run's overall status.

The result is only ad-hoc signed, not notarized (`ISSUE_TRIAGE.md`'s
`#3313` entry) — macOS blocks a first launch of anything downloaded this
way regardless of who built it. Before it'll open:

```bash
xattr -cr dist/downloaded/Virtaal.app
codesign --force --deep -s - dist/downloaded/Virtaal.app
```

Then launch it like any other app bundle - not directly (it's a
directory, not an executable file, so `./Virtaal.app` fails with
"permission denied"):

```bash
open dist/downloaded/Virtaal.app                              # normal launch
dist/downloaded/Virtaal.app/Contents/MacOS/virtaal --version   # or run the binary inside it directly, e.g. for --help/--version
```

Building one yourself instead (needs Homebrew's GTK3 stack set up
locally - see `.claude/skills/run-virtaal/SKILL.md`'s prerequisites, or
`devsupport/packaging/macos/build.sh` for a faster, non-self-contained
dev build that just wraps your own checkout):

```bash
devsupport/packaging/macos/build_standalone.sh
```

The same CI job also builds a real `.dmg` installer from that bundle
(`Virtaal-macos-dmg` artifact) - a drag-to-Applications disk image with
the app's own background art and icon, not just the raw `.app`. Build
one locally the same way, after `build_standalone.sh`:

```bash
devsupport/packaging/macos/build_dmg.sh
```

Same signing caveat as the `.app` above applies here too - it's not
notarized, so macOS will still block a first open (`xattr -cr` +
`codesign --force --deep -s -` on the extracted `.app`, same as above,
after mounting the `.dmg`).

## Testing on Windows locally (Mac/Linux devs)

Virtaal's a GUI app, so it's worth actually seeing it run on real
Windows 11, not just trusting CI's headless test pass. On an Apple
Silicon Mac, [UTM](https://mac.getutm.app) (free) runs Windows 11 ARM64
natively via Apple's own virtualization framework - fast, no CPU
emulation involved, unlike trying to run x86_64 Windows here.

### Set up the VM

1. Install UTM from [mac.getutm.app](https://mac.getutm.app) (or the
   Mac App Store).
2. Get a Windows 11 ARM64 installer ISO. Easiest: install
   [CrystalFetch](https://apps.apple.com/app/crystalfetch/id6461174912)
   (free, App Store) - it downloads the current official build directly
   from Microsoft. Alternatively, UTM's own guide links a direct
   [Windows 11 for Apple Silicon Macs](https://docs.getutm.app/guides/windows/)
   ISO download if you'd rather not install another app. Either way, you
   need a real Windows license to activate it - a VM doesn't get you
   around that.
3. In UTM: **+** → **Virtualize** → **Windows** → pick RAM/CPU (4GB/2
   cores minimum; more if your Mac has the headroom) → Continue → make
   sure **"Install Windows 10 or higher"** and **"Install drivers and
   SPICE tools"** are both checked → Browse to the ISO → Continue → give
   it at least 64GB of disk → Continue → Save.
4. Boot it, press any key when prompted to boot from the ISO. Newer UTM
   versions handle Secure Boot/TPM automatically; if Windows Setup
   refuses with "This PC can't run Windows 11," see
   [UTM's troubleshooting section](https://docs.getutm.app/guides/windows/)
   for the `LabConfig` registry bypass.
5. To skip the Microsoft-account requirement and set up a local account
   instead: at the network-connection screen during Setup, press
   **Shift+F10** for a command prompt and run `start ms-cxh:localonly`
   (the older `OOBE\BYPASSNRO` trick is now blocked by Microsoft on
   current builds).
6. Known UTM gotcha on Windows 11 24H2: the installer can go to a black
   screen because of the bundled guest-tools graphics driver. If that
   happens, eject the guest-tools ISO from UTM's CD menu, finish
   Windows Setup without it, then remount the tools ISO afterward and
   run "Install Windows Guest Tools" - you may need to reset the VM once
   more for the drivers to load cleanly.
7. Install the SPICE guest tools if they didn't run automatically (needed
   for working networking - without them Windows may insist there's no
   internet connection even once you're on the desktop).

### Set up the dev environment inside the VM

Match what `test-windows` in `.github/workflows/ci.yml` actually
installs, rather than improvising a different setup:

1. Install [Git for Windows](https://git-scm.com/download/win) and
   Python 3.14 (matching what `windows-latest` runs) from
   [python.org](https://www.python.org/downloads/windows/) - ARM64
   builds are available directly.
2. Install [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio)
   with the "Desktop development with C++" workload - this gives you
   `cl.exe` (needed to build PyGObject) and, usefully for debugging,
   `cdb.exe`/WinDbg under `Windows Kits\10\Debuggers\`.
3. Download the same `gvsbuild` GTK3 build CI uses (check
   `gvsbuild_version` in `ci.yml` for the current pinned version) from
   [wingtk/gvsbuild releases](https://github.com/wingtk/gvsbuild/releases)
   and extract to `C:\gtk`.
4. Clone the repo, then in a Developer PowerShell for VS (so `cl.exe` is
   on `PATH`), set the same environment variables `ci.yml`'s Windows job
   sets - `PKG_CONFIG_PATH`, `GI_TYPELIB_PATH`, `INCLUDE`/`LIB` pointing
   at `C:\gtk`, and add `C:\gtk\bin` to `PATH` - before
   `pip install --no-build-isolation .[test]`. Read through the
   `test-windows` job's steps directly for the exact current values and
   ordering (some of them, like `PKG_CONFIG_PATH`, get silently
   overwritten by other tools if set in the wrong order - see that
   job's own comments for why).

This is also the most useful environment for chasing the Windows CI
crash tracked in `ISSUE_TRIAGE.md` - no 30-minute `tmate` session time
limit, real WinDbg instead of just `cdb.exe`'s command-line interface,
and a real GUI to actually watch what happens.
