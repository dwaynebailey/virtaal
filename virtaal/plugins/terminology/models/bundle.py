#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The terms of the translation bundle open: its TBX files' terms in the
language open, while it is open, offered before every other source's
unless the user says otherwise. Read only: Add Term keeps adding to the
Local Files source."""

from translate.search.match import terminologymatcher
from translate.storage import po
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.common.pan_app import config_bool
from virtaal.plugins.terminology import termview
from virtaal.views.widgets.settingsdialog import BOOL, Field, SettingsDialog

from .basetermmodel import BaseTerminologyModel


class TerminologyModel(BaseTerminologyModel):
    __gtype_name__ = 'BundleTerminologyModel'
    display_name = _('Translation Bundle')
    description = _('The terms in the translation bundle open')

    default_config = {'first': True}
    settings = (
        # l10n: A setting of the Translation Bundle source of terms
        Field('first', _('_List its terms first'), BOOL),
    )

    def __init__(self, internal_name, controller):
        super().__init__(controller)
        self.internal_name = internal_name
        self.matcher = None
        self.load_config()
        self.configure_func = self.configure
        main_controller = controller.main_controller
        for signal in ('store-loaded', 'store-closed'):
            self._signal_tracker.connect(main_controller.store_controller, signal, self._reload)
        for signal in ('source-lang-changed', 'target-lang-changed'):
            self._signal_tracker.connect(main_controller.lang_controller, signal, self._reload)
        self._reload()

    def destroy(self):
        self._drop()
        super().destroy()

    def configure(self, parent):
        values = SettingsDialog(parent, self.display_name, self.settings, self.config).run()
        if values is not None:
            self.config.update(values)
            self.save_config()
            self._reload()

    def first(self):
        """Whether its terms are offered before other sources'."""
        return config_bool(self.config.get('first', True))

    def _reload(self, *_args):
        """Read the terms of the bundle open in its language."""
        had_terms = self.matcher is not None
        self._drop()
        main_controller = self.controller.main_controller
        bundle = main_controller.store_controller.bundle()
        target = getattr(main_controller.lang_controller.target_lang, 'code', None)
        pairs = bundle.terms(target) if bundle is not None and target else []
        if pairs:
            store = po.pofile()
            for term, translation in pairs:
                store.addsourceunit(term).target = translation
            self.matcher = terminologymatcher([store])
            TerminologyPlaceable.matchers.insert(0, self.matcher)
            if self.first():
                termview.first_matchers.append(self.matcher)
        if had_terms or pairs:
            self.controller.rescan_current_unit()

    def _drop(self):
        if self.matcher in TerminologyPlaceable.matchers:
            TerminologyPlaceable.matchers.remove(self.matcher)
        if self.matcher in termview.first_matchers:
            termview.first_matchers.remove(self.matcher)
        self.matcher = None
