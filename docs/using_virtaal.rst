
.. _using_virtaal#using_virtaal:

Using Virtaal
*************
Virtaal is meant to be powerful yet simple to use. You can increase your
productivity by ensuring you know all the shortcuts and tricks and without
being distracted by a cluttered interface.

Although most features are available using the mouse, Virtaal is designed to
encourage you to work as much as possible with your keyboard to increase your
speed and keep the translation fun.

When you have no file open in Virtaal, you'll see the Virtaal dashboard with
helpful links to your recent files and various common tasks in the program.

.. _using_virtaal#opening_a_file:

Opening a File
==============
Mostly you should be able to simply open a translation file by clicking on the
file in your file manager (Windows Explorer, Nautilus, Konqueror, etc.). The
file might be associated with another program in which case you can look for
Virtaal in the context menu by right-clicking on the file.

You can also run Virtaal and open a file with :menuselection:`File --> Open` or
:kbd:`Ctrl+O`.

You can invoke Virtaal from the command line with ::

    virtaal <filename>

See the :doc:`command line options <cli_options>` page for the full list of
flags Virtaal accepts (e.g. :option:`virtaal --lang`).

Opening Several Files
---------------------
Select several files in :menuselection:`File --> Open`, or give several files
or a folder on the command line, to translate them one after the other. Their
units form one list: each file's name shows above its first unit, and moving
past a file's last unit goes on into the next file. A file is read only once
you come near it, so a large folder opens quickly.

Open a folder with :menuselection:`File --> Open Folder…`, or from
:menuselection:`File --> Recent Files` once you have. A folder opens the
translation files in it and its subfolders (PO, XLIFF and Qt .ts), leaving out
templates.

The :menuselection:`View --> Files Panel` (:kbd:`F7`) beside the units lists
the folders and files with how much of each is translated. It is open for a
folder, and hidden until you show it for several files. Select a file or
folder in it, or several with :kbd:`Ctrl`, to see only their units; press
:kbd:`Esc` in the panel to see every file again. With nothing selected, the
file you are in shows in bold.

:menuselection:`Navigation --> Next File` (:kbd:`Ctrl+Shift+PgDown`) and
:menuselection:`Navigation --> Previous File` (:kbd:`Ctrl+Shift+PgUp`) go to
the first unit of the next or previous file, and :kbd:`Ctrl+Home` and
:kbd:`Ctrl+End` to the first unit of the first file and the last unit of the
last one. In a navigation mode such as Incomplete, they skip files with
nothing in it. Moving from unit to unit stops at the first and last unit of
all the files, while search matches and quality check failures go round from
the last file to the first. The navigation ribbon beside the units has a line
where each file starts.

When the files are in several languages, those in the current target language
open, and the language list at the top of the panel opens another language's
files. A file that doesn't say its language is shown with "(?)" and opens with
every language.

With several languages, :menuselection:`Edit --> Check All Languages…` runs
the quality checks that compare a translation with its source (printf and
Python placeholders, XML tags, escapes, newlines, accelerators and others you
can tick) on every language's files, and :menuselection:`Edit --> Find in All
Languages…` searches them all. Both are also in the menu beside the language
list. They read the files as saved, and list each problem or match with its
language, file and unit number. Double-click one to open that language's
files at the unit; Virtaal asks to save your changes first.

:menuselection:`File --> Save` saves every file you changed, and closing or
quitting lists the files with unsaved changes.

A list of supported translation formats can be found on the :doc:`features
<features>` page.

.. _using_virtaal#normal_translation:

Normal Translation
==================
After opening a file, the first translation unit will be shown, with your
cursor in the field below the source text. You can simply type your translation
and press :kbd:`Enter` when finished -- just like in your word processor.

Note that :kbd:`Enter` moves you to the next position where you want to type.
In the case of units with :wiki:`plurals <guide/translation/plurals>`, enter
will take you to the next line in the same unit.

If you have the correct :doc:`spell checkers <spell_checking>` installed, spell
checking should be active for both the source and the target text.

You can undo normally using :kbd:`Ctrl+Z`.

.. _using_virtaal#time_savers:

Time Savers
===========

.. _using_virtaal#auto-completion:

Auto-completion
---------------
Virtaal will save you some time by trying to complete some long words for you.
Users of OpenOffice.org will already love this feature. Suggestions only come
from the segment you are translating: long words in its source and target,
including what you have typed so far, and after just two letters, the source's
placeables (such as numbers, URLs and XML tags) and translations of its
terminology. Typing ``%``, ``$``, ``{``, ``<``, ``&`` or a digit suggests a
placeable straight away, starting with those you haven't used yet.

Case doesn't matter while you type: names keep their own case, and other words
start with a capital at the start of a sentence. Numbers are suggested with
your language's decimal and group separators and digits, and typing a digit in
either script finds them. A name in capitals or CamelCase is suggested the way
you last wrote it in this file, in Latin letters or in your own script. You
will see the auto-completion suggesting a possible word, and the suggestion can
be accepted by pressing :kbd:`Tab`. If the suggestion is not what you want, you can simply
continue typing the word you had in mind. If you accepted a suggestion that you
don't want, you can simply undo normally with :kbd:`Ctrl+Z`.

.. _using_virtaal#auto-correction:

Auto-correction
---------------
Virtaal will save you some time by fixing certain common typing mistakes or
spelling errors. Users of OpenOffice.org will already love this feature. How
mistakes are corrected depends on your language, and there might possibly not
be information for your language yet. Feel free to get involved in the project
to improve this feature for your language.

If Virtaal automatically corrected something which you didn't want, you can
simply undo the step with :kbd:`Ctrl+Z`.

.. _using_virtaal#copy_original_to_target:

Copy Original to Target
-----------------------
Sometimes it is easier to have the original string as a start to only replace a
few translatable elements. Translations containing XML markup or many variables
might be more work to type again than to just start with the source text. You
can replace your translation with the original text by pressing
:kbd:`Ctrl+Shift+V` (:menuselection:`Edit --> Transfer From Source`). To copy
the original text without changing it, press :kbd:`Ctrl+C` with nothing
selected and paste it with :kbd:`Ctrl+V`.

For some languages, you will see how Virtaal automatically changes the
punctuation marks to fit the conventions of your language. This could involve
quotation or other punctuation marks, or the spacing between certain elements.
For example, a "quotation" automatically becomes a « quotation » in French,
without the translator having to change the quote characters or the spacing.

If you don't want the changes to the source text that Virtaal automatically
did, press :kbd:`Ctrl+Z` once to undo them; press it again to undo the copy.

.. _using_virtaal#copy_a_placeable_to_the_target:

Copy a Placeable to the Target
------------------------------

:doc:`Placeables <placeables>` are special parts of the text that can be
automatically highlighted and easily inserted into the translation. You will
see that certain parts of the source text will be highlighted, and the first
placeable is selected. Press :kbd:`Alt+Down` to insert the selected placeable;
the next one is then selected, so you can keep pressing :kbd:`Alt+Down`. To
choose a different placeable, press :kbd:`Alt+Right` or :kbd:`Alt+Left`. The
selection stops at the first and last placeable.

.. _using_virtaal#copy_a_term_to_the_target:

Copy a Term to the Target
-------------------------

Highlighted text will show which terms Virtaal recognised, and allow you to
handle them as placeables. You can use :kbd:`Alt+Right` and :kbd:`Alt+Down` the
same way as with other placeables. If there is more than one suggestion for a
term, Virtaal will display the choices in a menu. Select the translation you
want, or press :kbd:`Esc` to continue typing.

.. _using_virtaal#use_a_suggestion_from_tm_or_mt:

Use a Suggestion from TM or MT
------------------------------
If Virtaal has a suggestion obtained from translation memory or machine
translation, it is displayed underneath the editing area. You can put the first
suggestion into the target text with :kbd:`Ctrl+1`, or use :kbd:`Ctrl+2`, etc.
to select the others. You can also double click the suggestion to obtain the
same effect.

A translation memory suggestion for text that differs from yours only in its
:doc:`placeables <placeables>` or its accelerator key is adapted to your text.
For example, if the translation memory has "Verwyder {0} lêers" for "Delete {0}
files", translating "Delete %(count)s files" suggests "Verwyder %(count)s
lêers". An accelerator key such as the ``_`` in "Maak lêer _oop" takes the
marker your file's quality checks expect, such as ``&`` for KDE. A placeable
that differs counts for little in the match percentage, so such a suggestion
scores close to 100%, while only identical text scores 100%. Your local
translation memory finds such a suggestion even when the placeables' own text
makes the two look quite different. Hover over an adapted suggestion to see
what the translation memory has.

Machine translation keeps your placeables too: Virtaal asks the service to
leave them untranslated and puts your own placeables in its suggestion. Should
the service lose or repeat one, the suggestion has each placeable once, with any
lost one at the end, and hovering over it shows what the service gave. Moses
suggestions are shown as they come.

.. _using_virtaal#navigation:

Navigation
==========
Above we saw how we can easily advance to the next point of translation by
pressing :kbd:`Enter`. You can also move around easily between rows with
:kbd:`Ctrl+Down` and :kbd:`Ctrl+Up`. To move in large steps, use
:kbd:`Ctrl+PgDown` and :kbd:`Ctrl+PgUp`. To go straight to the first or
last unit, use :kbd:`Ctrl+Home` and :kbd:`Ctrl+End`.

.. _using_virtaal#incomplete_mode:

Incomplete Mode
---------------
Virtaal will move you between certain rows. Normally it will move between all
rows, but if you activate the "Incomplete" mode, it will move between
untranslated and fuzzy units. This allows you to quickly find the places where
you need to work. Translations will still appear between the same rows in the
file so that you can see the context that you are translating in, unless you
choose less :ref:`context <using_virtaal#context>`.

.. _using_virtaal#workflow_mode:

Workflow Mode
-------------
This mode allows you to move between specific units sharing the same state(s),
which can be specified in detail. For example, with a PO file you could move
across translated items, or only untranslated ones. Or with XLIFF, you could
move only across units that need more work, review process, or both.

.. _using_virtaal#searching_mode:

Searching Mode
--------------
Activate searching mode in the mode selector at the top, choose
:menuselection:`Edit --> Find…`, or simply press :kbd:`Ctrl+F` or :kbd:`F3`.
Virtaal will then move between all the rows that correspond to your search
query. :menuselection:`Edit --> Find Next` (:kbd:`Ctrl+G`) and
:menuselection:`Edit --> Find Previous` (:kbd:`Ctrl+Shift+G`) step through the
matches. Translations will still appear between the same rows in the file
so that you can see the context that you are translating in, unless you choose
less :ref:`context <using_virtaal#context>`.

To move back from the search box to your translation, simply press
:kbd:`Enter`, or go back to another mode.

.. _using_virtaal#quality_checks_mode:

Quality Checks Mode
-------------------

In the “Quality checks” navigation mode, you can select certain quality checks
from the list of possible issues seen by Virtaal. For more information, visit
the :doc:`quality checks <checks>` page.

.. _using_virtaal#priority_mode:

Priority Mode
-------------
Some projects rank their messages by how much they matter to users, in a
:doc:`priority file <priority_files>`, so that the most important ones can
be translated first. In the "Priority" navigation mode, choose one or more
priority levels from the list next to the mode selector: Virtaal then moves
between those levels' untranslated and fuzzy units. Each level shows how
many of its units are translated, e.g. "Level 1 (40/52)"; a level with
nothing left to do is greyed out. Messages the priority file doesn't list
aren't in any level.

Virtaal finds the priority file beside the translation file (see
:ref:`where Virtaal looks <priority_files#finding>`); Virtaal's own
translations in its ``po`` folder have one. Otherwise the list shows "No
Priority File", and you can choose one with "Load Priority File…".

.. _using_virtaal#context:

Context
-------
In every mode except "All", the "Context" selector at the top chooses which
rows are shown. "All", the default, shows every row. "None" shows only the
rows the mode moves between, and 1, 2 or 3 also show that many rows on either
side of the row you are translating. A darker line marks each place rows are
hidden. If nothing matches, the list is empty.

Clicking a row the mode doesn't move between takes you there without leaving
the mode; :kbd:`Enter` then continues to the next matching row.

.. _using_virtaal#privacy_issues:

Privacy Issues
==============

Commercial users of Virtaal should be aware of certain privacy issues:

.. _using_virtaal#virtaals_log_file:

Virtaal's Log File
------------------

A record of some of the source text and matches served are kept on the local
computer in a file called virtaal_log.txt (on Windows XP machines, this file
may be found in Application Data\Virtaal).  The log file is not deleted when a
file is closed in Virtaal or when Virtaal exists.  The file can be safely
deleted manually.

.. _using_virtaal#virtaals_local_tm:

Virtaal's Local TM
------------------

When you save a file, its translated segments go into Virtaal's local
translation memory (TM), a file called ``tm.db`` in Virtaal's settings
folder. Saving the same file again replaces its earlier translations: a
segment you changed is updated, and one that is no longer translated (or is
now fuzzy) is removed. The TM records which file each translation came from,
so a translation that another file also has stays.

The consequences are that (a) your translations remain on the local machine and
(b) translations from all previous texts are served as matches for all future
texts.

To add translations you made elsewhere without opening them, use
:menuselection:`File --> Import into Translation Memory…` and pick one or more
files: a TMX file exported from another translation tool, or any file Virtaal
can open, such as PO or XLIFF. Their translations go under the languages the
file gives (a TMX file's languages, an XLIFF file's, a PO file's Language
header), or under the current language pair if it gives none. A notice then
says how many translations were added. Importing a file again updates its
translations the same way saving does. The menu item is there only while the
Local Translation Memory is one of the sources of suggestions.

Microsoft's translations of its own software, the "Translation and UI Strings
Glossaries", can be imported the same way. Download them from `Visual Studio
Dev Essentials <https://my.visualstudio.com/downloads?pid=6822>`_ (the import
file chooser links there; you need a Microsoft account), then import the
downloaded .zip file or the .csv files in it. Each file's translations go under
the language in its name, such as af-ZA. The few rows with broken quoting in
Microsoft's files are left out. Microsoft's notice at the top of each file
says how you may use them.

To keep a file's translations out of the local TM, for example ones you are
not yet sure of, untick :menuselection:`File --> Add to Local TM on Save` while
the file is open. If saving it already added translations, Virtaal asks before
removing them; a translation another file also added stays. Saving the file
then adds nothing, and Virtaal remembers this for the file the next time you
open it. Tick the item again to let the file back in: its translations are
added straight away, or when you next save it if it has unsaved changes. Like
the import item, this one is there only while the Local Translation Memory is
a source of suggestions.

To see what is in the local TM, open :menuselection:`Edit --> Preferences`,
select Translation Memory under Features and click its :guilabel:`Configure…`
button, then do the same for Local Translation Memory in the list of sources.
The
Local Translation Memory window lists each file, per language pair, with how
many translations it holds, when it last changed, and whether it was saved or
imported; hover over a file for its full path. A file that is no longer where
it was is marked "file not found", and translations added before Virtaal
recorded their files are listed as "Earlier translations (file not
recorded)".

- :guilabel:`Remove` takes the selected file's translations for that language
  pair out of the TM, after asking. A translation another file also added
  stays.
- :guilabel:`Export…` saves translations as a TMX file, which other
  translation tools can import: the selected row's language pair (or the
  current one, with nothing selected), only the selected file's translations
  unless you untick :guilabel:`Only the selected file`.

The first time this version of Virtaal opens a ``tm.db`` from an older
version, it saves a copy of it as ``tm-v0.db`` beside it before updating it.
An older Virtaal can still use the updated file, and the copy can be deleted
once you no longer need it.

It is safe to delete and/or rename the file.

.. _using_virtaal#trados:

Trados files
------------
Trados Studio bilingual files (.sdlxliff) open like other XLIFF files. Trados
splits a paragraph into segments, usually one per sentence, and Virtaal shows
each segment as its own unit, as Trados does. Text and tags between the
segments of a paragraph aren't shown and stay as they are. Each unit also uses
what Trados keeps with its segment:

- The segment's Trados status is the unit's state:

  ======================================== ==============
  Trados status                            Virtaal state
  ======================================== ==============
  Not translated (no status), with text    Needs work
  Rejected Translation, Rejected Sign-off  Rejected
  Draft                                    Needs review
  Translated                               Translated
  Approved Translation, Approved Sign-off  Reviewed
  ======================================== ==============

  Choosing another state sets the matching Trados status (Rejected and
  Reviewed set Rejected Translation and Approved Translation; Untranslated
  and Needs work clear the status). A unit whose state you don't change keeps
  its status, Sign-off included.
- Where the translation came from, such as ``tm, 100% match``, shows below
  the translation, as do Trados comments on the segment.
- A locked segment shows its translation and state but can't be changed, and
  Replace skips it. Other segments of the same paragraph can still be
  changed.

A paragraph of several segments whose translation isn't split into them shows
as one unit with the plain XLIFF state, and its Trados status is left as it is.
Virtaal doesn't handle Trados tracked changes: both the added and the deleted
text show in the translation, each as a placeable.

Translation memories and termbases from Trados can be used as they are,
without Trados:

- A Trados Studio translation memory (.sdltm), a Translator's Workbench text
  export (.txt) or a MultiTerm XML export (.xml) can be imported with
  :menuselection:`File --> Import into Translation Memory…`. A Trados Studio
  translation memory's translations go under its own languages, such as en-US
  and de-DE, and are suggested when translating from English to German too.
  Their formatting tags are left out of the text, and Virtaal never changes
  the file.
- A MultiTerm XML export (.xml) or a Trados Studio or MultiTerm termbase
  (.sdltb) can be added to the terminology files with
  :menuselection:`Edit --> Terminology Files…`. A termbase can hold many
  languages: Virtaal suggests its terms for the current language pair, from the
  original's language to the translation's. Each term's other translations and
  its fields, such as a definition, are kept with it. Virtaal only reads these
  files, so it can't add terms to them.

Reading a .sdltb termbase needs the access-parser Python module. Without it,
export the termbase from MultiTerm as MultiTerm XML and add that file instead.


.. _using_virtaal#network_based_translation_memory:

Network Based Translation Memory
--------------------------------

If you have the appropriate plugins enabled, Virtaal will deliver results from
network based translation memories. Since the source text is sent to the
service provider, take care that you are allowed to do that. In the case where
the source text is confidential, this is probably not a good idea.

.. _using_virtaal#contributions_to_remote_tms:

Contributions to Remote TMs
---------------------------

Although Virtaal may query remote translation memories such as Amagama,
none of your own translations are automatically uploaded or
contributed to a public or remote TM.  The only way you can contribute your
translations to a public or remote TM is to send the PO file yourself, for
example via e-mail.

.. _using_virtaal#user_information_in_po_files:

User Information in PO Files
----------------------------

The first time you use Virtaal, you'll be prompted for your name, mail address
and team information.  This information is then added to all PO files you
translate in future.  If a PO file already has an author, its author will be
commented out and your name will be added as the current author.  PO files from
opensource projects are often made public, and the details you entered into
Virtaal (your name and mail address) may subsequently become available to spam
harvesters and search engines, in clear text.

.. _using_virtaal#network_based_machine_translation:

Network Based Machine Translation
---------------------------------

If you have the appropriate plugins enabled, Virtaal will deliver results from
network based machine translation engines. Since the source text is sent to the
service provider, take care that you are allowed to do that. In the case where
the source text is confidential, this is probably not a good idea.
