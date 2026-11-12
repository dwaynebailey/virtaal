.. _weblate#weblate:

Weblate
*******

`Weblate <https://weblate.org/>`_ is a web-based translation platform. Many
open-source projects keep their translations on a Weblate server, such as
`Hosted Weblate <https://hosted.weblate.org/>`_ or their own. With the Weblate
plugin, Virtaal opens a translation from such a server, lets you work on it
offline like any other file, and sends your changes back when you choose.

The plugin is off until you turn it on: open :menuselection:`Edit -->
Preferences`, tick Weblate under Features, and click its
:guilabel:`Configure…` button.

.. _weblate#connecting:

Connecting to a server
======================

:guilabel:`Configure…` opens Translation Servers, the list of the servers you
use. Click :guilabel:`Add…` and give:

- :guilabel:`URL`: the server's address, such as
  ``https://hosted.weblate.org/``. It must start with ``https://``: Virtaal
  won't send your token unencrypted.
- :guilabel:`Token`: your API token, from the API access page of your profile
  on that server. Tick :guilabel:`Show key` to check what you pasted.

:guilabel:`Test Connection` says how many projects the token sees, or why the
server refused it. If you use wlc, Weblate's command-line client,
:guilabel:`Import from wlc` adds the servers and tokens from its settings,
which Virtaal only reads.

Each server has two options, both off at first:

- :guilabel:`Send finished units as you go` (see
  :ref:`weblate#as_you_go`).
- :guilabel:`Submit as suggestions`, for a project where you may suggest
  translations but not change them (see :ref:`weblate#review`).

.. _weblate#browsing:

Browsing and opening a translation
==================================

Choose :menuselection:`File --> Open from Translation Server…`. Pick a
server, then a project, a component (glossaries are marked) and a language,
which shows how much of it is translated. Projects load as you scroll; the
filter box finds a project by name and loads more of them to find it.

Opening a language downloads its file, in the format the project keeps it in,
into a work copy on your computer, and opens it. A monolingual component, such
as Android strings or JSON, opens with its English file without asking. The
work copy is an ordinary file:

- :menuselection:`File --> Save` writes only your copy. It never sends
  anything.
- You can work offline, quit, and open it again from Recent Files. Unit states
  that the file format can't hold, such as Reviewed or Needs review in a PO
  file, are kept with the work copy and still show.
- A language that holds changes you haven't submitted is shown in bold in the
  server's list, and opens as it is, without downloading over your work.

A bar below the menu says how many of your changes aren't submitted yet, with
buttons to submit or update.

.. _weblate#opening_many:

Opening many languages or components
------------------------------------

Choose several languages at once (Ctrl or Shift and a click), or right-click a
language and choose :guilabel:`Open in All Components` to open that language in
every component of its project. They open together as one list of units, with
the Files Panel, so checks and searches over all the files work on them (see
Opening Several Files in :doc:`using_virtaal`). Submit and Update then take in
every file, with one question and one list of conflicts.

.. _weblate#submit:

Submit and conflicts
====================

:menuselection:`File --> Submit to Server…` saves the file, says how many
changed units it will send, and sends them, one unit at a time, with their
state.

Before it sends anything, Virtaal asks the server which units changed there
since you downloaded them. A unit someone else changed in the meantime isn't
sent over their work: Changed on the Server lists each one with your text and
theirs, and you choose:

- :guilabel:`Keep mine`: yours is sent, replacing theirs.
- :guilabel:`Take theirs`: their text replaces yours in your copy.
- :guilabel:`Skip`: nothing happens now; it comes up again next time.

Submit then reports what was sent, and lists any unit the server refused, with
the server's reason. It stops cleanly if the translation is locked on the
server, if you cancel it, or if the server's request limit runs out; the next
Submit sends only what is left. Before a large Submit, Virtaal warns if the
server's request limit is low.

Weblate may store a unit as needing editing if it fails a check the project
enforces; Virtaal shows the state the server stored.

.. _weblate#update:

Update from Server
==================

:menuselection:`File --> Update from Server…` brings others' changes into your
copy, keeping yours:

- A unit changed only on the server takes the server's text.
- A unit changed only by you keeps yours, to send with the next Submit.
- A unit changed on both sides is a conflict, which you resolve as for Submit.

If strings were added, removed or reworded on the server, Virtaal offers to
download the translation again, and first says which of your unsubmitted
changes can't be kept.

.. _weblate#as_you_go:

Send finished units as you go
=============================

With :guilabel:`Send finished units as you go` on for a server, each time you
save, the units you saved as Translated or Reviewed are sent in the background,
with the same conflict check as Submit. Units still marked as needing work wait
for Submit. A unit someone else changed on the server isn't sent: a bar says so,
and Submit asks you about it. If the server can't be reached, Virtaal tries
again later, and when the network comes back, also after a restart.

.. _weblate#review:

Suggestions, comments and approving
===================================

Others' suggestions for the unit you are on show with the unit, with buttons
to :guilabel:`Accept`, :guilabel:`Accept and Approve` or :guilabel:`Reject`
them. A suggestion you accept goes to the server with your next Submit, in its
suggester's name. Comments on a unit show with its notes. Right-click a unit
for :guilabel:`Add Comment…`, or :guilabel:`Report a Source Problem…` to tell
the project's maintainers about a mistake in the original text.

Reviewed is offered on projects that review translations. If the server
refuses to let you approve, or to reject others' suggestions, Virtaal stops
offering that on the project.

With :guilabel:`Submit as suggestions` on for a server, Submit sends each
change as a suggestion instead of changing the translation.

Suggestions need Weblate 2026.8 or later, and comments 5.12 or later; on older
servers they don't show.

.. _weblate#checks:

Checks and flags
================

Weblate's check flags on a component or a string, such as ``max-length:40`` or
``ignore-end-stop``, steer Virtaal's own :doc:`quality checks <checks>`. A
unit that fails a check on the server fails Virtaal's check named Weblate,
which says "Weblate reports a failing check". Before Submit,
Virtaal says how many of your changes fail checks the project enforces, which
Weblate may then store as needing editing.

.. _weblate#glossaries:

Glossaries
==========

The terms of the project's glossaries show in the terminology pane, with no
setup: see :ref:`autoterm#weblate`.

.. _weblate#privacy:

What is sent, and where
=======================

Everything goes only to the servers you add, over HTTPS:

- **Sent:** your token, with every request; the translations and states you
  submit (or send as you go); suggestions you accept or reject, comments and
  source reports you write.
- **Downloaded:** the translations you open, their units and states, the
  units' suggestions and comments, and the project's glossaries.

Nothing goes to anyone else. The token is sent in a request header and is
never written to Virtaal's log.

Your servers and tokens are kept in ``weblate.ini`` in Virtaal's settings
folder, in the same layout as wlc's. A token can change translations others
see, so Virtaal writes the file readable only by you. Remove a server in
Translation Servers to delete its token from the file. The work copies are
kept in Virtaal's state folder.

Requirements
============

Weblate 5.6 or later.
