#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import importlib.util
import os

import pytest

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "devsupport",
    "packaging",
    "windows",
    "report_installer_warnings.py",
)

# What ISCC prints, its compiler's English (Compiler.Messages.pas).
LOG = """\
Inno Setup 7 (32-bit) Command-Line Compiler
Compiler engine version: Inno Setup 7.1.1
Licensee name: Unlicensed

Successful compile (51.234 sec). Resulting Setup program filename is:
D:\\a\\virtaal\\dist\\installer\\virtaal-1.1.0-setup.exe

Warning: A message named "ButtonNext" has not been defined for the "asturian" language. Will use the English message from Default.isl.
Warning: A message named "ArchiveIsCorrupted" has not been defined for the "asturian" language. Will use the English message from Default.isl.
Warning: Custom message "FileAssocTask" has not been defined for the "asturian" language. Will use the custom message from the first language in which it was defined: "english".
Warning: A message named "ArchiveIsCorrupted" has not been defined for the "zulu" language. Will use the English message from Default.isl.
Warning: Message name "WizardWelcome" in "languages\\Asturian.isl" is not recognized by this version of Inno Setup. Ignoring.
"""


@pytest.fixture
def report():
    spec = importlib.util.spec_from_file_location("report_installer_warnings", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_finds_the_version_and_each_languages_fallbacks(report):
    version, fallbacks, others = report.parse(LOG)

    assert version == "Inno Setup 7.1.1"
    assert fallbacks == {
        "asturian": ["ButtonNext", "ArchiveIsCorrupted", "FileAssocTask"],
        "zulu": ["ArchiveIsCorrupted"],
    }
    assert others == [
        'Message name "WizardWelcome" in "languages\\Asturian.isl" is not recognized by this'
        " version of Inno Setup. Ignoring."
    ]


def test_markdown_sorts_fallbacks_into_wizard_virtaal_and_the_rest(report):
    text = report.markdown(*report.parse(LOG))

    assert text.startswith("## Installer languages - Inno Setup 7.1.1\n")
    assert "| ast (asturian) | `ButtonNext` | `FileAssocTask` | 1 |" in text
    assert "| zu (zulu) | - | - | 1 |" in text
    assert "- ast (asturian): `ArchiveIsCorrupted`, `ButtonNext`, `FileAssocTask`" in text
    assert "Other compiler warnings (1)" in text


def test_markdown_without_fallbacks_or_a_version(report):
    text = report.markdown(None, {}, [], {}, {})

    assert "Inno Setup, version not found" in text
    assert "No language falls back to English." in text
    assert "Other compiler warnings" not in text


def test_read_log_takes_windows_powershells_utf16(report, tmp_path):
    (tmp_path / "a.log").write_bytes(LOG.encode("utf-16"))
    (tmp_path / "b.log").write_bytes(LOG.encode("utf-8"))

    assert report.read_log(tmp_path / "a.log") == report.read_log(tmp_path / "b.log") == LOG


def test_main_writes_the_job_summary_and_never_fails(report, tmp_path, monkeypatch, capsys):
    (tmp_path / "iscc.log").write_text(LOG, encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))

    assert report.main([str(tmp_path / "iscc.log")]) == 0
    assert report.main([]) == 0

    assert "## Installer languages" in (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "| ast (asturian) |" in capsys.readouterr().out
