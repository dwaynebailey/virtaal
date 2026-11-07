
.. _placeables#placeables:

Placeables
**********

Placeables are special parts of the text that can be automatically highlighted
and easily inserted into the translation. By using placeables, a translator can
avoid certain errors, such as retyping numbers or acronyms incorrectly, or
perhaps introducing an error when retyping some program codes, such as XML.
Virtaal will help you to insert these placeables easily, and save you time
because you don't have to type them.

You can choose which placeables Virtaal should recognise in the preferences.
Virtaal will visually highlight the placeables that it recognises in the text.

.. image:: /_static/xml-placeable.png

Some examples of placeables that Virtaal can help you with:

- Numbers
- Acronyms
- XML
- E-mail addresses and URLs
- Variables used in software localisation
- Inline tags in XLIFF files
- Invisible characters, such as a no-break space or a direction mark

.. _placeables#selecting_and_inserting:

Selecting and Inserting
=======================
The first placeable is selected when you start a unit. Press :kbd:`Alt+Down`
to insert the selected placeable; the next one is then selected, so you can keep
pressing :kbd:`Alt+Down`. To choose a different placeable, press
:kbd:`Alt+Right` or :kbd:`Alt+Left`. The selection stops at the first and last
placeable.

.. _placeables#invisible_characters:

Invisible Characters
====================
Some characters show as nothing at all, or look like an ordinary space. The
editor marks each one without changing the text: ° for a no-break space, a
small box with a short name for a zero-width character (ZWSP, ZWJ, ZWNJ, WJ,
BOM), a direction mark or isolate (LRM, RLM, LRI, PDI…) and a soft hyphen
(SHY), → for a tab and ↵ for a line separator. Point at a box to see the
character's full name. They are placeables, so :kbd:`Alt+Down` inserts one
from the source.

A space is marked with · where it is easy to miss: two or more together, or at
the start or end of a line.
