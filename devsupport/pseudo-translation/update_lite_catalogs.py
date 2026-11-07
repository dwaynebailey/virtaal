#!/usr/bin/env python3
#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Applies the lite catalog rule to po/lite/<domain>/, for each of
Virtaal's UI languages (po/*.po):

- the library's own installed catalog translates every message in the
  lite template: no lite catalog, an existing one is removed;
- otherwise: a lite catalog with every template message, each keeping
  its lite translation, else taking the library's own.

A lite catalog is merged into the library's own when shipped, the
library's translation winning, so only its gaps take effect; the
pre-filled messages cover a build host with an older library catalog.
po/LINGUAS-lite lists the lite catalogs that fill a gap - translate a
message the library's own catalog doesn't - for the languages Virtaal
ships (po/LINGUAS, see po/update-linguas.py). The rest are kept for
when they fill one, or their language ships.

inno, the Windows installer's wizard, has no installed catalogs: its
upstream is the vendored Inno Setup messages files
(devsupport/packaging/windows/languages/), and its template is
inno_lite.py's list. Its lite translations win at build time (see
generate_installer_translations.py). A message neither the Inno file
nor the lite catalog translates, whose English Virtaal's own catalog
(po/<lang>.po), its GTK or GLib lite catalog, or this host's GTK or GLib
catalog translates, is pre-filled from it, fuzzy, so it ships only once
a translator confirms it. A host without GTK's or GLib's catalogs just
pre-fills fewer, and a pre-filled message is kept as it is.
"""

import argparse
import gettext
import importlib.util
import os
import re
import sys

from translate.misc.multistring import multistring
from translate.storage import factory, pypo

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
PO_DIR = os.path.join(REPO_ROOT, "po")
LITE_DIR = os.path.join(REPO_ROOT, "po", "lite")
LINGUAS = os.path.join(REPO_ROOT, "po", "LINGUAS-lite")
SHIPPED = os.path.join(REPO_ROOT, "po", "LINGUAS")
PRIORITIES = os.path.join(REPO_ROOT, "po", "virtaal.priorities.yaml")
# Language names: pycountry's catalogs are their upstream, and the
# template is the names po/priorities.toml's [names] rules ask for.
NAME_DOMAINS = ("iso639-3", "iso639-5")
# The Windows installer's wizard: Inno Setup's messages files.
INNO = "inno"
PLURALS = os.path.join(REPO_ROOT, "virtaal", "support", "libi18n", "plurals.py")
INSTALLER = os.path.join(
    REPO_ROOT, "devsupport", "packaging", "windows", "generate_installer_translations.py"
)


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_pseudo_translation", os.path.join(HERE, "generate_pseudo_translation.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installer():
    spec = importlib.util.spec_from_file_location("generate_installer_translations", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ui_languages():
    """Virtaal's UI languages, without English variants: the libraries'
    messages are already English."""
    return sorted(
        name[: -len(".po")]
        for name in os.listdir(os.path.join(REPO_ROOT, "po"))
        if name.endswith(".po") and not name.startswith("en_")
    )


def shipped_languages():
    """The languages Virtaal ships its own translation for (po/LINGUAS)."""
    with open(SHIPPED, encoding="utf-8") as f:
        return set(f.read().split())


def _key(unit):
    return (unit.getcontext() or "", str(unit.source))


def _translations(store):
    """{(context, msgid): target} of a store's translated messages."""
    return {
        _key(unit): unit.target
        for unit in store.units
        if not unit.isheader() and unit.istranslated()
    }


def upstream_translations(domain, lang, upstream_dir):
    """The library's own translations for lang - its exact catalog, else
    its language's, as gettext looks them up - or {}. For inno, its
    vendored messages file's."""
    if domain == INNO:
        return _installer().vendored_translations(lang)
    path = gettext.find(domain, upstream_dir, [lang]) if upstream_dir else None
    if not path:
        return {}
    # Python's own reader: translate's can't read revision 1 .mo files
    # (GTK's).
    with open(path, "rb") as f:
        catalog = gettext.GNUTranslations(f)._catalog
    translations, plurals = {}, {}
    for key, target in catalog.items():
        if isinstance(key, tuple):
            key, form = key
            plurals.setdefault(key, {})[form] = target
        elif key and target:
            translations[key] = target
    for key, forms in plurals.items():
        if all(forms.values()):
            translations[key] = multistring([forms[i] for i in sorted(forms)])
    return {tuple(key.rpartition("\x04")[::2]): target for key, target in translations.items()}


def upstream_dir(generator, domain):
    """Where the library's own catalogs are on this host, or None."""
    if domain in NAME_DOMAINS:
        import pycountry

        return pycountry.LOCALES_DIR
    if domain == INNO:
        return str(_installer().LANGUAGES_DIR)
    return generator.library_locale_dir(generator.LIBRARY_SOURCES[domain][1])


def iso_code(domain, name):
    """The ISO code pycountry has for a language (family) name - its name
    or inverted name ("Sotho, Southern") - or None."""
    import pycountry

    database = pycountry.languages if domain == "iso639-3" else pycountry.language_families
    for record in database:
        if name in (getattr(record, "name", None), getattr(record, "inverted_name", None)):
            return record.alpha_3
    return None


def write_names_template(domain):
    """po/lite/<domain>/<domain>.pot: the language names
    po/virtaal.priorities.yaml lists for domain, at any level."""
    import yaml

    with open(PRIORITIES, encoding="utf-8") as f:
        levels = yaml.safe_load(f)["domains"].get(domain, {})
    names = sorted({name for level in levels.values() for name in level})
    store = pypo.pofile()
    store.updateheader(add=True, Content_Type="text/plain; charset=UTF-8")
    for name in names:
        unit = store.addsourceunit(name)
        code = iso_code(domain, name)
        if code:
            unit.addlocation(code)
    os.makedirs(os.path.join(LITE_DIR, domain), exist_ok=True)
    with open(os.path.join(LITE_DIR, domain, domain + ".pot"), "wb") as f:
        store.serialize(f)


def _plurals():
    spec = importlib.util.spec_from_file_location("plurals", PLURALS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _new_catalog(domain, lang, existing=None):
    """The catalog to write: existing's header, else a new one; either
    way with lang's Plural-Forms if it has none, or nplurals=0, which
    msgfmt rejects."""
    store, forms = pypo.pofile(), _plurals().plural_forms(lang)
    if existing is not None and existing.header():
        store.units = [existing.header()]
    else:
        fields = {"Language": lang, "Content_Type": "text/plain; charset=UTF-8"}
        store.updateheader(add=True, **fields, **({"Plural_Forms": forms} if forms else {}))
        if domain == INNO:
            # The template's checker hints, and the vendored file's credits.
            installer = _installer()
            installer.inno_lite.start_catalog(store, installer.vendored_text(lang))
    declared = store.parseheader().get("Plural-Forms", "")
    if forms and (not declared or re.match(r"\s*nplurals\s*=\s*0\b", declared)):
        store.updateheader(add=True, Plural_Forms=forms)
    return store


def _add_message(store, domain, message):
    """A unit for template message in store: its context, its
    developer notes and format flags, and a language name's code."""
    unit = store.addsourceunit(message.source)
    unit.setcontext(message.getcontext())
    if domain in NAME_DOMAINS:
        unit.addlocations(message.getlocations())
    if message.getnotes("developer"):
        unit.addnote(message.getnotes("developer"), origin="developer")
    for flag in message.typecomments:
        for name in flag.lstrip("#,").replace(",", " ").split():
            if name != "fuzzy":
                unit.settypecomment(name)
    return unit


# Guessing an inno message from the language's other catalogs, whose
# accelerator marker is _, where Inno's is &.
GUESS_DOMAINS = ("gtk30", "glib20")
# Inno's back and next buttons point the way: "< &Back", "&Next >".
_ARROWS = ("< ", " >")


def _plain(text, marker):
    """text without its accelerator marker (a doubled one is the
    character itself), and an ellipsis as three dots."""
    text = text.replace(marker * 2, "\0").replace(marker, "").replace("\0", marker)
    return text.replace("\u2026", "...")


def _guess_table(path):
    """{English without accelerator: translation} of the catalog at
    path's translated singular messages, leaving out English it
    translates in more than one way."""
    if not os.path.exists(path):
        return {}
    return _table(
        (str(unit.source), str(unit.target))
        for unit in factory.getobject(path).units
        if not unit.isheader() and unit.istranslated() and not unit.hasplural()
    )


def _host_table(domain, lang, directory):
    """_guess_table() of this host's own catalog for domain, if any."""
    if directory is None:
        return {}
    return _table(
        (msgid, str(target))
        for (_context, msgid), target in upstream_translations(domain, lang, directory).items()
        if len(getattr(target, "strings", [target])) == 1
    )


def _table(pairs):
    """_guess_table() of (English, translation) pairs."""
    found = {}
    for source, target in pairs:
        found.setdefault(_plain(source, "_"), set()).add(target)
    table = {}
    for english, targets in found.items():
        if len({_plain(t, "_") for t in targets}) == 1:
            # The one with an accelerator, if any.
            table[english] = max(sorted(targets), key=lambda t: "_" in t.replace("__", ""))
    return table


def _as_inno(translation, msgid):
    """translation (with _ as its accelerator marker) as Inno writes
    msgid's: & as the marker, on a letter of the translation if msgid
    has one, and msgid's dots and arrows."""
    wants_key = "&" in msgid.replace("&&", "")
    text = translation.replace("&", "&&").replace("__", "\0")
    if not wants_key:
        # An access key in brackets, "OK(_O)", goes with its brackets.
        text = re.sub(r"\s*\(_\w\)", "", text)
    if "_" in text:
        text = text.replace("_", "&" if wants_key else "", 1).replace("_", "")
    elif wants_key:
        first = next((i for i, c in enumerate(text) if c.isalnum()), None)
        if first is not None:
            text = text[:first] + "&" + text[first:]
    text = text.replace("\0", "_")
    if "..." in msgid:
        text = text.replace("\u2026", "...")
    before, after = _ARROWS
    if msgid.startswith(before):
        text = before + text
    if msgid.endswith(after):
        text += after
    return text


def inno_guesses(lang, messages, host_dirs=None):
    """{(context, msgid): translation} for the inno messages whose
    English, without accelerators, dots or arrows, Virtaal's own
    catalog for lang, else its GTK or GLib lite catalog, else the GTK
    or GLib catalog in host_dirs ({domain: locale directory}) translates."""
    host_dirs = host_dirs or {}
    tables = (
        [_guess_table(os.path.join(PO_DIR, lang + ".po"))]
        + [_guess_table(os.path.join(LITE_DIR, d, lang + ".po")) for d in GUESS_DOMAINS]
        + [_host_table(d, lang, host_dirs.get(d)) for d in GUESS_DOMAINS]
    )
    guesses = {}
    for message in messages:
        english = _plain(str(message.source), "&")
        before, after = _ARROWS
        english = english.removeprefix(before).removesuffix(after)
        found = next((table[english] for table in tables if english in table), None)
        if found:
            guesses[_key(message)] = _as_inno(found, str(message.source))
    return guesses


def _fills(domain, lite, upstream):
    """Whether a lite translation changes what ships: it translates a
    message upstream doesn't - or, for inno, whose lite translations
    win, translates it differently."""
    if domain == INNO:
        return bool(lite) and lite != upstream
    return bool(lite) and not upstream


def update(domain, lang, upstream, host_dirs=None):
    """Applies the rule to po/lite/<domain>/<lang>.po given the library's
    own translations (and for inno, where this host's GTK and GLib
    catalogs are, see inno_guesses()); returns how many messages it
    translates that the library's own doesn't (0 when it isn't needed,
    or fills no gap yet) - see _fills()."""
    template = factory.getobject(os.path.join(LITE_DIR, domain, domain + ".pot"))
    messages = [unit for unit in template.units if not unit.isheader()]
    path = os.path.join(LITE_DIR, domain, lang + ".po")

    existing = factory.getobject(path) if os.path.exists(path) else None
    template_keys = {_key(unit) for unit in messages}
    # A language-name catalog keeps names beyond the template (Zulu's
    # Southern African languages).
    extras = (
        [
            unit
            for unit in existing.units
            if not unit.isheader()
            and not unit.isobsolete()
            and unit.istranslated()
            and _key(unit) not in template_keys
        ]
        if existing and domain in NAME_DOMAINS
        else []
    )

    lite = _translations(existing) if existing else {}
    if all(_key(unit) in upstream for unit in messages) and not extras:
        # inno's lite translations win: one that differs keeps it.
        if not any(_fills(domain, lite.get(_key(u)), upstream[_key(u)]) for u in messages):
            if os.path.exists(path):
                os.remove(path)
            return 0

    previous = (
        {
            _key(unit): unit
            for unit in existing.units
            if not unit.isheader() and not unit.isobsolete()
        }
        if existing
        else {}
    )
    store = _new_catalog(domain, lang, existing)
    # A message the Inno file doesn't translate would show in English.
    guesses = inno_guesses(lang, messages, host_dirs) if domain == INNO else {}
    fills = 0
    for message in messages:
        unit = _add_message(store, domain, message)
        before = previous.get(_key(message))
        if before is not None and before.getnotes("translator"):
            unit.addnote(before.getnotes("translator"), origin="translator")
        target = lite.get(_key(message)) or upstream.get(_key(message))
        if target:
            unit.target = target
            fills += _fills(domain, target, upstream.get(_key(message)))
        elif before is not None and before.isfuzzy() and before.target:
            # Unfinished work: kept, still fuzzy, so it doesn't ship.
            unit.target = before.target
            unit.markfuzzy()
        elif _key(message) in guesses:
            # A guess, for a translator to confirm.
            unit.target = guesses[_key(message)]
            unit.markfuzzy()
    for unit in extras:
        code = iso_code(domain, str(unit.source))
        if code is None:
            # Never looked up: pycountry's names change (Swazi became Swati).
            print(
                "warning: po/lite/%s/%s.po: %r isn't a name pycountry has"
                % (domain, lang, str(unit.source)),
                file=sys.stderr,
            )
        elif code not in unit.getlocations():
            unit.addlocation(code)
    store.units.extend(extras)
    if existing:
        store.units.extend(unit for unit in existing.units if unit.isobsolete())
    with open(path, "wb") as f:
        store.serialize(f)
    return fills


def gap_fills(domain, lang, upstream):
    """How many messages po/lite/<domain>/<lang>.po translates that the
    library's own catalog doesn't, without changing anything."""
    path = os.path.join(LITE_DIR, domain, lang + ".po")
    if not os.path.exists(path):
        return 0
    template = factory.getobject(os.path.join(LITE_DIR, domain, domain + ".pot"))
    lite = _translations(factory.getobject(path))
    return sum(
        1
        for unit in template.units
        if not unit.isheader() and _fills(domain, lite.get(_key(unit)), upstream.get(_key(unit)))
    )


def unlisted(found):
    """The "<domain>/<lang>" entries in found that po/LINGUAS-lite lacks."""
    with open(LINGUAS, encoding="utf-8") as f:
        listed = {line.strip() for line in f if line.strip()}
    return sorted(entry for entry in found if entry not in listed)


def write_linguas(shipped):
    """po/LINGUAS-lite: other domains' lines as they are, these domains'
    from shipped ({domain: [lang, ...]})."""
    with open(LINGUAS, encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f if line.strip()]
    lines = [line for line in lines if line.split("/")[0] not in shipped]
    lines += ["%s/%s" % (domain, lang) for domain, langs in shipped.items() for lang in langs]
    with open(LINGUAS, "w", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in sorted(lines)))


def main():
    generator = _generator()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "domains",
        nargs="+",
        choices=[
            d
            for d in list(generator.LIBRARY_SOURCES) + [INNO]
            if d in NAME_DOMAINS or os.path.isdir(os.path.join(LITE_DIR, d))
        ],
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="change nothing; fail if a lite catalog that fills a gap for a shipped "
        "language is missing from po/LINGUAS-lite, so it wouldn't ship",
    )
    args = parser.parse_args()

    shipped, languages = {}, shipped_languages()
    if args.check:
        found = []
        for domain in args.domains:
            directory = upstream_dir(generator, domain)
            if directory is None:
                sys.exit("%s isn't installed - can't read its own translations" % domain)
            found += [
                "%s/%s" % (domain, lang)
                for lang in languages
                if gap_fills(domain, lang, upstream_translations(domain, lang, directory))
            ]
        missing = unlisted(found)
        for entry in missing:
            print(
                "::error::po/lite/%s.po fills a gap but po/LINGUAS-lite doesn't list %s, so it won't ship"
                % (entry, entry)
            )
        sys.exit(1 if missing else 0)

    for domain in args.domains:
        directory = upstream_dir(generator, domain)
        if directory is None:
            sys.exit("%s isn't installed - can't read its own translations" % domain)
        if domain in NAME_DOMAINS:
            write_names_template(domain)
        shipped[domain] = []
        # inno's guesses also come from this host's GTK and GLib catalogs.
        host_dirs = (
            {d: upstream_dir(generator, d) for d in GUESS_DOMAINS} if domain == INNO else None
        )
        for missing in sorted(d for d, path in (host_dirs or {}).items() if path is None):
            print(
                "warning: %s isn't installed - inno guesses leave it out" % missing, file=sys.stderr
            )
        for lang in ui_languages():
            upstream = upstream_translations(domain, lang, directory)
            fills = update(domain, lang, upstream, host_dirs)
            if fills and lang in languages:
                shipped[domain].append(lang)
        print(
            "%s: %d lite catalogs fill a gap (upstream: %s)"
            % (domain, len(shipped[domain]), directory)
        )
    write_linguas(shipped)


if __name__ == "__main__":
    main()
