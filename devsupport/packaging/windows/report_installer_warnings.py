#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Reports what an Inno Setup compile (ISCC's output, saved by
build_installer.ps1) says about the installer's languages: the
compiler's version, and each language's English fallbacks - the
messages its messages file or virtaal.iss's [CustomMessages] doesn't
define, which the compiler takes from English and warns about.

Prints Markdown, also appended to $GITHUB_STEP_SUMMARY when set. Never
fails: a fallback is for translators to fill (see
po/lite/inno/README.md), not a build error.

Usage: report_installer_warnings.py ISCC_LOG
"""

import importlib.util
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION_RE = re.compile(r"Compiler engine version: (.+)")
WARNING_RE = re.compile(r"Warning: (.*)")
# Compiler.Messages.pas's SCompilerMessagesMissingMessageWarning and
# SCompilerCustomMessagesMissingLangWarning.
FALLBACK_RES = (
    re.compile(r'A message named "([^"]+)" has not been defined for the "([^"]+)" language'),
    re.compile(r'Custom message "([^"]+)" has not been defined for the "([^"]+)" language'),
)


def _installer():
    spec = importlib.util.spec_from_file_location(
        "generate_installer_translations", HERE / "generate_installer_translations.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_log(path):
    """ISCC's output: UTF-8, or UTF-16 as Windows PowerShell's Tee-Object
    writes it."""
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8-sig", errors="replace")


def parse(text):
    """(compiler version or None, {language: [message, ...]} of English
    fallbacks, [every other warning])."""
    version, fallbacks, others = None, {}, []
    for line in text.splitlines():
        match = VERSION_RE.search(line)
        if match:
            version = match.group(1).strip()
        match = WARNING_RE.search(line)
        if not match:
            continue
        for pattern in FALLBACK_RES:
            fallback = pattern.search(match.group(1))
            if fallback:
                fallbacks.setdefault(fallback.group(2), []).append(fallback.group(1))
                break
        else:
            others.append(match.group(1).strip())
    return version, fallbacks, others


def kinds():
    """{message: kind}: the wizard messages po/lite/inno translates, and
    virtaal.iss's own, translated in po/<lang>.po."""
    installer = _installer()
    found = dict.fromkeys(installer.MESSAGE_KEY_TO_MSGID, "virtaal")
    found.update(dict.fromkeys(installer.inno_lite.SECTION, "wizard"))
    return found, {installer.inno_name(code): code for code in installer.INNO_LANGUAGES} | {
        installer.inno_name(code): code for code in installer.NEW_LANGUAGES
    }


def _keys(messages):
    return ", ".join("`%s`" % key for key in sorted(messages)) or "-"


def _rows(fallbacks, kind, codes):
    """A table row per language, and the details lines listing every
    fallback."""
    rows, details = [], []
    for language, messages in sorted(fallbacks.items()):
        groups = {"wizard": [], "virtaal": [], None: []}
        for message in messages:
            groups[kind.get(message)].append(message)
        name = "%s (%s)" % (codes[language], language) if language in codes else language
        rows.append(
            "| %s | %s | %s | %d |"
            % (name, _keys(groups["wizard"]), _keys(groups["virtaal"]), len(groups[None]))
        )
        details.append("- %s: %s" % (name, _keys(messages)))
    return rows, details


def markdown(version, fallbacks, others, kind=None, codes=None):
    """The job summary: the compiler, a row per language with English
    fallbacks, then every fallback and every other warning."""
    kind, codes = (kind, codes) if kind is not None else kinds()
    lines = [
        "## Installer languages - %s" % (version or "Inno Setup, version not found"),
        "",
        "Messages each installer language shows in English, the compiler's fallback: the wizard's"
        " (`po/lite/inno/<lang>.po`), Virtaal's own (`po/<lang>.po`), and the rest of Inno's,"
        " which aren't in the wizard's list.",
        "",
    ]
    if not fallbacks:
        lines += ["No language falls back to English.", ""]
    else:
        rows, details = _rows(fallbacks, kind, codes)
        lines += [
            "| Language | Wizard | Virtaal's own | Other Inno messages |",
            "|---|---|---|---|",
        ]
        lines += rows + ["", "<details><summary>Every fallback, by language</summary>", ""]
        lines += details + ["", "</details>", ""]
    if others:
        lines += ["<details><summary>Other compiler warnings (%d)</summary>" % len(others), ""]
        lines += ["- %s" % warning for warning in others]
        lines += ["", "</details>", ""]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: report_installer_warnings.py ISCC_LOG", file=sys.stderr)
        return 0
    text = markdown(*parse(read_log(argv[0])))
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
