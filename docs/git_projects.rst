.. _git_projects#git_projects:

Git projects
************

Many teams keep their translations in a Git repository, one file per language,
such as ``po/af.po``. With the Git plugin, Virtaal opens your language's file
from such a repository, lets you work on it offline like any other file, and
commits and pushes your changes when you choose. Others' changes are merged
into yours unit by unit, so you never see a Git merge conflict.

The plugin is experimental and off until you turn it on: open
:menuselection:`Edit --> Preferences`, tick Git (experimental) under
Features, and click its :guilabel:`Configure…` button.

.. _git_projects#adding:

Adding a project
================

:guilabel:`Configure…` opens Translation Servers. Click :guilabel:`Add…`
(choose Git if other platforms are on) and give:

- :guilabel:`URL`: the repository's address, as you would give it to
  ``git clone``, such as ``https://example.org/team/program.git`` or
  ``git@example.org:team/program.git``.
- :guilabel:`Branch`: the branch the team translates, such as ``main``.
- :guilabel:`Language files`: where each language's file is, with ``*`` where
  the language's code goes, such as ``po/*.po`` or
  ``locale/*/LC_MESSAGES/program.po``.

:guilabel:`Test Connection` checks that the repository has that branch.

Git doesn't need to be installed: Virtaal has Git built in. To sign in, it
uses:

- For an ``ssh`` or ``git@`` address, your computer's SSH (built into Windows
  10 and later, macOS and Linux), with your SSH key and SSH agent. Virtaal
  never asks for an SSH passphrase, so add a key that has one to the agent
  first (``ssh-add``; on Windows, start the OpenSSH Authentication Agent
  service).
- For an ``https`` address, a username and password, or the access token
  most Git hosts want in its place. Virtaal looks for one in your computer's
  password store (Windows Credential Manager, the macOS Keychain, GNOME
  Keyring or KWallet), then asks Git's own credential helper if Git is
  installed, then asks you. Tick :guilabel:`Remember in this computer’s
  password store` to have the password store keep it; otherwise Virtaal
  forgets it when it quits. Virtaal never keeps a password or token in its own
  settings.

Commits are in your name and email from Git's own settings (``user.name`` and
``user.email`` in ``~/.gitconfig``), read even without Git installed, else
those in Virtaal's preferences.

.. _git_projects#opening:

Opening your language
=====================

Choose :menuselection:`File --> Open from Translation Server…`, then the
project and your language. Virtaal fetches the branch into a copy of the
repository of its own, in its state folder, and opens your language's file
from it as a work copy. As for :doc:`Weblate <weblate>`,
:menuselection:`File --> Save` writes only your copy; you can work offline,
quit, and open it again from Recent Files.

.. _git_projects#submit:

Submit
======

:menuselection:`File --> Submit to Server…` fetches the branch again and
compares each unit you changed with the repository's file:

- A unit no one else changed is sent.
- A unit someone else changed too is a conflict: Changed on the Server shows
  your text and theirs, and you choose :guilabel:`Keep mine`,
  :guilabel:`Take theirs` or :guilabel:`Skip`.

Your units then go into the file as the branch has it now, and Virtaal commits
that one file, in your name and email (see above), and pushes it. Nothing else
in the repository is changed, and a push is never forced. If someone pushed in the
meantime, Virtaal fetches again and commits on top of their work; if they
changed one of the very units you are sending, Submit stops before anything is
pushed and asks you to update first.

.. _git_projects#update:

Update from Server
==================

:menuselection:`File --> Update from Server…` fetches the branch and brings
others' changes into your copy, unit by unit: a unit changed only in the
repository takes theirs, one changed only by you keeps yours, and one changed
on both sides is a conflict. If strings were added, removed or reworded (the
maintainer updated the file from its template), Virtaal offers to open the new
file and keeps your unsubmitted changes where it can.

With :guilabel:`Send finished units as you go` on for the project, each save
commits and pushes the units you saved as Translated.

.. _git_projects#fork:

Submitting from your fork (GitHub)
==================================

Most translators can't push to a project's repository. For a project on
GitHub, Virtaal can submit your work as a pull request from your own copy of
the repository (your fork) instead: tick :guilabel:`Submit as a pull request
from my fork` for the project. Left as it is, Virtaal asks GitHub whether you
may push, and submits from your fork if you may not.

GitHub needs a personal access token for this, which you make on GitHub under
:menuselection:`Settings --> Developer settings --> Personal access tokens`:

- a fine-grained token with read and write access to Contents and Pull
  requests (for your fork, or all your repositories, since the fork may not
  exist yet), or
- a classic token with the ``public_repo`` scope.

Virtaal asks for it the first time, and keeps it for the session, or in your
computer's password store if you tick :guilabel:`Remember in this computer’s
password store` (as ``Virtaal GitHub github.com``), never in its own settings.
Signing in with your browser instead of a token isn't available yet.

The first Submit makes your fork if you have none, waits until GitHub has it
ready, pushes your language's file to a branch of its own on the fork
(``virtaal/af`` for Afrikaans) and opens a pull request against the project's
branch. The pull request holds one commit: each later Submit while it is open
makes that commit again on the project's newest work, with all your changes,
and replaces it on the branch, so the maintainers see one clean change. Virtaal
only replaces the branch while it holds nothing but what Virtaal pushed; if
someone else pushed to it, Submit stops and says so. Nothing is ever pushed to
the project itself.

Submit adds a commit on top instead, so a reviewer sees what changed:

- when someone reviewed or commented on the pull request since your last
  Submit: the commit is "Address review comments", or the summary you type;
- when you tick :guilabel:`Add as a separate commit` in Submit's question,
  for a deliberate batch, with an optional :guilabel:`Commit summary`.
  Virtaal remembers the choice for the project.

Submit's question says which it will do. Update from Server still brings in
the project's own branch, unit by unit, and keeps the changes waiting in your
pull request. When you open the file, and after each Submit, a notice says
whether your pull request is open, was merged or was closed, with its link.
Once it is merged, the next Submit starts a new branch and pull request.

For GitHub Enterprise, add its API to the ``[github]`` section of
``gitprojects.ini`` in Virtaal's settings folder::

    [github]
    git.example.org = https://git.example.org/api/v3

A project's ``pull_branch`` setting there changes the branch's name
(``virtaal/*``, with ``*`` for the language's code).

Limits
======

- One file per language, in a format with its source text in it, such as PO or
  XLIFF. Monolingual formats with a separate template are not supported yet.
- A unit holds only the states its file format can: Reviewed is kept with your
  work copy but goes into a PO file as translated.
- Every Git project needs its own repository address: two branches of one
  repository can't both be added yet.
- The whole branch is fetched, so a first fetch of a large repository takes a
  while.
- Pull requests are for GitHub only; GitLab and Codeberg aren't supported
  yet. A pull request Virtaal opened is followed only on the computer that
  opened it.
