# Reusable PowerShell helpers for driving the packaged Windows
# virtaal.exe from CI (or a local Windows/VM session): launch, get a
# real window handle via Win32, read its geometry, send keystrokes via
# SendKeys, read the frozen-build log files, and clean up.
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

# A PowerShell here-string's closing "@ must start at column 1, which is
# incompatible with a YAML block scalar's indentation requirement when
# this gets inlined into a workflow step - keeping the type definition
# as one plain string (not a here-string) here too, even though this
# file itself isn't YAML, so a future copy-paste into a workflow step
# doesn't reintroduce that exact bug.
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class VirtaalWin32 { [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd); public struct RECT { public int Left; public int Top; public int Right; public int Bottom; } }'

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

    $proc = Start-Process -FilePath $ExePath -ArgumentList $Arguments -PassThru
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
