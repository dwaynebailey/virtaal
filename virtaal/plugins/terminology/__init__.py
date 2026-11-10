#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from virtaal.controllers.baseplugin import BasePlugin

from .termcontroller import TerminologyController

# Online sources that stay off until turned on, also in a profile saved
# before they were added.
OFF_UNTIL_ENABLED = ('microsoft',)


class Plugin(BasePlugin):
    display_name = _('Terminology Help')
    description = _('Terminology suggestions')
    version = 0.1
    default_config = {
        'backends_dialog_width': 400,
        # autoterm's default URL (terminology.locamotion.org) is dead -
        # disabled until it has somewhere working to point at. The
        # plugin itself (fetch-any-URL terminology) stays, since it's a
        # distinct concept from Pontoon's own fixed, specific source.
        'disabled_models': 'autoterm,microsoft',
        'known_models': '',
        'max_matches': '5',
        'min_quality': '70',
    }

    # INITIALIZERS #
    def __init__(self, internal_name, main_controller):
        self.configure_func = self.configure
        self.internal_name = internal_name
        self.main_controller = main_controller
        self._init_plugin()

    def _init_plugin(self):
        self.load_config()
        self._config_int('backends_dialog_width', 1, 32767)
        self.config['disabled_models'] = self.config['disabled_models'].split(',')
        # A profile saved before autoterm's own default disabling (see
        # default_config above) keeps whatever disabled_models it last
        # saved, silently re-enabling a plugin whose one URL has been
        # dead for years - this isn't a user preference to honour, so
        # enforce it regardless of what an old config says.
        if 'autoterm' not in self.config['disabled_models']:
            self.config['disabled_models'].append('autoterm')
        self._keep_off_until_enabled(OFF_UNTIL_ENABLED)
        self._config_int('max_matches', 0, 1000)
        # A quality of 0 divides by zero when matching.
        self._config_int('min_quality', 1, 100)

        self.controller = TerminologyController(self.main_controller, self.config)
