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

.PARAMETER HumanDelayMs
Runs at full speed (0, the default) unless set - every dialog, click,
and keystroke already has its own short, tuned settle delay, and a
dialog that opens and closes within a few hundred milliseconds is real
but too fast for a human to actually see. Set this (e.g. 2000 for a
2-second pause) to watch a run happen instead of only reading its
transcript afterward - applies uniformly to every interaction, not
just one check.

.EXAMPLE
.\devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1

.EXAMPLE
.\devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1 -InstallerPath C:\Downloads\virtaal-1.0.0-beta1-setup.exe -KeepInstalled

.EXAMPLE
.\devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1 -HumanDelayMs 2000
#>
param(
    [string]$InstallerPath,
    [switch]$SkipInitialUninstall,
    [switch]$KeepInstalled,
    [int]$HumanDelayMs = 0
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here "virtaal_install_helpers.ps1")
. (Join-Path $here "virtaal_ui_test_helpers.ps1")
if ($HumanDelayMs -gt 0) { Set-VirtaalHumanDelay -Milliseconds $HumanDelayMs }

$results = @()
function Add-Result([string]$Name, [string]$Status, [string]$Detail = "") {
    $script:results += [PSCustomObject]@{ Name = $Name; Status = $Status; Detail = $Detail }
    $marker = switch ($Status) { "Pass" { "[PASS]" }; "Fail" { "[FAIL]" }; default { "[SKIP]" } }
    Write-Host "$marker $Name $(if ($Detail) { "- $Detail" })"
}

function Invoke-VirtaalCheck {
    <#
    .SYNOPSIS
    Runs one check's $Body, catching any *unexpected* exception as a Fail
    result instead of letting it abort every check after it and skip
    Tear down entirely - confirmed live 2026-08-24: an unhandled
    Start-Process error partway through the battery left Virtaal
    installed and every later check unrun, with no summary at all. $Body
    is dot-sourced (not called in a child scope) so it can set $t itself
    and have this wrapper's own finally still see it for cleanup; $Body
    is expected to call Add-Result for its own outcome - this wrapper
    only adds one itself if $Body throws before getting there.
    #>
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][scriptblock]$Body)
    # A plain text transcript can't rewrite an earlier line in place, so
    # this is a second line ([TESTING] now, [PASS]/[FAIL]/[SKIP] later
    # from Add-Result) rather than an actual in-place update - still
    # useful for watching a run live, since some checks take many
    # seconds and there was previously no way to tell what was currently
    # in flight versus just... nothing happening yet.
    Write-Host "[TESTING] $Name"
    $t = $null
    try {
        . $Body
    } catch {
        Add-Result $Name "Fail" "unexpected error: $_"
    } finally {
        if ($t) { Stop-VirtaalTest $t }
    }
}

$script:scratchDir = Join-Path $env:TEMP "virtaal-test-scratch"
function New-VirtaalScratchFile {
    <#
    .SYNOPSIS
    Copies a repo-relative source file (e.g. "po\af.po") out to a
    disposable scratch directory under %TEMP%, well outside the repo,
    and returns the copy's full path. Any check that actually saves
    (Ctrl+S) or otherwise risks writing must use a scratch copy, never a
    checked-in file directly - so no combination of SendKeys timing,
    the wrong dialog button, or a real bug can ever leave a git-tracked
    file modified on disk. Cleaned up in Tear down.
    #>
    param([Parameter(Mandatory)][string]$SourceRelativePath, [Parameter(Mandatory)][string]$Suffix)
    New-Item -ItemType Directory -Path $script:scratchDir -Force | Out-Null
    $base = [IO.Path]::GetFileNameWithoutExtension($SourceRelativePath)
    $ext = [IO.Path]::GetExtension($SourceRelativePath)
    $destPath = Join-Path $script:scratchDir "$base-$Suffix$ext"
    Copy-Item -Path $SourceRelativePath -Destination $destPath -Force
    return $destPath
}

function Open-VirtaalFileViaDialog {
    <#
    .SYNOPSIS
    Sends Ctrl+O, waits for the file-open dialog, types $FileNameToFind
    via GTK's interactive/type-ahead search (the same mechanism the
    run-virtaal skill's macOS driver relies on for its native
    NSOpenPanel), and presses Enter. Returns the dialog's HWND on
    success or $null if Ctrl+O never opened one - doesn't verify the
    resulting title itself, since different callers want different
    filename checks.

    Confirmed live, 2026-08-24: this exact type-ahead-then-Enter
    sequence succeeded in one run and failed to actually select
    anything in another, immediately after the *same* code change, on
    the *same* machine - consistent with a race between the dialog
    reporting itself as foreground (which Wait-VirtaalPopup already
    waits for) and its internal widget layout/keyboard focus actually
    finishing settling, rather than a real app bug. Extracted into one
    place (was duplicated across three checks) specifically so a
    reliability fix here - the added settle delay below - benefits all
    of them at once, and so a future failure has one call site to add
    more diagnostics to instead of three.
    #>
    param([Parameter(Mandatory)]$Instance, [Parameter(Mandatory)][string]$FileNameToFind, [int]$DialogTimeoutSeconds = 8)
    Send-VirtaalKeys $Instance "^o"
    $dlg = Wait-VirtaalPopup $Instance -TimeoutSeconds $DialogTimeoutSeconds
    if (-not $dlg) { return $null }
    Start-Sleep -Milliseconds 500
    Send-VirtaalPopupKeys $dlg $FileNameToFind
    Start-Sleep -Milliseconds 500
    Send-VirtaalPopupKeys $dlg "{ENTER}"
    Start-Sleep -Milliseconds 1000
    return $dlg
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

Invoke-VirtaalCheck "Launches cleanly" {
    # Mirrors CI's "Verify the bundle actually runs" step, against the
    # real installed exe instead of the raw dist\virtaal\ bundle.
    # Confirmed live, 2026-08-24: this specific launch - the very first
    # one right after a fresh install, with nothing warm in any OS/AV
    # file cache yet - can genuinely take longer than Start-VirtaalTest's
    # CI-tuned defaults (8s + up to 10s more) allow for, especially on a
    # slower/emulated local VM rather than a GitHub Actions runner; every
    # later launch in the same run (same exe, same machine) got a window
    # handle immediately. Give this one specifically a more generous
    # budget rather than either widening the shared default (which would
    # slow down every CI check too, where the tighter budget has never
    # actually been a problem) or risking a false Fail here on a
    # slow-but-fine cold start.
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po" -WaitSeconds 20 -HandleTimeoutSeconds 20
    if (-not $t) {
        Add-Result "Launches cleanly" "Fail" "no window handle - see log above"
    } else {
        $logsClean = Assert-VirtaalLogsClean
        Add-Result "Launches cleanly" $(if ($logsClean) { "Pass" } else { "Fail" }) $(if (-not $logsClean) { "unexpected log output - see above" })
    }
}

Invoke-VirtaalCheck "Fresh install: no modified marker on open" {
    # Directly targets "the unsaved marker seems to persist between
    # reinstalls" (reported live, 2026-08-24) - a truly clean
    # uninstall+install cycle followed by opening an untouched file
    # should never show the "*" modified marker in the title. If this
    # fails, that's real evidence something is being retained outside
    # the app itself (e.g. a stray config/state file an uninstall isn't
    # removing) rather than a code-only bug in this session's
    # modified-flag fixes.
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "po\af.po"
    if (-not $t) {
        Add-Result "Fresh install: no modified marker on open" "Fail" "app didn't launch"
    } else {
        $title = Get-VirtaalTitle $t
        $isModified = $title.StartsWith("*")
        Add-Result "Fresh install: no modified marker on open" $(if ($isModified) { "Fail" } else { "Pass" }) "title=`"$title`""
    }
}

Invoke-VirtaalCheck "Navigation doesn't grow window" {
    # Mirrors CI's own check for storetreeview.py's select_index() growth
    # bug (fixed 7fd21615) - kept here too since this is testing the real
    # installed build, not just whatever CI happened to build from.
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
}

Invoke-VirtaalCheck "Type + Ctrl+Z clears modified marker" {
    # Exercises this session's undo/modified-flag fix chain (4a10f77a,
    # 10a51457, dfb2a447, 297f0aaa) end-to-end against the real installed
    # app. Assumes the target text box has default keyboard focus when a
    # file first opens - if that assumption doesn't hold on a given
    # machine/GTK theme, typing "x" won't actually modify anything and
    # the check reports Skip rather than a false Fail, so a focus quirk
    # doesn't masquerade as a regression.
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
}

Invoke-VirtaalCheck "Keyboard shortcuts don't crash" {
    # Mirrors CI's own check.
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
}

Invoke-VirtaalCheck "Ctrl+C doesn't modify, Ctrl+V does" {
    # "Keyboard shortcuts don't crash" above only ever checked that
    # Ctrl+X/C/V don't crash, never that they do the *right* thing to
    # the modified flag - the same signal-correctness theme as this
    # session's real bugs (a mutating action failing to mark modified,
    # or - just as easily, given how many of those bugs were about a
    # signal firing when it *shouldn't* - a non-mutating one wrongly
    # marking modified). Select-all + Copy from a clean state must
    # leave the marker off; pasting straight back over that same
    # selection must turn it on, since Paste is a real edit regardless
    # of whether the resulting text happens to match.
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "po\af.po"
    if (-not $t) {
        Add-Result "Ctrl+C doesn't modify, Ctrl+V does" "Fail" "app didn't launch"
    } else {
        $titleClean = Get-VirtaalTitle $t
        if ($titleClean.StartsWith("*")) {
            Add-Result "Ctrl+C doesn't modify, Ctrl+V does" "Skip" "file already showed modified before any interaction (title=`"$titleClean`")"
        } else {
            Send-VirtaalKeys $t "^a"
            Send-VirtaalKeys $t "^c"
            $titleAfterCopy = Get-VirtaalTitle $t
            if ($titleAfterCopy.StartsWith("*")) {
                Add-Result "Ctrl+C doesn't modify, Ctrl+V does" "Fail" "Ctrl+C alone set the modified marker (title=`"$titleAfterCopy`") - Copy must not mutate"
            } else {
                Send-VirtaalKeys $t "^v"
                $titleAfterPaste = Get-VirtaalTitle $t
                Add-Result "Ctrl+C doesn't modify, Ctrl+V does" $(if ($titleAfterPaste.StartsWith("*")) { "Pass" } else { "Skip" }) "title after paste=`"$titleAfterPaste`"$(if (-not $titleAfterPaste.StartsWith('*')) { ' - Ctrl+V had no observable effect, possibly nothing was selected to copy' })"
            }
        }
    }
}

Invoke-VirtaalCheck "Alt+Enter opens Properties and closes cleanly" {
    # <Virtaal>/File/Properties (propertiesview.py) - same shape as the
    # existing Ctrl+P Preferences check.
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "Alt+Enter opens Properties and closes cleanly" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "%{ENTER}"
        $dlg = Wait-VirtaalPopup $t -TimeoutSeconds 5
        if (-not $dlg) {
            Add-Result "Alt+Enter opens Properties and closes cleanly" "Fail" "no dialog appeared"
        } else {
            $dlgTitle = Get-VirtaalWindowText $dlg
            Close-VirtaalPopup $t $dlg
            $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
            $logsClean = if ($stillAlive) { Assert-VirtaalLogsClean } else { $false }
            Add-Result "Alt+Enter opens Properties and closes cleanly" $(if ($stillAlive -and $logsClean) { "Pass" } else { "Fail" }) "dialog title=`"$dlgTitle`"$(if (-not $stillAlive) { ' - process exited after closing it' } elseif (-not $logsClean) { ' - unexpected log output' })"
        }
    }
}

Invoke-VirtaalCheck "F11 fullscreen restores the original window size" {
    # mnu_fullscreen / F11 - _on_fullscreen() (mainview.py) is a plain
    # passthrough to GTK's own main_window.fullscreen()/unfullscreen(),
    # no custom geometry save/restore logic in Virtaal's own code at
    # all - so if the size isn't restored correctly, that's GTK/GDK's
    # Windows backend, not something to fix here directly (though
    # Virtaal could still work around it by saving/restoring the size
    # itself, the way this same session's window-growth saga eventually
    # worked around a different GTK/Windows sizing quirk). Reported
    # live, 2026-08-24: "F11 returns to a different size" - this used to
    # only check for a crash; now measures the actual width/height
    # before fullscreen and after returning from it, so this is a real,
    # repeatable regression check instead of relying on catching it live
    # each time. A few pixels of slop is allowed (window-manager/DPI
    # rounding is a real, benign source of small differences) - the
    # threshold is deliberately the same 50px this battery already uses
    # for the navigation-growth check, not a tight pixel-exact match.
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "F11 fullscreen restores the original window size" "Fail" "app didn't launch"
    } else {
        $widthBefore = Get-VirtaalWidth $t
        $heightBefore = Get-VirtaalHeight $t
        Send-VirtaalKeys $t "{F11}" -SettleMs 500
        Send-VirtaalKeys $t "{F11}" -SettleMs 500
        $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
        if (-not $stillAlive) {
            Add-Result "F11 fullscreen restores the original window size" "Fail" "process exited"
        } else {
            $logsClean = Assert-VirtaalLogsClean
            $widthAfter = Get-VirtaalWidth $t
            $heightAfter = Get-VirtaalHeight $t
            $widthDelta = [Math]::Abs($widthAfter - $widthBefore)
            $heightDelta = [Math]::Abs($heightAfter - $heightBefore)
            $sizeRestored = $widthDelta -le 50 -and $heightDelta -le 50
            if (-not $logsClean) {
                Add-Result "F11 fullscreen restores the original window size" "Fail" "unexpected log output - see above"
            } elseif (-not $sizeRestored) {
                $shot = Save-VirtaalScreenshot $t
                Add-Result "F11 fullscreen restores the original window size" "Fail" "before=${widthBefore}x${heightBefore}, after=${widthAfter}x${heightAfter} - screenshot: $shot"
            } else {
                Add-Result "F11 fullscreen restores the original window size" "Pass" "before=${widthBefore}x${heightBefore}, after=${widthAfter}x${heightAfter}"
            }
        }
    }
}

Invoke-VirtaalCheck "Multi-step undo clears modified marker" {
    # "Type + Ctrl+Z clears modified marker" above only ever tested a
    # single edit/undo pair. The actual fix this session made
    # (UndoModel's clean_index/is_at_clean_position(), 4a10f77a) is
    # about the undo *stack position* matching where it was at open -
    # a single-step test can't distinguish "compares against a fixed
    # start" from "correctly walks back an arbitrary number of steps",
    # so this exercises two edits and two undos.
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "po\af.po"
    if (-not $t) {
        Add-Result "Multi-step undo clears modified marker" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "x"
        Send-VirtaalKeys $t "y"
        $titleAfterTwoEdits = Get-VirtaalTitle $t
        if (-not $titleAfterTwoEdits.StartsWith("*")) {
            Add-Result "Multi-step undo clears modified marker" "Skip" "typing didn't set the modified marker (title=`"$titleAfterTwoEdits`") - target field may not have had default focus"
        } else {
            Send-VirtaalKeys $t "^z"
            $titleAfterOneUndo = Get-VirtaalTitle $t
            Send-VirtaalKeys $t "^z"
            $titleAfterTwoUndos = Get-VirtaalTitle $t
            $stillModified = $titleAfterTwoUndos.StartsWith("*")
            Add-Result "Multi-step undo clears modified marker" $(if ($stillModified) { "Fail" } else { "Pass" }) "after 1 undo=`"$titleAfterOneUndo`", after 2 undos=`"$titleAfterTwoUndos`""
        }
    }
}

Invoke-VirtaalCheck "Welcome screen launches cleanly" {
    # Every other check above passes a file on the command line, which
    # Virtaal opens synchronously before the event loop even starts
    # (virtaal/main.py's _open_with_file) - plugin loading (checks, undo,
    # TM, ...) is deferred to run afterwards via GObject.idle_add. With
    # no file argument at all, main.py instead takes the
    # _open_with_welcome path, where *everything* (UnitController,
    # ModeController, ..., and the same deferred plugin loading) is
    # queued via the same idle_add mechanism - a genuinely different
    # startup code path none of the file-argument checks above ever
    # touch.
    $t = Start-VirtaalTest -ExePath $install.ExePath
    if (-not $t) {
        Add-Result "Welcome screen launches cleanly" "Fail" "app didn't launch"
    } else {
        $logsClean = Assert-VirtaalLogsClean
        Add-Result "Welcome screen launches cleanly" $(if ($logsClean) { "Pass" } else { "Fail" }) $(if (-not $logsClean) { "unexpected log output - see above" })
    }
}

Invoke-VirtaalCheck "Menu navigation" {
    # Opens and closes each top-level menu via its Alt+mnemonic (see
    # share\virtaal\virtaal.ui's "_File"/"_Edit"/"_View"/"_Navigation"/
    # "_Help" labels) - a cheap way to exercise menu construction/
    # rendering for every menu, not just the handful of items the
    # shortcut checks activate directly.
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
}

Invoke-VirtaalCheck "Welcome screen: open dialog + Recent Files" {
    # Covers two asks at once (they're the same underlying flow):
    # opening from the welcome screen via a real file-open dialog (not a
    # CLI argument), and reopening via File > Recent Files afterwards.
    # Also the closest thing in this battery to the exact sequence
    # behind this session's reopen-modified-flag bug family (change,
    # close, reopen a *different* file) - this variant reopens the
    # *same* file, so it won't catch that specific bug, but exercises
    # the same dialog/menu machinery.
    #
    # The file-open dialog is a separate top-level window -
    # Send-VirtaalKeys can't be used once it's open (it always forces
    # the *main* window foreground first, which would steal focus back
    # off the dialog); Send-VirtaalPopupKeys targets the dialog's own
    # HWND instead. Typing a filename with no field explicitly focused
    # relies on GTK's built-in interactive/type-ahead search in the file
    # list, the same mechanism the run-virtaal skill's macOS driver
    # relies on for its native NSOpenPanel.
    #
    # The "r" mnemonic jump for Recent Files (rather than counting
    # Down-arrow presses to it) is deliberately order-independent -
    # it'll keep working even if virtaal.ui's File menu items get
    # reordered, as long as "_Recent Files" keeps that mnemonic. Assumes
    # the Recent Files submenu auto-highlights its most-recent (topmost)
    # entry when opened, which {ENTER} then activates.
    $t = Start-VirtaalTest -ExePath $install.ExePath
    if (-not $t) {
        Add-Result "Welcome screen: open dialog + Recent Files" "Fail" "app didn't launch"
    } else {
        $dlg = Open-VirtaalFileViaDialog $t "af.po"
        if (-not $dlg) {
            Add-Result "Welcome screen: open dialog + Recent Files" "Fail" "Ctrl+O never opened a file dialog"
        } else {
            $titleAfterOpen = Get-VirtaalTitle $t
            if ($titleAfterOpen -notmatch "af\.po") {
                $shot = Save-VirtaalScreenshot $t
                Add-Result "Welcome screen: open dialog + Recent Files" "Fail" "title after Open dialog=`"$titleAfterOpen`" - expected it to mention af.po - screenshot: $shot"
            } else {
                Send-VirtaalKeys $t "^w"
                Start-Sleep -Milliseconds 500
                Send-VirtaalKeys $t "%f"
                Send-VirtaalKeys $t "r"
                Send-VirtaalKeys $t "{RIGHT}"
                Send-VirtaalKeys $t "{ENTER}"
                Start-Sleep -Milliseconds 1000
                $titleAfterRecent = Get-VirtaalTitle $t
                if ($titleAfterRecent -match "af\.po") {
                    Add-Result "Welcome screen: open dialog + Recent Files" "Pass" "title after Recent Files=`"$titleAfterRecent`""
                } else {
                    $shot = Save-VirtaalScreenshot $t
                    Add-Result "Welcome screen: open dialog + Recent Files" "Fail" "title after Recent Files=`"$titleAfterRecent`" - screenshot: $shot"
                }
            }
        }
    }
}

Invoke-VirtaalCheck "Ctrl+P opens Preferences" {
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
}

Invoke-VirtaalCheck "Ctrl+F search/filter" {
    # SearchMode ("Includes only units matching the given search string"
    # - modes\searchmode.py) is Virtaal's filter-by-string feature -
    # embedded in the main window (a mode swapped in by ModeController),
    # not a separate dialog, so no Wait-VirtaalPopup needed here.
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
}

Invoke-VirtaalCheck "F8 quality checks panel" {
    # Toggles the panel on and off against devsupport\testfiles\
    # checks.po, a file specifically built to exercise translate-
    # toolkit's checks - if any single check throws on real data, that's
    # exactly the kind of thing that lands as a traceback in the app's
    # own log, which Assert-VirtaalLogsClean would catch (this doesn't
    # read the checks panel's actual *contents*, no UI Automation tree
    # available here to do that - just that showing/hiding it and
    # running checks against a file designed to trigger several of them
    # doesn't crash or log an error).
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
}

Invoke-VirtaalCheck "Placeable navigation/transfer shortcuts" {
    # <Virtaal>/Edit/Prev Placeable, Next Placeable (Alt+Left/Right -
    # jump between placeables in the target) and Transfer (Alt+Down -
    # copies source to an empty target) - see unitview.py's
    # _setup_key_bindings. Only Alt+Down has an observable effect worth
    # checking for (copying into an *empty* target sets the modified
    # marker); like "Type + Ctrl+Z" above, this is Skipped rather than
    # Failed if the current unit's target already had text (transfer
    # then does nothing) rather than assuming every test file's first
    # unit is untranslated.
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
}

Invoke-VirtaalCheck "Click navigation" {
    # No UI Automation tree is available here to ask "where is row 2",
    # so this clicks at a guessed position (fractions of the window's
    # own rect - roughly where the unit-list strip sits, per Virtaal's
    # general layout: menu/toolbar at top, a compact list of units below
    # that, the source/target editor filling the rest) and checks for a
    # plausible *effect* (typing afterwards sets the modified marker,
    # meaning the click landed in an editable target field) rather than
    # verifying the click precisely. Screenshots are saved either way,
    # specifically so a human (or a future Claude session with this same
    # repo shared back) can look at where the click actually landed and
    # retune XFraction/YFraction if this keeps Skipping.
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
}

Invoke-VirtaalCheck "Click: check-type (Project Type) selector" {
    # ChecksProjectView's "Checks: <name>" PopupMenuButton (e.g. GNOME/
    # Mozilla/OpenOffice style) - checksprojview.py packs it into the
    # status bar via pack_start, so bottom-*left*, not bottom-right
    # (that's the language-pair selector below - two different widgets,
    # only one of them actually on the right). Both are PopupMenuButtons
    # that open a Gtk.Menu on click rather than a real top-level dialog,
    # so Wait-VirtaalPopup's GetForegroundWindow() approach isn't used
    # here (a GTK popup menu doesn't reliably take Win32 foreground focus
    # the way a real Gtk.Dialog does) - Escape closes it either way
    # (harmless even if nothing was open), and this only confirms no
    # crash/log error, the same honest bar as "Click navigation" above.
    # The status bar is a fixed-height strip flush against the bottom
    # edge regardless of window size, so a Y-fraction close to 1.0 should
    # be a *more* reliable guess than the unit-list click's Y-fraction
    # (which scales with total window height) - still just a guess
    # without a UI Automation tree, hence the saved screenshots.
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "Click: check-type (Project Type) selector" "Fail" "app didn't launch"
    } else {
        $shotBefore = Save-VirtaalScreenshot $t
        Send-VirtaalClick $t -XFraction 0.12 -YFraction 0.98
        $shotAfter = Save-VirtaalScreenshot $t
        Send-VirtaalKeys $t "{ESC}"
        $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
        $logsClean = if ($stillAlive) { Assert-VirtaalLogsClean } else { $false }
        Add-Result "Click: check-type (Project Type) selector" $(if ($stillAlive -and $logsClean) { "Pass" } else { "Fail" }) "$(if (-not $stillAlive) { 'process exited' } elseif (-not $logsClean) { 'unexpected log output - see above' } else { 'no crash - see screenshots to confirm the menu actually opened' }) - screenshots: $shotBefore, $shotAfter"
    }
}

Invoke-VirtaalCheck "Click: language-pair selector" {
    # LanguageView's "<source> -> <target>" PopupMenuButton -
    # langview.py packs it into the status bar via pack_end, so this one
    # genuinely is bottom-right. Same best-effort approach and caveats as
    # the check-type selector check above.
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "devsupport\testfiles\checks.po"
    if (-not $t) {
        Add-Result "Click: language-pair selector" "Fail" "app didn't launch"
    } else {
        $shotBefore = Save-VirtaalScreenshot $t
        Send-VirtaalClick $t -XFraction 0.9 -YFraction 0.98
        $shotAfter = Save-VirtaalScreenshot $t
        Send-VirtaalKeys $t "{ESC}"
        $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
        $logsClean = if ($stillAlive) { Assert-VirtaalLogsClean } else { $false }
        Add-Result "Click: language-pair selector" $(if ($stillAlive -and $logsClean) { "Pass" } else { "Fail" }) "$(if (-not $stillAlive) { 'process exited' } elseif (-not $logsClean) { 'unexpected log output - see above' } else { 'no crash - see screenshots to confirm the menu actually opened' }) - screenshots: $shotBefore, $shotAfter"
    }
}

Invoke-VirtaalCheck "Ctrl+S actually saves and clears modified marker" {
    # Never tested before this - every earlier check either undoes a
    # change before closing or never leaves the file dirty at all. Uses
    # a scratch copy (see New-VirtaalScratchFile) and checks the file's
    # own LastWriteTime actually changed on disk, not just that the UI
    # marker cleared - directly relevant given this whole session was
    # about the modified marker not always reflecting reality.
    $scratch = New-VirtaalScratchFile -SourceRelativePath "po\af.po" -Suffix "save-test"
    $mtimeBefore = (Get-Item $scratch).LastWriteTimeUtc
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "`"$scratch`""
    if (-not $t) {
        Add-Result "Ctrl+S actually saves and clears modified marker" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "x"
        $titleAfterType = Get-VirtaalTitle $t
        if (-not $titleAfterType.StartsWith("*")) {
            Add-Result "Ctrl+S actually saves and clears modified marker" "Skip" "typing didn't set the modified marker (title=`"$titleAfterType`") - target field may not have had default focus"
        } else {
            Send-VirtaalKeys $t "^s" -SettleMs 800
            # Confirmed live, 2026-08-24: this failed once with the
            # marker still set *and* the file unwritten - i.e. Ctrl+S
            # appeared to do nothing at all. A blocking dialog this
            # check wasn't checking for (e.g. storemodel.py's
            # translator-details prompt, or a real error) sitting
            # unhandled in front of the main window would produce
            # exactly that combination - check for one before assuming
            # the save simply didn't happen, and if the failure recurs
            # without a popup either, at least leave a screenshot to
            # look at instead of just two data points and no context.
            $blockingDlg = Wait-VirtaalPopup $t -TimeoutSeconds 2
            if ($blockingDlg) {
                $blockingDlgTitle = Get-VirtaalWindowText $blockingDlg
                $shot = Save-VirtaalScreenshot $t
                Close-VirtaalPopup $t $blockingDlg
                Add-Result "Ctrl+S actually saves and clears modified marker" "Fail" "an unexpected dialog (title=`"$blockingDlgTitle`") blocked the save - screenshot: $shot"
            } else {
                $titleAfterSave = Get-VirtaalTitle $t
                $mtimeAfter = (Get-Item $scratch).LastWriteTimeUtc
                $markerCleared = -not $titleAfterSave.StartsWith("*")
                $fileWritten = $mtimeAfter -gt $mtimeBefore
                if ($markerCleared -and $fileWritten) {
                    Add-Result "Ctrl+S actually saves and clears modified marker" "Pass" "title after save=`"$titleAfterSave`", file written=$fileWritten"
                } else {
                    $shot = Save-VirtaalScreenshot $t
                    Add-Result "Ctrl+S actually saves and clears modified marker" "Fail" "title after save=`"$titleAfterSave`", file written=$fileWritten - screenshot: $shot"
                }
            }
        }
    }
}

Invoke-VirtaalCheck "Real unsaved-changes dialog appears and Discard works" {
    # Never tested before this either - every earlier check that closes
    # a file either isn't actually dirty, or already undid its change
    # first. mainview.py's confirm_dialog has three buttons (Save/
    # _Discard/Cancel, Save is the *default* response) - explicitly
    # activating Discard via its own Alt+D mnemonic rather than
    # Enter/the default button, since accidentally hitting Save here
    # would write real content (mitigated anyway by using a scratch
    # copy, but there's no reason to rely on that as the only
    # safeguard). Confirms the scratch file's mtime is untouched
    # afterward - proof Discard genuinely discarded rather than saving.
    $scratch = New-VirtaalScratchFile -SourceRelativePath "po\af.po" -Suffix "discard-test"
    $mtimeBefore = (Get-Item $scratch).LastWriteTimeUtc
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "`"$scratch`""
    if (-not $t) {
        Add-Result "Real unsaved-changes dialog appears and Discard works" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "x"
        $titleAfterType = Get-VirtaalTitle $t
        if (-not $titleAfterType.StartsWith("*")) {
            Add-Result "Real unsaved-changes dialog appears and Discard works" "Skip" "typing didn't set the modified marker (title=`"$titleAfterType`") - target field may not have had default focus"
        } else {
            Send-VirtaalKeys $t "^w"
            $dlg = Wait-VirtaalPopup $t -TimeoutSeconds 5
            if (-not $dlg) {
                Add-Result "Real unsaved-changes dialog appears and Discard works" "Fail" "no confirm dialog appeared despite a real unsaved change"
            } else {
                Send-VirtaalPopupKeys $dlg "%d"
                Start-Sleep -Milliseconds 500
                $stillAlive = Get-Process -Id $t.Process.Id -ErrorAction SilentlyContinue
                $mtimeAfter = if (Test-Path $scratch) { (Get-Item $scratch).LastWriteTimeUtc } else { $null }
                $fileUntouched = $mtimeAfter -eq $mtimeBefore
                Add-Result "Real unsaved-changes dialog appears and Discard works" $(if ($stillAlive -and $fileUntouched) { "Pass" } else { "Fail" }) "$(if (-not $stillAlive) { 'process exited after Discard' } elseif (-not $fileUntouched) { 'file was modified on disk - Discard may have actually saved' } else { 'file untouched, process alive' })"
            }
        }
    }
}

Invoke-VirtaalCheck "Change file A, discard, open different file B: no spurious modified" {
    # The actual shape of this session's headline bug (reported live:
    # change file A, close/discard, open a *different* file B - B showed
    # modified with nothing changed there yet - fixed across 10a51457,
    # dfb2a447, 297f0aaa). "Welcome screen: open dialog + Recent Files"
    # above reopens the *same* file, which doesn't exercise this at all
    # - this check is the real regression test for that bug family.
    # Both scratch copies live in the same directory, so the file-open
    # dialog (which defaults to the last-used directory - wherever A
    # was) can type-ahead-find B's filename directly.
    $scratchA = New-VirtaalScratchFile -SourceRelativePath "po\af.po" -Suffix "reopen-A"
    $scratchB = New-VirtaalScratchFile -SourceRelativePath "po\ar.po" -Suffix "reopen-B"
    $scratchBName = Split-Path -Leaf $scratchB
    $t = Start-VirtaalTest -ExePath $install.ExePath -Arguments "`"$scratchA`""
    if (-not $t) {
        Add-Result "Change file A, discard, open different file B: no spurious modified" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "x"
        $titleAfterType = Get-VirtaalTitle $t
        if (-not $titleAfterType.StartsWith("*")) {
            Add-Result "Change file A, discard, open different file B: no spurious modified" "Skip" "typing didn't set the modified marker on A (title=`"$titleAfterType`") - target field may not have had default focus"
        } else {
            Send-VirtaalKeys $t "^w"
            $dlg = Wait-VirtaalPopup $t -TimeoutSeconds 5
            if (-not $dlg) {
                Add-Result "Change file A, discard, open different file B: no spurious modified" "Fail" "no confirm dialog appeared for A despite a real unsaved change"
            } else {
                Send-VirtaalPopupKeys $dlg "%d"
                Start-Sleep -Milliseconds 500
                $openDlg = Open-VirtaalFileViaDialog $t $scratchBName
                if (-not $openDlg) {
                    Add-Result "Change file A, discard, open different file B: no spurious modified" "Fail" "Ctrl+O never opened a file dialog after discarding A"
                } else {
                    $titleAfterOpenB = Get-VirtaalTitle $t
                    $bOpened = $titleAfterOpenB -match [regex]::Escape($scratchBName)
                    $bModified = $titleAfterOpenB.StartsWith("*")
                    if ($bOpened -and -not $bModified) {
                        Add-Result "Change file A, discard, open different file B: no spurious modified" "Pass" "title after opening B=`"$titleAfterOpenB`""
                    } else {
                        $shot = Save-VirtaalScreenshot $t
                        Add-Result "Change file A, discard, open different file B: no spurious modified" "Fail" "title after opening B=`"$titleAfterOpenB`" - screenshot: $shot"
                    }
                }
            }
        }
    }
}

Invoke-VirtaalCheck "Layout glitch diagnostic screenshot (File>Open path)" {
    # Not a pass/fail check - ISSUE_TRIAGE.md's "widgets overlapping near
    # the top of the window on first opening a file" bug is still open
    # (two attempted fixes both caused real CI hangs and were reverted -
    # see storeview.py's show()) and has never reproduced locally on
    # macOS despite direct attempts. There's no way to detect "widgets
    # are overlapping" from window geometry/title alone - this just
    # takes a screenshot at the exact moment the reported repro
    # describes (fresh launch, Ctrl+O, type-ahead select, Enter) so a
    # human - or a future Claude session with this same repo shared back
    # - has something to actually look at on every run instead of only
    # when someone happens to notice it live.
    $t = Start-VirtaalTest -ExePath $install.ExePath
    if (-not $t) {
        Add-Result "Layout glitch diagnostic screenshot (File>Open path)" "Fail" "app didn't launch"
    } else {
        Send-VirtaalKeys $t "^o"
        $dlg = Wait-VirtaalPopup $t -TimeoutSeconds 8
        if (-not $dlg) {
            Add-Result "Layout glitch diagnostic screenshot (File>Open path)" "Fail" "Ctrl+O never opened a file dialog"
        } else {
            # Deliberately not using Open-VirtaalFileViaDialog here - its
            # settle delay after Enter (1s) is tuned for reliable
            # type-ahead, but this check specifically wants a screenshot
            # as soon as possible after opening, before the glitch (if
            # present) potentially clears on its own.
            Start-Sleep -Milliseconds 500
            Send-VirtaalPopupKeys $dlg "af.po"
            Start-Sleep -Milliseconds 500
            Send-VirtaalPopupKeys $dlg "{ENTER}"
            Start-Sleep -Milliseconds 300
            $shot = Save-VirtaalScreenshot $t
            Add-Result "Layout glitch diagnostic screenshot (File>Open path)" "Skip" "diagnostic only, not auto-verified - inspect $shot for ISSUE_TRIAGE.md's open layout glitch"
        }
    }
}

Write-Host "`n=== 4. Tear down ==="
if (Test-Path $script:scratchDir) {
    Remove-Item -Path $script:scratchDir -Recurse -Force -ErrorAction SilentlyContinue
}
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
