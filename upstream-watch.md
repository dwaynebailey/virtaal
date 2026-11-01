# Upstream CAT tool watch

Virtaal's TM and terminology plugins read files and formats produced by other
desktop CAT tools (see `virtaal/plugins/tm/models/`,
`virtaal/plugins/terminology/models/` and `virtaal/plugins/migration.py`,
which imports settings and translation memory from Poedit and Lokalize).
This file tracks the latest stable release of the upstream tools we
interoperate with, so that a TM/terminology storage format change or a
notable new import/interop feature doesn't go unnoticed. It is checked and
updated by a monthly automated review; when a tracked tool ships a new
release, that review investigates whether anything here needs updating and
opens a PR with its findings.

| Tool | Latest tracked version | Date checked | Notes |
| --- | --- | --- | --- |
| Poedit | 3.10 | 2026-11-01 | New release (14 Oct 2026): initial XLIFF 2.2 support, gettext 1.0 support, macOS 27 compat fixes. No changes to the settings file format or keys (`last_file_path`/`translator_name`/`translator_email`) that `migration.py`'s `poedit_settings_import()` reads; Poedit's own TM import was already removed from Virtaal (Python 2 bsddb only). No action needed. |
| Lokalize (KDE) | 26.08.1 | 2026-11-01 | No newer stable release; KDE Gear's next release (26.12) is not out yet. |
| OmegaT | 6.1.1 | 2026-11-01 | No newer stable release. |
