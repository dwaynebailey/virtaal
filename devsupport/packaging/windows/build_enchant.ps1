# Builds enchant with MSVC against gvsbuild's GLib (C:\gtk), with its
# hunspell and winspell (Windows' own spell checker) providers, for the
# self-contained bundle:
#   build_enchant.ps1 C:\enchant
# Writes bin\libenchant.dll and .lib, include\, and lib\enchant-2\ with
# the providers; skipped when the output already has this build. Run in
# an MSVC developer shell. gvsbuild's own build of enchant has winspell
# only; this uses its nmake files, pinned.

param([Parameter(Mandatory = $true)][string]$Out)

$ErrorActionPreference = "Stop"

$EnchantVersion = "2.8.21"
$EnchantSha256 = "dd2a762697c463148a8f59867089a5ebf2dd1449d869f93764b76c12bcf8acc0"
$HunspellVersion = "1.7.2"
$HunspellSha256 = "11ddfa39afe28c28539fe65fc4f1592d410c1e9b6dd7d8a91ca25d85e9ec65b8"
$Gvsbuild = "https://raw.githubusercontent.com/wingtk/gvsbuild/64c7871cce839215ccb2f16e027c4b909fb91da2/gvsbuild/patches/enchant"
$GvsbuildFiles = [ordered]@{
    "enchant-headers.patch" = "4780e8d61626971dfa3de2553d2a7c0a86d85abe52f79a56662b18af5a61c1d1"
    "src/config.h" = "a430e9c3d0862c3c3cfb63fffffe8ba77a64fa7706a0329f0ef2e54ec9809f9e"
    "src/configmake.h" = "38719dacbfe818fc8f449e5781217da12c080474dcce764b839a3ab2ffbaa1ab"
    "src/gnulib-compat.h" = "7cbe1efb6e66d797419ada60ce19950d187c340b0382a5fe17979f2c2026a1a8"
    "src/libenchant.rc" = "34072f90e19ba8accc7c6f35fef3c5a77db88666fec26a17b0e26e645d33e1d4"
    "src/makefile-2.mak" = "66f3faeb15343fdf02a47525a188ae20dee712aab9f5dd75a05b66ff32c74f18"
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
    git apply --whitespace=nowarn "$src\gvsbuild\enchant-headers.patch"
    if ($LASTEXITCODE) { throw "enchant-headers.patch didn't apply" }
    # A checkout can give it CRLF line ends.
    [System.IO.File]::WriteAllText("$src\utf8-paths.patch", ((Get-Content $Patch -Raw) -replace "`r`n", "`n"))
    git apply --whitespace=nowarn "$src\utf8-paths.patch"
    if ($LASTEXITCODE) { throw "$Patch didn't apply" }
} finally {
    Pop-Location
}

# libenchant and the winspell provider, as gvsbuild builds them.
Push-Location "$enchant\src"
try {
    nmake /nologo -f makefile-2.mak DLL=1 X64=1 MFLAGS=/MD "GLIBDIR=$Gtk\include\glib-2.0" "CPREFIX=C:/gtk" libenchant enchant_winspell
    if ($LASTEXITCODE) { throw "nmake failed" }
} finally {
    Pop-Location
}
$release = "$enchant\bin\release"

# The hunspell provider, with hunspell built in.
$includes = @("$enchant", "$enchant\src", "$enchant\lib", "$enchant\libgnu", $hunspell,
    "$Gtk\include\glib-2.0", "$Gtk\lib\glib-2.0\include") | ForEach-Object { "/I$_" }
$sources = @("$enchant\providers\enchant_hunspell.cpp") + (Get-ChildItem "$hunspell\*.cxx").FullName
Push-Location $src
try {
    cl /nologo /O2 /MD /EHsc /std:c++17 /W1 /DWIN32 /D_WIN32 /D_CRT_SECURE_NO_WARNINGS /DHUNSPELL_STATIC `
        /D_ENCHANT_BUILD=1 "/FI$enchant\src\config.h" @includes @sources `
        /LD /Fe:enchant_hunspell.dll /link "/LIBPATH:$Gtk\lib" "$release\libenchant.lib" `
        glib-2.0.lib gobject-2.0.lib /EXPORT:init_enchant_provider
    if ($LASTEXITCODE) { throw "cl failed" }
} finally {
    Pop-Location
}

Copy-Item "$release\libenchant.dll", "$release\libenchant.lib" "$Out\bin"
Copy-Item "$release\enchant_winspell.dll", "$src\enchant_hunspell.dll" "$Out\lib\enchant-2"
Copy-Item "$enchant\lib\enchant.h", "$enchant\lib\enchant-provider.h" "$Out\include"
foreach ($dll in "$Out\bin\libenchant.dll", "$Out\lib\enchant-2\enchant_hunspell.dll", "$Out\lib\enchant-2\enchant_winspell.dll") {
    dumpbin /nologo /dependents $dll
}

Remove-Item -Recurse -Force $src
Set-Content -Path $stamp -Value $stampWant
Write-Host "Built enchant $EnchantVersion in $Out"
