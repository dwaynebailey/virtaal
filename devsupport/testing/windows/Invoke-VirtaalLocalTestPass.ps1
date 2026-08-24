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

# --- Check: welcome screen launches cleanly with no file argument ---
# Every other check above passes a file on the command line, which
# Virtaal opens synchronously before the event loop even starts
# (virtaal/main.py's _open_with_file) - plugin loading (checks, undo,
# TM, ...) is deferred to run afterwards via GObject.idle_add. With no
# file argument at all, main.py instead takes the _open_with_welcome
# path, where *everything* (UnitController, ModeController, ..., and
# the same deferred plugin loading) is queued via the same idle_add
# mechanism - a genuinely different startup code path none of the
# file-argument checks above ever touch.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath
    if (-not $t) {
        Add-Result "Welcome screen launches cleanly" "Fail" "app didn't launch"
    } else {
        $logsClean = Assert-VirtaalLogsClean
        Add-Result "Welcome screen launches cleanly" $(if ($logsClean) { "Pass" } else { "Fail" }) $(if (-not $logsClean) { "unexpected log output - see above" })
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: menu navigation ---
# Opens and closes each top-level menu via its Alt+mnemonic (see
# share\virtaal\virtaal.ui's "_File"/"_Edit"/"_View"/"_Navigation"/
# "_Help" labels) - a cheap way to exercise menu construction/rendering
# for every menu, not just the handful of items the shortcut checks
# activate directly.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "Menu navigation" "Fail" "app didn't launch"
    } else {
        foreach ($mnemonic in @("f", "e", "v", "n", "h")) {
            Send-VirtaalKeys $t "%$mnemonic"
            Send-VirtaalKeys $t "{ESC}"
        }
        $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
        $logsClean = if ($stillAlive) { Assert-VirtaalLogsClean } else { $false }
        Add-Result "Menu navigation" $(if ($stillAlive -and $logsClean) { "Pass" } else { "Fail" }) $(if (-not $stillAlive) { "process exited" } elseif (-not $logsClean) { "unexpected log output - see above" })
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: welcome screen -> Open dialog -> Recent Files reopens it ---
# Covers two asks at once (they're the same underlying flow): opening
# from the welcome screen via a real file-open dialog (not a CLI
# argument), and reopening via File > Recent Files afterwards. Also the
# closest thing in this battery to the exact sequence behind this
# session's reopen-modified-flag bug family (change, close, reopen a
# *different* file) - this variant reopens the *same* file, so it won't
# catch that specific bug, but exercises the same dialog/menu machinery.
#
# The file-open dialog is a separate top-level window - Send-VirtaalKeys
# can't be used once it's open (it always forces the *main* window
# foreground first, which would steal focus back off the dialog);
# Send-VirtaalPopupKeys targets the dialog's own HWND instead. Typing a
# filename with no field explicitly focused relies on GTK's built-in
# interactive/type-ahead search in the file list, the same mechanism the
# run-virtaal skill's macOS driver relies on for its native NSOpenPanel.
#
# The "r" mnemonic jump for Recent Files (rather than counting Down-
# arrow presses to it) is deliberately order-independent - it'll keep
# working even if virtaal.ui's File menu items get reordered, as long as
# "_Recent Files" keeps that mnemonic. Assumes the Recent Files submenu
# auto-highlights its most-recent (topmost) entry when opened, which
# {ENTER} then activates.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath
    if (-not $t) {
        Add-Result "Welcome screen: open dialog + Recent Files" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "^o"
        $dlg = Wait-VirtaalPopup $t -TimeoutSeconds 8
        if (-not $dlg) {
            Add-Result "Welcome screen: open dialog + Recent Files" "Fail" "Ctrl+O never opened a file dialog"
        } else {
            Send-VirtaalPopupKeys $dlg "af.po"
            Start-Sleep -Milliseconds 500
            Send-VirtaalPopupKeys $dlg "{ENTER}"
            Start-Sleep -Milliseconds 1000
            $titleAfterOpen = Get-VirtaalTitle $t
            if ($titleAfterOpen -notmatch "af\.po") {
                Add-Result "Welcome screen: open dialog + Recent Files" "Fail" "title after Open dialog=`"$titleAfterOpen`" - expected it to mention af.po"
            } else {
                Send-VirtaalKeys $t "^w"
                Start-Sleep -Milliseconds 500
                Send-VirtaalKeys $t "%f"
                Send-VirtaalKeys $t "r"
                Send-VirtaalKeys $t "{RIGHT}"
                Send-VirtaalKeys $t "{ENTER}"
                Start-Sleep -Milliseconds 1000
                $titleAfterRecent = Get-VirtaalTitle $t
                Add-Result "Welcome screen: open dialog + Recent Files" $(if ($titleAfterRecent -match "af\.po") { "Pass" } else { "Fail" }) "title after Recent Files=`"$titleAfterRecent`""
            }
        }
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: Ctrl+P opens Preferences and closes cleanly ---
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "Ctrl+P opens Preferences" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "^p"
        $dlg = Wait-VirtaalPopup $t -TimeoutSeconds 5
        if (-not $dlg) {
            Add-Result "Ctrl+P opens Preferences" "Fail" "no dialog appeared"
        } else {
            $dlgTitle = Get-VirtaalWindowText $dlg
            Close-VirtaalPopup $t $dlg
            $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
            $logsClean = if ($stillAlive) { Assert-VirtaalLogsClean } else { $false }
            Add-Result "Ctrl+P opens Preferences" $(if ($stillAlive -and $logsClean) { "Pass" } else { "Fail" }) "dialog title=`"$dlgTitle`"$(if (-not $stillAlive) { ' - process exited after closing it' } elseif (-not $logsClean) { ' - unexpected log output' })"
        }
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: Ctrl+F search/filter ---
# SearchMode ("Includes only units matching the given search string" -
# modes\searchmode.py) is Virtaal's filter-by-string feature - embedded
# in the main window (a mode swapped in by ModeController), not a
# separate dialog, so no Wait-VirtaalPopup needed here.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "Ctrl+F search/filter" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "^f"
        Send-VirtaalKeys $t "test"
        Send-VirtaalKeys $t "{ESC}"
        $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
        $logsClean = if ($stillAlive) { Assert-VirtaalLogsClean } else { $false }
        Add-Result "Ctrl+F search/filter" $(if ($stillAlive -and $logsClean) { "Pass" } else { "Fail" }) $(if (-not $stillAlive) { "process exited" } elseif (-not $logsClean) { "unexpected log output - see above" })
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: F8 quality checks panel ---
# Toggles the panel on and off against devsupport\testfiles\checks.po,
# a file specifically built to exercise translate-toolkit's checks - if
# any single check throws on real data, that's exactly the kind of thing
# that lands as a traceback in the app's own log, which
# Assert-VirtaalLogsClean would catch (this doesn't read the checks
# panel's actual *contents*, no UI Automation tree available here to do
# that - just that showing/hiding it and running checks against a file
# designed to trigger several of them doesn't crash or log an error).
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "F8 quality checks panel" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "{F8}"
        Send-VirtaalKeys $t "{F8}"
        $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
        $logsClean = if ($stillAlive) { Assert-VirtaalLogsClean } else { $false }
        Add-Result "F8 quality checks panel" $(if ($stillAlive -and $logsClean) { "Pass" } else { "Fail" }) $(if (-not $stillAlive) { "process exited" } elseif (-not $logsClean) { "unexpected log output - see above" })
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: placeable navigation/transfer shortcuts (Alt+Left/Right/Down) ---
# <Virtaal>/Edit/Prev Placeable, Next Placeable (Alt+Left/Right - jump
# between placeables in the target) and Transfer (Alt+Down - copies
# source to an empty target) - see unitview.py's _setup_key_bindings.
# Only Alt+Down has an observable effect worth checking for (copying
# into an *empty* target sets the modified marker); like "Type + Ctrl+Z"
# above, this is Skipped rather than Failed if the current unit's target
# already had text (transfer then does nothing) rather than assuming
# every test file's first unit is untranslated.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "Placeable navigation/transfer shortcuts" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "%{RIGHT}"
        Send-VirtaalKeys $t "%{LEFT}"
        $titleBefore = Get-VirtaalTitle $t
        Send-VirtaalKeys $t "%{DOWN}"
        $titleAfter = Get-VirtaalTitle $t
        $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
        if (-not $stillAlive) {
            Add-Result "Placeable navigation/transfer shortcuts" "Fail" "process exited"
        } elseif ($titleAfter.StartsWith("*") -and -not $titleBefore.StartsWith("*")) {
            Add-Result "Placeable navigation/transfer shortcuts" "Pass" "Alt+Down transferred source to an empty target"
        } else {
            $logsClean = Assert-VirtaalLogsClean
            Add-Result "Placeable navigation/transfer shortcuts" $(if ($logsClean) { "Skip" } else { "Fail" }) $(if ($logsClean) { "no crash; Alt+Down had no observable effect - target likely wasn't empty" } else { "unexpected log output - see above" })
        }
    }
} finally {
    if ($t) { Stop-VirtaalTest $t }
}

# --- Check: click navigation (best-effort) ---
# No UI Automation tree is available here to ask "where is row 2", so
# this clicks at a guessed position (fractions of the window's own
# rect - roughly where the unit-list strip sits, per Virtaal's general
# layout: menu/toolbar at top, a compact list of units below that, the
# source/target editor filling the rest) and checks for a plausible
# *effect* (typing afterwards sets the modified marker, meaning the
# click landed in an editable target field) rather than verifying the
# click precisely. Screenshots are saved either way, specifically so a
# human (or a future Claude session with this same repo shared back)
# can look at where the click actually landed and retune XFraction/
# YFraction if this keeps Skipping.
$t = $null
try {
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "po\af.po"
    if (-not $t) {
        Add-Result "Click navigation" "Fail" "app didn't launch"
    } else {
        $shotBefore = Save-VirtaalScreenshot $t
        Send-VirtaalClick $t -XFraction 0.5 -YFraction 0.15
        Send-VirtaalKeys $t "x"
        $title = Get-VirtaalTitle $t
        $shotAfter = Save-VirtaalScreenshot $t
        $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
        if (-not $stillAlive) {
            Add-Result "Click navigation" "Fail" "process exited - screenshots: $shotBefore, $shotAfter"
        } elseif ($title.StartsWith("*")) {
            Add-Result "Click navigation" "Pass" "click appears to have focused an editable field - screenshots: $shotBefore, $shotAfter"
        } else {
            Add-Result "Click navigation" "Skip" "click at (0.5, 0.15) didn't land in an editable field (title=`"$title`") - see $shotBefore / $shotAfter to retune the coordinates"
        }
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
