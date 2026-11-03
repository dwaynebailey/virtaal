#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

from translate.storage import factory

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
        return os.path.abspath(localfile)

    def _accepts_response(self, content, ext):
        return self._parses_as(content, ext)

    def _parses_as(self, content, ext):
        """A captive-portal/signon network responds with its own login
        page instead of the real file, but still with a normal 200
        status - check the response actually parses as the format
        we're about to save it as before trusting it."""
        from io import BytesIO

        buf = BytesIO(content)
        buf.name = 'dummy.%s' % (ext)
        try:
            factory.getobject(buf)
        except Exception:
            return False
        return True
