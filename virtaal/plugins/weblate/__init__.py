#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Weblate: translate a Weblate server's translations in Virtaal (#3337).

The plug-in adds Weblate to the translation platforms
(virtaal.support.platforms), which own the server list and File > Open
from Translation Server…. Phase W1 connects and browses. Off until
enabled (pan_app.Settings' plugin_state).
"""

from virtaal.controllers.baseplugin import BasePlugin
from virtaal.support.platforms import registry

from .backend import WeblatePlatform


class Plugin(BasePlugin):
    display_name = _('Weblate')
    description = _('Translate projects hosted on Weblate servers')
    version = 0.1

    # INITIALIZERS #
    def __init__(self, internal_name, main_controller):
        self.internal_name = internal_name
        self.main_controller = main_controller
        self.configure_func = self.configure
        self.platform = WeblatePlatform()
        registry.add(self.platform, main_controller)

    # METHODS #
    def configure(self, parent=None):
        registry.configure(parent)

    def destroy(self):
        registry.remove(self.platform)
