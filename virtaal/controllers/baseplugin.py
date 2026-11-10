#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from virtaal.common import pan_app
from virtaal.common.configurable import Configurable


class PluginUnsupported(Exception):
    pass


class BasePlugin(Configurable):
    """The base interface to be implemented by all plug-ins."""

    CONFIG_FILENAME = "plugins.ini"

    configure_func = None
    """A function that starts the plug-in's configuration, if available."""
    description = ''
    """A description about the plug-in's purpose."""
    display_name = ''
    """The plug-in's name, suitable for display."""
    version = '0'
    """The plug-in's version number."""
    controller = None
    """The plug-in's own sub-controller, for plug-ins whose configure()/
        destroy() are the shared implementation below (one exposing a
        C{view.select_backends()})."""

    # INITIALIZERS #
    def __new__(cls, *args, **kwargs):
        """Create a new plug-in instance and check that it is valid."""
        if not cls.display_name:
            raise Exception('No name specified')
        if str(cls.version) <= '0':
            raise Exception('Invalid version number specified')
        return super().__new__(cls)

    def __init__(self):
        raise NotImplementedError('This interface cannot be instantiated.')

    # METHODS #
    def _config_int(self, name, minimum, maximum):
        """Replaces config[name] with it as a whole number from minimum
        to maximum, or with its default_config value."""
        self.config[name] = pan_app.config_int(
            self.config[name], int(self.default_config[name]), minimum, maximum, name
        )

    def _keep_off_until_enabled(self, names):
        """Disables each source in names that this profile's known_models
        doesn't list yet, then lists it there: a profile saved before a
        source existed has a disabled_models without it, which would
        otherwise switch that source on."""
        known = [name for name in self.config['known_models'].split(',') if name]
        for name in names:
            if name not in known:
                known.append(name)
                if name not in self.config['disabled_models']:
                    self.config['disabled_models'].append(name)
        self.config['known_models'] = known

    def configure(self, parent):
        """Shows C{self.controller}'s backend-selection dialog and persists
        the resulting config. Used by plug-ins that set
        C{self.configure_func = self.configure}."""
        self.controller.view.select_backends(parent)
        self.save_config()

    def destroy(self):
        """This method is called by C{PluginController.shutdown()} and should be
        implemented by all plug-ins that need to do clean-up."""
        if self.controller is not None:
            self.save_config()
            self.controller.destroy()
