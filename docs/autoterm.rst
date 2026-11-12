
.. _autoterm:

Automatic Terminology Assistance
********************************

Virtaal can provide terminology help in many ways. One of the most powerful
ways is where Virtaal downloads a recommended file of localisation terms for
your language.

By default, this file comes from `Mozilla Pontoon
<https://pontoon.mozilla.org/terminology/>`_, for any language pair it
covers - no configuration needed for that.

This powerful feature has many benefits:

- This works without any configuration, as long as a terminology plugin
  providing it is enabled.
- Inexperienced translators will get terminology configured, even if they don't
  know that they should be doing something like this.
- Since the terminology is not packaged with Virtaal, teams can maintain their
  terminology, and don't need to "have it ready" for a Virtaal release.
- Since Virtaal will check every few days for new versions of the file,
  translators can use up to date terminology, without having to even know that
  maintenance was done on the list.

Your own terminology files, in any format Virtaal supports, go under
:menuselection:`Edit --> Terminology Files…`. With the Weblate plugin on,
Virtaal also uses the glossary of the Weblate project you translate: see
:ref:`autoterm#weblate`.

.. _autoterm#microsoft:

Microsoft Terminology
=====================

Virtaal can also suggest the terms Microsoft uses in its software, from the
`Microsoft Terminology Collection
<https://learn.microsoft.com/globalization/reference/microsoft-terminology>`_,
when you translate from English. It is off until you turn it on: open
:menuselection:`Edit --> Preferences`, select Terminology Help under Features,
click its :guilabel:`Configure…` button and tick Microsoft Terminology.

Virtaal then downloads only your language's file from Microsoft (up to a few
megabytes) and keeps it in its settings folder. It checks once a week whether
Microsoft has a newer one. Nothing you translate is sent to Microsoft.

.. _autoterm#weblate:

Weblate Glossaries
==================

Weblate keeps a project's terms in a glossary, a component of the project.
When you open a translation from a Weblate server (:menuselection:`File -->
Open from Translation Server…`, once Weblate is turned on under Features in
:menuselection:`Edit --> Preferences`), Virtaal also shows the terms of that
project's glossaries in your language, with no setup.

Virtaal downloads each glossary that has your language, as a TBX file, and
keeps it with your work, so the terms are there offline too. Each time you
open a translation of that project, and each time you choose
:menuselection:`File --> Update from Server…`, it asks Weblate whether the
glossary changed, at most once a minute, and downloads it again only if it
did. A changed term shows at once.
Nothing you translate is sent; Virtaal only downloads the glossaries, with
the token you gave for that server.

The terms are read only: :menuselection:`Edit --> Add Term…` still adds to
your own terminology file, not to the Weblate glossary. To stop using Weblate
glossaries, untick Translation Server Glossaries in the list behind Terminology
Help's :guilabel:`Configure…` button.
