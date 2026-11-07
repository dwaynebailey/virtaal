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
generate_installer_translations.py).
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


def _fills(domain, lite, upstream):
    """Whether a lite translation changes what ships: it translates a
    message upstream doesn't - or, for inno, whose lite translations
    win, translates it differently."""
    if domain == INNO:
        return bool(lite) and lite != upstream
    return bool(lite) and not upstream


def update(domain, lang, upstream):
    """Applies the rule to po/lite/<domain>/<lang>.po given the library's
    own translations; returns how many messages it translates that
    the library's own doesn't (0 when it isn't needed, or fills no
    gap yet) - see _fills()."""
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
        for lang in ui_languages():
            fills = update(domain, lang, upstream_translations(domain, lang, directory))
            if fills and lang in languages:
                shipped[domain].append(lang)
        print(
            "%s: %d lite catalogs fill a gap (upstream: %s)"
            % (domain, len(shipped[domain]), directory)
        )
    write_linguas(shipped)


if __name__ == "__main__":
    main()
