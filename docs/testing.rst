
.. _testing#testing:

Testing
*******

.. note:: This replaces a page that described running Dogtail/Accerciser
   GUI tests against Python 2.5 (2008) - none of that tooling is used
   any more. Three real, current layers exist instead, covering
   different things.

.. _testing#unit_tests:

1. The pytest Suite
====================

Real, automated, runs in CI on every push across Python 3.10 through
3.14 (Linux) and macOS - see ``.github/workflows/ci.yml``'s ``test``/
``test-macos`` jobs. Lives under ``virtaal/`` as ``test_*.py`` files
next to the code they cover (e.g. ``virtaal/test/test_storemodel.py``,
``virtaal/plugins/test_spellchecker.py``), plus a couple of
``demo_*.py`` files (``virtaal/views/widgets/demo_textbox.py`` and
siblings) that are deliberately *not* picked up by pytest - those are
manual, interactive GTK demo runners predating the automated suite,
kept for hands-on widget debugging, not real coverage.

Run it the same way CI does::

  pip install --no-build-isolation .[test]
  pytest -rvxs virtaal

On Linux without a real display, wrap it in ``xvfb-run`` (CI does)::

  xvfb-run -a --server-args="-screen 0 1024x768x24" pytest -rvxs virtaal

.. _testing#run_virtaal:

2. Driving a Real Instance (``run-virtaal``)
=============================================

For anything the pytest suite can't reach - does the app actually
*launch*, does a real file open correctly, does a plugin load without
error - there's a small bash driver at
``.claude/skills/run-virtaal/driver.sh`` that launches a real Virtaal
instance in the background, screenshots it, and reads its log, without
needing to sit and watch a window::

  .claude/skills/run-virtaal/driver.sh setup    # once, creates .venv
  .claude/skills/run-virtaal/driver.sh launch devsupport/testfiles/checks.po
  .claude/skills/run-virtaal/driver.sh alive     # "yes"/"no"
  .claude/skills/run-virtaal/driver.sh screenshot /tmp/shot.png
  .claude/skills/run-virtaal/driver.sh log
  .claude/skills/run-virtaal/driver.sh quit

Despite living under ``.claude/skills/`` (written for/by Claude Code
agents working on this fork), every command above is a plain shell
script - equally usable by hand. Its own ``SKILL.md`` documents a long
list of real gotchas found running this app for real (missing-
dependency failure modes, a known native teardown segfault, why plain
``python3 -m venv`` doesn't work on macOS) worth reading regardless of
whether you're using Claude Code.

Real translation files for manual testing live under
``devsupport/testfiles/`` - ``checks.po`` (exercises most quality
checks), ``plurals.po``/``plurals-zero.po`` (nplurals=3 and nplurals=1
respectively), and others added as specific bugs were found and fixed.

.. _testing#windows_battery:

3. The Windows UI Regression Battery
======================================

A PowerShell script,
``devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1``, drives
a real installed Windows build through a real UI regression battery -
install, launch, send real keystrokes/clicks via Win32 APIs, screenshot,
assert on window titles and log content, uninstall. Windows-specific
because it exercises real Windows behaviour (installer, window
management, antivirus-on-first-launch timing) that can't be
represented any other way.

Run the full battery::

  .\devsupport\testing\windows\Invoke-VirtaalLocalTestPass.ps1

Useful flags, found worth having while chasing specific bugs:

- ``-RunTest <N>`` (or a comma list, e.g. ``-RunTest 8,17``) - run only
  specific checks by number, instead of the whole battery. Much faster
  for iterating on one fix; save the full unfiltered run for a final
  "did anything else regress" pass.
- ``-DebugLog`` on an individual check's own ``Start-VirtaalTest`` call
  (already wired into specific checks in the script) surfaces that
  one launch's Python-level debug logging.
- ``-AppDebugLog`` turns debug logging on for *every* launch in the
  run. Confirmed live to sometimes cause its own cascading slowdown
  across a full run (extra log output competing for I/O with the
  transcript capture) - useful for a targeted ``-RunTest`` drill-down,
  not recommended as the default for a full run.
- ``-HumanDelayMs <N>`` slows every scripted interaction so a human
  can actually watch the run happen, at the cost of a much longer
  total run time.

The script's own extensive inline comments document a long history of
real bugs found and fixed through this exact tool - worth reading
before changing it, several non-obvious Windows/PowerShell gotchas are
explained there in detail (native stderr handling under strict
``ErrorActionPreference``, WebDAV-mounted shared-folder quirks,
``git diff --quiet``'s exact exit-code contract).
