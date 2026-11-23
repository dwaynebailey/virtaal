#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

from virtaal.common import pan_app

from ..cachedtermmodel import CachedRemoteTerminologyModel


class TerminologyModel(CachedRemoteTerminologyModel):
    """A terminology back-end to access the Translate.org.za-managed terminology."""

    __gtype_name__ = 'AutoTermTerminology'
    display_name = _('Localization Terminology')
    description = _('Selected localization terminology')

    _l10n_URL = 'http://terminology.locamotion.org/l10n/%(srclang)s/%(tgtlang)s'

    TERMDIR = os.path.join(pan_app.get_config_dir(), 'autoterm')

    # METHODS #
    def _etag_key(self, localfile):
        return os.path.abspath(localfile).lower()
