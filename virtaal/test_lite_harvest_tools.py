#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "devsupport", "pseudo-translation"
)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS_DIR, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def resolve():
    return _load("resolve_sources")


@pytest.fixture(scope="module")
def templates():
    return _load("write_lite_templates")


def _catalogs(resolve, domain, originals):
    entries = []
    for original in originals:
        for form in original.rpartition("\x04")[2].split("\0"):
            entries.append((original, form, resolve.msgid_pattern(form)))
    return {domain: entries}


@pytest.mark.parametrize(
    "form, shown, matches",
    [
        ("Open '%s'", "Open 'af.po'", True),
        ("%.1f KB", "1.5 KB", True),
        ("100%% done", "100% done", True),
        ("Ctrl+%(number_key)d", "Ctrl+1", True),
        # Ends at a word boundary, so composite strings resolve.
        ("_Save", "_Saved", False),
        ("_Save", "_Save", True),
    ],
)
def test_msgid_pattern(resolve, form, shown, matches):
    assert bool(resolve.msgid_pattern(form).match(shown)) == matches


def test_resolve_text_takes_the_longest_match(resolve):
    catalogs = _catalogs(resolve, "gtk30", ["_Save", "Save _As"])

    assert resolve.resolve_text("gtk:Save _As", catalogs) == [("gtk30", "Save _As")]


def test_resolve_text_reads_a_lite_gap_marker_as_its_tag(resolve):
    catalogs = _catalogs(resolve, "gtk30", ["_Help"])

    assert resolve.resolve_text("gtk!:_Help", catalogs) == [("gtk30", "_Help")]


def test_resolve_text_prefers_literal_text_over_placeholders(resolve):
    catalogs = _catalogs(resolve, "glib20", ["format-size\x04%.1f", "kB"])

    assert resolve.resolve_text("glib:kB", catalogs) == [("glib20", "kB")]


def test_resolve_text_keeps_every_context_with_the_same_text(resolve):
    catalogs = _catalogs(resolve, "gtk30", ["_Save", "Stock label\x04_Save"])

    assert sorted(resolve.resolve_text("gtk:_Save", catalogs)) == [
        ("gtk30", "Stock label\x04_Save"),
        ("gtk30", "_Save"),
    ]


def test_resolve_text_resolves_each_tag_in_a_composite_string(resolve):
    catalogs = {
        **_catalogs(resolve, "iso639-3", ["English", "Afrikaans"]),
        **_catalogs(resolve, "gtk30", ["%s / %s available"]),
    }

    assert resolve.resolve_text("vt:_1. iso:English → iso:Afrikaans", catalogs) == [
        ("iso639-3", "English"),
        ("iso639-3", "Afrikaans"),
    ]


def test_load_catalogs_matches_msgids_without_their_markup(resolve, tmp_path):
    # The harvest records labels' text, not their Pango markup.
    generator = _load("generate_pseudo_translation")
    mo_dir = tmp_path / "pseudo-source" / "LC_MESSAGES"
    mo_dir.mkdir(parents=True)
    generator.write_mo(
        str(mo_dir / "gtkspell3.mo"), {"<i>(no suggestions)</i>": "spell:<i>(no suggestions)</i>"}
    )

    catalogs = resolve.load_catalogs(str(tmp_path))

    assert resolve.resolve_text("spell:(no suggestions)", catalogs) == [
        ("gtkspell3", "<i>(no suggestions)</i>")
    ]


def test_resolve_text_resolves_virtaals_own_strings(resolve):
    assert resolve.resolve_text("vt:_Find…", _catalogs(resolve, "virtaal", ["_Find…"])) == [
        ("virtaal", "_Find…")
    ]


@pytest.mark.parametrize(
    "shown, expected",
    [
        ([False], False),
        ([False, True], True),
    ],
)
def test_resolve_marks_a_message_shown_if_any_sighting_was(resolve, shown, expected):
    # A dialog built ahead of being opened is walked while still hidden.
    harvest = {
        "strings": [
            {"text": "vt:Settings", "screen": "step %d" % i, "shown": s}
            for i, s in enumerate(shown)
        ]
    }

    result = resolve.resolve(harvest, _catalogs(resolve, "virtaal", ["Settings"]))

    assert result["virtaal"][0]["shown"] is expected


def test_resolve_text_reports_a_tag_nothing_matches(resolve):
    assert resolve.resolve_text("gtk:Unknown", _catalogs(resolve, "gtk30", ["_Save"])) == [
        ("gtk30", None)
    ]


@pytest.mark.parametrize(
    "within, window, expected",
    [
        ("Menu", "Window 'vt:Virtaal'", "menus"),
        ("", "Dialog 'vt:Add Term'", "Add Term dialog"),
        ("", "Dialog 'gtk!:About'", "About dialog"),
        ("", "MessageDialog ''", "message dialogs"),
        ("", "Window 'vt:quality-checks.po - Virtaal'", "main window"),
        ("", "Window None", "main window"),
        ("", "FileChooserDialog None", None),
    ],
)
def test_place(templates, within, window, expected):
    assert templates._place(within, window) == expected


def test_harvested_leaves_out_file_chooser_only_strings(templates, tmp_path):
    resolved = tmp_path / "resolved.json"
    resolved.write_text(
        '{"gtk30": ['
        '{"original": "Computer", "screens": [], "within": ["FileChooser"], "windows": ["FileChooserDialog None"]},'
        '{"original": "_Open", "screens": [], "within": ["", "FileChooser"], "windows": ["Window None"]}'
        '], "glib20": []}',
        encoding="utf-8",
    )

    found = templates.harvested([str(resolved)])

    assert set(found["gtk30"]) == {"_Open"}


def test_merge_keeps_each_translation_language(templates, tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "LITE_DIR", str(tmp_path))
    domain = tmp_path / "glib20"
    domain.mkdir()
    header = (
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n"Language: %s\\n"\n\n'
    )
    (domain / "glib20.pot").write_text(
        header % "templates" + 'msgid "kB"\nmsgstr ""\n', encoding="utf-8"
    )
    (domain / "af.po").write_text(header % "af" + 'msgid "kB"\nmsgstr "kG"\n', encoding="utf-8")

    templates.merge("glib20", templates.write_template("glib20", {"kB": set()}))

    assert '"Language: af\\n"' in (domain / "af.po").read_text(encoding="utf-8")


def test_write_template_replaces_where_a_message_was_seen(templates, tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "LITE_DIR", str(tmp_path))
    (tmp_path / "gtk30").mkdir()
    (tmp_path / "gtk30" / "gtk30.pot").write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        '#. Help->About\nmsgid "_About"\nmsgstr ""\n',
        encoding="utf-8",
    )

    for _ in range(2):
        path = templates.write_template("gtk30", {"_About": {"menus"}})

    text = Path(path).read_text(encoding="utf-8")
    assert "#. Help->About\n#. Seen in: menus\nmsgid" in text


def test_write_template_starts_a_new_domain(templates, tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "LITE_DIR", str(tmp_path))

    path = templates.write_template("gtk-mac-integration", {"Quit %s": set()})

    text = Path(path).read_text(encoding="utf-8")
    assert '"Project-Id-Version: gtk-mac-integration lite\\n"' in text
    assert 'msgid "Quit %s"' in text


@pytest.fixture
def lite_catalogs(tmp_path, monkeypatch):
    module = _load("update_lite_catalogs")
    monkeypatch.setattr(module, "LITE_DIR", str(tmp_path))
    monkeypatch.setattr(module, "LINGUAS", str(tmp_path / "LINGUAS-lite"))
    (tmp_path / "mac").mkdir()
    (tmp_path / "mac" / "mac.pot").write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Quit %s"\nmsgstr ""\n\nmsgid "Show All"\nmsgstr ""\n',
        encoding="utf-8",
    )
    return module, tmp_path / "mac"


def test_lite_catalog_is_retired_when_upstream_translates_everything(lite_catalogs):
    module, domain_dir = lite_catalogs
    (domain_dir / "fr.po").write_text('msgid "Show All"\nmsgstr "Tout"\n', encoding="utf-8")

    translated = module.update(
        "mac", "fr", {("", "Quit %s"): "Quitter %s", ("", "Show All"): "Tout afficher"}
    )

    assert translated == 0
    assert not (domain_dir / "fr.po").exists()


def test_lite_catalog_keeps_its_translations_and_fills_in_upstreams(lite_catalogs):
    from translate.storage import factory

    module, domain_dir = lite_catalogs
    (domain_dir / "af.po").write_text(
        'msgid ""\nmsgstr ""\n"Language: af\\n"\n\nmsgid "Show All"\nmsgstr "Wys alles"\n',
        encoding="utf-8",
    )

    translated = module.update("mac", "af", {("", "Quit %s"): "Verlaat %s"})

    targets = {
        str(u.source): str(u.target)
        for u in factory.getobject(str(domain_dir / "af.po")).units
        if not u.isheader()
    }
    # Only "Show All" fills a gap: upstream already has "Quit %s".
    assert translated == 1
    assert targets == {"Quit %s": "Verlaat %s", "Show All": "Wys alles"}


def test_lite_catalog_filling_no_gap_is_kept_but_counts_nothing(lite_catalogs):
    module, domain_dir = lite_catalogs

    translated = module.update("mac", "ja", {("", "Quit %s"): "%s を終了"})

    assert translated == 0
    assert (domain_dir / "ja.po").exists()


def test_lite_catalog_keeps_fuzzy_work_and_translator_comments(lite_catalogs):
    from translate.storage import factory

    module, domain_dir = lite_catalogs
    (domain_dir / "am.po").write_text(
        'msgid ""\nmsgstr ""\n"Language: am\\n"\n\n'
        '# (review) capitals\nmsgid "Show All"\nmsgstr "Wys alles"\n\n'
        '#, fuzzy\nmsgid "Quit %s"\nmsgstr "Verlaat %s"\n',
        encoding="utf-8",
    )

    translated = module.update("mac", "am", {})

    units = {
        str(u.source): u
        for u in factory.getobject(str(domain_dir / "am.po")).units
        if not u.isheader()
    }
    assert translated == 1
    assert units["Show All"].getnotes("translator") == "(review) capitals"
    assert units["Quit %s"].isfuzzy() and str(units["Quit %s"].target) == "Verlaat %s"


def test_lite_catalog_takes_upstream_over_its_fuzzy_work(lite_catalogs):
    from translate.storage import factory

    module, domain_dir = lite_catalogs
    (domain_dir / "am.po").write_text(
        '#, fuzzy\nmsgid "Quit %s"\nmsgstr "Verlaat %s"\n', encoding="utf-8"
    )

    module.update("mac", "am", {("", "Quit %s"): "Verlaat tog %s"})

    units = {
        str(u.source): u
        for u in factory.getobject(str(domain_dir / "am.po")).units
        if not u.isheader()
    }
    assert not units["Quit %s"].isfuzzy() and str(units["Quit %s"].target) == "Verlaat tog %s"


def test_linguas_lists_only_lite_catalogs_that_translate_something(lite_catalogs):
    module, domain_dir = lite_catalogs
    linguas = domain_dir.parent / "LINGUAS-lite"
    linguas.write_text("gtk30/zu\nmac/old\n", encoding="utf-8")

    module.write_linguas({"mac": ["af"]})

    assert linguas.read_text(encoding="utf-8") == "gtk30/zu\nmac/af\n"


def test_gap_fills_counts_lite_translations_upstream_lacks_without_writing(lite_catalogs):
    module, domain_dir = lite_catalogs
    (domain_dir / "ar.po").write_text(
        'msgid "Quit %s"\nmsgstr "Quit-ar %s"\n\nmsgid "Show All"\nmsgstr "Show-ar"\n',
        encoding="utf-8",
    )
    before = (domain_dir / "ar.po").read_text(encoding="utf-8")

    count = module.gap_fills("mac", "ar", {("", "Show All"): "Upstream"})

    assert count == 1
    assert (domain_dir / "ar.po").read_text(encoding="utf-8") == before


def test_unlisted_names_gap_fillers_missing_from_linguas_lite(lite_catalogs):
    module, domain_dir = lite_catalogs
    (domain_dir.parent / "LINGUAS-lite").write_text("mac/af\n", encoding="utf-8")

    assert module.unlisted(["mac/af", "mac/ar"]) == ["mac/ar"]


def test_names_template_lists_the_names_the_priorities_ask_for(
    lite_catalogs, tmp_path, monkeypatch
):
    module, _domain_dir = lite_catalogs
    priorities = tmp_path / "virtaal.priorities.yaml"
    priorities.write_text(
        "domains:\n  iso639-3:\n    '1':\n    - English\n    '2':\n    - Zulu\n    - Arabic\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "PRIORITIES", str(priorities))

    module.write_names_template("iso639-3")

    text = (tmp_path / "iso639-3" / "iso639-3.pot").read_text(encoding="utf-8")
    assert [
        line for line in text.splitlines() if line.startswith("msgid \"") and line != 'msgid ""'
    ] == ['msgid "Arabic"', 'msgid "English"', 'msgid "Zulu"']


def test_names_catalog_keeps_names_beyond_the_template(lite_catalogs, tmp_path):
    module, _domain_dir = lite_catalogs
    names = tmp_path / "iso639-3"
    names.mkdir()
    (names / "iso639-3.pot").write_text(
        'msgid ""\nmsgstr ""\n\nmsgid "English"\nmsgstr ""\n', encoding="utf-8"
    )
    (names / "zu.po").write_text(
        'msgid "English"\nmsgstr ""\n\nmsgid "Venda"\nmsgstr "isiVenda"\n', encoding="utf-8"
    )

    module.update("iso639-3", "zu", {("", "English"): "isiNgisi"})

    text = (names / "zu.po").read_text(encoding="utf-8")
    assert 'msgstr "isiVenda"' in text and 'msgstr "isiNgisi"' in text


def test_names_upstream_is_pycountrys_catalogs(lite_catalogs):
    import pycountry

    module, _domain_dir = lite_catalogs

    assert module.upstream_dir(None, "iso639-3") == pycountry.LOCALES_DIR


@pytest.mark.parametrize(
    "name, code", [("Hebrew", "heb"), ("Sotho, Southern", "sot"), ("Swazi", None)]
)
def test_iso_code_matches_a_name_or_inverted_name(lite_catalogs, name, code):
    module, _domain_dir = lite_catalogs

    assert module.iso_code("iso639-3", name) == code


def test_names_template_gives_each_name_its_iso_code(lite_catalogs, tmp_path, monkeypatch):
    module, _domain_dir = lite_catalogs
    priorities = tmp_path / "virtaal.priorities.yaml"
    priorities.write_text("domains:\n  iso639-3:\n    '1':\n    - English\n", encoding="utf-8")
    monkeypatch.setattr(module, "PRIORITIES", str(priorities))

    module.write_names_template("iso639-3")

    assert "#: eng\nmsgid \"English\"" in (tmp_path / "iso639-3" / "iso639-3.pot").read_text(
        encoding="utf-8"
    )


@pytest.fixture
def inno_catalogs(lite_catalogs, tmp_path):
    module, _domain_dir = lite_catalogs
    inno = tmp_path / "inno"
    inno.mkdir()
    (inno / "inno.pot").write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        '#. Setup\'s title bar.\n#, qt-format\nmsgctxt "SetupWindowTitle"\n'
        'msgid "Setup - %1"\nmsgstr ""\n\n'
        'msgctxt "ButtonNext"\nmsgid "&Next >"\nmsgstr ""\n',
        encoding="utf-8",
    )
    upstream = {
        ("SetupWindowTitle", "Setup - %1"): "Opstelling - %1",
        ("ButtonNext", "&Next >"): "&Volgende >",
    }
    return module, inno, upstream


def test_inno_catalog_starts_with_its_checker_hints_flags_and_credits(inno_catalogs):
    from translate.storage import factory

    module, inno, upstream = inno_catalogs
    del upstream[("ButtonNext", "&Next >")]

    # af's vendored Afrikaans.isl names its translator.
    assert module.update("inno", "af", upstream) == 0

    store = factory.getobject(str(inno / "af.po"))
    assert store.parseheader()["X-Accelerator-Marker"] == "&"
    assert "Leon Odendaal" in store.header().getnotes("translator")
    units = {u.getcontext(): u for u in store.units if not u.isheader()}
    assert units["SetupWindowTitle"].hastypecomment("qt-format")
    assert units["SetupWindowTitle"].getnotes("developer") == "Setup's title bar."
    assert str(units["SetupWindowTitle"].target) == "Opstelling - %1"
    assert not units["ButtonNext"].target


def test_an_inno_translation_that_differs_from_upstream_ships(inno_catalogs):
    module, inno, upstream = inno_catalogs
    (inno / "af.po").write_text(
        'msgctxt "ButtonNext"\nmsgid "&Next >"\nmsgstr "&Volgende"\n', encoding="utf-8"
    )

    # Upstream translates everything, but af's lite translation wins.
    assert module.update("inno", "af", upstream) == 1
    assert module.gap_fills("inno", "af", upstream) == 1
    assert 'msgstr "&Volgende"' in (inno / "af.po").read_text(encoding="utf-8")

    (inno / "af.po").write_text(
        'msgctxt "ButtonNext"\nmsgid "&Next >"\nmsgstr "&Volgende >"\n', encoding="utf-8"
    )
    assert module.update("inno", "af", upstream) == 0
    assert not (inno / "af.po").exists()


@pytest.fixture
def guessing(inno_catalogs, tmp_path, monkeypatch):
    """A language with no Inno file, with Virtaal's and GTK's catalogs."""
    module, inno, _upstream = inno_catalogs
    monkeypatch.setattr(module, "PO_DIR", str(tmp_path / "po"))
    (tmp_path / "po").mkdir()
    (tmp_path / "gtk30").mkdir()
    (inno / "inno.pot").write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        + "".join(
            'msgctxt "%s"\nmsgid "%s"\nmsgstr ""\n\n' % pair
            for pair in (
                ("ButtonBack", "< &Back"),
                ("ButtonNext", "&Next >"),
                ("ButtonCancel", "Cancel"),
                ("ButtonYes", "&Yes"),
                ("ButtonWizardBrowse", "B&rowse..."),
                ("ButtonFinish", "&Finish"),
                ("SetupAppTitle", "Setup"),
            )
        ),
        encoding="utf-8",
    )
    return module, inno, tmp_path


def _catalog(path, *pairs):
    path.write_text(
        "".join('msgid "%s"\nmsgstr "%s"\n\n' % pair for pair in pairs), encoding="utf-8"
    )


def test_a_language_with_no_inno_file_gets_fuzzy_guesses_from_its_other_catalogs(guessing):
    from translate.storage import factory

    module, inno, root = guessing
    _catalog(
        root / "po" / "xx.po",
        ("_Next", "_Volgende"),
        ("Browse…", "Blaai…"),
        ("_Finish", "Klaar"),
        ("Setup", "Opstel"),
        ("Setup", "Opstelling"),
    )
    _catalog(
        root / "gtk30" / "xx.po", ("_Cancel", "_Kanselleer"), ("_Yes", "_Ja"), ("_Back", "_Terug")
    )

    # Guesses don't ship: nothing fills a gap.
    assert module.update("inno", "xx", {}) == 0

    units = {u.getcontext(): u for u in factory.getobject(str(inno / "xx.po")).units}
    assert {key: str(u.target) for key, u in units.items() if u.isfuzzy()} == {
        "ButtonBack": "< &Terug",
        "ButtonNext": "&Volgende >",
        # No access key in Inno's English.
        "ButtonCancel": "Kanselleer",
        "ButtonYes": "&Ja",
        # One added on the translation's first letter.
        "ButtonWizardBrowse": "&Blaai...",
        "ButtonFinish": "&Klaar",
    }
    # Translated two ways: no guess.
    assert not units["SetupAppTitle"].target


def test_virtaals_own_catalog_comes_first_and_a_guess_is_kept(guessing):
    module, inno, root = guessing
    _catalog(root / "po" / "xx.po", ("Cancel", "Kanselleer"))
    _catalog(root / "gtk30" / "xx.po", ("_Cancel", "_Stop"))
    module.update("inno", "xx", {})
    (root / "po" / "xx.po").write_text("", encoding="utf-8")

    module.update("inno", "xx", {})

    assert '#, fuzzy\nmsgctxt "ButtonCancel"\nmsgid "Cancel"\nmsgstr "Kanselleer"' in (
        inno / "xx.po"
    ).read_text(encoding="utf-8")


def test_a_language_with_an_inno_file_gets_guesses_for_its_gaps_only(guessing):
    from translate.storage import factory

    module, inno, root = guessing
    _catalog(root / "po" / "xx.po", ("Cancel", "Kanselleer"), ("_Yes", "_Jip"))

    module.update("inno", "xx", {("ButtonYes", "&Yes"): "&Ja"})

    units = {u.getcontext(): u for u in factory.getobject(str(inno / "xx.po")).units}
    assert (str(units["ButtonYes"].target), units["ButtonYes"].isfuzzy()) == ("&Ja", False)
    assert (str(units["ButtonCancel"].target), units["ButtonCancel"].isfuzzy()) == (
        "Kanselleer",
        True,
    )


def _host_catalog(locale_dir, lang, domain, *units):
    """A compiled host catalog: (context, msgid, msgstr) units, a list
    msgstr making a plural."""
    from translate.misc.multistring import multistring
    from translate.storage import mo

    store = mo.mofile()
    for context, source, target in units:
        if isinstance(target, list):
            unit = store.addsourceunit(multistring([source, source + "s"]))
            unit.target = multistring(target)
        else:
            unit = store.addsourceunit(source)
            unit.target = target
        if context:
            unit.setcontext(context)
    path = locale_dir / lang / "LC_MESSAGES"
    path.mkdir(parents=True, exist_ok=True)
    (path / (domain + ".mo")).write_bytes(bytes(store))


def test_the_hosts_gtk_and_glib_catalogs_guess_after_the_repositorys(guessing):
    from translate.storage import factory

    module, inno, root = guessing
    host = root / "host"
    _host_catalog(
        host,
        "xx",
        "gtk30",
        ("Stock label", "_Cancel", "_Kanselleer"),
        ("", "_Back", "_Terug"),
        ("", "_Next", ["_Volgende", "_Volgendes"]),
        ("", "_OK", "Goed(_O)"),
    )
    _host_catalog(host, "xx", "glib20", ("", "Finish", "Klaar"))
    _catalog(root / "gtk30" / "xx.po", ("_Back", "_Agter"))

    module.update("inno", "xx", {}, {"gtk30": str(host), "glib20": str(host)})

    units = {u.getcontext(): u for u in factory.getobject(str(inno / "xx.po")).units}
    assert {key: str(u.target) for key, u in units.items() if u.isfuzzy()} == {
        # The lite catalog first.
        "ButtonBack": "< &Agter",
        "ButtonCancel": "Kanselleer",
        "ButtonFinish": "&Klaar",
    }
    # A plural is no guess.
    assert not units["ButtonNext"].target


@pytest.mark.parametrize("host_dirs", [None, {"gtk30": None}, {"gtk30": "missing"}])
def test_no_host_catalog_guesses_less_and_keeps_earlier_guesses(guessing, host_dirs):
    module, inno, root = guessing
    host = root / "host"
    _host_catalog(host, "xx", "gtk30", ("", "_Cancel", "_Kanselleer"), ("", "_Yes", "_Ja"))
    module.update("inno", "xx", {}, {"gtk30": str(host)})
    if host_dirs and host_dirs["gtk30"] == "missing":
        host_dirs = {"gtk30": str(root / "missing")}

    module.update("inno", "xx", {}, host_dirs)

    text = (inno / "xx.po").read_text(encoding="utf-8")
    assert '#, fuzzy\nmsgctxt "ButtonCancel"\nmsgid "Cancel"\nmsgstr "Kanselleer"' in text
    assert '#, fuzzy\nmsgctxt "ButtonYes"\nmsgid "&Yes"\nmsgstr "&Ja"' in text


@pytest.mark.parametrize(
    ("translation", "msgid", "expected"),
    [
        ("Goed(_O)", "OK", "Goed"),
        ("Goed (_O)", "OK", "Goed"),
        ("Klaar(_F)", "&Finish", "Klaar(&F)"),
    ],
)
def test_a_key_in_brackets_goes_when_inno_has_none(lite_catalogs, translation, msgid, expected):
    module, _domain_dir = lite_catalogs

    assert module._as_inno(translation, msgid) == expected


def test_inno_upstream_is_the_vendored_messages_files(lite_catalogs):
    module, _domain_dir = lite_catalogs

    assert module.upstream_dir(None, "inno").endswith(os.path.join("windows", "languages"))
    german = module.upstream_translations("inno", "de", None)
    assert german[("ButtonNext", "&Next >")] == "&Weiter >"


def test_every_gap_filling_inno_catalog_ships():
    # update_lite_catalogs.py --check inno, which needs nothing installed:
    # inno's upstream is in the repository.
    module = _load("update_lite_catalogs")
    found = [
        "inno/%s" % lang
        for lang in module.shipped_languages()
        if module.gap_fills("inno", lang, module.upstream_translations("inno", lang, None))
    ]
    assert module.unlisted(found) == []


def test_a_new_lite_catalog_takes_the_plural_table_for_a_language_ttk_lacks(lite_catalogs):
    module, domain_dir = lite_catalogs

    module.update("mac", "lg", {})

    assert '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"' in (domain_dir / "lg.po").read_text(
        encoding="utf-8"
    )


def test_an_existing_lite_catalogs_nplurals_0_is_replaced(lite_catalogs):
    module, domain_dir = lite_catalogs
    (domain_dir / "lg.po").write_text(
        'msgid ""\nmsgstr ""\n"Language: lg\\n"\n"Plural-Forms: nplurals=0; plural=0;\\n"\n'
        '"X-Kept: yes\\n"\n\nmsgid "Show All"\nmsgstr "Laga byonna"\n',
        encoding="utf-8",
    )

    module.update("mac", "lg", {})

    text = (domain_dir / "lg.po").read_text(encoding="utf-8")
    assert "nplurals=0" not in text
    assert '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"' in text
    assert '"X-Kept: yes\\n"' in text and 'msgstr "Laga byonna"' in text


@pytest.mark.skipif(not shutil.which("msgfmt"), reason="needs gettext's msgfmt")
def test_every_lite_catalog_passes_msgfmt_check(tmp_path):
    failed = []
    for path in sorted(Path(TOOLS_DIR).parents[1].glob("po/lite/*/*.po")):
        result = subprocess.run(
            ["msgfmt", "-c", "-o", str(tmp_path / "out.mo"), str(path)], capture_output=True
        )
        if result.returncode:
            failed.append("%s: %s" % (path.name, result.stderr.decode(errors="replace")))
    assert failed == []
