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
| Poedit | 3.9.1 | 2026-09-01 | baseline |
| Lokalize (KDE) | 26.08.0 | 2026-09-01 | baseline |
| OmegaT | 6.1.1 | 2026-09-01 | baseline |
