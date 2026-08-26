
.. _index#virtaal:

Virtaal
*******

.. image::  /_static/virtaal_logo.png
   :alt: Virtaal logo
   :align: right

Virtaal is a graphical translation tool. It is meant to be easy to use and
powerful at the same time. Although the initial focus is on software
translation (localisation or l10n), we definitely intend it to be useful for
several purposes.

Virtaal is built on the powerful API of the `Translate Toolkit
<http://toolkit.translatehouse.org>`_. "Virtaal" is an Afrikaans play on words
meaning "For Language", but also refers to translation.

Read more about the :doc:`features <features>` in Virtaal, or view the
:doc:`screenshots <screenshots>`.  You can also download a `screencast
<http://l10n.mozilla-community.org/pootle/screencasts/virtaal-0.3.ogv>`_ (33MB,
Ogg Theora format) to see some of these features in action.

Learn more about :doc:`using Virtaal <using_virtaal>`, available
:doc:`shortcuts <cheatsheet>` and some extra :doc:`tips and tricks <tips>` for
people who want to customise their installation.

.. toctree::
   :maxdepth: 1
   :hidden:

   using_virtaal
   features
   screenshots
   cheatsheet
   tips

.. _index#installation:

Installation
============

.. note:: This documents `dwaynebailey/virtaal
   <https://github.com/dwaynebailey/virtaal>`_, an active fork
   continuing development (including a Python 3 port) after the
   original `translate/virtaal
   <https://github.com/translate/virtaal>`_ project archived. There's
   no polished download page or distro packages for this fork yet -
   the options below are what's actually available today.

+-----------------+------------------------------------------------------------------------+-----------------------------------------------+
| Platform        | Instructions                                                           | Notes                                         |
+=================+========================================================================+===============================================+
| Windows         | Grab the latest ``Virtaal-windows-installer`` artifact from a          | Built fresh on every commit to ``py3``        |
|                 | successful `Actions run                                                |                                               |
|                 | <https://github.com/dwaynebailey/virtaal/actions>`_, or build it       |                                               |
|                 | yourself - see :doc:`building`                                         |                                               |
+-----------------+------------------------------------------------------------------------+-----------------------------------------------+
| macOS           | Grab the latest ``Virtaal-macos-dmg`` artifact the same way, or build  | Built fresh on every commit to ``py3``        |
|                 | it yourself - see :doc:`building`                                      |                                               |
+-----------------+------------------------------------------------------------------------+-----------------------------------------------+
| Linux and other | Run from a checkout, or build it yourself - see :doc:`building`        | No distro packages exist for this fork        |
+-----------------+------------------------------------------------------------------------+-----------------------------------------------+

.. _index#contact:

Contact
=======
- `Report bugs or request features on this fork
  <https://github.com/dwaynebailey/virtaal/issues/new>`_ - the
  primary, actively-watched channel for this fork specifically.
- The wider Translate community (covers the whole Translate Toolkit/
  Pootle/Virtaal family, not just this fork) still has a `Gitter
  channel <https://gitter.im/translate/pootle>`_ and a `Translate-devel
  mailing list <https://lists.sourceforge.net/lists/listinfo/translate-devel>`_ -
  both confirmed still reachable, though quieter than they once were.

.. _index#contributing:

Contributing
============
There are many ways of contributing to Virtaal. Join the mailing list or IRC
channel to join our effort. You can join our effort to distribute Virtaal by
sharing informing with people, writing documentation or packaging for more
platforms.

If you would like to contribute to the Virtaal software, you can start by
reading the instructions on the following pages:

.. toctree::
   :maxdepth: 1

   localising_virtaal
   building
   testing
   development_plans
   suggestions
