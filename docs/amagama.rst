
.. _amagama:

Amagama
*******

Virtaal provides TM suggestions from the Amagama Translation Memory server.
Amagama contains translations of FOSS projects.

The plugin queries a web service over the Internet, and suggestions might
therefore take a moment before they are displayed. Also keep in mind that your
source text is sent unencrypted over the Internet, and therefore no
confidential translation should be done using this plug-in.

Where the server supports it, Virtaal sends your text with each placeable
(a variable, tag, URL, e-mail address, file name or number) replaced by a
marker, so those never leave your computer. Amagama then also finds
translations whose placeables differ from yours, and Virtaal puts your own
placeables into the suggestion. An older server gets the text as it is.

Remember that the suggestions from the Amagama plugin come from a variety of
sources, and might not be perfect for what you need. It is meant as a way to
help you increase your productivity, but still needs your expertise.

Choosing Amagama's Projects
===========================
To choose which projects Amagama's suggestions come from, open
:menuselection:`Edit --> Preferences`, select Translation Memory under Features
and click its :guilabel:`Configure…` button, then do the same for Amagama in
the list of sources. The Amagama Sources window lists the projects Amagama has
translations from for your languages, with their licences. Untick a project to
leave out its suggestions. A project's weight says how much to prefer it: 100%
is usual, and Amagama gives projects it trusts less a lower weight, such as
80%. Suggestions are listed by their match percentage times the weight of their
best project, so a project weighted 150% comes before others with a slightly
better match, while the percentage shown stays the match. Exact (100%) matches
always come first. Suggestions from other sources count as 100%. When Amagama
has more suggestions than Virtaal shows, it keeps those from projects with a
higher weight. Each change applies to the next suggestions.

To use only translations under licences that suit you, choose them under
:guilabel:`Licenses`: :guilabel:`Free software` keeps projects under free and
open source licences, copyleft such as the GPL and permissive such as MIT;
:guilabel:`Permissive only` keeps those under permissive licences, such as MIT,
BSD, Apache or CC-BY. Hold the pointer over the choice to see the licences it
keeps. For others, choose :guilabel:`Custom…` and list them as SPDX
identifiers separated by commas, such as ``GPL-*, MIT``: ``*`` stands for any
characters, and ``!`` leaves a licence out, so ``!AGPL-*`` accepts every
licence but the AGPL. A project whose licence offers a choice, such as ``MIT OR
GPL-2.0-only``, is kept if one of the choices is listed. Projects with no known
licence are then left out. The choice is kept as ``licence`` in the
``[amagama]`` section of ``tm.ini``.

Virtaal keeps the list of projects, so the window still works offline.
