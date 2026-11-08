
.. _checks:
.. _checks#quality_checks:

Quality Checks
**************

Virtaal provides a powerful way of reviewing translations for quality. It
exposes most of the :ref:`pofilter checks <toolkit:pofilter_tests>` that can
test for several issues that can affect the quality of your translations.

If Virtaal indicates a possible problem with a translation, it doesn’t mean
that the translation is necessarily wrong, just that you might want to review
it. You should also select the correct project type (GNOME, KDE, Mozilla, etc.)
in the project type selection. This will improve the accuracy of the quality
checks.

Virtaal shows the results of the quality checks in two ways: interactively for
the current unit while you type, and as a navigation mode, similar to searching
or "incomplete" mode.

.. _checks#interactive_checking:

Interactive Checking
====================
Virtaal will perform the quality checks on the current translation while you
type. Only the names of the possible issues are listed, but you can click on
them for more detail. The same details can also be shown by pressing :kbd:`F8`.
This might explain what the test mean, or could even mention detailed
information about the possible error.

You can also read the detailed descriptions of the
:ref:`pofilter checks <toolkit:pofilter_tests>`.

.. _checks#dismiss:

Dismissing a result
-------------------
If a translation is right in spite of a check, click :guilabel:`Dismiss`
beside the result in the details. Virtaal stops reporting that check for
the translation, also in the Quality Checks navigation mode, and lists it
under the others in the details, where :guilabel:`Restore` brings it back.
When only dismissed results are left, the checks button says how many,
for example "✓ 2 dismissed". Once the source or the translation changes,
the check is reported again; undoing the change dismisses it again.

Virtaal remembers a dismissed result in the file where it can, so it goes
wherever the file goes: as a translator comment in PO files, which survives
``msgmerge``, for example ``# (dismissed-check) endpunc 3f2a9c1e``, as a
translator note in XLIFF and Qt .ts files, and as a comment just before the
key in Java .properties, Fluent, Apple .strings and Android strings.xml
files. Virtaal also keeps them for you when you save, which is all a JSON or
ARB file has, and brings back any that another program removed from the
file while the translation is still the same.

.. _checks#msgfmt:

msgfmt
------
For PO files, Virtaal also reports what ``msgfmt -c`` would, in GNU
gettext's own words: format strings that don't match the source (going by
the ``c-format``, ``python-format`` and other flags), plural forms that
don't fit the header's ``Plural-Forms``, translations that don't start or
end with a newline like the source, and problems in the header. A file
with such a problem won't compile with ``msgfmt -c``.

Unlike ``msgfmt``, Virtaal also checks fuzzy translations.

.. _checks#quality_checks_navigation_mode:

Quality Checks Navigation Mode
==============================
In the "Quality checks" navigation mode, you can select certain quality checks
from the list of possible issues seen by Virtaal. The navigation mode allows
for batch review of quality checks. Selecting the name of a test will step you
through the translations that fail the test.

At each translation you have access to the test details as described above.

You might need to save to ensure that the navigation results are up to date.
