#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Git projects: translate a team's files kept in a Git repository.

The plug-in adds Git to the translation platforms
(virtaal.support.platforms), which own the server list, File > Open
from Translation Server… and the work copies opened from it; Submit
commits and pushes, Update fetches and merges unit by unit (backend.py).
A server wanting a password asks for it (signin.py). Experimental: tested against local repositories only. Off until enabled
(pan_app.Settings' plugin_state).
"""

from virtaal.controllers.baseplugin import BasePlugin
from virtaal.support.platforms import registry

from .backend import GitPlatform
from .signin import asker


class Plugin(BasePlugin):
    # l10n: A plug-in's name in the plug-in list, for one not yet tested on the real service; %(name)s is the service, such as Transifex
    display_name = _('%(name)s (experimental)') % {'name': 'Git'}
    # l10n: The Git projects plug-in's description in the plug-in list
    description = _('Translate files a team keeps in a Git repository')
    version = 0.1

    # INITIALIZERS #
    def __init__(self, internal_name, main_controller):
        self.internal_name = internal_name
        self.main_controller = main_controller
        self.configure_func = self.configure
        self.platform = GitPlatform()
        credentials = self.platform.credentials
        credentials.ask = asker(lambda: main_controller.view.main_window, credentials.store.usable)
        registry.add(self.platform, main_controller)

    # METHODS #
    def configure(self, parent=None):
        registry.configure(parent)

    def destroy(self):
        registry.remove(self.platform)
