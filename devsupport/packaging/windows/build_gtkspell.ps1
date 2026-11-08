# Builds gtkspell3 with MSVC against gvsbuild's GTK (C:\gtk) and
# build_enchant.ps1's enchant, for the self-contained bundle:
#   build_gtkspell.ps1 C:\gtkspell C:\enchant
# Writes bin\libgtkspell3-3-0.dll and lib\girepository-1.0\GtkSpell-3.0.typelib;
# skipped when the output already has this build. Run in an MSVC
# developer shell.

param([Parameter(Mandatory = $true)][string]$Out, [Parameter(Mandatory = $true)][string]$Enchant)

$ErrorActionPreference = "Stop"

$GtkSpellVersion = "3.0.10"
$GtkSpellSha256 = "b040f63836b347eb344f5542443dc254621805072f7141d49c067ecb5a375732"
$Gtk = "C:\gtk"
$Here = $PSScriptRoot
$Gir = Join-Path $Here "GtkSpell-3.0.gir"

$stampWant = "$GtkSpellSha256 $((Get-Content (Join-Path $Enchant 'built-from') -Raw).Trim()) $((Get-FileHash $PSCommandPath).Hash) $((Get-FileHash $Gir).Hash)"
$stamp = Join-Path $Out "built-from"
if ((Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq $stampWant)) {
    Write-Host "gtkspell3 $GtkSpellVersion already built in $Out"
    exit 0
}

function Get-Pinned($Url, $Sha256, $Path) {
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
New-Item -ItemType Directory -Force -Path $src, "$Out\bin", "$Out\lib\girepository-1.0" | Out-Null

Get-Pinned "https://deb.debian.org/debian/pool/main/g/gtkspell3/gtkspell3_$GtkSpellVersion.orig.tar.xz" $GtkSpellSha256 "$src\gtkspell.tar.xz"
7z x "$src\gtkspell.tar.xz" "-o$src" -y | Out-Null
7z x "$src\gtkspell.tar" "-o$src" -y | Out-Null
$gtkspell = Join-Path $src "gtkspell3-$GtkSpellVersion"

# What configure would write: translations, nothing else optional.
@"
#define ENABLE_NLS 1
#define GETTEXT_PACKAGE "gtkspell3"
#define PACKAGE_NAME "gtkspell3"
#define HAVE_BIND_TEXTDOMAIN_CODESET 1
"@ | Set-Content -Path "$gtkspell\config.h" -Encoding ascii

# gtkspell.h has no export annotations.
$symbols = Select-String -CaseSensitive -Path "$gtkspell\gtkspell\gtkspell.h" -Pattern '\b(gtk_spell_[a-z_]+)\s*\(' -AllMatches |
    ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
(@("LIBRARY libgtkspell3-3-0", "EXPORTS") + $symbols) | Set-Content -Path "$src\gtkspell.def" -Encoding ascii

$includes = @(
    "$gtkspell", "$Enchant\include", "$Gtk\include\gtk-3.0", "$Gtk\include\glib-2.0", "$Gtk\lib\glib-2.0\include",
    "$Gtk\include\pango-1.0", "$Gtk\include\harfbuzz", "$Gtk\include\cairo", "$Gtk\include\gdk-pixbuf-2.0",
    "$Gtk\include\atk-1.0", "$Gtk\include\fribidi", "$Gtk\include"
) | ForEach-Object { "/I$_" }
$libs = "gtk-3.lib", "gdk-3.lib", "pango-1.0.lib", "gobject-2.0.lib", "glib-2.0.lib", "intl.lib", "$Enchant\bin\libenchant.lib"
Push-Location $src
try {
    # enchant.h uses ssize_t, which MSVC lacks.
    cl /nologo /O2 /MD /W1 /Dssize_t=__int64 @includes `
        "$gtkspell\gtkspell\gtkspell.c" "$gtkspell\gtkspell\gtkspell-win32.c" `
        /LD /Fe:libgtkspell3-3-0.dll /link "/LIBPATH:$Gtk\lib" "/DEF:$src\gtkspell.def" @libs
    if ($LASTEXITCODE) { throw "cl failed" }
} finally {
    Pop-Location
}
Copy-Item "$src\libgtkspell3-3-0.dll" "$Out\bin"
dumpbin /nologo /dependents "$Out\bin\libgtkspell3-3-0.dll"

& "$Gtk\bin\g-ir-compiler.exe" --includedir "$Gtk\share\gir-1.0" -o "$Out\lib\girepository-1.0\GtkSpell-3.0.typelib" $Gir
if ($LASTEXITCODE) { throw "g-ir-compiler failed" }

Remove-Item -Recurse -Force $src
Set-Content -Path $stamp -Value $stampWant
Write-Host "Built gtkspell3 $GtkSpellVersion in $Out"
