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
