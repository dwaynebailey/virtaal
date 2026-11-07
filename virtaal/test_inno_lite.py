#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import importlib.util
import os

import pytest

WINDOWS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "devsupport",
    "packaging",
    "windows",
)


@pytest.fixture(scope="module")
def inno():
    spec = importlib.util.spec_from_file_location(
        "inno_lite", os.path.join(WINDOWS_DIR, "inno_lite.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _default_isl(inno, path, changed=None):
    """A Default.isl with the committed template's English (changed:
    {key: other English})."""
    sections = {}
    for key, msgid in inno.template_messages().items():
        value = (changed or {}).get(key, msgid)
        sections.setdefault(inno.SECTION[key], []).append("%s=%s" % (key, inno.to_isl(value)))
    lines = ["; *** Inno Setup version 9.9.9+ English messages ***", ""]
    for section, entries in sections.items():
        lines += ["[%s]" % section, "; *** A heading", "; used for example as 'X'"] + entries + [""]
    path.write_text("\r\n".join(lines), encoding="utf-8", newline="")
    return path


def test_decode_reads_bom_utf8_and_code_page_files(inno):
    text = "[LangOptions]\r\nLanguageName=Asturianu\r\n[Messages]\r\nButtonNext=&Siguiente >\r\n"
    assert inno.decode(inno.BOM + text.encode("utf-8")) == text
    assert inno.decode(text.encode("utf-8")) == text
    # Not UTF-8, no LanguageCodePage: 1252, as Asturian.isl is.
    assert inno.decode("Prémi".encode("cp1252")) == "Prémi"
    greek = "LanguageCodePage=1253\r\nButtonNext=Επόμενο\r\n"
    assert inno.decode(greek.encode("cp1253")) == greek


def test_parse_keeps_each_entrys_own_comments(inno):
    sections = inno.parse(
        "; credits\n\n[Messages]\n; *** Buttons\n; used for example as 'Run X'\n"
        "RunEntryExec = Run %1\n\nButtonOK=OK\n[CustomMessages]\nLaunchProgram=Launch %1\n"
    )
    assert sections["Messages"]["RunEntryExec"] == ("Run %1", ["used for example as 'Run X'"])
    assert sections["Messages"]["ButtonOK"] == ("OK", [])
    assert sections["CustomMessages"]["LaunchProgram"].value == "Launch %1"


def test_credits_are_the_comments_above_the_first_section(inno):
    text = "; *** Afrikaans messages ***\n;\n; Created by: Leon\n\n[LangOptions]\n; not this\n"
    assert inno.credits(text) == ["*** Afrikaans messages ***", "", "Created by: Leon"]


def test_line_breaks_are_newlines_in_po(inno):
    value = "The folder:%n%n%1%n%nalready exists."
    assert inno.to_po(value) == "The folder:\n\n%1\n\nalready exists."
    assert inno.to_isl(inno.to_po(value)) == value
    assert inno.to_isl("a\r\nb") == "a%nb"


def test_wizard_list_has_each_key_once_and_the_template_all_of_them(inno):
    keys = [key for _section, _where, group in inno.WIZARD_MESSAGES for key in group]
    assert len(keys) == len(set(keys))
    assert list(inno.template_messages()) == keys


def test_template_is_the_list_with_notes_and_checker_hints(inno, tmp_path):
    from translate.storage import pypo

    out = tmp_path / "inno.pot"
    inno.write_template(_default_isl(inno, tmp_path / "Default.isl"), out)

    store = pypo.pofile(out.read_bytes())
    assert inno.template_messages(out) == inno.template_messages()
    header = store.parseheader()
    assert header["X-Accelerator-Marker"] == "&"
    assert header["X-Project-Style"] == "qt"
    assert header["Project-Id-Version"] == "Inno Setup 9.9.9+ wizard"
    units = {unit.getcontext(): unit for unit in store.units if not unit.isheader()}
    notes = units["SelectDirDesc"].getnotes("developer")
    assert "The Select Destination Location page" in notes
    assert inno.NAME_NOTE in notes
    # Default.isl's own comment, not its heading.
    notes = units["SetupAppTitle"].getnotes("developer")
    assert "used for example as 'X'" in notes
    assert "*** A heading" not in notes
    assert units["LaunchProgram"].hastypecomment("qt-format")
    assert inno.ARGUMENT_NOTE in units["LaunchProgram"].getnotes("developer")
    assert not units["ButtonNext"].hastypecomment("qt-format")


def test_template_needs_every_listed_message(inno, tmp_path):
    default = tmp_path / "Default.isl"
    default.write_text("[Messages]\nButtonNext=&Next >\n", encoding="utf-8")
    with pytest.raises(KeyError, match="SetupAppTitle"):
        inno.write_template(default, tmp_path / "inno.pot")


def test_stale_names_messages_whose_english_changed(inno, tmp_path):
    assert inno.stale(_default_isl(inno, tmp_path / "Default.isl")) == []
    changed = _default_isl(inno, tmp_path / "New.isl", {"ButtonNext": "Next"})
    assert inno.stale(changed) == ["ButtonNext"]


def test_translations_are_keyed_by_context_and_template_msgid(inno):
    found = inno.translations(
        "[Messages]\nButtonNext=&Volgende >\nDirExists=Die gids:%n%n%1\nButtonBack=\n"
        "[CustomMessages]\nLaunchProgram=Begin %1\nNotListed=x\n"
    )
    assert found == {
        ("ButtonNext", "&Next >"): "&Volgende >",
        ("DirExists", inno.template_messages()["DirExists"]): "Die gids:\n\n%1",
        ("LaunchProgram", "Launch %1"): "Begin %1",
    }


def _catalog(path, entries):
    """entries: (msgctxt, msgid, msgstr, fuzzy)."""
    text = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
    for context, msgid, msgstr, fuzzy in entries:
        text += "%smsgctxt %s\nmsgid %s\nmsgstr %s\n\n" % (
            "#, fuzzy\n" if fuzzy else "",
            _quote(context),
            _quote(msgid),
            _quote(msgstr),
        )
    path.write_text(text, encoding="utf-8")
    return path


def _quote(text):
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def test_lite_messages_skip_untranslated_fuzzy_and_broken_ones(inno, tmp_path):
    po = _catalog(
        tmp_path / "zu.po",
        [
            ("ButtonNext", "&Next >", "&Okulandelayo >", False),
            ("ButtonBack", "< &Back", "< &Emuva", True),
            ("ButtonCancel", "Cancel", "", False),
            ("LaunchProgram", "Launch %1", "Qalisa", False),
            ("DirExistsTitle", "Folder Exists", "Ifolda ikhona", False),
            ("NotListed", "x", "y", False),
        ],
    )
    messages, warnings = inno.lite_messages(po)
    assert messages == {
        ("Messages", "ButtonNext"): "&Okulandelayo >",
        ("Messages", "DirExistsTitle"): "Ifolda ikhona",
    }
    assert len(warnings) == 1
    assert "LaunchProgram keeps English" in warnings[0]
    assert "%1" in warnings[0]


def test_build_merges_lite_into_the_vendored_file(inno, tmp_path):
    vendored = tmp_path / "Asturian.isl"
    vendored.write_bytes(
        "; Asturian\r\n[LangOptions]\r\nLanguageName=Asturianu\r\n\r\n[Messages]\r\n"
        "ButtonNext=&Siguiente >\r\nButtonBack=< &Atrás\r\n\r\n".encode("cp1252")
    )
    po = _catalog(
        tmp_path / "ast.po",
        [
            ("ButtonNext", "&Next >", "&Siguiente >", False),
            ("ButtonBack", "< &Back", "< &Tornar", False),
            ("ButtonCancel", "Cancel", "Encaboxar", False),
            ("DirExists", "x %1", "La carpeta:\n\n%1", False),
            ("LaunchProgram", "Launch %1", "Executar %1", False),
        ],
    )
    text, warnings = inno.build(po, vendored)
    lines = text.splitlines()
    assert warnings == []
    assert lines[0].startswith("; Generated by generate_installer_translations.py from")
    assert "languages\\Asturian.isl" in lines[0]
    assert lines[1:5] == ["; Asturian", "[LangOptions]", "LanguageName=Asturianu", ""]
    assert lines[5:10] == [
        "[Messages]",
        "ButtonNext=&Siguiente >",
        "ButtonBack=< &Tornar",
        "ButtonCancel=Encaboxar",
        "DirExists=La carpeta:%n%n%1",
    ]
    assert lines[10:] == ["", "[CustomMessages]", "LaunchProgram=Executar %1", ""]


def test_build_writes_a_new_language_from_its_options(inno, tmp_path):
    po = _catalog(tmp_path / "zu.po", [("ButtonNext", "&Next >", "&Okulandelayo >", False)])
    text, _warnings = inno.build(po, options=inno.LangOptions("isiZulu", 0x0435, 1252))
    assert text.splitlines()[1:] == [
        "[LangOptions]",
        "LanguageName=isiZulu",
        "LanguageID=$0435",
        "LanguageCodePage=1252",
        "",
        "[Messages]",
        "ButtonNext=&Okulandelayo >",
        "",
    ]


def test_write_is_utf8_with_a_bom_and_crlf(inno, tmp_path):
    inno.write(tmp_path / "out" / "Zulu.isl", "[Messages]\nButtonNext=&Okulandelayo >\n")
    assert (tmp_path / "out" / "Zulu.isl").read_bytes() == (
        inno.BOM + b"[Messages]\r\nButtonNext=&Okulandelayo >\r\n"
    )


def test_main_writes_the_template(inno, tmp_path, monkeypatch, capsys):
    default, out = _default_isl(inno, tmp_path / "Default.isl"), tmp_path / "inno.pot"
    monkeypatch.setattr(inno, "TEMPLATE", out)
    assert inno.main([str(default)]) == 0
    assert inno.template_messages() == inno.template_messages(
        inno.REPO_ROOT / "po" / "lite" / "inno" / "inno.pot"
    )
    assert inno.main([]) == 1
