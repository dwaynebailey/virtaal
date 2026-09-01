# New CAT tool scout tracker

This file tracks desktop computer-assisted translation (CAT) tools that
could be relevant to Virtaal's translation-memory (TM) plugin — either as
inspiration, or as a tool whose TM/terminology data Virtaal might one day
be able to import or interoperate with.

Scope is deliberately narrow: **desktop** tools that are actively
maintained, support an **offline/local translation memory** (not just a
cloud/SaaS backend), and are **software-localization-flavored** (aimed at
PO files, software UI strings, or similar — not general document/literary
translation). Large commercial/enterprise CAT suites (memoQ, Trados,
Phrase, Smartcat, etc.) are out of scope, as are tools focused purely on
prose/document translation.

Poedit, Lokalize, and OmegaT are intentionally **not** listed here — they
are tracked by a separate, dedicated monthly check. Do not re-add them.

This tracker is maintained by an automated monthly scouting check. New
rows are added only for genuinely new tools not already listed here (a
minor fork or rebrand of a listed tool doesn't count as new).

| Tool | First noted | TM/terminology format | Notes |
|------|-------------|------------------------|-------|
| Gtranslator (GNOME Translation Editor) | 2026-09 | SQLite-backed "translation memory" plugin, learned from open PO files | GNOME's native gettext PO editor; actively maintained. TM is local/per-user rather than a portable TMX file, so interop would likely mean reading its SQLite schema directly rather than a standard exchange format. |
| Autshumato Integrated Translation Environment (ITE) | 2026-09 | TMX | South African (translate.org.za-adjacent) open-source CAT tool bundling TM, glossary and MT lookup. TMX is a format translate-toolkit already reads/writes, so importing an Autshumato TM into Virtaal is plausible in principle; project's maintenance activity should be re-checked before relying on it. |
