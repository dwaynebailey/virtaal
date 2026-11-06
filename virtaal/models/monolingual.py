#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A monolingual translation file paired with its template as one
bilingual store in memory. No GTK here."""

import os

from virtaal.support import converters


def unit_key(unit):
    locations = unit.getlocations()
    return locations[0] if locations else None


def _target_text(unit):
    return str(unit.target) if unit.target else ''


class MonolingualPair:
    """C{target} translated from C{template}. C{store} is the bilingual
    store to edit; L{save()} writes only the target."""

    def __init__(self, target, template, language=None):
        from virtaal.models.fileset import path_language

        self.converter = converters.monolingual_converter(target)
        if self.converter is None:
            raise ValueError('Not a monolingual file: %s' % target)
        self.target = os.path.abspath(target)
        self.template = os.path.abspath(template)
        self.store = self.converter.to_bilingual(self.template, self.target)
        self.store.filename = self.target
        self.store.settargetlanguage(language or path_language(self.target) or '')
        self.store.setsourcelanguage(path_language(self.template) or 'en')

    @property
    def name(self):
        return self.converter.name

    def units(self):
        """(key, unit) of the units to translate."""
        for unit in self.store.units:
            if unit.isheader() or not unit.istranslatable():
                continue
            key = unit_key(unit)
            if key is not None:
                yield key, unit

    def save(self, path=None):
        """Write the target (or C{path}) in its own format."""
        path = os.path.abspath(path or self.target)
        translations = {key: _target_text(unit) for key, unit in self.units() if unit.target}
        data = self.converter.to_monolingual(self.template, self.target, translations)
        with open(path, 'wb') as f:
            f.write(data)
        self.target = path
        self.store.filename = path
