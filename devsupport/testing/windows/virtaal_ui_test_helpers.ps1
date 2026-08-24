# Reusable PowerShell helpers for driving the packaged Windows
# virtaal.exe from CI (or a local Windows/VM session): launch, get a
# real window handle via Win32, read its geometry/title, send
# keystrokes via SendKeys, detect and drive popup dialogs (file choosers,
# Preferences, ...), simulate a mouse click, save a screenshot, read the
# frozen-build log files, and clean up.
#
# Built 2026-08-24 by generalizing the ad-hoc script written directly
# into ci.yml's "Verify repeated navigation doesn't grow the window"
# step (which found and confirmed the fix for a real bug: the window
# growing wider on every Enter-to-advance during translation, see
# storetreeview.py's select_index()). Pulled out into its own file so
# future Windows-side regression checks - menu navigation, keyboard
# shortcuts, dialogs, anything else that needs to actually drive the
# running app rather than just confirm it launched - don't need to
# reinvent Win32 P/Invoke boilerplate each time. Mirrors the same
# motivation as the run-virtaal skill's driver.sh on macOS, for the
# platform where this whole family of resize/layout bugs has actually
# lived so far - CI is a faster, more reliable reproduction environment
# than a human in a VM once a check like this exists (confirmed
# directly: the select_index() bug above went from "reported live,
# no way to verify" to "reproduced and fixed in one CI round-trip"
# once this kind of check existed).
#
# Usage: dot-source this file, then call the functions below.
#   . .\devsupport\testing\windows\virtaal_ui_test_helpers.ps1
#   $t = Start-VirtaalTest -ExePath .\dist\virtaal\virtaal.exe -Arguments "po\af.po"
#   if (-not $t) { exit 1 }  # Start-VirtaalTest already logged why
#   $widthBefore = Get-VirtaalWidth $t
#   Send-VirtaalKeys $t "{ENTER}"
#   $widthAfter = Get-VirtaalWidth $t
#   Stop-VirtaalTest $t

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# A PowerShell here-string's closing "@ must start at column 1, which is
# incompatible with a YAML block scalar's indentation requirement when
# this gets inlined into a workflow step - keeping the type definition
# as one plain string (not a here-string) here too, even though this
# file itself isn't YAML, so a future copy-paste into a workflow step
# doesn't reintroduce that exact bug.
#
# GetForegroundWindow (added 2026-08-24): the simplest reliable way to
# detect a newly-opened dialog (e.g. Preferences via Ctrl+P) without the
# considerably more involved EnumWindows-plus-delegate-marshaling
# dance - a GTK dialog on Windows takes the foreground when it opens, so
# comparing GetForegroundWindow() against the main Instance.Hwnd after
# sending the key that should open one is enough to detect it and read
# its title.
#
# SetCursorPos/mouse_event (added 2026-08-24): for click-based
# navigation checks. mouse_event is the older, simpler Win32 mouse
# simulation API (vs. the newer SendInput) - sufficient here since
# nothing needs multi-touch or precise timing, just a plain left click.
# Add-Type-compiled types live for the lifetime of the PowerShell
# *session*, not this script, and a .NET type - once compiled via
# Add-Type in a process - can never be redefined in that same process
# (a hard CLR limitation, not something any guard here can work around).
# Re-dot-sourcing this file a second time in the same window (e.g.
# running Invoke-VirtaalLocalTestPass.ps1 twice without closing the
# terminal) throws "Cannot add type. The type name 'VirtaalWin32'
# already exists." without a guard - but a plain existence guard
# (tried first, 2026-08-24) creates a *worse*, silent failure mode:
# confirmed live the same day, adding SetCursorPos/mouse_event/
# GetForegroundWindow to this class in a later commit meant every
# session that had already loaded the *older* VirtaalWin32 kept right on
# "succeeding" at the guard (the type still exists!) while silently
# missing the new methods - producing five separate, cryptic
# "does not contain a method named '...'" failures scattered across
# unrelated checks instead of one clear error. Check for the specific
# members this version needs instead of mere existence, so a stale
# session fails fast with one clear, actionable message instead. This
# still can't *fix* a stale session - that's the unavoidable part, no
# guard can redefine an already-compiled .NET type - it can only make
# the failure obvious instead of confusing.
$existingVirtaalWin32 = ([System.Management.Automation.PSTypeName]'VirtaalWin32').Type
if ($existingVirtaalWin32) {
    $requiredMethods = @('GetWindowRect', 'SetForegroundWindow', 'GetForegroundWindow', 'GetWindowTextLength', 'GetWindowText', 'SetCursorPos', 'mouse_event')
    $existingMethodNames = @($existingVirtaalWin32.GetMethods() | ForEach-Object { $_.Name })
    $missingMethods = @($requiredMethods | Where-Object { $existingMethodNames -notcontains $_ })
    if ($missingMethods) {
        throw "This PowerShell session already has an older VirtaalWin32 type loaded (missing: $($missingMethods -join ', ')) from before virtaal_ui_test_helpers.ps1 was last updated - a .NET type can't be redefined in the same process once compiled, so this session can't recover on its own. Close this PowerShell window, open a new one, and re-run."
    }
} else {
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; using System.Text; public class VirtaalWin32 { [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd); [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd); [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount); [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y); [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo); public struct RECT { public int Left; public int Top; public int Right; public int Bottom; } }'
}

function Start-VirtaalTest {
    <#
    .SYNOPSIS
    Launches the packaged virtaal.exe and waits for a real window handle.
    Returns $null (and writes a ::error:: line) if no window ever appears
    - callers should check for that rather than assume success.
    #>
    param(
        [string]$ExePath = ".\dist\virtaal\virtaal.exe",
        [string]$Arguments = "",
        [int]$WaitSeconds = 8,
        [int]$HandleTimeoutSeconds = 10
    )

    # Windows PowerShell 5.1's Start-Process rejects -ArgumentList "" -
    # "Cannot validate argument on parameter 'ArgumentList'. The argument
    # is null or empty" - a *terminating* error, confirmed live
    # 2026-08-24 (the welcome-screen checks, the first callers here that
    # ever omitted -Arguments, crashed the whole battery: every check
    # before them always passed a real file path, so this never got
    # exercised until now). Only pass -ArgumentList at all when there's
    # something non-empty to pass.
    $startProcessArgs = @{ FilePath = $ExePath; PassThru = $true }
    if ($Arguments) { $startProcessArgs['ArgumentList'] = $Arguments }
    $proc = Start-Process @startProcessArgs
    Start-Sleep -Seconds $WaitSeconds

    $stillRunning = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    if (-not $stillRunning) {
        Write-Host "::error::$ExePath exited within ${WaitSeconds}s of launch"
        Write-VirtaalLogs
        return $null
    }

    $hwnd = [IntPtr]::Zero
    $deadline = (Get-Date).AddSeconds($HandleTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $proc.Refresh()
        if ($proc.MainWindowHandle -ne [IntPtr]::Zero) {
            $hwnd = $proc.MainWindowHandle
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if ($hwnd -eq [IntPtr]::Zero) {
        Write-Host "::error::Never got a main window handle for $ExePath"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        return $null
    }

    return [PSCustomObject]@{ Process = $proc; Hwnd = $hwnd }
}

function Get-VirtaalRect {
    param([Parameter(Mandatory)]$Instance)
    $rect = New-Object VirtaalWin32+RECT
    [VirtaalWin32]::GetWindowRect($Instance.Hwnd, [ref]$rect) | Out-Null
    return $rect
}

function Get-VirtaalWidth {
    param([Parameter(Mandatory)]$Instance)
    $rect = Get-VirtaalRect $Instance
    return $rect.Right - $rect.Left
}

function Get-VirtaalHeight {
    param([Parameter(Mandatory)]$Instance)
    $rect = Get-VirtaalRect $Instance
    return $rect.Bottom - $rect.Top
}

function Get-VirtaalWindowText {
    <#
    .SYNOPSIS
    Low-level: reads any window's title text via Win32 GetWindowText,
    given a raw HWND (not a Start-VirtaalTest instance) - the primitive
    Get-VirtaalTitle and the popup-dialog checks (Ctrl+P Preferences,
    etc.) both build on.
    #>
    param([Parameter(Mandatory)][IntPtr]$Hwnd)
    $len = [VirtaalWin32]::GetWindowTextLength($Hwnd)
    if ($len -eq 0) { return "" }
    $sb = New-Object System.Text.StringBuilder ($len + 1)
    [VirtaalWin32]::GetWindowText($Hwnd, $sb, $sb.Capacity) | Out-Null
    return $sb.ToString()
}

function Get-VirtaalTitle {
    <#
    .SYNOPSIS
    Reads the instance's window title via Win32 GetWindowText - not the
    same as $Instance.Process.MainWindowTitle, which is a one-time
    snapshot .NET took right after the process's main window first
    appeared and does *not* update as the title changes afterwards (e.g.
    the "*" modified-marker mainview.py's set_saveable() prepends - see
    ISSUE_TRIAGE.md's reopen-modified-flag bug family). Needed for any
    check that has to observe the modified marker rather than just
    whether the app is alive.
    #>
    param([Parameter(Mandatory)]$Instance)
    return Get-VirtaalWindowText $Instance.Hwnd
}

function Wait-VirtaalPopup {
    <#
    .SYNOPSIS
    Waits for a *different* top-level window than the instance's main
    one to take the foreground (e.g. after Ctrl+P for Preferences, or
    any other action that opens a dialog) - GTK dialogs on Windows take
    the foreground when they open, so this is simpler and more reliable
    than enumerating all of a process's top-level windows. Returns the
    popup's HWND, or $null if nothing new appeared within the timeout
    (the caller's action didn't open a dialog, or it failed to).
    #>
    param([Parameter(Mandatory)]$Instance, [int]$TimeoutSeconds = 5)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $fg = [VirtaalWin32]::GetForegroundWindow()
        if ($fg -ne [IntPtr]::Zero -and $fg -ne $Instance.Hwnd) {
            return $fg
        }
        Start-Sleep -Milliseconds 200
    }
    return $null
}

function Send-VirtaalPopupKeys {
    <#
    .SYNOPSIS
    Like Send-VirtaalKeys, but activates an arbitrary HWND (typically one
    from Wait-VirtaalPopup - a file-open dialog, Preferences, ...)
    instead of always the instance's main window. Send-VirtaalKeys itself
    can't be reused for this: it unconditionally forces the *main*
    window to the foreground first, which would steal focus back off a
    dialog that's supposed to be receiving these keys instead.
    #>
    param([Parameter(Mandatory)][IntPtr]$Hwnd, [Parameter(Mandatory)][string]$Keys, [int]$SettleMs = 300)
    [VirtaalWin32]::SetForegroundWindow($Hwnd) | Out-Null
    Start-Sleep -Milliseconds 300
    [System.Windows.Forms.SendKeys]::SendWait($Keys)
    Start-Sleep -Milliseconds $SettleMs
}

function Close-VirtaalPopup {
    <#
    .SYNOPSIS
    Closes a popup/dialog window found via Wait-VirtaalPopup by
    activating it and sending Escape, then gives focus back to the
    instance's main window so subsequent Send-VirtaalKeys calls land in
    the right place again.
    #>
    param([Parameter(Mandatory)]$Instance, [Parameter(Mandatory)][IntPtr]$PopupHwnd)
    Send-VirtaalPopupKeys $PopupHwnd "{ESC}"
    [VirtaalWin32]::SetForegroundWindow($Instance.Hwnd) | Out-Null
    Start-Sleep -Milliseconds 200
}

function Send-VirtaalClick {
    <#
    .SYNOPSIS
    Simulates a real left mouse click at a position within the
    instance's window, given as fractions (0.0-1.0) of its current
    width/height rather than absolute pixels, so it at least scales with
    the window's own size instead of assuming a fixed one. This is
    inherently best-effort: there's no UI Automation tree here to ask
    "where is the treeview's second row", so a click check's coordinates
    are a guess based on Virtaal's general layout (menu/toolbar at top,
    the unit list as a strip below that, the source/target editor
    filling most of the rest) - use Save-VirtaalScreenshot right after a
    click to confirm/tune where it actually landed if a click-based
    check isn't behaving as expected.
    #>
    param([Parameter(Mandatory)]$Instance, [Parameter(Mandatory)][double]$XFraction, [Parameter(Mandatory)][double]$YFraction, [int]$SettleMs = 300)
    [VirtaalWin32]::SetForegroundWindow($Instance.Hwnd) | Out-Null
    Start-Sleep -Milliseconds 300
    $rect = Get-VirtaalRect $Instance
    $x = [int]($rect.Left + ($rect.Right - $rect.Left) * $XFraction)
    $y = [int]($rect.Top + ($rect.Bottom - $rect.Top) * $YFraction)
    [VirtaalWin32]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 50
    [VirtaalWin32]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero) # MOUSEEVENTF_LEFTDOWN
    Start-Sleep -Milliseconds 50
    [VirtaalWin32]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero) # MOUSEEVENTF_LEFTUP
    Start-Sleep -Milliseconds $SettleMs
}

function Save-VirtaalScreenshot {
    <#
    .SYNOPSIS
    Captures the instance's window to a PNG - the same role as the
    run-virtaal skill's driver.sh screenshot on macOS: something to
    actually look at when a check's result needs visual confirmation
    (most usefully, tuning Send-VirtaalClick's coordinates). Defaults to
    landing under devsupport\testing\windows\.local-test-runs\ - the
    same gitignored, host-readable location Invoke-VirtaalLocalTestPass.
    ps1's transcript uses when this repo checkout is itself a shared
    folder.
    #>
    param([Parameter(Mandatory)]$Instance, [string]$Path)
    if (-not $Path) {
        # $PSScriptRoot, not $MyInvocation.MyCommand.Path - the latter is
        # empty inside a function (confirmed locally: only reliable at a
        # script's own top level, not from a function it defines, even
        # though the function itself is still defined by, and dot-sourced
        # from, this same .ps1 file).
        $dir = Join-Path $PSScriptRoot ".local-test-runs"
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        # Millisecond precision, not just yyyyMMdd-HHmmss - a check that
        # takes a before/after pair (or two checks running back to back)
        # can easily land in the same second otherwise, and two Saves
        # racing for the same filename is a plausible contributor to the
        # GDI+ error below.
        $Path = Join-Path $dir "screenshot-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').png"
    }
    $rect = Get-VirtaalRect $Instance
    $width = $rect.Right - $rect.Left
    $height = $rect.Bottom - $rect.Top
    if ($width -le 0 -or $height -le 0) { return $null }
    $bmp = New-Object System.Drawing.Bitmap $width, $height
    try {
        $graphics = [System.Drawing.Graphics]::FromImage($bmp)
        try {
            $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bmp.Size)
        } finally {
            $graphics.Dispose()
        }
        # Bitmap.Save throwing a bare "A generic error occurred in GDI+"
        # is a known-flaky .NET pattern, seen live 2026-08-24 - GDI+
        # gives no detail on *why*, but a transient lock on a
        # freshly-written file (e.g. antivirus real-time scanning, which
        # this session has already found plausible evidence for on this
        # same VM - see the eu.po "first launch after install is slow"
        # investigation) is a typical cause. A short retry is cheap
        # insurance against exactly that kind of transient failure.
        $saved = $false
        $lastError = $null
        for ($attempt = 1; $attempt -le 3 -and -not $saved; $attempt++) {
            try {
                $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
                $saved = $true
            } catch {
                $lastError = $_
                Start-Sleep -Milliseconds 300
            }
        }
        if (-not $saved) { throw $lastError }
    } finally {
        $bmp.Dispose()
    }
    return $Path
}

function Send-VirtaalKeys {
    <#
    .SYNOPSIS
    Activates the instance's window, then sends a SendKeys string to it
    (e.g. "{ENTER}", "{DOWN}", "^z" for Ctrl+Z, "%{F4}" for Alt+F4 - see
    .NET's SendKeys documentation for the full syntax). SendKeys always
    goes to whatever window is frontmost at the OS level regardless of
    which handle you have, so activating first isn't optional - the same
    lesson the run-virtaal skill's macOS driver learned about System
    Events' keystroke command not being scoped by which process you
    address in the script.
    #>
    param([Parameter(Mandatory)]$Instance, [Parameter(Mandatory)][string]$Keys, [int]$SettleMs = 200)
    [VirtaalWin32]::SetForegroundWindow($Instance.Hwnd) | Out-Null
    Start-Sleep -Milliseconds 300
    [System.Windows.Forms.SendKeys]::SendWait($Keys)
    Start-Sleep -Milliseconds $SettleMs
}

function Get-VirtaalLogs {
    <#
    .SYNOPSIS
    Reads the frozen build's log files. pan_app.py redirects stdout/
    stderr here for packaged builds only (not process-level redirection)
    - this is where a real traceback ends up, not wherever PowerShell
    would otherwise capture output from Start-Process.
    #>
    $stdout = @(Get-Content "$env:APPDATA\Virtaal\stdout_virtaal.log" -ErrorAction SilentlyContinue)
    $stderr = @(Get-Content "$env:APPDATA\Virtaal\stderr_virtaal.log" -ErrorAction SilentlyContinue)
    return [PSCustomObject]@{ Stdout = $stdout; Stderr = $stderr }
}

function Write-VirtaalLogs {
    $logs = Get-VirtaalLogs
    Write-Host "--- stdout log ---"
    $logs.Stdout
    Write-Host "--- stderr log ---"
    $logs.Stderr
}

function Assert-VirtaalLogsClean {
    <#
    .SYNOPSIS
    Same allowlist-of-lines shape as the "Verify the bundle actually
    runs" step in ci.yml: fails (writes ::error:: and returns $false) if
    either log file contains a line not covered by $AllowlistPatterns
    (an array of regex strings). Starts with no allowlist by default -
    the known-clean baseline for this frozen build is empty logs.
    #>
    param([string[]]$AllowlistPatterns = @())
    $logs = Get-VirtaalLogs
    $lines = @($logs.Stdout + $logs.Stderr) | Where-Object { $_ }
    $unexpected = $lines | Where-Object {
        $line = $_
        -not ($AllowlistPatterns | Where-Object { $line -match $_ })
    }
    if ($unexpected) {
        Write-Host "::error::Unexpected output in the bundle's log"
        Write-VirtaalLogs
        return $false
    }
    return $true
}

function Stop-VirtaalTest {
    param($Instance)
    if ($Instance -and $Instance.Process) {
        Stop-Process -Id $Instance.Process.Id -Force -ErrorAction SilentlyContinue
    }
    # Belt-and-suspenders, same as ci.yml's existing steps: catches any
    # child/renamed process Stop-Process on the original PID missed.
    Get-Process -Name virtaal -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
