# Windows installer lite catalogs (inno)

The Windows installer's wizard is Inno Setup's. Its messages come from
Inno's messages file for each language, vendored in
`devsupport/packaging/windows/languages/`. Some of those are out of
date, and some languages have none, so parts of the wizard, or all of
it, show in English. These catalogs fill those gaps.

## What's here

`inno.pot` holds the wizard messages our installer shows, not all of
Inno's: the list, with why each group is there, is `WIZARD_MESSAGES` in
`devsupport/packaging/windows/inno_lite.py`. Each message's msgctxt is
its Inno key and its msgid the English from Inno's `Default.isl`.

Its messages have their own priority, Installer (see
`po/priorities.toml`): no release needs them, and translation packs
hold them in `inno-<lang>-installer.po`, beside `<lang>-installer.po`
with Virtaal's own installer messages.

A language has a catalog when its Inno file doesn't translate every
message here, or it has no Inno file. It starts with what that file
translates, and its translators' credits as header comments.

## Translating

Translate `<lang>.po` as any other PO file. The comment above each
message says where the wizard shows it. Keep:

- `%1`, `%2`, ...: what Setup fills in (often "Virtaal"). They're
  marked `qt-format`, so `msgfmt -c` and Virtaal's Qt checks catch a
  lost or extra one.
- `[name]` and `[name/ver]`: Virtaal's name, and its version.
- Line breaks: Inno's `%n`, a real line break here.
- `&`: the access key, as in `&Next >`. `&&` is a plain `&`.

A translation that loses a `%1` or `[name]` is left out of the
installer (the build warns), so it shows in English.

Better still, send your translation to Inno Setup too
(<https://jrsoftware.org/files/istrans/>), so every Inno installer has
it.

## How the installer uses them

`po/LINGUAS-lite` lists `inno/<lang>` for each catalog that changes the
installer: it translates a message the Inno file doesn't, or
translates one differently. Unlike the other lite catalogs, these
translations win over upstream's (the vendored file's).

For each listed language, `generate_installer_translations.py` (run by
`build_installer.ps1`) writes `generated-languages\<Name>.isl`: the
vendored file with this catalog's translations, or, for a language Inno
has no file for, one from this catalog alone, with `NEW_LANGUAGES`'
language name, Windows language ID and code page. Anything neither
translates shows in English. An unlisted language uses its vendored
file as it is, or stays English.

CI's build-windows-installer job compiles every catalog here, listed
or not, and its Build installer summary names the Inno Setup version
and each language's messages that show in English.

## Refreshing

When Inno Setup's English changes (the build warns), or to add a
message to `WIZARD_MESSAGES`, rewrite the template from an Inno Setup
install's `Default.isl`, then the catalogs and `po/LINGUAS-lite`:

```sh
python devsupport/packaging/windows/inno_lite.py "C:\Program Files (x86)\Inno Setup 7\Default.isl"
python devsupport/pseudo-translation/update_lite_catalogs.py inno
```

After vendoring a newer Inno file, run the second one: it pre-fills from
the vendored files, and retires a catalog the file now covers.
