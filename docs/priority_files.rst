.. _priority_files:

Priority Files
**************
A priority file ranks a project's messages by how much they matter to
users, so that translators can do the most important ones first. Virtaal's
:ref:`Priority mode <using_virtaal#priority_mode>` reads one to move
between a level's untranslated and fuzzy messages, and shows each level's
progress.

Any project can publish one beside its translations. Virtaal's own,
``po/virtaal.priorities.yaml``, is generated from what Virtaal shows on
screen, but a priority file can be written by hand or by any tool.

.. _priority_files#format:

Format 1
========
A priority file is a YAML document, in UTF-8, named
``<name>.priorities.yaml``. For example:

.. code-block:: yaml

   format: 1
   priorities:
   - id: '1'
     name: Level 1
     order: 1
     description: The welcome screen and the menus.
   - id: '2'
     name: Level 2
     order: 2
     description: Settings, and screens seen once.
   domains:
     myapp:
       '1':
       - _Open
       - "File menu\x04_Quit"
       - '%d file'
       '2':
       - Preferences

It has these keys:

``format``
  ``1``. A file in any other format is ignored.

``priorities``
  The levels, a list. Each has:

  - ``id``: a short string that ``domains`` uses for the level.
  - ``name``: the name translators see, in the project's source language.
  - ``order``: a whole number; lower is more important. Levels are listed
    by it, and no two levels share one.
  - ``description`` (optional): what the level holds.

``domains``
  The messages at each level, for each gettext domain (one translation
  file's messages, such as ``myapp`` for ``myapp.pot``): a mapping from
  the domain to a mapping from a level's ``id`` to a list of messages.
  Each message is its msgid, or its msgctxt, the character U+0004 (written
  ``"\x04"`` in a double-quoted YAML string) and its msgid when it has a
  context. A plural is listed by its singular msgid. A message listed at
  two levels is at the more important one.

Other keys are ignored. Generated files can, for example, record the
template they were made from (``pot-creation-date``).

Write every id and message as a string: quote a message YAML would
otherwise read as a number or a boolean, such as ``'1'``, ``'%d file'``
or ``'Yes'``. YAML libraries do this when they write a file.

A message the file doesn't list has no level; a translation file can be
newer than its priority file.

.. _priority_files#finding:

Where Virtaal looks
===================
For a translation file, Virtaal uses the first of these that it can read:

#. The file its PO header's ``X-Priority-File`` field names, relative to
   the translation file's folder, e.g.
   ``"X-Priority-File: myapp.priorities.yaml\n"``.
#. ``<stem>.priorities.yaml`` beside it, where ``<stem>`` is the translation
   file's name up to its first dot: ``myapp.priorities.yaml`` for
   ``myapp.po``.
#. Any ``*.priorities.yaml`` in the same folder, by name, which suits a
   ``po/<language>.po`` layout.
#. Any ``*.priorities.yaml`` in the folder above, by name, which suits a
   ``<language>/myapp.po`` layout.

A priority file can also be loaded by hand from the Priority mode's menu.

When a priority file lists several domains, Virtaal uses the one that
shares the most messages with the translation file, or for a tie the
one the priority file is named after.
