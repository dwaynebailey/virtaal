#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import os

from translate.search.match import terminologymatcher
from translate.storage import factory, po
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.common import pan_app
from virtaal.plugins.terminology.models.basetermmodel import BaseTerminologyModel
from virtaal.support import trados_compat  # noqa: F401 - applies its patch on import
from virtaal.support.multiterm import MissingReaderError

from .localfileview import LocalFileView


class TerminologyModel(BaseTerminologyModel):
    """
    Terminology model that loads terminology from a PO file on the local filesystem.
    """

    __gtype_name__ = 'LocalFileTerminologyModel'
    display_name = _('Local Files')
    description = _('Local terminology files')

    default_config = {
        'extendfile': os.path.join(pan_app.get_config_dir(), 'terminology.po'),
        'files': '',
    }

    # INITIALIZERS #
    def __init__(self, internal_name, controller):
        super().__init__(controller)

        self.matcher = None
        self.internal_name = internal_name
        self.stores = []
        self._loaded = {}

        self.load_config()
        self.load_files()
        self.view = LocalFileView(self)
        lang_controller = controller.main_controller.lang_controller
        for signal in ('source-lang-changed', 'target-lang-changed'):
            self._signal_tracker.connect(lang_controller, signal, self._on_lang_changed)

    # METHODS #
    def destroy(self):
        self.view.destroy()
        self.save_config()
        super().destroy()
        if self.matcher in TerminologyPlaceable.matchers:
            TerminologyPlaceable.matchers.remove(self.matcher)

    def add_term(self, store, source, target, comment=''):
        """Add a term to store, save it and make it matchable.

        Returns the new unit, or None if store already has it."""
        for unit in store.units:
            if unit.source.strip() == source.strip() and unit.target.strip() == target.strip():
                return None
        ids = {unit.getid() for unit in store.units}
        unit = store.addsourceunit(source)
        unit.target = target
        if isinstance(store, po.pofile):
            # A repeated msgid is invalid PO, so tell it apart by its target (#1238).
            context, n = target, 1
            while unit.getid() in ids:
                unit.setcontext(context)
                n += 1
                context = '%s %d' % (target, n)
        if comment:
            unit.addnote(comment)
        store.save()
        # Not extendtm(): only inittm() lowercases the sources it matches on.
        self.matcher.inittm(self.stores)
        return unit

    def _select_languages(self, store):
        """Have a termbase of many languages give the current pair.
        @returns: Whether its terms changed."""
        if not hasattr(store, 'select_languages'):
            return False
        lang_controller = self.controller.main_controller.lang_controller
        source = getattr(lang_controller.source_lang, 'code', None)
        target = getattr(lang_controller.target_lang, 'code', None)
        return store.select_languages(source, target)

    def _on_lang_changed(self, _lang_controller, _code):
        changed = [self._select_languages(store) for store in self.stores]
        if any(changed):
            self.matcher.inittm(self.stores)
            self.controller.rescan_current_unit()

    def get_duplicates(self, src_text, tgt_text):
        base_src_text = src_text.strip()
        base_tgt_text = tgt_text.strip()
        for store in self.stores:
            for unit in store.units:
                if unit.source.strip() == base_src_text and unit.target.strip() == base_tgt_text:
                    return True
        return False

    def get_extend_store(self):
        extendfile = self.config['extendfile']
        for store in self.stores:
            if os.path.abspath(getattr(store, 'filename', '')) == os.path.abspath(extendfile):
                return store
        return None

    def get_store_for_filename(self, filename):
        for store in self.stores:
            if os.path.abspath(getattr(store, 'filename', '')) == os.path.abspath(filename):
                return store
        return None

    def get_units_with_source(self, src_text):
        stripped_src_text = src_text.strip().lower()
        units = []
        for store in self.stores:
            for unit in store.units:
                if unit.source.strip().lower() == stripped_src_text:
                    units.append(unit)
        return units

    def load_config(self):
        super().load_config()
        conffiles = []
        for filename in self.config['files'].split(','):
            if os.path.exists(filename):
                conffiles.append(filename)
        self.config['files'] = conffiles

        if not os.path.exists(self.config['extendfile']) and len(self.config['files']) > 0:
            self.config['extendfile'] = self.config['files'][0]

        if not os.path.exists(self.config['extendfile']):
            self.config['extendfile'] = self.default_config['extendfile']

        if self.config['extendfile'] not in self.config['files']:
            self.config['files'].append(self.config['extendfile'])

    def load_files(self):
        """Load the configured files, reading only those not loaded yet."""
        if self.matcher in TerminologyPlaceable.matchers:
            TerminologyPlaceable.matchers.remove(self.matcher)

        loaded = {}
        for filename in self.config['files']:
            if not filename:
                continue
            if filename not in self._loaded:
                if not os.path.isfile(filename):
                    logging.debug('Not a file: "%s"' % (filename))
                try:
                    store = factory.getobject(filename)
                except MissingReaderError as e:
                    logging.warning("Can't read %s: %s", filename, e)
                    continue
                self._select_languages(store)
                self._loaded[filename] = store
            loaded[filename] = self._loaded[filename]
        self._loaded = loaded
        self.stores = list(loaded.values())
        self.matcher = terminologymatcher(self.stores)
        TerminologyPlaceable.matchers.append(self.matcher)
