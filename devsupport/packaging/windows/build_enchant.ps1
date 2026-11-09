# Builds enchant with MSVC against gvsbuild's GLib (C:\gtk), with its
# hunspell and winspell (Windows' own spell checker) providers, for the
# self-contained bundle:
#   build_enchant.ps1 C:\enchant
# Writes bin\libenchant.dll and .lib, include\, and lib\enchant-2\ with
# the providers; skipped when the output already has this build. Run in
# an MSVC developer shell. gvsbuild's own build of enchant has winspell
# only; this uses its MSVC headers, pinned, and its compiler flags.

param([Parameter(Mandatory = $true)][string]$Out)

$ErrorActionPreference = "Stop"

$EnchantVersion = "2.8.21"
$EnchantSha256 = "dd2a762697c463148a8f59867089a5ebf2dd1449d869f93764b76c12bcf8acc0"
$HunspellVersion = "1.7.2"
$HunspellSha256 = "11ddfa39afe28c28539fe65fc4f1592d410c1e9b6dd7d8a91ca25d85e9ec65b8"
$Gvsbuild = "https://raw.githubusercontent.com/wingtk/gvsbuild/64c7871cce839215ccb2f16e027c4b909fb91da2/gvsbuild/patches/enchant"
$GvsbuildFiles = [ordered]@{
    "src/config.h" = "a430e9c3d0862c3c3cfb63fffffe8ba77a64fa7706a0329f0ef2e54ec9809f9e"
    "src/gnulib-compat.h" = "7cbe1efb6e66d797419ada60ce19950d187c340b0382a5fe17979f2c2026a1a8"
    "src/sys/file.h" = "6bc613acf69e5f07ffe7ea38aac227200f99aaea5ee34b5c89e9a041e9593c31"
}
$Gtk = "C:\gtk"
$Patch = Join-Path $PSScriptRoot "enchant-hunspell-utf8-paths.patch"

$stampWant = "$EnchantSha256 $HunspellSha256 $($GvsbuildFiles.Values -join ' ') $((Get-FileHash $PSCommandPath).Hash) $((Get-FileHash $Patch).Hash)"
$stamp = Join-Path $Out "built-from"
if ((Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq $stampWant)) {
    Write-Host "enchant $EnchantVersion already built in $Out"
    exit 0
}

function Get-Pinned($Url, $Sha256, $Path) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
    for ($attempt = 1; ; $attempt++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing -TimeoutSec 300
            break
        } catch {
            if ($attempt -ge 4) { throw }
            Start-Sleep -Seconds (10 * $attempt)
        }
    }
    if ((Get-FileHash $Path -Algorithm SHA256).Hash -ne $Sha256.ToUpper()) { throw "$Url`: unexpected sha256" }
}

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Out
$src = Join-Path $Out "src"
New-Item -ItemType Directory -Force -Path $src, "$Out\bin", "$Out\include", "$Out\lib\enchant-2" | Out-Null

Get-Pinned "https://github.com/rrthomas/enchant/releases/download/v$EnchantVersion/enchant-$EnchantVersion.tar.gz" $EnchantSha256 "$src\enchant.tar.gz"
Get-Pinned "https://github.com/hunspell/hunspell/releases/download/v$HunspellVersion/hunspell-$HunspellVersion.tar.gz" $HunspellSha256 "$src\hunspell.tar.gz"
foreach ($name in $GvsbuildFiles.Keys) {
    Get-Pinned "$Gvsbuild/$name" $GvsbuildFiles[$name] (Join-Path "$src\gvsbuild" $name)
}
foreach ($archive in "enchant", "hunspell") {
    7z x "$src\$archive.tar.gz" "-o$src" -y | Out-Null
    7z x "$src\$archive.tar" "-o$src" -y | Out-Null
}
$enchant = Join-Path $src "enchant-$EnchantVersion"
$hunspell = Join-Path $src "hunspell-$HunspellVersion\src\hunspell"
Copy-Item -Recurse -Force "$src\gvsbuild\src\*" "$enchant\src"
Push-Location $enchant
try {
    # A checkout can give it CRLF line ends.
    [System.IO.File]::WriteAllText("$src\utf8-paths.patch", ((Get-Content $Patch -Raw) -replace "`r`n", "`n"))
    git apply --whitespace=nowarn "$src\utf8-paths.patch"
    if ($LASTEXITCODE) { throw "$Patch didn't apply" }
} finally {
    Pop-Location
}

# What the build system would define. In a header: quotes don't survive
# PowerShell's command lines.
@"
#define INSTALLPREFIX "C:/gtk"
#define INSTALLDIR "C:/gtk/bin"
"@ | Set-Content -Path "$enchant\src\virtaal-paths.h" -Encoding ascii
# enchant adds the "-2" itself; gvsbuild's configmake.h has it already.
@"
#define PKGDATADIR INSTALLPREFIX "/share/enchant"
#define PKGLIBDIR INSTALLPREFIX "/lib/enchant"
#define SYSCONFDIR INSTALLPREFIX "/etc"
"@ | Set-Content -Path "$enchant\src\configmake.h" -Encoding ascii
$includes = @("$enchant", "$enchant\src", "$enchant\lib", "$enchant\libgnu", $hunspell,
    "$Gtk\include\glib-2.0", "$Gtk\lib\glib-2.0\include") | ForEach-Object { "/I$_" }
$common = @("/nologo", "/O2", "/MD", "/W1", "/DWIN32", "/D_WIN32", "/D_WINDOWS",
    "/DUNICODE", "/D_UNICODE", "/D_CRT_SECURE_NO_WARNINGS", "/D_ENCHANT_BUILD=1", "/DVALA_EXTERN=extern",
    "/FI$enchant\src\virtaal-paths.h", "/FI$enchant\src\config.h") + $includes
$glib = "glib-2.0.lib", "gobject-2.0.lib", "gmodule-2.0.lib", "gio-2.0.lib"
$release = "$src\release"
New-Item -ItemType Directory -Force -Path "$release\lib", "$release\winspell", "$release\hunspell" | Out-Null

function Invoke-Cl($Arguments) {
    cl @Arguments
    if ($LASTEXITCODE) { throw "cl failed" }
}

# libenchant, exporting its enchant_ functions.
Push-Location "$release\lib"
try {
    $sources = @((Get-ChildItem "$enchant\lib\*.c").FullName) + @("$enchant\libgnu\flock.c", "$enchant\libgnu\relocatable.c")
    Invoke-Cl ($common + @("/DENABLE_RELOCATABLE=1", "/DENABLE_COSTLY_RELOCATABLE=1", "/DNO_XMALLOC=1",
        "/DPIC=1", "/DBUILDING_DLL=1", "/c") + $sources)
    $exports = dumpbin /nologo /symbols *.obj | ForEach-Object {
        if ($_ -match 'SECT[0-9A-F]+\s.*External\s+\|\s+(enchant_\w+)$') { $Matches[1] }
    } | Sort-Object -Unique
    if (-not ($exports -contains "enchant_broker_init")) { throw "No enchant_ functions to export" }
    (@("LIBRARY libenchant", "EXPORTS") + $exports) | Set-Content -Path libenchant.def -Encoding ascii
    link /nologo /DLL /OUT:libenchant.dll /DEF:libenchant.def *.obj "/LIBPATH:$Gtk\lib" @glib advapi32.lib
    if ($LASTEXITCODE) { throw "link failed" }
} finally {
    Pop-Location
}

# The winspell provider, as gvsbuild builds it.
Push-Location "$release\winspell"
try {
    Invoke-Cl ($common + @("/EHsc", "/std:c++17", "/D_WIN32_WINNT=_WIN32_WINNT_WIN8", "/DNTDDI_VERSION=NTDDI_WIN8",
        "/FI$enchant\src\gnulib-compat.h", "$enchant\providers\enchant_winspell.cpp", "$enchant\libgnu\bcp47.c",
        "/LD", "/Fe:enchant_winspell.dll", "/link", "/LIBPATH:$Gtk\lib", "$release\lib\libenchant.lib") + $glib + @(
        "ole32.lib", "oleaut32.lib", "uuid.lib", "/EXPORT:init_enchant_provider"))
} finally {
    Pop-Location
}

# The hunspell provider, with hunspell built in.
Push-Location "$release\hunspell"
try {
    $sources = @("$enchant\providers\enchant_hunspell.cpp") + (Get-ChildItem "$hunspell\*.cxx").FullName
    Invoke-Cl ($common + @("/EHsc", "/std:c++17", "/DHUNSPELL_STATIC") + $sources + @("/LD", "/Fe:enchant_hunspell.dll",
        "/link", "/LIBPATH:$Gtk\lib", "$release\lib\libenchant.lib", "glib-2.0.lib", "gobject-2.0.lib",
        "/EXPORT:init_enchant_provider"))
} finally {
    Pop-Location
}

Copy-Item "$release\lib\libenchant.dll", "$release\lib\libenchant.lib" "$Out\bin"
Copy-Item "$release\winspell\enchant_winspell.dll", "$release\hunspell\enchant_hunspell.dll" "$Out\lib\enchant-2"
Copy-Item "$enchant\lib\enchant.h", "$enchant\lib\enchant-provider.h" "$Out\include"
foreach ($dll in "$Out\bin\libenchant.dll", "$Out\lib\enchant-2\enchant_hunspell.dll", "$Out\lib\enchant-2\enchant_winspell.dll") {
    dumpbin /nologo /dependents $dll
}

Remove-Item -Recurse -Force $src
Set-Content -Path $stamp -Value $stampWant
Write-Host "Built enchant $EnchantVersion in $Out"
