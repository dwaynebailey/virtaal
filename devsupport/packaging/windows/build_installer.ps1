# Builds dist\installer\virtaal-<version>-setup.exe from the standalone
# bundle build_standalone.ps1 produces - run that first.
#
# Needs Inno Setup's command-line compiler (iscc.exe) on PATH - install via
# https://jrsoftware.org/isdl.php or `choco install innosetup` (verified
# still actively maintained: 7.1.0 released August 2026, not a stale
# dependency to be wary of).

$ErrorActionPreference = "Stop"

$RepoRoot = (git rev-parse --show-toplevel)
Set-Location $RepoRoot

if (-not (Test-Path "dist\virtaal\virtaal.exe")) {
    Write-Error "dist\virtaal\virtaal.exe not found - run build_standalone.ps1 first."
    exit 1
}

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    Write-Error "iscc.exe not found on PATH - install Inno Setup (https://jrsoftware.org/isdl.php) first."
    exit 1
}

$version = & python -c "from virtaal.__version__ import ver; print(ver)"

New-Item -ItemType Directory -Force -Path dist\installer | Out-Null

# The compiler's own English, to check po/lite/inno/inno.pot's against:
# beside iscc.exe, unless that's a shim.
$defaultIsl = @(
    (Join-Path (Split-Path $iscc.Source) "Default.isl")
    (Get-ChildItem "${env:ProgramFiles(x86)}\Inno Setup*\Default.isl", "$env:ProgramFiles\Inno Setup*\Default.isl" -ErrorAction SilentlyContinue).FullName
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
$generate = @("devsupport\packaging\windows\installer-strings.iss")
if ($defaultIsl) { $generate += @("--default-isl", $defaultIsl) }
& python devsupport\packaging\windows\generate_installer_translations.py @generate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Kept for report_installer_warnings.py: the compiler's version and its
# English-fallback warnings. Continue: its stderr lines aren't errors.
$log = "dist\installer\iscc.log"
$ErrorActionPreference = "Continue"
& iscc "/DMyAppVersion=$version" devsupport\packaging\windows\virtaal.iss 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log
$isccExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($isccExit -ne 0) { exit $isccExit }

# Never fails the build: English fallbacks are for translators to fill.
& python devsupport\packaging\windows\report_installer_warnings.py $log
$global:LASTEXITCODE = 0

Write-Host "Built dist\installer\virtaal-$version-setup.exe"
