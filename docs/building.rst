
.. _building#building:

Building
********

.. note:: This describes `dwaynebailey/virtaal
   <https://github.com/dwaynebailey/virtaal>`_'s ``py3`` branch - the
   active fork, GTK3 and Python 3 throughout. It replaces an older
   version of this page describing PyGTK/GTK2 and py2exe, neither of
   which apply to this codebase any more.

Get the source from the ``py3`` branch::

  git clone --branch py3 https://github.com/dwaynebailey/virtaal.git

The definitive, always-current reference for every command below is
``.github/workflows/ci.yml`` - CI runs the real build on every push,
so if something here ever drifts from what's actually in that file,
trust the workflow.

.. _building#running_from_source:

Running From Source
====================

System prerequisites (not installable via pip - see
``pyproject.toml``'s own comment on why GTK3/PyGObject specifically
aren't declared as a normal dependency):

- GTK3 and PyGObject - install via your system's package manager
  (``apt``/``dnf``/``brew``/`gvsbuild
  <https://github.com/wingtk/gvsbuild>`_ on Windows), not pip
- Enchant + gtkspell3 (optional, only needed for spell checking)

Then, from the checkout::

  pip install --no-build-isolation .[test]
  python bin/virtaal

The ``--no-build-isolation`` matters: ``setup.py`` imports
translate-toolkit directly at the top level (to compile ``.mo``
files), so translate-toolkit needs to already be importable before
setup.py itself runs - install it first if a plain ``pip install .``
doesn't work::

  pip install translate-toolkit
  pip install --no-build-isolation .

macOS specifically: a plain ``python3 -m venv`` can't see Homebrew's
GTK3/PyGObject at all - create the venv with
``--system-site-packages`` instead. The ``run-virtaal`` Claude Code
skill (``.claude/skills/run-virtaal/``) documents a fully worked
macOS setup, including every gotcha found running this app for real
during this fork's Python 3 port - worth reading even if you're not
using Claude Code, the underlying commands are just as valid run by
hand.

.. _building#building_distributable_packages:

Building Distributable Packages
================================

Both platforms use `PyInstaller <https://pyinstaller.org/>`_ to
freeze the app, then a platform-native installer format on top. CI's
``build-windows-installer``/``build-macos-app`` jobs run these exact
scripts and upload the results as workflow artifacts on every push -
see :doc:`the Installation section of the front page <index>` for
where to grab a pre-built one instead of building your own.

Windows
-------

Requires `gvsbuild <https://github.com/wingtk/gvsbuild>`_'s GTK3
build and `Inno Setup <https://jrsoftware.org/isinfo.php>`_, in
addition to the running-from-source prerequisites above::

  devsupport\packaging\windows\build_standalone.ps1
  devsupport\packaging\windows\build_installer.ps1

The first produces a frozen ``dist\virtaal\`` tree (PyInstaller,
one-dir mode - see that script's own comments for why not
``--onefile``); the second wraps it into a single
``virtaal-<version>-setup.exe`` via Inno Setup
(``devsupport/packaging/windows/virtaal.iss``).

macOS
-----

::

  devsupport/packaging/macos/build_standalone.sh
  devsupport/packaging/macos/build_dmg.sh

The first produces ``Virtaal.app`` (PyInstaller); the second wraps it
into a ``.dmg`` via `dmgbuild
<https://dmgbuild.readthedocs.io/>`_ (settings in
``devsupport/packaging/macos/dmgbuild-settings.py``).

Linux
-----

No frozen/installer build exists for Linux in this fork - run from
source (above), or package it yourself using the "Running From
Source" dependencies as your spec.

.. _building#building_the_docs:

Building These Docs
====================

::

  pip install Sphinx
  cd docs
  make html

CI builds these same docs with ``-W`` (warnings treated as errors) -
run the same way locally before pushing a docs change, rather than
finding out from a failed CI run.
