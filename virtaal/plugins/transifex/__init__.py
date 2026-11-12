#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Transifex: translate a Transifex project's resources in Virtaal.

The plug-in adds Transifex to the translation platforms
(virtaal.support.platforms), which own the server list, File > Open
from Translation Server… and the work copies opened from it.
Experimental: tested against a fake server only
(devsupport/testing/faketransifex.py), never a real account. Off until
enabled (pan_app.Settings' plugin_state).
"""

from virtaal.controllers.baseplugin import BasePlugin
from virtaal.support.platforms import registry

from .backend import TransifexPlatform


class Plugin(BasePlugin):
    # l10n: A plug-in's name in the plug-in list, for one not yet tested on the real service; %(name)s is the service, such as Transifex
    display_name = _('%(name)s (experimental)') % {'name': 'Transifex'}
    description = _('Translate projects hosted on Transifex')
    version = 0.1

    # INITIALIZERS #
    def __init__(self, internal_name, main_controller):
        self.internal_name = internal_name
        self.main_controller = main_controller
        self.configure_func = self.configure
        self.platform = TransifexPlatform()
        registry.add(self.platform, main_controller)

    # METHODS #
    def configure(self, parent=None):
        registry.configure(parent)

    def destroy(self):
        registry.remove(self.platform)
