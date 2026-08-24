# Windows testing recipes

Two layers of Windows-side testing exist for Virtaal, covering different
things:

- **CI** (`.github/workflows/ci.yml`'s `build-windows-installer` job)
  builds and UI-tests the raw PyInstaller bundle (`dist\virtaal\
  virtaal.exe`) directly, then packages it into an installer as a final
  step - it never actually *runs* that installer or its uninstaller.
  Good for catching regressions in the app itself on every push, fast.
- **This directory**, run by hand on a real Windows machine (VM or
  physical box), tests the actual install/uninstall/reinstall cycle a
  real user goes through - something CI's bundle-only checks can't
  reach at all. At least one real report ("the unsaved marker seems to
  persist between reinstalls") was specifically about behaviour across
  that cycle.

## Files

- `virtaal_ui_test_helpers.ps1` - drives a running `virtaal.exe`
  (launch, read window geometry/title via Win32, send keystrokes, read
  the frozen build's log files, clean up). Used by both CI and the
  local recipe below.
- `virtaal_install_helpers.ps1` - installs/uninstalls Virtaal's real
  Inno Setup package silently and idempotently, via the Windows
  uninstall registry (works whether the install was per-user or
  machine-wide - doesn't assume a fixed path).
- `Invoke-VirtaalLocalTestPass.ps1` - orchestrates both: uninstall ->
  install -> a battery of UI regression checks against the real
  installed app -> uninstall again, with a pass/fail summary and a
  process exit code.

## Running a local test pass

Windows' default PowerShell execution policy (`Restricted`) blocks
running any local `.ps1` file at all, including this one - if you hit
`... cannot be loaded because running scripts is disabled on this
system`, allow it for just the current window (reverts automatically
when you close it, no permanent/system-wide change):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

From the repo root, in PowerShell, on Windows:

```powershell
# Auto-discovers the newest installer under dist\installer or
# dist\Virtaal-windows-installer
.\devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1
```

To test a specific installer (e.g. one downloaded from a CI run):

```powershell
gh run download <run-id> -R dwaynebailey/virtaal -n Virtaal-windows-installer -D dist
.\devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1
```

Or point at one directly:

```powershell
.\devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1 -InstallerPath C:\Downloads\virtaal-1.0.0-beta1-setup.exe
```

Useful switches:

- `-SkipInitialUninstall` - test installing *on top of* whatever's
  already there, instead of starting from a clean slate (the default).
  Use this to specifically test an upgrade path.
- `-KeepInstalled` - leave Virtaal installed afterwards instead of
  tearing back down to a clean slate. Useful when you want to keep
  poking at it by hand right after the automated battery finishes.

The script exits 0 if every check passed, 1 otherwise - safe to use as
a gate in a self-hosted Windows CI runner later, not just interactively.

Every run's full console output is also written to a transcript under
`.local-test-runs\<timestamp>.log` (gitignored) next to this README -
if this repo checkout is itself a shared folder (e.g. a UTM share from
a macOS host into a Windows VM), that transcript is readable directly
from the host side afterwards without anything needing to be
copy-pasted out of the Windows terminal. The app's own stdout/stderr
logs (`%APPDATA%\Virtaal\std{out,err}_virtaal.log`) and the installer's
log (`%TEMP%\virtaal-install-*.log`) live outside the repo tree and
aren't captured this way on their own - but their content gets printed
to the console (and so into the transcript too) automatically whenever
a check that reads them fails. The click-navigation check also saves
before/after screenshots to the same `.local-test-runs\` directory
regardless of outcome, for the same reason - readable from the host
side without anything needing to be copied out by hand.

### What the battery covers

- App launches cleanly (both with a file argument and from a bare
  welcome screen - genuinely different startup code paths, see
  `virtaal/main.py`'s `_open_with_file` vs `_open_with_welcome`).
- A fresh install shows no modified marker on an untouched file.
- Repeated navigation doesn't grow the window.
- Type + Ctrl+Z clears the modified marker.
- Common shortcuts (Ctrl+Z/X/C/V/O) don't crash.
- Menu navigation (every top-level menu opens/closes cleanly).
- Welcome screen -> a real File > Open dialog (not a CLI argument) ->
  File > Recent Files reopens the same file.
- Ctrl+P opens Preferences and it closes cleanly.
- Ctrl+F's search/filter (`modes/searchmode.py`) doesn't crash.
- F8's quality-checks panel doesn't crash or log an error against a
  file built to exercise several checks.
- Placeable navigation/transfer (Alt+Left/Right/Down) - Alt+Down
  specifically verified to copy source into an empty target.
- Click navigation, and clicking the two status-bar `PopupMenuButton`s
  (check-type/"Project Type" bottom-left, language-pair bottom-right) -
  all three best-effort (see `Send-VirtaalClick`'s own comments for why
  these are inherently a guess without a UI Automation tree available).

None of this reaches into a check's *content* (e.g. what the checks
panel actually lists, or whether the "right" recent file reopened
beyond its filename appearing in the title) - there's no UI Automation
tree wired up here, just Win32 window geometry/title/foreground-window
plus SendKeys and a plain Win32 mouse click. That's enough to catch
crashes, hangs, and the specific modified-flag/resize regressions this
session was about; deeper content assertions would need a real
Automation-tree library (e.g. FlaUI) wired in as a bigger follow-up.

## Using the pieces separately

Both helper files are meant to be dot-sourced and driven directly too,
e.g. for a quick manual check or a one-off repro:

```powershell
. .\devsupport\testing\windows\virtaal_install_helpers.ps1
. .\devsupport\testing\windows\virtaal_ui_test_helpers.ps1

Uninstall-Virtaal | Out-Null
$install = Install-Virtaal -InstallerPath C:\Downloads\virtaal-1.0.0-beta1-setup.exe
$t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "po\af.po"
Get-VirtaalTitle $t          # e.g. "af.po - Virtaal"
Send-VirtaalKeys $t "x"
Get-VirtaalTitle $t          # e.g. "*af.po - Virtaal"
Send-VirtaalKeys $t "^z"
Get-VirtaalTitle $t          # back to "af.po - Virtaal"
Stop-VirtaalTest $t
Uninstall-Virtaal | Out-Null
```

`Get-VirtaalInstallInfo` (in `virtaal_install_helpers.ps1`) is a
read-only status check - "is Virtaal currently installed, from where,
what version" - with no side effects, useful before deciding whether to
touch anything.
