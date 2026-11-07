#!/usr/bin/env python3
#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The installer wizard's lite catalogs (po/lite/inno/): Inno Setup
messages files (.isl) <-> PO.

Each message is a PO message: its Inno key as msgctxt, its English
(Default.isl's) as msgid. Inno's "%n" line break is a real newline in
PO, so translators see it and the newline checks catch a lost one;
"%1" arguments are flagged qt-format, which msgfmt -c checks, and "&"
is the accelerator marker (X-Accelerator-Marker; X-Project-Style qt for
Virtaal's own checks).

The template holds only WIZARD_MESSAGES, the messages virtaal.iss's
wizard shows. To rewrite it from the Default.isl of an Inno Setup
install:

    python devsupport/packaging/windows/inno_lite.py "C:\\Program Files (x86)\\Inno Setup 7\\Default.isl"

then refresh the catalogs from the vendored languages\\*.isl with
update_lite_catalogs.py inno.
"""

import re
import sys
from collections import namedtuple
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / "po" / "lite" / "inno" / "inno.pot"
BOM = b"\xef\xbb\xbf"

# The messages our installer shows: (Default.isl section, where the
# wizard shows them - each message's note for translators - keys).
# Worked out from virtaal.iss's [Setup] directives, Inno's defaults for
# the ones it leaves out, and its sections; its [Code] shows no messages
# of its own (only Log()). Left out: pages and features it doesn't use
# (Welcome, License, Password, Information, User Information,
# Components, disk spanning, downloads, archives), the About Setup menu
# item, and the rare error boxes. To add one, add its key to a group,
# or a new group, then rerun this script and update_lite_catalogs.py
# inno.
WIZARD_MESSAGES = (
    # Every page.
    (
        "Messages",
        "Setup's title bar and taskbar button.",
        ("SetupAppTitle", "SetupWindowTitle"),
    ),
    (
        "Messages",
        "A button on the wizard's pages or its message boxes.",
        (
            "ButtonBack",
            "ButtonNext",
            "ButtonInstall",
            "ButtonOK",
            "ButtonCancel",
            "ButtonYes",
            "ButtonNo",
            "ButtonFinish",
            "ButtonWizardBrowse",
        ),
    ),
    # ShowLanguageDialog=yes, Inno's default: shown first.
    (
        "Messages",
        "The Select Setup Language dialog, shown first.",
        ("SelectLanguageTitle", "SelectLanguageLabel"),
    ),
    # PrivilegesRequired=lowest with PrivilegesRequiredOverridesAllowed=
    # dialog: Text2 and "me only (recommended)", not Text1 and "all users
    # (recommended)".
    (
        "Messages",
        "The dialog asking whether to install for you only or for all users,"
        " shown after the language.",
        (
            "PrivilegesRequiredOverrideTitle",
            "PrivilegesRequiredOverrideInstruction",
            "PrivilegesRequiredOverrideText2",
            "PrivilegesRequiredOverrideCurrentUserRecommended",
            "PrivilegesRequiredOverrideAllUsers",
        ),
    ),
    (
        "Messages",
        "The question when you cancel Setup.",
        ("ExitSetupTitle", "ExitSetupMessage"),
    ),
    # DisableDirPage=auto, Inno's default: a first install. DiskSpaceGBLabel
    # is for an install of 1 GB or more. DirExistsWarning=auto: a folder
    # that isn't the previous install's.
    (
        "Messages",
        "The Select Destination Location page, its Browse dialog and the"
        " question when the chosen folder exists.",
        (
            "WizardSelectDir",
            "SelectDirDesc",
            "SelectDirLabel3",
            "SelectDirBrowseLabel",
            "DiskSpaceMBLabel",
            "BrowseDialogTitle",
            "BrowseDialogLabel",
            "ButtonNewFolder",
            "NewFolderName",
            "DirExistsTitle",
            "DirExists",
        ),
    ),
    # [Icons] makes a Start menu folder; DisableProgramGroupPage=auto,
    # Inno's default: a first install. No AllowNoIcons, so no "Don't
    # create a Start menu folder".
    (
        "Messages",
        "The Select Start Menu Folder page.",
        (
            "WizardSelectProgramGroup",
            "SelectStartMenuFolderDesc",
            "SelectStartMenuFolderLabel3",
            "SelectStartMenuFolderBrowseLabel",
        ),
    ),
    # [Tasks]: file associations, the desktop shortcut, uninstalling 0.7.
    (
        "Messages",
        "The Select Additional Tasks page.",
        ("WizardSelectTasks", "SelectTasksDesc", "SelectTasksLabel2"),
    ),
    # [Tasks] desktopicon's Description and GroupDescription.
    (
        "CustomMessages",
        "The Select Additional Tasks page: the desktop shortcut, and the heading above it.",
        ("AdditionalIcons", "CreateDesktopIcon"),
    ),
    # Shown by default (DisableReadyPage=no); the memo lists the folder,
    # the Start menu folder and the tasks chosen.
    (
        "Messages",
        "The Ready to Install page, and its summary of what you chose.",
        (
            "WizardReady",
            "ReadyLabel1",
            "ReadyLabel2a",
            "ReadyMemoDir",
            "ReadyMemoGroup",
            "ReadyMemoTasks",
        ),
    ),
    # CloseApplications=yes and RestartApplications=yes, Inno's defaults:
    # it stops here when Virtaal is running during an upgrade, with
    # ApplicationsFound2, not ApplicationsFound. PrepareToInstall in
    # [Code] never returns a message.
    (
        "Messages",
        "The Preparing to Install page, shown when Virtaal is running while you upgrade it.",
        (
            "WizardPreparing",
            "PreparingDesc",
            "ApplicationsFound2",
            "CloseApplications",
            "DontCloseApplications",
            "ErrorCloseApplications",
        ),
    ),
    # Progress for what virtaal.iss does: closing a running Virtaal,
    # [InstallDelete] and [Files], [Icons], [Registry], the uninstaller,
    # restarting Virtaal, and undoing it all on Cancel.
    (
        "Messages",
        "The Installing page, and its progress messages.",
        (
            "WizardInstalling",
            "InstallingLabel",
            "StatusClosingApplications",
            "StatusCreateDirs",
            "StatusExtractFiles",
            "StatusCreateIcons",
            "StatusCreateRegistryEntries",
            "StatusSavingUninstall",
            "StatusRunProgram",
            "StatusRestartingApplications",
            "StatusRollback",
        ),
    ),
    # FinishedLabel, not FinishedLabelNoIcons: [Icons] always makes
    # shortcuts.
    (
        "Messages",
        "The Setup Completed page.",
        ("FinishedHeadingLabel", "FinishedLabel", "ClickFinish"),
    ),
    # [Run]'s postinstall Description, and [Icons]' uninstall shortcut.
    (
        "CustomMessages",
        "The Setup Completed page's box to start Virtaal, and the uninstall"
        " shortcut in the Start menu.",
        ("LaunchProgram", "UninstallProgram"),
    ),
    (
        "Messages",
        "Uninstalling: the question, the progress window and the result.",
        (
            "UninstallAppTitle",
            "UninstallAppFullTitle",
            "ConfirmUninstall",
            "WizardUninstalling",
            "UninstallStatusLabel",
            "StatusUninstalling",
            "UninstalledAll",
            "UninstalledMost",
        ),
    ),
    # PrivilegesRequiredOverridesAllowed: with an install for each mode,
    # Inno marks each one's name. The installer is always 32-bit, so the
    # bitness marks never show.
    (
        "Messages",
        "Virtaal's name in Windows' list of installed apps when it is"
        " installed both for you only and for all users.",
        (
            "UninstallDisplayNameMark",
            "UninstallDisplayNameMarkAllUsers",
            "UninstallDisplayNameMarkCurrentUser",
        ),
    ),
)

SECTION = {key: section for section, _where, keys in WIZARD_MESSAGES for key in keys}
SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
ENTRY_RE = re.compile(r"^\s*([A-Za-z0-9_.]+)\s*=(.*)$")
# Inno's numbered arguments, and the [name]-style values its wizard
# fills in. A translation must keep the same ones.
PLACEHOLDER_RE = re.compile(r"%\d|\[(?:name|name/ver|mb|gb|kb)\]")
NAME_NOTE = "[name] and [name/ver] are Virtaal's name (and version): keep them as they are."
ARGUMENT_NOTE = "Setup fills in %1 (and %2, ...): keep each one."

# A language's [LangOptions] when it has no Inno messages file of its own.
LangOptions = namedtuple("LangOptions", "name language_id codepage")

Entry = namedtuple("Entry", "value comments")


def decode(data):
    """An .isl file's text: UTF-8, with or without a BOM (as .islu files
    and Inno's own are), else in its LanguageCodePage (1252 if 0 or
    unset: Asturian.isl has none)."""
    if data.startswith(BOM):
        return data[len(BOM) :].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        match = re.search(rb"^\s*LanguageCodePage\s*=\s*(\d+)", data, re.MULTILINE)
        codepage = int(match.group(1)) if match else 0
        return data.decode("cp%d" % (codepage or 1252))


def read(path):
    return decode(Path(path).read_bytes())


def parse(text):
    """{section: {key: Entry(value, comments)}}: each entry with the
    comment lines right above it."""
    sections, section, comments = {}, None, []
    for line in text.splitlines():
        stripped = line.strip()
        match = SECTION_RE.match(line)
        if match:
            section, comments = sections.setdefault(match.group(1), {}), []
        elif stripped.startswith(";"):
            # Not "; *** Buttons": a heading over a group of messages.
            if not stripped[1:].strip().startswith("***"):
                comments.append(stripped[1:].strip())
        elif not stripped:
            comments = []
        elif section is not None and ENTRY_RE.match(line):
            key, value = ENTRY_RE.match(line).groups()
            section[key] = Entry(value.strip(), comments)
            comments = []
    return sections


def credits(text):
    """The comments above an .isl file's first section, as they are:
    who translated it, and their notes."""
    lines = []
    for line in text.splitlines():
        if SECTION_RE.match(line):
            break
        if line.strip():
            lines.append(re.sub(r"^\s*;\s?", "", line).rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return lines


def to_po(value):
    """An .isl message as PO text: %n is a newline."""
    return value.replace("%n", "\n")


def to_isl(text):
    """PO text as an .isl message value."""
    return text.replace("\r\n", "\n").replace("\n", "%n")


def placeholders(text):
    return sorted(PLACEHOLDER_RE.findall(text))


def translations(text):
    """{(msgctxt, msgid): translation} of the wizard messages an .isl
    file translates, msgid being the template's."""
    sections = parse(text)
    template = template_messages()
    found = {}
    for key, msgid in template.items():
        if _value(sections, key):
            found[(key, msgid)] = to_po(_value(sections, key))
    return found


def template_messages(path=None):
    """{key: msgid} of the template."""
    from translate.storage import pypo

    with open(path or TEMPLATE, "rb") as f:
        store = pypo.pofile(f)
    return {unit.getcontext(): str(unit.source) for unit in store.units if not unit.isheader()}


def _header(store, project):
    store.updateheader(
        add=True,
        Project_Id_Version=project,
        Content_Type="text/plain; charset=UTF-8",
        X_Accelerator_Marker="&",
        X_Project_Style="qt",
    )


def start_catalog(store, isl_text=None):
    """A new lite catalog's header: the template's fields, and, from its
    vendored .isl file, its translators as header comments."""
    _header(store, "Inno Setup wizard lite")
    if isl_text:
        for line in credits(isl_text):
            store.header().addnote(line, origin="translator")


def write_template(default_isl, path=None):
    """Writes the template: WIZARD_MESSAGES' English from default_isl,
    each with where it shows and Default.isl's own comments."""
    from translate.storage import pypo

    text = read(default_isl)
    sections = parse(text)
    version = re.search(r"Inno Setup version (\S+) English", text)
    store = pypo.pofile()
    _header(store, "Inno Setup %s wizard" % (version.group(1) if version else ""))
    for section, where, keys in WIZARD_MESSAGES:
        for key in keys:
            entry = sections.get(section, {}).get(key)
            if entry is None:
                raise KeyError("%s has no [%s] %s" % (default_isl, section, key))
            unit = store.addsourceunit(to_po(entry.value))
            unit.setcontext(key)
            arguments = re.search(r"%\d", entry.value)
            notes = [where] + entry.comments
            notes += [NAME_NOTE] if "[name" in entry.value else []
            notes += [ARGUMENT_NOTE] if arguments else []
            for note in notes:
                unit.addnote(note, origin="developer")
            if arguments:
                unit.settypecomment("qt-format")
    path = Path(path or TEMPLATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        store.serialize(f)


def _value(sections, key):
    """key's value in parse()'s sections, or ""."""
    entry = sections.get(SECTION.get(key, "Messages"), {}).get(key)
    return entry.value if entry is not None else ""


def stale(default_isl, path=None):
    """The template's keys whose English default_isl changed or lacks."""
    sections = parse(read(default_isl))
    return sorted(
        key
        for key, msgid in template_messages(path).items()
        if to_po(_value(sections, key)) != msgid
    )


def lite_messages(po_path):
    """{(section, key): .isl value} of a lite catalog's translations,
    and a warning for each one left out because it doesn't keep the
    English's %1 and [name] placeholders."""
    from translate.storage import pypo

    with open(po_path, "rb") as f:
        store = pypo.pofile(f)
    found, warnings = {}, []
    for unit in store.units:
        key = unit.getcontext()
        if unit.isheader() or unit.isfuzzy() or not unit.istranslated() or key not in SECTION:
            continue
        if placeholders(str(unit.target)) != placeholders(str(unit.source)):
            warnings.append(
                "%s: %s keeps English: its translation doesn't have %s"
                % (po_path, key, " ".join(placeholders(str(unit.source))) or "no placeholders")
            )
            continue
        found[(SECTION[key], key)] = to_isl(str(unit.target))
    return found, warnings


def _merge_lines(lines, messages):
    """lines with each of messages' entries replaced, or added at the
    end of its section; a section the file lacks is added at its end."""
    out, section, done = [], None, set()

    def close(section):
        missing = [k for (s, k) in messages if s == section and (s, k) not in done]
        while out and not out[-1].strip():
            out.pop()
        out.extend("%s=%s" % (key, messages[(section, key)]) for key in missing)
        done.update((section, key) for key in missing)
        out.append("")

    for line in lines:
        match, entry = SECTION_RE.match(line), ENTRY_RE.match(line)
        if match:
            if section is not None:
                close(section)
            section = match.group(1)
        elif entry and (section, entry.group(1)) in messages:
            line = "%s=%s" % (entry.group(1), messages[(section, entry.group(1))])
            done.add((section, entry.group(1)))
        out.append(line)
    if section is not None:
        close(section)
    for new in dict.fromkeys(s for (s, _k) in messages if (s, _k) not in done):
        out.append("[%s]" % new)
        close(new)
    return out


def build(po_path, vendored=None, options=None):
    """An .isl file's text: the vendored one's, or one for options (a
    LangOptions) when there is none, with the lite catalog's
    translations - which win - and the warnings lite_messages() gives.
    Anything neither translates is left out, so the compiler uses
    English for it, and warns."""
    messages, warnings = lite_messages(po_path)
    if vendored is not None:
        lines = read(vendored).splitlines()
    else:
        lines = [
            "[LangOptions]",
            "LanguageName=%s" % options.name,
            "LanguageID=$%04X" % options.language_id,
            "LanguageCodePage=%d" % options.codepage,
        ]
    note = "; Generated by generate_installer_translations.py from po/lite/inno/%s%s." % (
        Path(po_path).name,
        " and languages\\%s" % Path(vendored).name if vendored is not None else "",
    )
    return "\n".join([note] + _merge_lines(lines, messages)) + "\n", warnings


def write(path, text):
    """Writes an .isl file: UTF-8 with a BOM, which the compiler reads
    as UTF-8 whatever the language's code page, and CRLF line ends."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(BOM + text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: inno_lite.py PATH/TO/Default.isl", file=sys.stderr)
        return 1
    write_template(argv[0])
    print("Wrote %s" % TEMPLATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
