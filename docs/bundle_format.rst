.. _bundle_format:

Translation bundle format (.virtaal)
************************************
A translation bundle holds source documents and their translations into any
number of languages in one file. It is a zip file laid out so that other tools
can read and write it; this page describes version 1.

Members
=======

``mimetype``
  ``application/x-virtaal-bundle``: the first member, stored (not
  compressed), as in an OpenDocument file.
``manifest.json``
  What the bundle holds (below).
``sources/<document>``
  Each source document, as it was added.
``trans/<lang>/<document>.<ext>``
  The file to translate for each document and target language: XLIFF 1.2
  (``.xlf``) for OpenDocument, Gettext PO (``.po``) for HTML and plain text.
``targets/<lang>/<document>``
  Each translated document, made from the source document and its
  translation.
``baselines/<lang>/<document>.<ext>``
  In a master, each language's file as it was when the bundle was split (see
  below).
``tm/``, ``terms/``
  TMX translation memories and TBX glossaries, kept as they are.

``<lang>`` is a language code as Virtaal writes it (``zu``, ``pt_BR``).
Member names use ``/`` and never ``..`` or an absolute path. Other members are
kept as they are when Virtaal saves the bundle.

A translated document leaves out translations that are untranslated or need
work: those paragraphs keep the source document's text.

The manifest
============
``manifest.json`` is a UTF-8 JSON object:

``format``
  ``"virtaal-bundle"``.
``version``
  ``1``. A tool refuses a bundle with a higher version.
``id``
  The bundle's own id, a ``urn:uuid:`` URI.
``name``
  The project's name, used to name the files made from the bundle
  (``manual.zu.virtaal``, ``manual.zu.tipp``).
``created``, ``modified``
  UTC times, ``2026-01-31T12:00:00Z``.
``creator``
  ``tool``, ``version`` and ``toolkit`` (the translate-toolkit version) of the
  tool that last wrote the bundle.
``source_language``
  The source documents' language.
``target_languages``
  The languages the documents are translated into; can be empty.
``documents``
  One object per source document: ``name`` (unique in the bundle),
  ``converter`` (``odf``, ``html`` or ``txt``), ``source`` (its
  member), ``file_name`` (the name of its files under ``trans/``), ``sha256``
  (of the source document) and, if known, ``original`` (the path it was added
  from).
``translations``
  One object per document and target language: ``document``, ``language``,
  ``file`` (the member to translate), ``sha256`` (of that file as last
  written), ``target`` (the translated document's member) and ``target_of``
  (the ``sha256`` of the file the translated document was made from; a
  different value means it is out of date and is made again).

A bundle split from a master also has ``split_from``: the master's ``id``,
the ``language`` and the ``sources`` (``{document name: sha256}``) it was made
from. A master that was split has ``splits``: for each language, the split
bundle's ``id``, when it was made and its ``sources``.

Splitting and merging
=====================
A master with several target languages can be split into one bundle per
language, an ordinary bundle with one target language and ``split_from``. The
master keeps a copy of each language's files as split under ``baselines/``.

Merging a returned bundle compares it with that copy, unit by unit (by unit
id): a unit only the translator changed is taken, a unit changed in both is a
conflict for the user to decide, and only that bundle's language changes. A
bundle whose ``split_from`` names another master, or whose documents changed,
is refused. If the master's source documents changed after the split, the
returned files and the copy are first brought up to the new sources, and units
whose source changed are marked as needing review.

TIPP
====
:menuselection:`File --> Translation Bundle --> Export as TIPP…` writes a TIPP
2.0 package (a ``translate-strict-bitext`` request) for one target language,
its files as XLIFF in ``resources/bilingual/``. A TIPP package has one
language pair, so a bundle gives one per language.
