# CAT Tool Competitor Research

One-time deep-dive research deliverable, produced 2026-08-26. Scope: map the
Computer-Aided Translation (CAT) tool landscape — open source and commercial,
desktop only — and describe how Virtaal compares to and interoperates with it.

This is **not** the same thing as the two lightweight monthly routines that
maintain `virtaal/plugins/tm/known-cat-tools.md` and
`virtaal/plugins/tm/upstream-watch.md`. Those two files track a narrow set of
open-source tools' releases (Poedit/Lokalize/OmegaT) and explicitly exclude
large commercial tools. This document is broader (it includes commercial
tools) and deeper (feature-by-feature and file-format comparisons, plus a
look at proprietary project formats). It is a snapshot, not something this
document or any routine keeps up to date.

**What counts as a CAT tool here:** software built around segment-based
translation of source text against a translation memory, typically with
terminology/glossary support. Machine-translation-only engines and pure
project-management platforms with no editing surface of their own are
excluded, as are web-only/SaaS tools (Smartcat, Phrase, Wordfast Anywhere,
Weblate, XTM Cloud, etc.) — desktop editors only, even when a tool also
happens to offer a web client alongside its desktop app.

**Methodology note:** Virtaal's own feature descriptions below come from
reading its source directly (`virtaal/plugins/`, `virtaal/controllers/`) on
the `py3` branch, plus the installed `translate-toolkit` package it depends
on — not from README claims. Competitor data comes from vendor
documentation, release notes, and community sources gathered by web
research; a number of vendor domains (docs.rws.com, docs.memoq.com,
apps.kde.org, gitlab.gnome.org, cafetran.com and others) were unreachable
from the research environment's network egress, so some competitor figures
(exact prices, precise release dates for a handful of smaller tools) rely on
secondary sources such as GitHub mirrors, distro package trackers, and search
snippets rather than a direct fetch of the primary page. Anywhere this
applies it is flagged **"unconfirmed"** rather than stated as fact. Treat
this document as a research snapshot dated 2026-08-26, not a live-maintained
reference.

---

## 1. Virtaal's own CAT capabilities (ground truth from source)

This section is what the feature-map and format tables below compare
everything else against. All claims are cited to `py3`-branch source.

- **Fuzzy TM matching** — Levenshtein-distance based, via translate-toolkit's
  `LevenshteinComparer` (`translate.search.lshtein`), used both by the local
  TM server (`virtaal/support/tmdb.py`) and by the "Current File" TM model
  (`virtaal/plugins/tm/models/currentfile.py`). Default minimum similarity is
  **75%**, default **5** suggestions shown
  (`virtaal/plugins/tm/tmcontroller.py`). TM backends, all under
  `virtaal/plugins/tm/models/` (`BaseTMModel` subclasses):
  - **Local Translation Memory** (`localtm.py`) — a local `tmserver`
    subprocess backed by SQLite, seeded from whatever
    `translate.storage.factory.getobject()` can open (PO, XLIFF, TMX, TS,
    etc.), continuously updated from the currently-saved file.
  - **Current File** (`currentfile.py`) — matches against already-translated
    units in the open file, plus PO's previous-msgid / XLIFF `<alt-trans>`
    alternates.
  - **Amagama** (`amagama.py`) — remote lookup-only TM server
    (`amagama-live.translatehouse.org`).
  - **Remote Server** (`remotetm.py`) — client for a user-configured
    `tmserver`-compatible host.
  - **Google Translate / Moses / Apertium** (`google_translate.py`,
    `moses.py`, `apertium.py`) — machine-translation engines, not TM in the
    strict sense; the UI distinguishes MT suggestions (no `quality` score,
    shown as `?`) from scored TM matches (shown as a `%`).
- **Terminology/glossary** — "Local Files" backend
  (`virtaal/plugins/terminology/models/localfile/`) defaults to a **PO**
  file (`terminology.po`) but loads anything `translate.storage.factory` can
  parse. A separate "Localization Terminology" backend (`autoterm.py`)
  downloads a **TBX-Basic** (ISO 30042) file per source→target locale from
  Mozilla Pontoon and parses it with a hand-rolled `lxml` parser, because
  translate-toolkit's own `tbx.py` targets the older MARTIF-rooted TBX and
  can't read TBX-Basic — English-source projects only. Terminology matches
  are a real inline placeable
  (`translate.storage.placeables.terminology.TerminologyPlaceable`); when a
  term has multiple candidate translations, a combo box is inserted directly
  into the target text box and selecting an entry **inserts the translation
  into the target**, not just displays it. `Ctrl+T` adds a new term to the
  local terminology file from the current unit.
- **QA checks** — a thin UI wrapper (`virtaal/controllers/checkscontroller.py`)
  around translate-toolkit's `translate.filters.checks`, selectable by
  project style: `StandardChecker`, `OpenOfficeChecker` (labelled
  "LibreOffice" in the UI), `MozillaChecker`, `DrupalChecker`,
  `GnomeChecker`, `KdeChecker`. This gives Virtaal the full standard
  `pofilter` check set: `untranslated`, `unchanged`, `escapes`, `newlines`,
  `tabs`, `singlequoting`, `doublequoting`, `doublespacing`, `puncspacing`,
  `printf`, `accelerators`, `variables`, `functions`, `emails`, `urls`,
  `numbers`, `startwhitespace`/`endwhitespace`, `startpunc`/`endpunc`,
  `purepunc`, `brackets`, `sentencecount`, `startcaps`, `simplecaps`,
  `acronyms`, `doublewords`, `notranslatewords`, `musttranslatewords`,
  `validchars`, `filepaths`, `xmltags`, `kdecomments`,
  `compendiumconflicts`, `simpleplurals`, `spellcheck`, `credits`,
  `nplurals`, `isfuzzy`/`isreview` — checked interactively while typing and
  via a "Quality Checks" navigation mode that steps through failing units.
  Virtaal defines no check logic of its own.
- **Placeable/inline-tag handling** — `virtaal/controllers/placeablescontroller.py`
  wraps `translate.storage.placeables.general` parsers: CamelCase, ALL-CAPS,
  command-line options, e-mail, file names, printf-style (`%s`), Python
  (`%(name)s`), Java MessageFormat (`{0}`), Qt (`%1`), numbers, punctuation,
  URLs, XML entities, XML tags, plus terminology matches. `Alt+Left`/
  `Alt+Right` step the placeable selection, `Alt+Down` copies the
  highlighted source placeable into the target (applying target-language
  punctuation rules).
- **Plural-form handling** — per-target-language `nplurals`
  (`virtaal/controllers/langcontroller.py`) drives the unit view
  (`unitview.py`) to render exactly that many independent target text
  boxes for a pluralised unit, padding missing forms with empty strings.
  `Tab`/`Shift+Tab` move between plural-form fields; the `nplurals` QA
  check flags a mismatch against the target language's expected count.
- **Spell-checking** — GtkSpell 3.0 over Enchant
  (`virtaal/plugins/spellchecker.py`), per-textbox, language auto-matched
  from the unit's source/target language against installed Enchant
  dictionaries (with a Hunspell dictionary auto-download fallback on
  Windows/macOS).
- **Keyboard-centric workflow** — a full, documented shortcut set
  (`docs/cheatsheet.rst`): open/save/quit, unit navigation
  (`Ctrl+Up/Down`, `Ctrl+PgUp/PgDown`), search (`Ctrl+F`/`F3`,
  `Ctrl+G`/`Ctrl+Shift+G`), placeable navigation/insertion
  (`Alt+Left/Right/Down`), TM-suggestion acceptance (`Ctrl+1`…`Ctrl+9`),
  checks/suggestions toggling (`F8`/`F9`), workflow-state advance
  (`Ctrl+Enter`/`Ctrl+Shift+Enter`), term insertion (`Ctrl+T`), plural-field
  and mode-selector focus movement (`Tab`, `Ctrl+Tab`,
  `Ctrl+Shift+Tab`). The one historical gap — Search mode had no
  keyboard-only exit — was fixed with `Escape` (`virtaal/modes/searchmode.py`).
  Open → translate → save is achievable without a mouse.
- **Extensibility** — a pure-Python plugin system
  (`virtaal/controllers/plugincontroller.py`): a plugin is a module
  exposing a `Plugin` class, auto-discovered from `~/.virtaal/virtaal_plugins`
  or the bundled `virtaal/plugins`, no rebuild/packaging step required. TM,
  terminology, and lookup backends are themselves plugins under this system.
  Two hidden (debug-only) plugins provide **live in-process scripting**: a
  bespoke Python console (`_python_console.py`) and an IPython console
  (`_ipython_console/`), both exposing live references to the running
  application's controllers/views for runtime introspection/automation.
  There is no public documented scripting API for end users — this is a
  developer-facing capability, hidden by default.
- **Project-level workflow** — Virtaal has per-unit workflow **states**
  (via translate-toolkit's `translate.storage.workflow`:
  Untranslated/Needs work/Rejected/Needs review/Translated/Reviewed,
  mapped from PO's fuzzy flag or XLIFF's native `state` attribute) and a
  "Workflow Mode" that filters navigation to units in chosen states. This is
  a **single-user, single-file, in-place** state-tracking mechanism — there
  is no evidence anywhere in Virtaal's controllers of multi-user roles,
  reviewer assignment, sign-off/approval gating, or any server-side/shared
  review workflow. `Alt+Enter` shows word/unit-count statistics for the open
  file.
- **File formats Virtaal can open/edit directly** (from
  `translate.storage.factory.supported_files()`, as wired into the file-open
  dialog in `virtaal/views/mainview.py`): Gettext PO/POT, Gettext MO/GMO
  (read-only compiled), XLIFF (`.xlf`/`.xliff`/`.sdlxliff` — the latter
  opened as **generic XLIFF**, see §5), Qt `.qm` (read-only compiled), TBX,
  TMX, Qt Linguist `.ts`, Qt Phrase Book `.qph`, OmegaT glossary
  (`.tab`/`.utf8`), UTX, Haiku `.catkeys`, Fluent `.ftl`, WiX `.wxl`, plus
  gzip/bzip2-compressed variants of all of the above. TMX and TBX are
  technically editable as documents through the same generic factory
  dispatch as PO/XLIFF, though Virtaal's actual TM/terminology plugins treat
  TMX/TBX as backing *data* rather than as a dedicated editing workflow.
  `.csv` is explicitly excluded (`mainview.py` comment: "we can't open
  generic .csv formats").

---

## 2. Competitor landscape

### 2.1 Open source

| Tool | License | Cost | Platform(s) | Latest release | Maintenance |
|---|---|---|---|---|---|
| OmegaT | GPL-3.0-or-later | Free | Windows/macOS/Linux (Java) | 6.1.0 (2026-08-02) | Active |
| Poedit | MIT (core); Pro/Pro+ paid tiers add official binaries + extra features | Freemium (Pro one-time + 12mo updates, or Pro+ subscription) | Windows/macOS/Linux | 3.9.1 (2026-06-04) | Active |
| Lokalize | GPL (KDE Gear) | Free | Linux (native), Windows (packaged) | 26.08.0 (2026-08-20) | Active |
| Gtranslator | GPL-3.0-or-later | Free | Linux (GNOME) | ~50.0 (~2026-04-20, distro-packaging date; upstream date unconfirmed) | Active |
| Autshumato ITE | GPL v2/v3 (OmegaT-based fork with plugins) | Free | Windows/macOS/Linux (Java) | ~3.0.0 (SourceForge last-update 2022-12-15) | Dormant (&gt;36 months) |
| Heartsome TMX Editor | GPL-2.0 | Free | Windows/macOS/Linux (Java) | v8.0, no confirmed date; vendor (Heartsome Technologies) defunct since ~2014 | Dormant — and arguably not a full CAT editor (see note) |
| Anaphraseus | GPL-family | Free | Cross-platform (LibreOffice/OpenOffice extension) | 2.08 (2019-12-22) | Dormant (&gt;36 months) |

Notes:
- **Heartsome TMX Editor** is a TMX/TBX maintenance and conversion utility
  (cleaning, format conversion, QA on TM content) rather than a
  segment-by-segment source-vs-target translation editor with its own
  project workflow — it borders on the brief's "pure TM utility" exclusion.
  Included because it's still commonly cited in this space, but weighted
  accordingly in the feature map below.
- Also investigated and excluded as not currently viable/relevant: **Open
  Language Tools** (Sun Microsystems, CDDL; last release 1.3.1, 2010, project
  host defunct), **OmegaT+** (an early-2000s OmegaT fork, abandoned),
  **BasicCAT** (small GPLv2 hobby project on GitHub, maintenance/TMX-TBX
  status unconfirmed). **bitext2tmx** was excluded as a bitext-alignment
  tool that produces TMX rather than a segment-based editor.
- **CafeTran Espresso** and **Swordfish Translation Editor** are
  Java-based and cross-platform like the tools above, but are commercial
  products (CafeTran is proprietary freemium; Swordfish's core is
  Eclipse Public License but sold with paid support) — see §2.2.

### 2.2 Commercial

| Tool | Vendor | License / cost model | Platform(s) | Latest release | Maintenance |
|---|---|---|---|---|---|
| Wordfast Classic + Pro | Wordfast LLC | 3-year term license (not perpetual); Classic = MS Word plugin, Pro = standalone | Classic: Win/macOS (needs Word); Pro: Win/macOS/Linux (Java) | Pro 11.2.0 (~2026-05) | Active |
| Trados Studio | RWS Holdings (ex-SDL) | Subscription for new buyers; legacy owners can perpetually upgrade | Windows only | Studio 2026 Release (~2026-07-02) | Active |
| memoQ | memoQ Translation Technologies (ex-Kilgray) | Subscription, from ~US$436/yr individual | Windows only (macOS/Linux via VM, or via memoQWeb) | 12.4.45 (~2026-07-28) | Active |
| STAR Transit NXT / TermStar | STAR Group | Per-seat (e.g. Freelance Pro ~€1,195) | Windows only | SP18 Update 9 (rev. 2026-03-12) | Active |
| Déjà Vu X3 | Atril Solutions | Per-seat (Pro/Workgroup/TEAMServer editions) | Windows only | 9.00.807 (~2026-04) | Active |
| CafeTran Espresso | CafeTran (Igor Kmitowski) | Proprietary freemium: free tier capped at 1,000 TM units/500 glossary terms; €80/yr or €200 one-time to remove caps | Windows/macOS/Linux (Java) | Version/date unconfirmed this session (last confirmed community changelog entry: 11.0, 2021-12-03; forum evidence of continued 2024–2025 activity) | Likely active, unconfirmed |
| Swordfish Translation Editor | Maxprograms | Commercial + support subscription; core built on the open-source OpenXLIFF filters (Eclipse Public License) | Windows/macOS/Linux (Java) | 5.17.0 (2026-02-18) | Active |
| Across Language Server (desktop client) | Across Systems | Enterprise seat licensing; free/premium Across Translator Edition for freelancers | Windows only | Last clearly confirmed stable: v7.0 (2019-02-13); later service releases exist but couldn't be pinned to a date | Maintained but slow / unconfirmed |
| MetaTexis | MetaTexis Software | Low-cost MS Word plugin | Windows only (needs Word) | v3.171 (2012-10) | Dormant (&gt;36 months) |
| RWS Passolo | RWS Holdings | Per-edition licensing, bundled with Trados portfolio | Windows only | Tracks Trados release cadence | Active — but see caveat below |
| MultiTrans (MultiCorpora) | MultiCorpora/Terminotix family | Desktop modules + freelance edition | Windows | No evidence found post-~2019 | Dormant/unconfirmed |
| LogiTerm Pro | Terminotix | Per-seat desktop | Windows | Not found | Unknown, needs vendor check |

Notes:
- **RWS Passolo** is a software-string/UI localization tool (resource
  files, dialogs, executables) rather than a general document CAT editor.
  It shares the mechanics this brief cares about (TM leverage with
  fuzzy/concordance matching, terminology, QA) so it's included with this
  caveat, but its object of translation is UI strings inside a mock of the
  running application, not free-flowing segmented text.
- **XTM Suite** (the on-premises variant of XTM International's product,
  as distinct from XTM Cloud) was investigated and **excluded**: even
  self-hosted, end-user access is via a browser against the private server,
  not a native desktop client — an architectural web app regardless of
  hosting location.
- **Fluenta** (Maxprograms) was investigated and **excluded**: it's a DITA
  translation-workflow manager that packages content into XLIFF and hands
  it to a separate CAT tool (e.g. Swordfish) — it has no segment-editing UI
  of its own, so it fails the brief's "excludes pure project-management
  platforms with no editing surface" test.

---

## 3. Feature comparison matrix

Legend: **Yes** = supported and confirmed; **Partial** = supported with a
meaningful limitation (noted below the table or inline); **No** = not
supported / not found; **Unk.** = not confirmed in this research pass
(genuine gap, not assumed "No"); **N/A** = not applicable given the tool's
scope (e.g. Heartsome is not really a translation editor).

| Tool | Fuzzy TM matching | Terminology/glossary | QA checks | Placeable/tag handling | Plural forms | Spell-check | Keyboard-centric | Extensibility | Project workflow/review |
|---|---|---|---|---|---|---|---|---|---|
| **Virtaal** | Yes — Levenshtein, multi-backend (local/current-file/remote/Amagama) | Yes — local PO-based + TBX-Basic autoterm; inserts into target | Yes — full `pofilter` set via translate-toolkit, per project style | Yes — 10+ placeable types, tab-to-select + copy-to-target | Yes — N independent target fields per `nplurals` | Yes — GtkSpell/Enchant | Yes — full documented shortcut set | Yes — Python plugin system + hidden dev Python/IPython console | Partial — single-user per-unit states only, no multi-user review/approval |
| OmegaT | Yes — configurable threshold, multiple simultaneous TMs | Yes — glossary matching/highlighting; writable glossary is TSV, TBX is read-only reference | Yes — tag/consistency checks + optional LanguageTool integration | Yes — tag protection/placeholders | Yes — CLDR-based | Yes — Hunspell/Morfologik | Yes | Yes — Groovy/JavaScript scripting + Java plugin API | Partial — team sync via SVN/Git, no formal sign-off states |
| Poedit | Yes | Yes — personal glossary panel, CSV import (no TBX) | Yes — length/case/format-string checks | Yes — placeholder/format-string validation | Yes — gettext plural rules | Yes — Enchant | Yes | No public plugin/scripting API | No — single-user oriented |
| Lokalize | Yes | Yes — glossary stored as (a subset of) TBX | Yes — diff/QA modes, Pology/posieve integration for PO | Yes | Yes | Yes | Yes | Partial — plugin/extension hooks | Partial — "Sync" mode merges translations across branches/files, project overview; no multi-user sign-off |
| Gtranslator | Partial — internal "Learn Buffer" TM, not standards-based | No standards-based glossary (plugin-based lookups only) | Unk. | Unk. | Partial — inherits gettext plural handling, no dedicated UI found | Yes | Unk. | Yes — plugin system (lookups, tag insertion, VCS) | No |
| Autshumato ITE | Yes (inherits OmegaT) | Yes (inherits OmegaT) + regional shared TM/glossary server | Yes (inherits OmegaT) | Yes (inherits OmegaT) | Yes (inherits OmegaT) | Yes (inherits OmegaT) | Yes (inherits OmegaT) | Yes (inherits OmegaT) | Partial (inherits OmegaT) |
| Heartsome TMX Editor | N/A — it's a TM maintenance/conversion tool, not a live-translation fuzzy-match editor | Partial — TBX/HSTB conversion, not in-line lookup | Yes — QA on TMX content itself | No | No | No | N/A | N/A | N/A |
| Anaphraseus | Yes — inexact match search | Partial — Wordfast-style glossary, not TBX | No dedicated suite found | Partial — term recognition only | Unk. | Inherits LibreOffice/OpenOffice spell-check | Partial — macro-driven inside Writer | Partial — LibreOffice Basic macros only | No |
| Wordfast Classic/Pro | Yes | Yes — TBX/tab-delimited import-export | Yes | Yes | Unk. (Pro) | Yes | Yes (Classic esp. — runs inside Word) | Partial — macro-based (Classic) | Partial — "Needs Translation" segment flags |
| Trados Studio | Yes — plus concordance search | Yes — MultiTerm termbases, Terminology Verifier | Yes — QA Checker 3.0, Tag Verifier | Yes — Tag Verifier | Yes | Yes | Partial — extensive but mouse-heavy ribbon UI; AutoSuggest keyboard flow exists | Yes — RWS AppStore + Studio API SDK | Yes — task-sequence workflow (translate → review → sign-off), reviewer roles |
| memoQ | Yes — plus LiveDocs corpora (bitext alignment beyond classic TM) | Yes — QTerm server, forbidden terms, fuzzy term matching | Yes — configurable severities | Yes | Unk. | Yes | Yes — extensive shortcuts | Yes — documented Web Services API | Yes — project roles, resource console |
| STAR Transit NXT/TermStar | Yes | Yes — TermStar (MARTIF/TBX v2+v3/TMX/Excel/CSV) | Yes — report manager | Unk. | Unk. | Unk. | Unk. | Partial | Yes — Freelance vs. Professional/Enterprise tiers, "TM Containers" |
| Déjà Vu X3 | Yes — pretranslation combining TM+termbase+"Lexicon" | Yes — Lexicon/termbase, AutoWrite/AutoSearch | Yes | Unk. | Unk. | Unk. | Unk. | Partial | Partial — RTF "External View" round-trip for non-DVX reviewers |
| CafeTran Espresso | Yes | Yes — TBX import | Yes | Yes | Unk. | Unk. | Yes | Partial | Partial — Track Changes (accept/reject) |
| Swordfish | Yes — plus concordance | Yes — multi-termbase, TBX/GlossML/CSV via companion tool Anchovy | Unk. dedicated suite; positioned on format interoperability instead | Yes (via OpenXLIFF filters) | Unk. | Unk. | Unk. | Yes — open-source OpenXLIFF filter layer is itself extensible | Unk. |
| MetaTexis | Yes | Yes — TM/TDB import-export | Unk. | Unk. | Unk. | Inherits MS Word | Partial — runs inside Word | Unk. | Unk. |
| Across | Yes — crossTank | Yes — crossTerm | Yes | Unk. | Unk. | Unk. | Unk. | Unk. | Yes — enterprise task-management/workflow is its core design centre |
| RWS Passolo | Yes — fuzzy + concordance, multi-TM with penalty weighting | Yes | Yes | Partial — UI-resource specific, not general inline tags | N/A (UI strings) | Unk. | Unk. | Unk. | Partial — localization-engineering workflow, not document review |

Given the depth of research possible in one pass, the smaller/legacy
commercial tools (STAR Transit, Across, Déjà Vu, MetaTexis, LogiTerm,
MultiTrans, Passolo) have more "Unk." cells than the major five (Trados,
memoQ, Wordfast, CafeTran, Swordfish) — those gaps are genuine and flagged
rather than guessed at.

**Where Virtaal is unusual/weaker relative to this landscape:**
- Virtaal's terminology insertion-into-target and placeable
  tab-to-select/copy mechanism is comparably strong to the major
  commercial tools despite being much smaller in scope.
- Virtaal's QA checks are a direct, complete exposure of
  translate-toolkit's standard `pofilter` set — comparable in breadth to
  Poedit/OmegaT/Lokalize, narrower than Trados/memoQ's configurable
  enterprise QA profiles.
- Virtaal has **no multi-user review/approval workflow** at all — every
  commercial tool above with a stated project-workflow feature (Trados,
  memoQ, STAR Transit, Across) supports this, and it's the single biggest
  structural gap between Virtaal and the enterprise commercial tier. This
  is an architectural difference (single desktop editor vs. project/LSP
  platform with a CAT front-end) rather than a missing feature Virtaal
  could bolt on without a server component.
- Virtaal's embedded live Python/IPython console is unusual even among
  competitors — none of the researched competitors expose an interactive
  in-process scripting console; the closest analogues are OmegaT's
  Groovy/JavaScript scripting and memoQ's/Trados's compiled-plugin APIs,
  which are all more structured/sandboxed than Virtaal's raw REPL access to
  its own controllers. Virtaal's console is developer-only and hidden by
  default, though, so it is not a comparable *end-user* feature.

---

## 4. File-format support matrix

Formats a tool can open/edit **directly as a translatable document**
(not merely as a TM/glossary data source). "✓*" marks a proprietary format
the tool defines itself.

| Tool | PO | XLIFF (generic) | SDLXLIFF | MQXLIFF | TXML | TMX (as source) | TBX (as source) | TS (Qt) | DOCX/XLSX/PPTX (via filters) | Own format |
|---|---|---|---|---|---|---|---|---|---|---|
| Virtaal | ✓ | ✓ | ✓ (as generic XLIFF only) | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ (no Office filters) | — |
| OmegaT | ✓ | ✓ (1.2/2.x) | ✗ | ✗ | ✗ | ✓ (native TM) | reference-only | ✗ | ✓ | — |
| Poedit | ✓ | ✓ (incl. 2.1) | ✗ | ✗ | ✗ | ✓ (TM only) | ✗ | ✓ | ✗ | — |
| Lokalize | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ (TM) | ✓ (glossary) | ✓ | ✗ | — |
| Gtranslator | ✓ | ✗ | ✗ | ✗ | ✗ | Unk. | ✗ | ✗ | ✗ | — |
| Wordfast Classic/Pro | via filters | ✓ | Unk. | ✗ | ✓* | ✓ | ✓ | ✗ | ✓ | **TXML** (Pro); Classic works in-place in DOC/DOCX |
| Trados Studio | via filters | ✓ | ✓* | ✗ | Unk. | ✓ (via `.sdltm` export) | ✓ | ✗ | ✓ | **SDLXLIFF** + `.sdlproj`/`.sdlppx` project package |
| memoQ | via filters | ✓ | ✓ (reads) | ✓* | ✓ (reads) | ✓ | ✓ | Unk. | ✓ | **MQXLIFF**/`.mqxlz` + `.mqppx` project package |
| STAR Transit/TermStar | via filters | Unk. | Unk. | Unk. | Unk. | ✓ | ✓ | Unk. | ✓ | proprietary Transit bilingual/project structure |
| Déjà Vu X3 | via filters | ✓ (External View round-trip) | Unk. | Unk. | Unk. | ✓ | Likely, unconfirmed | Unk. | ✓ | **.itd** |
| CafeTran Espresso | via filters | ✓ (native project format) | reads (per listings) | reads (per listings) | reads (per listings) | ✓ (native TM format) | ✓ | Unk. | ✓ | project format is XLIFF-based, not a distinct binary |
| Swordfish | via filters | ✓ | ✓ (reads, via OpenXLIFF) | ✓ (reads, via OpenXLIFF) | ✓ (reads/TXLF) | ✓ | ✓ | Unk. | ✓ | none — built on open OpenXLIFF filters |
| MetaTexis | ✗ | Unk. | ✗ | ✗ | ✗ | ✓ (TM) | ✓ (TM) | ✗ | ✓ (in-place in Word) | works in-place in DOC/DOCX |
| Across | via filters | Unk. | Unk. | Unk. | Unk. | ✓ | ✓ | Unk. | ✓ | crossTank/crossTerm + proprietary project container |
| Heartsome TMX Editor | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ (native) | ✓ | ✗ | ✗ (Word/Excel/tab-TXT bilingual conversion only) | — |
| Anaphraseus | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ (TM) | ✗ | ✗ | ✗ (works inside ODF documents) | — |
| Autshumato ITE | ✓ (inherits OmegaT) | ✓ (inherits OmegaT) | ✗ | ✗ | ✗ | ✓ | reference-only | ✗ | ✓ | — |

---

## 5. TM/terminology interchange: TMX and TBX

The question here is narrower than §4: does each tool support the open
**TMX** (translation memory) and **TBX** (terminology) interchange
standards that translate-toolkit and Virtaal already understand natively —
as opposed to only offering a closed/proprietary export.

| Tool | TMX export/import | TBX export/import | Notes |
|---|---|---|---|
| Virtaal | Yes (native, via translate-toolkit) | Yes (native, via translate-toolkit) | Baseline — this is the standard translate-toolkit-family support level |
| OmegaT | Yes — TMX is its native TM format | Import-only, read-only reference glossary; writable glossaries are TSV | |
| Poedit | Yes (added in 2.1) | **No** — glossary import is CSV, not TBX | Explicit proprietary-only gap for terminology |
| Lokalize | Yes | Yes — glossary stored using (a subset of) TBX | |
| Gtranslator | Unknown/undocumented — internal "Learn Buffer" TM predates confirmed TMX support | **No** | |
| Autshumato ITE | Yes (inherits OmegaT) | Import-only (inherits OmegaT) | |
| Heartsome TMX Editor | Yes (core purpose) | Yes | |
| Anaphraseus | Yes | Unknown/undocumented — native glossary is Wordfast-style, not TBX | |
| Wordfast Classic/Pro | Yes (Pro native; Classic via an "Export TM as TMX" filter) | Yes | |
| Trados Studio | Yes, via export from the proprietary `.sdltm` internal TM database | Yes (`.tbx`, MultiTerm XML, `.xlsx`, `.csv`) — legacy `.sdltb` no longer accepted by the newest cloud terminology feature | Internal TM storage is **not** TMX itself; TMX is an export/import format layered on top |
| memoQ | Yes | Yes (also MultiTerm XML, Excel, CSV) | Internal TM storage is proprietary; TMX is interchange only |
| STAR Transit/TermStar | Yes | Yes — explicitly MARTIF, TBX v2 **and** v3, TMX, Excel, CSV | Terminology import/export/merge limited to Professional/Freelance Pro editions |
| Déjà Vu X3 | Yes | Likely yes, but not explicitly confirmed by name in vendor marketing copy found this pass | Flagged for direct verification |
| CafeTran Espresso | Yes — TMX is its native TM format | Yes (import) | One of the more "open" commercial tools format-wise |
| Swordfish | Yes, including via companion tool Anchovy | Yes (also GlossML, CSV) | |
| MetaTexis | Yes | Yes | |
| Across | Yes | Yes | |
| MultiTrans | Unknown | Unknown | Uses a full-text "TextBase" bitext repository rather than classic sentence TM, so TMX fit is unclear without direct vendor confirmation |
| LogiTerm Pro | Unknown | Unknown | No confirmed documentation found |
| RWS Passolo | Unknown/undocumented | Unknown/undocumented | Object of translation is UI resources, not general TM records |

**Takeaway:** every actively-maintained tool researched supports TMX
export/import in some form — it is a near-universal baseline in this
market, as expected from a mature ISO/LISA-era standard. TBX support is
less universal: Poedit and Gtranslator explicitly do **not** support it
(both use a proprietary/simpler glossary format instead), and several
smaller commercial tools have no confirmable public statement either way.

---

## 6. Proprietary project-format investigation

For tools with their own project/package format, the question is whether
that format is documented anywhere, and whether Virtaal or translate-toolkit
could plausibly read/write it without the original tool installed.

### SDLXLIFF / `.sdlproj` / `.sdlppx` (Trados Studio)

- **SDLXLIFF** is XLIFF 1.2 plus SDL/RWS-specific extensions (segment
  confirmation levels beyond standard XLIFF `state`, tracked changes,
  locked segments, etc.). **translate-toolkit already has a relevant
  module**: `translate/storage/xliff.py`'s `Xliff1File` class lists
  `sdlxliff` directly in its `Extensions` (`["xlf", "xliff", "sdlxliff"]`,
  `xliff.py`), and `translate/storage/factory.py` maps the `sdlxliff`
  extension straight to `Xliff1File` — so **Virtaal can already open an
  `.sdlxliff` file today**, via the same generic-XLIFF code path used for
  `.xlf`. The catch: this reads it as *plain XLIFF 1.2*, ignoring every
  SDL-specific extension element/attribute (confirmation status
  granularity, tracked-change history, locked/context-match metadata) —
  round-tripping through Virtaal would silently drop that SDL-specific
  metadata on save, which is fine for basic translation but would corrupt
  a Trados-side workflow that depends on it.
- An XSD for the SDL extensions is referenced in RWS's own tooling
  (`Sdl.FileTypeSupport.Bilingual.SdlXliff...xsd`) but isn't prominently
  published as a standalone download; RWS points developers to its
  community forum for it.
- `.sdlppx`/`.sdlproj` (the portable project package and project
  definition) are, per a widely-cited community write-up
  ("Dissecting SDL Trados Studio project files (SDLPPX)",
  translationtribulations.com) and corroborated by third-party tool support,
  **plain ZIP archives** containing the `.sdlxliff` files, original source
  documents, and TM/termbase data as XML — openable with any standard
  archive tool, not an opaque binary container.
- Third-party tools (memoQ, Swordfish/OpenXLIFF, MateCat) already import
  `.sdlxliff`/`.sdlppx` without Trados installed, which independently
  confirms the format is tractable without the original tool.
- **Conclusion:** Virtaal/translate-toolkit could plausibly read a
  `.sdlppx` package (unzip it, then read the `.sdlxliff` files it contains
  through the existing `Xliff1File` path) today with no new code, at the
  cost of losing SDL-specific round-trip metadata. A dedicated SDLXLIFF
  subclass that preserves the SDL extension elements would be a bounded,
  well-precedented follow-on project (comparable to what OpenXLIFF already
  does), not a reverse-engineering exercise from scratch.
- `.sdltm` (Trados's internal TM database) is a separate, SQLite-based
  proprietary format, distinct from SDLXLIFF; translate-toolkit has no
  module for it, and it's out of scope for "project format" here since it's
  a TM database, not a project/bilingual-document format — but note that
  Trados itself exports `.sdltm` content to TMX, so the interchange path
  (§5) doesn't require reading `.sdltm` directly.

### MQXLIFF / `.mqxlz` / `.mqppx` (memoQ)

- **MQXLIFF** is also XLIFF-based with memoQ-specific extensions,
  officially described by memoQ (at a conceptual/purpose level, not a full
  published XSD) as designed for lossless round-trip export/import.
  **translate-toolkit has no dedicated module for it** — unlike
  `sdlxliff`, `mqxliff` does not appear in `factory.py`'s extension map or
  `xliff.py`'s `Extensions` list, so Virtaal would currently fail to
  recognise a `.mqxliff` file by extension at all (it isn't wired into the
  factory's format dispatch), even though the file's XML body is itself
  standard-XLIFF-shaped underneath the memoQ extensions.
- Community reverse-engineering exists at the "practical automation" level
  — a public two-part Medium write-up documents parsing MQXLIFF via
  `lxml` for spreadsheet round-tripping — confirming the format is plain,
  readable XML, not obfuscated.
- Third-party tools (Swordfish/OpenXLIFF) already read/write MQXLIFF
  without memoQ installed, again confirming tractability.
- `.mqppx` (memoQ's project package) is, like `.sdlppx`, a container
  format for handoff; no direct confirmation of its exact internal
  structure was obtained in this pass, but given memoQ's own MQXLIFF sits
  on XLIFF and its close family resemblance to Trados's package model, a
  ZIP-based container is the reasonable working assumption pending direct
  inspection of a sample file.
- **Conclusion:** Virtaal/translate-toolkit could plausibly add MQXLIFF
  support the same way SDLXLIFF is already handled (add `mqxliff`/`mqxlz`
  to the factory's extension map, pointing at `Xliff1File` or a purpose-
  built subclass) — but this does not exist today, unlike SDLXLIFF, which
  already works via the generic path.

### TXML (Wordfast Pro)

- TXML is Wordfast's XML-based "pivot" bilingual format for Wordfast Pro.
  No official published schema was found; it is documented only in
  Wordfast's own user-guide PDFs. It is structurally XML (not a schema
  Virtaal/translate-toolkit currently has any module for — no `txml`
  extension appears in translate-toolkit's storage factory).
- Third-party interoperability is well established: memoQ, CafeTran, and
  Swordfish/OpenXLIFF all implement TXML import/export, which is itself
  evidence the format has already been reverse-engineered/interoperated
  with by others without Wordfast's cooperation or an official spec.
- **Conclusion:** no existing translate-toolkit module; plausible to add
  given competitors have already done the reverse-engineering legwork, but
  would require original implementation work in translate-toolkit/Virtaal
  — there's nothing to plug in today.

### `.itd` (Déjà Vu X3)

- A community reverse-engineering effort exists in principle (a Wikibooks
  page titled "CAT-Tools/DéjàVu X/ITD format" was found by title, though it
  could not be fetched directly in this research pass to confirm its
  depth/completeness) but no official Atril-published schema was found.
  translate-toolkit has no `.itd` module.
- **Conclusion:** less tractable than SDLXLIFF/MQXLIFF/TXML — smaller
  ecosystem of third-party readers found, and the one community
  reverse-engineering resource located couldn't be verified in depth this
  pass. Flagged for a follow-up direct read of that Wikibooks page before
  drawing a firmer conclusion.

### STAR Transit's proprietary bilingual/project structure, and Across's project container

- Neither format has a public schema or a reverse-engineering write-up
  that turned up in this research pass. Both vendors document their
  *terminology* export formats in detail (TermStar: MARTIF/TBX v2+v3/TMX/
  Excel/CSV; crossTerm: TBX import) but not their core project/bilingual
  file internals.
- **Conclusion:** these two remain the most closed of the formats
  investigated — no existing translate-toolkit module, and (unlike
  SDLXLIFF/MQXLIFF/TXML) no evidence of third-party tools already having
  done the reverse-engineering work either. Reading/writing them without
  the original tool would require original reverse-engineering effort with
  no known head start.

### Summary

| Format | Vendor | Publicly documented? | Reverse-engineered by others? | translate-toolkit module today? | Could Virtaal plausibly read/write without the original tool? |
|---|---|---|---|---|---|
| SDLXLIFF | Trados Studio | Partially (XSD referenced, not prominently published) | Yes (memoQ, Swordfish, MateCat) | **Yes** — `xliff.py`/`factory.py` already map it to generic XLIFF | **Yes, today**, as generic XLIFF (loses SDL-specific metadata) |
| `.sdlppx`/`.sdlproj` | Trados Studio | Community write-up confirms plain ZIP+XML | Yes | No dedicated module, but it's just a ZIP of SDLXLIFF+source+TM/termbase | Yes, plausible (unzip + reuse the SDLXLIFF path) |
| MQXLIFF/`.mqxlz` | memoQ | Conceptual description only, no full XSD found | Yes (community Python parsing, Swordfish) | No | Plausible, needs new code (not wired into the factory today) |
| `.mqppx` | memoQ | Not found | Not confirmed | No | Unclear without inspecting a sample file |
| TXML | Wordfast Pro | No official schema found | Yes (memoQ, CafeTran, Swordfish) | No | Plausible, needs new code |
| `.itd` | Déjà Vu X3 | Not found (community effort exists but unverified) | Partial/unverified | No | Less clear; needs follow-up |
| Transit project format | STAR Transit | Not found | Not found | No | Unclear — most closed format investigated |
| Across project container | Across Systems | Not found | Not found | No | Unclear — most closed format investigated |

---

## 7. Caveats and confidence

- This is a one-time snapshot dated 2026-08-26, produced under a research
  session whose network egress blocked a number of primary vendor/project
  domains (docs.rws.com, docs.memoq.com, support.memoq.com, wordfast.com,
  apps.kde.org, invent.kde.org, gitlab.gnome.org, l10n.gnome.org,
  cafetran.com, en.wikipedia.org, en.wikibooks.org, and others). Where a
  figure rests on a secondary source (GitHub mirror, distro package
  tracker, search-result snippet) rather than a direct fetch of the primary
  page, this is noted inline (exact prices and some release dates for
  smaller/legacy tools are the weakest data points — CafeTran's current
  version number chief among them).
- "Unk." cells in the feature matrix are genuine research gaps, not
  assumed negatives — a follow-up pass with unrestricted network access to
  vendor documentation would resolve most of them.
- Virtaal's own feature descriptions (§1) are the most solid content in
  this document — they're drawn directly from reading `py3`-branch source
  and the installed `translate-toolkit` package, not from marketing copy.
