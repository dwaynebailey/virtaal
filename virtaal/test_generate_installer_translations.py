#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import importlib.util
import os
import sys

import pytest

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "devsupport",
    "packaging",
    "windows",
    "generate_installer_translations.py",
)


@pytest.fixture
def gen():
    spec = importlib.util.spec_from_file_location("generate_installer_translations", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_linguas_code_has_an_installer_decision(gen):
    # Fails when a translation is added to po/LINGUAS - map it to a
    # vendored .isl or list it in NO_INNO_LANGUAGE.
    gen.installer_languages(gen.read_linguas())


def test_mapped_and_unmapped_are_disjoint(gen):
    assert not set(gen.INNO_LANGUAGES) & gen.NO_INNO_LANGUAGE
    assert not set(gen.NEW_LANGUAGES) & (set(gen.INNO_LANGUAGES) | gen.NO_INNO_LANGUAGE)


def test_each_language_has_its_own_installer_name(gen):
    names = [gen.inno_name(code) for code in list(gen.INNO_LANGUAGES) + list(gen.NEW_LANGUAGES)]
    assert len(names) == len(set(names))
    assert "english" not in names


def test_new_languages_have_valid_lang_options(gen):
    for code, (_stem, options) in gen.NEW_LANGUAGES.items():
        assert options.name and options.name != "English", code
        assert 0 <= options.language_id <= 0xFFFF, code
        assert options.codepage in (0, 1252), code


def test_vendored_files_match_the_mapping(gen):
    vendored = {p.name for p in gen.LANGUAGES_DIR.iterdir()}
    assert vendored == set(gen.INNO_LANGUAGES.values())


def test_every_message_is_in_the_pot(gen):
    # A msgid missing from the POT never gets translated, silently
    # leaving that installer string in English.
    from translate.storage import pypo

    pot = pypo.pofile((gen.REPO_ROOT / "po" / "virtaal.pot").read_bytes())
    msgids = {unit.source for unit in pot.units}
    assert set(gen.MESSAGE_KEY_TO_MSGID.values()) <= msgids


def test_unmapped_code_is_an_error(gen):
    with pytest.raises(ValueError, match="xx"):
        gen.installer_languages(["de", "xx"])


def test_only_linguas_languages_are_offered(gen):
    include = gen.build_include(["de", "zu", "af"])
    languages = include.split("[CustomMessages]")[0]
    assert languages.splitlines() == [
        "[Languages]",
        'Name: "english"; MessagesFile: "compiler:Default.isl"',
        'Name: "afrikaans"; MessagesFile: "languages\\Afrikaans.isl"',
        'Name: "german"; MessagesFile: "languages\\German.isl"',
        "",
    ]


def test_custom_messages_skip_fuzzy_and_untranslated(gen, tmp_path, monkeypatch):
    (tmp_path / "po").mkdir()
    (tmp_path / "po" / "de.po").write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "TBX Glossary"\nmsgstr "TBX-Glossar"\n\n'
        '#, fuzzy\nmsgid "Qt Phrase Book"\nmsgstr "Qt-Phrasenbuch"\n\n'
        'msgid "Qt Message File"\nmsgstr ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    lines = gen.build_custom_messages(["de"])
    assert "german.TbxFileType=TBX-Glossar" in lines
    assert "TbxFileType=TBX Glossary" in lines
    assert not any(line.startswith(("german.QphFileType", "german.QmFileType")) for line in lines)


def test_main_writes_bom_and_rejects_unmapped(gen, tmp_path, monkeypatch):
    out = tmp_path / "installer-strings.iss"
    monkeypatch.setattr(gen, "read_linguas", lambda: ["de"])
    monkeypatch.setattr(sys, "argv", ["gen", str(out)])
    gen.main()
    assert out.read_bytes().startswith(b"\xef\xbb\xbf[Languages]")

    monkeypatch.setattr(gen, "read_linguas", lambda: ["xx"])
    with pytest.raises(SystemExit) as exc:
        gen.main()
    assert exc.value.code == 1


def test_a_new_language_is_offered_once_po_linguas_lite_lists_it(gen):
    assert gen.installer_languages(["de", "zu"]) == ["de"]
    assert gen.installer_languages(["de", "zu"], {"zu"}) == ["de", "zu"]
    include = gen.build_include(["de", "zu", "ast"], {"zu", "ast"})
    assert 'Name: "zulu"; MessagesFile: "generated-languages\\Zulu.isl"' in include
    assert 'Name: "asturian"; MessagesFile: "generated-languages\\Asturian.isl"' in include
    assert 'Name: "german"; MessagesFile: "languages\\German.isl"' in include


def test_read_lite_takes_only_inno_lines(gen, tmp_path, monkeypatch):
    (tmp_path / "po").mkdir()
    (tmp_path / "po" / "LINGUAS-lite").write_text("gtk30/zu\ninno/ast\ninno/zu\n", encoding="utf-8")
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    assert gen.read_lite() == {"ast", "zu"}


def _lite_po(tmp_path, lang, entries):
    path = tmp_path / "po" / "lite" / "inno" / (lang + ".po")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
    for context, msgid, msgstr in entries:
        text += 'msgctxt "%s"\nmsgid "%s"\nmsgstr "%s"\n\n' % (context, msgid, msgstr)
    path.write_text(text, encoding="utf-8")


def test_messages_files_for_listed_languages(gen, tmp_path, monkeypatch):
    _lite_po(tmp_path, "zu", [("ButtonNext", "&Next >", "&Okulandelayo >")])
    _lite_po(tmp_path, "ast", [("ButtonCancel", "Cancel", "Encaboxar")])
    _lite_po(tmp_path, "de", [("ButtonCancel", "Cancel", "Nein")])
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    out = tmp_path / "generated"

    assert gen.write_messages_files(["ast", "de", "zu"], {"ast", "zu"}, out) == []

    assert sorted(p.name for p in out.iterdir()) == ["Asturian.isl", "Zulu.isl"]
    zulu = gen.inno_lite.read(out / "Zulu.isl")
    assert "LanguageName=isiZulu\nLanguageID=$0435\n" in zulu.replace("\r\n", "\n")
    assert "ButtonNext=&Okulandelayo >" in zulu
    asturian = gen.inno_lite.parse(gen.inno_lite.read(out / "Asturian.isl"))
    assert asturian["Messages"]["ButtonCancel"].value == "Encaboxar"
    # The rest of the vendored file, decoded from its code page.
    assert asturian["LangOptions"]["LanguageName"].value == "Asturianu"
    assert asturian["Messages"]["ButtonYes"].value == "&S\u00ed"


def test_vendored_translations_are_the_wizard_messages_it_has(gen):
    german = gen.vendored_translations("de")
    assert german[("ButtonNext", "&Next >")] == "&Weiter >"
    assert gen.vendored_translations("zu") == {}


def test_main_writes_messages_files_and_warns_of_a_stale_template(
    gen, tmp_path, monkeypatch, capsys
):
    out = tmp_path / "installer-strings.iss"
    (tmp_path / "generated-languages").mkdir()
    (tmp_path / "generated-languages" / "Old.isl").write_text("", encoding="utf-8")
    default = tmp_path / "Default.isl"
    default.write_text("[Messages]\nButtonNext=&Next >\n", encoding="utf-8")
    _lite_po(tmp_path, "zu", [("ButtonNext", "&Next >", "&Okulandelayo >")])
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gen, "read_linguas", lambda: ["de", "zu"])

    gen.main([str(out), "--every-lite", "--default-isl", str(default)])

    assert 'Name: "zulu"' in out.read_text(encoding="utf-8-sig")
    assert sorted(p.name for p in (tmp_path / "generated-languages").iterdir()) == ["Zulu.isl"]
    printed = capsys.readouterr().out
    assert "::warning title=Installer lite template out of date::" in printed
    assert "ButtonBack" in printed and "ButtonNext," not in printed
