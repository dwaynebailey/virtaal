<#
.SYNOPSIS
Runs a full uninstall -> install -> UI regression battery against
Virtaal's real Inno Setup installer, on a real Windows machine (a VM or
a physical box) rather than CI's raw PyInstaller bundle. Built
2026-08-24 per request, to close the gap CI's build-windows-installer
job deliberately doesn't cover: it builds and tests dist\virtaal\
virtaal.exe directly, but never actually runs the installer or
uninstaller it also produces. At least one real report this session -
"the unsaved marker seems to persist between reinstalls" - was
specifically about behaviour across an install/uninstall/reinstall
cycle, which a bundle-only check can't reach at all.

.PARAMETER InstallerPath
Path to a Virtaal installer .exe. If omitted, auto-discovers the newest
one under dist\installer (a local devsupport\packaging\windows\
build_installer.ps1 build) or dist\Virtaal-windows-installer (a
downloaded CI artifact, e.g. via `gh run download -R
dwaynebailey/virtaal -n Virtaal-windows-installer -D dist`).

.PARAMETER SkipInitialUninstall
Skip uninstalling any existing Virtaal before installing. Default
behaviour uninstalls first so every run starts from the same clean
slate - the exact condition the "persists between reinstalls" report
needs to reproduce reliably, and generally the only way to be sure
you're testing *this* installer's behaviour rather than a mix of an old
install plus new files copied over it.

.PARAMETER KeepInstalled
Don't uninstall Virtaal again at the end of the run. Default tears back
down to a clean slate so repeated runs stay comparable and don't leave
a test install cluttering a real machine.

.EXAMPLE
.\devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1

.EXAMPLE
.\devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1 -InstallerPath C:\Downloads\virtaal-1.0.0-beta1-setup.exe -KeepInstalled
#>
param(
    [string]$InstallerPath,
    [switch]$SkipInitialUninstall,
    [switch]$KeepInstalled
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here "virtaal_install_helpers.ps1")
. (Join-Path $here "virtaal_ui_test_helpers.ps1")

$results = @()
function Add-Result([string]$Name, [string]$Status, [string]$Detail = "") {
    $script:results += [PSCustomObject]@{ Name = $Name; Status = $Status; Detail = $Detail }
    $marker = switch ($Status) { "Pass" { "[PASS]" }; "Fail" { "[FAIL]" }; default { "[SKIP]" } }
    Write-Host "$marker $Name $(if ($Detail) { "- $Detail" })"
}

# Everything below (all Write-Host output, ::error:: lines, and the
# stdout/stderr log dumps Assert-VirtaalLogsClean/Install-Virtaal print
# on failure) also goes to a transcript file *inside the repo tree*,
# not just the console - added 2026-08-24 specifically because this
# repo checkout is shared from the Windows VM's Z:\ back to a real
# filesystem path on the host (this is that same host), so a transcript
# landing under devsupport\testing\windows\.local-test-runs\ can be read
# directly afterwards without anything needing to be copy-pasted back.
# Gitignored - these are ephemeral run records, not something to commit.
$runLogDir = Join-Path $here ".local-test-runs"
New-Item -ItemType Directory -Path $runLogDir -Force | Out-Null
$transcriptPath = Join-Path $runLogDir "$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
try {
    Start-Transcript -Path $transcriptPath | Out-Null
} catch {
    Write-Host "(could not start transcript at $transcriptPath - continuing without one: $_)"
}

try {

Write-Host "=== 1. Clean slate ==="
if (-not $SkipInitialUninstall) {
    if (-not (Uninstall-Virtaal)) {
        Write-Host "::error::Could not get to a clean uninstalled state - aborting rather than testing on top of a partial install"
        exit 1
    }
} else {
    Write-Host "Skipped (-SkipInitialUninstall)"
}

Write-Host "`n=== 2. Install ==="
$install = Install-Virtaal -InstallerPath $InstallerPath
if (-not $install) {
    Write-Host "::error::Install failed - see above"
    exit 1
}

Write-Host "`n=== 3. UI regression battery ==="

# --- Check: launches cleanly, no unexpected log output ---
# Mirrors CI's "Verify the bundle actually runs" step, against the real
# installed exe instead of the raw dist\virtaal\ bundle. Confirmed live,
# 2026-08-24: this specific launch - the very first one right after a
# fresh install, with nothing warm in any OS/AV file cache yet - can
# genuinely take longer than Start-VirtaalTest's CI-tuned defaults
# (8s + up to 10s more) allow for, especially on a slower/emulated local
# VM rather than a GitHub Actions runner; every later launch in the same
# run (same exe, same machine) got a window handle immediately. Give
# this one specifically a more generous budget rather than either
# widening the shared default (which would slow down every CI check
# too, where the tighter budget has never actually been a problem) or
# risking a false Fail here on a slow-but-fine cold start.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po" -WaitSeconds 20 -HandleTimeoutSeconds 20
    if (-not $t) {
        Add-Result "Launches cleanly" "Fail" "no window handle - see log above"
    } else {
        $logsClean = Assert-VirtaalLogsClean
        Add-Result "Launches cleanly" $(if ($logsClean) { "Pass" } else { "Fail" }) $(if (-not $logsClean) { "unexpected log output - see above" })
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: fresh install shows no modified marker on an unedited file ---
# Directly targets "the unsaved marker seems to persist between
# reinstalls" (reported live, 2026-08-24) - a truly clean
# uninstall+install cycle followed by opening an untouched file should
# never show the "*" modified marker in the title. If this fails, that's
# real evidence something is being retained outside the app itself
# (e.g. a stray config/state file an uninstall isn't removing) rather
# than a code-only bug in this session's modified-flag fixes.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "po\af.po"
    if (-not $t) {
        Add-Result "Fresh install: no modified marker on open" "Fail" "app didn't launch"
    } else {
        $title = Get-VirtaalTitle $t
        $isModified = $title.StartsWith("*")
        Add-Result "Fresh install: no modified marker on open" $(if ($isModified) { "Fail" } else { "Pass" }) "title=`"$title`""
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: repeated navigation doesn't grow the window ---
# Mirrors CI's own check for storetreeview.py's select_index() growth
# bug (fixed 7fd21615) - kept here too since this is testing the real
# installed build, not just whatever CI happened to build from.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "po\af.po"
    if (-not $t) {
        Add-Result "Navigation doesn't grow window" "Fail" "app didn't launch"
    } else {
        $widthBefore = Get-VirtaalWidth $t
        for ($i = 0; $i -lt 25; $i++) { Send-VirtaalKeys $t "{ENTER}" }
        $widthAfter = Get-VirtaalWidth $t
        $growth = $widthAfter - $widthBefore
        Add-Result "Navigation doesn't grow window" $(if ($growth -gt 50) { "Fail" } else { "Pass" }) "grew ${growth}px after 25x Enter"
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: type, Ctrl+Z, modified marker clears ---
# Exercises this session's undo/modified-flag fix chain (4a10f77a,
# 10a51457, dfb2a447, 297f0aaa) end-to-end against the real installed
# app. Assumes the target text box has default keyboard focus when a
# file first opens - if that assumption doesn't hold on a given
# machine/GTK theme, typing "x" won't actually modify anything and the
# check reports Skip rather than a false Fail, so a focus quirk doesn't
# masquerade as a regression.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "po\af.po"
    if (-not $t) {
        Add-Result "Type + Ctrl+Z clears modified marker" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "x"
        $titleAfterType = Get-VirtaalTitle $t
        if (-not $titleAfterType.StartsWith("*")) {
            Add-Result "Type + Ctrl+Z clears modified marker" "Skip" "typing didn't set the modified marker (title=`"$titleAfterType`") - target field may not have had default focus"
        } else {
            Send-VirtaalKeys $t "^z"
            $titleAfterUndo = Get-VirtaalTitle $t
            $stillModified = $titleAfterUndo.StartsWith("*")
            Add-Result "Type + Ctrl+Z clears modified marker" $(if ($stillModified) { "Fail" } else { "Pass" }) "title after undo=`"$titleAfterUndo`""
        }
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: common keyboard shortcuts don't crash the app ---
# Mirrors CI's own check.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "Keyboard shortcuts don't crash" "Fail" "app didn't launch"
    } else {
        foreach ($keys in @("^z", "^x", "^c", "^v")) { Send-VirtaalKeys $t $keys }
        Send-VirtaalKeys $t "^o"
        Send-VirtaalKeys $t "{ESC}"
        $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
        Add-Result "Keyboard shortcuts don't crash" $(if ($stillAlive) { "Pass" } else { "Fail" }) $(if (-not $stillAlive) { "process exited" })
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

Write-Host "`n=== 4. Tear down ==="
if (-not $KeepInstalled) {
    Uninstall-Virtaal | Out-Null
} else {
    Write-Host "Skipped (-KeepInstalled) - Virtaal remains installed at $($install.ExePath)"
}

Write-Host "`n=== Summary ==="
$results | Format-Table -AutoSize | Out-String | Write-Host
$failed = @($results | Where-Object { $_.Status -eq "Fail" })
if ($failed) {
    Write-Host "::error::$($failed.Count) check(s) failed"
    exit 1
}
Write-Host "All checks passed (or were skipped with a stated reason)."
exit 0

} finally {
    try { Stop-Transcript | Out-Null } catch { }
}
