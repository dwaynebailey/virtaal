
.. _apertium_mt:

Apertium MT
***********

`Apertium <http://www.apertium.org/about/>`_ is Free software for machine
translation (MT). It started as a project to translate between closely related
languages, but later versions also supported language pairs that were not that
close to each other. Most of the current language pairs offered by Apertium do
not use English as the source language.

Virtaal has a plugin to provide the Apertium output as suggestions. Contact the
Apertium project for information on what language pairs are supported, what
quality can be expected, and to hear how to get involved. More information is
available on `their wiki <http://wiki.apertium.org/>`_.

Apertium leaves your :doc:`placeables <placeables>` untranslated, though it
keeps each where it is in your text, even when the words around it move.

The plugin queries Apertium's public web service over the Internet, and
suggestions might therefore take a moment before they are displayed. Keep in
mind that your source text is sent over the Internet, and therefore no
confidential translation should be done using this plug-in.

To use your own Apertium server (APy) instead, set ``url`` in the
``[apertium]`` section of ``tm.ini`` in Virtaal's settings folder, for example
``url = http://localhost:2737``. The public server is run by volunteers, so
Virtaal looks up at most one unit ahead there (``prefetch`` in the same
section, off by default) and never asks it for a whole file; your own server
can be looked up two units ahead and asked with
:ref:`Fetch Suggestions for This File <using_virtaal#fetch_suggestions_for_a_whole_file>`.

Remember that the suggestions from the Apertium plugin are unreviewed
machine-generated translations, that could be wrong, inaccurate, or flawed in
some other way. It is meant as a way to help you increase your productivity,
not to substitute the expertise of a human translator.
