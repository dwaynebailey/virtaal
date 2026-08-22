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
