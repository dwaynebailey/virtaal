#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The terms of the glossaries of the projects opened from translation
servers (#3337), for any platform that can_glossary, such as Weblate's.

While a work copy is open (support/platforms/workcopy.py), the
glossaries of its project in its language show in the terminology
pane: those kept from before at once, and those the server has now once
it answers (support/platforms/glossary.py). Update from Server asks the
server again for those due, and a changed glossary shows at once. With
no translation platform's plug-in on, or no work copy open, it holds no
terms and asks nothing. Nothing being translated is sent; only the
glossaries are downloaded, from servers the user added. Read only: Add
Term keeps adding to the Local Files source.
"""

import logging

from translate.search.match import terminologymatcher
from translate.storage import factory
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.support import platforms
from virtaal.support.platforms import glossary

from .basetermmodel import BaseTerminologyModel


class TerminologyModel(BaseTerminologyModel):
    __gtype_name__ = 'PlatformGlossaryTerminology'
    # l10n: A terminology source: the glossaries of projects on translation servers such as Weblate
    display_name = _('Translation Server Glossaries')
    description = _('The glossaries of the projects you open from translation servers')

    def __init__(self, internal_name, controller):
        super().__init__(controller)
        self.internal_name = internal_name
        self.main_controller = controller.main_controller
        self.matcher = None
        self.stores = []
        self.shown = []
        """The Glossaries whose files the matcher holds."""
        self._asking = set()
        store_controller = getattr(self.main_controller, 'store_controller', None)
        if store_controller is not None:
            for signal in ('store-loaded', 'store-closed'):
                self._signal_tracker.connect(store_controller, signal, self._on_store_changed)
        self._load([])
        self._on_store_changed()
        glossary.watch(self.refresh_due)

    def destroy(self):
        glossary.unwatch(self.refresh_due)
        if self.matcher in TerminologyPlaceable.matchers:
            TerminologyPlaceable.matchers.remove(self.matcher)
        super().destroy()

    # The work copies open #

    def _platform(self, copy):
        for platform in platforms.registry.platforms:
            if platform.id == copy.record.get('platform') and platform.can_glossary:
                return platform
        return None

    def wanted(self):
        """The Glossaries of the work copies open, one each project and
        language."""
        store_controller = getattr(self.main_controller, 'store_controller', None)
        copies = (getattr(store_controller, 'work_copies', None) or {}).values()
        found = {}
        for copy in copies:
            if self._platform(copy) is not None:
                kept = glossary.for_copy(copy)
                found.setdefault(kept.folder, kept)
        return list(found.values())

    def _on_store_changed(self, *_args):
        wanted = self.wanted()
        self._load(wanted)
        self.refresh_due(wanted)

    def refresh_due(self, wanted=None):
        """Asks again for those of wanted (the work copies open's) that
        are due: on opening, and after Update from Server."""
        for kept in self.wanted() if wanted is None else wanted:
            if kept.due():
                self.refresh(kept)

    def refresh(self, kept):
        """Asks kept's server for its glossaries now, and shows them if
        they changed and are still wanted."""
        platform = next((p for p in platforms.registry.platforms if p.id == kept.platform_id), None)
        server = platforms.registry.servers().find(platform, kept.server) if platform else None
        if server is None or kept.folder in self._asking:
            return
        self._asking.add(kept.folder)

        def done(changed):
            self._asking.discard(kept.folder)
            wanted = self.wanted()
            if changed and any(w.folder == kept.folder for w in wanted):
                self._load(wanted)

        def failed(error):
            self._asking.discard(kept.folder)
            logging.info('No glossaries from %s: %r', kept.server, error)

        kept.refresh(server.session(), done, failed)

    # The terms #

    def _load(self, wanted):
        stores = []
        for kept in wanted:
            for path in kept.files():
                try:
                    stores.append(factory.getobject(path))
                except Exception as e:
                    logging.warning("Can't read the glossary %s: %s", path, e)
        if self.matcher in TerminologyPlaceable.matchers:
            TerminologyPlaceable.matchers.remove(self.matcher)
        self.stores = stores
        self.shown = wanted
        self.matcher = terminologymatcher(stores)
        TerminologyPlaceable.matchers.append(self.matcher)
        if stores or wanted:
            self.controller.rescan_current_unit()
