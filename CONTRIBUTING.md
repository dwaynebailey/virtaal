# Contributing to Virtaal

## Set up pre-commit

This repo uses [pre-commit](https://pre-commit.com) for basic hygiene
checks (trailing whitespace, end-of-file newlines, valid YAML/TOML, no
leftover merge-conflict markers, no accidental large files, valid Python
syntax, no stray debugger breakpoints) — see `.pre-commit-config.yaml`.
It's deliberately minimal for now: no formatter/linter is enforced yet.

Install it once per checkout so the checks run automatically before each
commit, rather than only finding out in CI after pushing:

```bash
pip install pre-commit
pre-commit install
```

CI also runs these same hooks (the `pre-commit` job in
`.github/workflows/ci.yml`), but only against files that changed in that
push/PR — not the whole tree, since a lot of pre-existing files don't
pass yet (see `ISSUE_TRIAGE.md`'s dev-tooling section). Running
`pre-commit install` locally means you find out before pushing, not
after.

## Testing a macOS build without building it yourself

Every push builds a real, self-contained `Virtaal.app` in CI (the
`build-macos-app` job) and uploads it as a build artifact — useful for
testing a specific commit's behaviour on macOS without needing a local
Homebrew/GTK3 setup, or for grabbing a colleague's branch to try out.

```bash
devsupport/packaging/macos/download-latest-app.sh          # current branch
devsupport/packaging/macos/download-latest-app.sh py3      # or any branch by name
```

Downloads the newest such artifact for that branch (via `gh`, so you
need it authenticated against this repo) and extracts it to
`dist/downloaded/Virtaal.app`. Deliberately doesn't just look at "the
latest CI run" — `test-windows` is a known, separately-tracked failure
that keeps the *whole run* red even on commits where the macOS build
succeeded and uploaded fine, so it queries the artifacts API directly
instead of trusting the run's overall status.

The result is only ad-hoc signed, not notarized (`ISSUE_TRIAGE.md`'s
`#3313` entry) — macOS blocks a first launch of anything downloaded this
way regardless of who built it. Before it'll open:

```bash
xattr -cr dist/downloaded/Virtaal.app
codesign --force --deep -s - dist/downloaded/Virtaal.app
```

Then launch it like any other app bundle - not directly (it's a
directory, not an executable file, so `./Virtaal.app` fails with
"permission denied"):

```bash
open dist/downloaded/Virtaal.app                              # normal launch
dist/downloaded/Virtaal.app/Contents/MacOS/virtaal --version   # or run the binary inside it directly, e.g. for --help/--version
```

Building one yourself instead (needs Homebrew's GTK3 stack set up
locally - see `.claude/skills/run-virtaal/SKILL.md`'s prerequisites, or
`devsupport/packaging/macos/build.sh` for a faster, non-self-contained
dev build that just wraps your own checkout):

```bash
devsupport/packaging/macos/build_standalone.sh
```
