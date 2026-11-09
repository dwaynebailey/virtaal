#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Generates the [Languages] and [CustomMessages] sections virtaal.iss
#includes (see build_installer.ps1), and the wizard's messages files
for the languages with a lite catalog. Run with the same Python
environment bin/virtaal runs with.

The installer offers the po/LINGUAS languages in INNO_LANGUAGES, and
those in NEW_LANGUAGES that po/LINGUAS-lite lists an inno catalog for
(see inno_lite.py). A po/LINGUAS code in none of INNO_LANGUAGES,
NEW_LANGUAGES and NO_INNO_LANGUAGE is an error, so a newly shipped
translation forces a decision about its installer language.

A language po/LINGUAS-lite lists inno/<lang> for gets a messages file
in generated-languages/: its vendored one (if any) with
po/lite/inno/<lang>.po's translations, which win. Any other language
uses its vendored file as it is.

[CustomMessages] holds virtaal.iss's own strings, translated from
po/*.po. Each msgid must be in po/virtaal.pot - the installer-only
ones are listed in devsupport/tmp_strings.py.

Usage: generate_installer_translations.py OUTPUT_PATH [--default-isl PATH]
       [--every-lite]

--default-isl: the compiler's Default.isl, to warn when
po/lite/inno/inno.pot's English is out of date with it.
--every-lite: use every inno lite catalog, as if po/LINGUAS-lite listed
them all, to check that each one compiles before it ships.
"""

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

from translate.storage import pypo

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
LANGUAGES_DIR = HERE / "languages"
GENERATED_DIR = "generated-languages"
DOMAIN = "inno"


def _inno_lite():
    spec = importlib.util.spec_from_file_location("inno_lite", HERE / "inno_lite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inno_lite = _inno_lite()

# po/LINGUAS code -> vendored Inno Setup messages file in languages\,
# copied from jrsoftware/issrc's Files/Languages (Unofficial/ for the
# ones Inno doesn't ship itself), unmodified but for Asturian.isl's
# LanguageID. The [Languages] Name is the file's lowercased stem.
INNO_LANGUAGES = {
    "af": "Afrikaans.isl",
    "ar": "Arabic.isl",
    # Last updated for Inno 4.0.x - more wizard strings fall back to
    # English than for any other language here. Its LanguageID was
    # US English's ($0409); Windows has none for Asturian, so $0000.
    "ast": "Asturian.isl",
    "be": "Belarusian.isl",
    "bg": "Bulgarian.isl",
    # Generic Bengali, not bn_IN specifically.
    "bn_IN": "Bengali.islu",
    "ca": "Catalan.isl",
    "ca@valencia": "Valencian.isl",
    "cs": "Czech.isl",
    "da": "Danish.isl",
    "de": "German.isl",
    "el": "Greek.isl",
    "en_GB": "EnglishBritish.isl",
    "es": "Spanish.isl",
    "eu": "Basque.isl",
    "fi": "Finnish.isl",
    "fr": "French.isl",
    "gl": "Galician.isl",
    "he": "Hebrew.isl",
    "hu": "Hungarian.isl",
    "is": "Icelandic.isl",
    "it": "Italian.isl",
    "ja": "Japanese.isl",
    "ko": "Korean.isl",
    "lt": "Lithuanian.isl",
    "ne": "Nepali.islu",
    "nl": "Dutch.isl",
    "pl": "Polish.isl",
    "pt": "Portuguese.isl",
    "pt_BR": "BrazilianPortuguese.isl",
    "ru": "Russian.isl",
    "si": "Sinhala.islu",
    "sk": "Slovak.isl",
    "sr": "SerbianCyrillic.isl",
    "sv": "Swedish.isl",
    "th": "Thai.isl",
    "tr": "Turkish.isl",
    "uk": "Ukrainian.isl",
    "vi": "Vietnamese.isl",
    "zh_CN": "ChineseSimplified.isl",
    "zh_TW": "ChineseTraditional.isl",
}

# po/LINGUAS code -> (the file stem, as INNO_LANGUAGES', LangOptions)
# for a language Inno has no messages file for: its file is written from
# po/lite/inno/<lang>.po alone, English for what that doesn't translate.
# LanguageName is the language's own name, as the Select Setup Language
# dialog lists it; LanguageID its Windows LANGID, for picking it by the
# Windows display language (0 if Windows has none); LanguageCodePage 0
# where no ANSI code page has its letters (the file is UTF-8 anyway).
NEW_LANGUAGES = {
    "ak": ("Akan", inno_lite.LangOptions("Akan", 0, 0)),
    "am": ("Amharic", inno_lite.LangOptions("አማርኛ", 0x045E, 0)),
    "cgg": ("Chiga", inno_lite.LangOptions("Rukiga", 0, 1252)),
    "ff": ("Fulah", inno_lite.LangOptions("Fula", 0x0867, 0)),
    "fo": ("Faroese", inno_lite.LangOptions("Føroyskt", 0x0438, 1252)),  # codespell:ignore fo
    "lg": ("Ganda", inno_lite.LangOptions("Luganda", 0, 0)),
    "nso": ("NorthernSotho", inno_lite.LangOptions("Sesotho sa Leboa", 0x046C, 1252)),
    # Inno's own Occitan.isl is Aranese, not the Occitan oc.po is in.
    "oc": ("Occitan", inno_lite.LangOptions("Occitan", 0x0482, 1252)),
    "pa": ("Punjabi", inno_lite.LangOptions("ਪੰਜਾਬੀ", 0x0446, 0)),
    "son": ("Songhay", inno_lite.LangOptions("Songhay", 0, 0)),
    "st": ("Sesotho", inno_lite.LangOptions("Sesotho", 0x0430, 1252)),
    "sw": ("Swahili", inno_lite.LangOptions("Kiswahili", 0x0441, 1252)),
    "te": ("Telugu", inno_lite.LangOptions("తెలుగు", 0x044A, 0)),  # codespell:ignore te
    "zu": ("Zulu", inno_lite.LangOptions("isiZulu", 0x0435, 1252)),
}

# po/LINGUAS codes whose installer stays in English.
NO_INNO_LANGUAGE = {"en_ZA"}

# CustomMessages key -> msgid.
MESSAGE_KEY_TO_MSGID = {
    "FileAssocTask": "Associate Virtaal with translation file types (.po, .xlf, .tmx, ...)",
    "EditWithVirtaal": "Edit with Virtaal",
    "UninstallOldVirtaalTask": "Uninstall the older Virtaal 0.7 first",
    "PoFileType": "Gettext PO file",
    "MoFileType": "Gettext MO file",
    "XliffFileType": "XLIFF Translation File",
    "SdlXliffFileType": "SDL XLIFF Translation File",
    "MemoqXliffFileType": "memoQ XLIFF Translation File",
    "PhraseXliffFileType": "Phrase XLIFF Translation File",
    "WordfastTxmlFileType": "Wordfast TXML Translation File",
    "QmFileType": "Qt Message File",
    "TbxFileType": "TBX Glossary",
    "TmxFileType": "TMX Translation Memory",
    "QphFileType": "Qt Phrase Book",
    "FluentFileType": "Fluent file",
}


def _stem(po_code):
    if po_code in INNO_LANGUAGES:
        return Path(INNO_LANGUAGES[po_code]).stem
    return NEW_LANGUAGES[po_code][0]


def inno_name(po_code):
    return _stem(po_code).lower()


def _read_lines(path):
    text = path.read_text(encoding="utf-8")
    return [code for line in text.splitlines() if (code := line.split("#")[0].strip())]


def read_linguas():
    return _read_lines(REPO_ROOT / "po" / "LINGUAS")


def read_lite():
    """The languages po/LINGUAS-lite lists an inno catalog for."""
    entries = _read_lines(REPO_ROOT / "po" / "LINGUAS-lite")
    return {lang for domain, _sep, lang in (e.partition("/") for e in entries) if domain == DOMAIN}


def installer_languages(linguas, lite=()):
    """The po/LINGUAS codes the installer offers, by [Languages] name;
    lite: the languages po/LINGUAS-lite lists an inno catalog for."""
    decided = set(INNO_LANGUAGES) | set(NEW_LANGUAGES) | NO_INNO_LANGUAGE
    unmapped = [code for code in linguas if code not in decided]
    if unmapped:
        raise ValueError(
            "po/LINGUAS codes with no installer language decision: %s - add each to "
            "INNO_LANGUAGES (vendoring its .isl), NEW_LANGUAGES or NO_INNO_LANGUAGE"
            % ", ".join(unmapped)
        )
    return sorted(
        (
            code
            for code in linguas
            if code in INNO_LANGUAGES or (code in NEW_LANGUAGES and code in lite)
        ),
        key=inno_name,
    )


def lite_catalog(po_code):
    return REPO_ROOT / "po" / "lite" / DOMAIN / (po_code + ".po")


def vendored_text(po_code):
    """po_code's vendored messages file's text, or None."""
    if po_code not in INNO_LANGUAGES:
        return None
    return inno_lite.read(LANGUAGES_DIR / INNO_LANGUAGES[po_code])


def vendored_translations(po_code):
    """{(msgctxt, msgid): translation} of the wizard messages po_code's
    vendored messages file translates, or {} if it has none."""
    text = vendored_text(po_code)
    return inno_lite.translations(text) if text is not None else {}


def write_messages_files(po_codes, lite, out_dir):
    """Writes out_dir/<stem>.isl for each of po_codes with an inno lite
    catalog; returns build()'s warnings."""
    warnings = []
    for code in po_codes:
        if code not in lite:
            continue
        vendored = LANGUAGES_DIR / INNO_LANGUAGES[code] if code in INNO_LANGUAGES else None
        options = None if vendored else NEW_LANGUAGES[code][1]
        text, found = inno_lite.build(lite_catalog(code), vendored, options)
        inno_lite.write(out_dir / (_stem(code) + ".isl"), text)
        warnings += found
    return warnings


def translated_messages(po_code):
    po_path = REPO_ROOT / "po" / f"{po_code}.po"
    if not po_path.exists():
        return {}
    store = pypo.pofile(po_path.read_bytes())
    wanted = set(MESSAGE_KEY_TO_MSGID.values())
    found = {}
    for unit in store.units:
        if unit.source in wanted and unit.istranslated() and not unit.isfuzzy():
            found[unit.source] = str(unit.target)
    return found


def messages_file(po_code, lite=()):
    """po_code's MessagesFile, relative to virtaal.iss."""
    if po_code in lite:
        return f"{GENERATED_DIR}\\{_stem(po_code)}.isl"
    return f"languages\\{INNO_LANGUAGES[po_code]}"


def build_languages(po_codes, lite=()):
    lines = ["[Languages]", 'Name: "english"; MessagesFile: "compiler:Default.isl"']
    for code in po_codes:
        lines.append(f'Name: "{inno_name(code)}"; MessagesFile: "{messages_file(code, lite)}"')
    return lines


def build_custom_messages(po_codes):
    lines = ["[CustomMessages]"]
    for key, msgid in MESSAGE_KEY_TO_MSGID.items():
        lines.append(f"{key}={msgid}")
    for code in po_codes:
        messages = translated_messages(code)
        for key, msgid in MESSAGE_KEY_TO_MSGID.items():
            if msgid in messages:
                lines.append(f"{inno_name(code)}.{key}={messages[msgid]}")
    return lines


def build_include(linguas, lite=()):
    po_codes = installer_languages(linguas, lite)
    return (
        "\n".join(build_languages(po_codes, lite) + [""] + build_custom_messages(po_codes)) + "\n"
    )


def stale_template_warning(default_isl):
    """A ::warning:: naming the template messages whose English the
    compiler's Default.isl has changed, or None."""
    stale = inno_lite.stale(default_isl)
    if not stale:
        return None
    return (
        "::warning title=Installer lite template out of date::po/lite/inno/inno.pot's English"
        " differs from this Inno Setup's Default.isl for %s - rerun inno_lite.py and"
        " update_lite_catalogs.py inno (see po/lite/inno/README.md)" % ", ".join(stale)
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output", type=Path, help="the [Languages]/[CustomMessages] include")
    parser.add_argument("--default-isl", type=Path, help="the compiler's Default.isl")
    parser.add_argument(
        "--every-lite", action="store_true", help="use every inno lite catalog (a check)"
    )
    args = parser.parse_args(argv)
    linguas = read_linguas()
    if args.every_lite:
        lite = {code for code in linguas if lite_catalog(code).is_file()}
    else:
        lite = read_lite()
    try:
        include = build_include(linguas, lite)
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    # utf-8-sig: Inno Setup only treats an included script file as UTF-8
    # if it starts with a BOM, otherwise it assumes the local codepage
    # and corrupts every non-Latin-script translation here.
    args.output.write_text(include, encoding="utf-8-sig")
    out_dir = args.output.parent / GENERATED_DIR
    shutil.rmtree(out_dir, ignore_errors=True)
    for warning in write_messages_files(installer_languages(linguas, lite), lite, out_dir):
        print("::warning::%s" % warning)
    if args.default_isl:
        warning = stale_template_warning(args.default_isl)
        if warning:
            print(warning)


if __name__ == "__main__":
    main()
