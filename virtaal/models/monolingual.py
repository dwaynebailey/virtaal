#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A monolingual translation file paired with its template as one
bilingual store in memory. No GTK here."""

import os

from virtaal.support import baseline, converters
from virtaal.support.baseline import FUZZY, TRANSLATED, UNTRANSLATED, text_hash


def unit_key(unit):
    locations = unit.getlocations()
    return locations[0] if locations else None


def _target_text(unit):
    return str(unit.target) if unit.target else ''


class _Review:
    """Why a unit needs review, as the open-time comparison found."""

    def __init__(self, previous_source=None, outside=False):
        self.previous_source = previous_source
        self.outside = outside


class MonolingualPair:
    """C{target} translated from C{template}. C{store} is the bilingual
    store to edit; L{save()} writes only the target. Untranslated keys
    get the template's text there, unless C{skip_untranslated} (if not
    given, what the baseline last recorded)."""

    def __init__(self, target, template, language=None, skip_untranslated=None):
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
        self.reviews = {}
        """{key: _Review} of the units marked for review on open."""
        self.carried = 0
        """Translations carried over from a renamed key on open."""
        self.skip_untranslated = skip_untranslated
        self._compare()

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

    def _compare(self):
        """Mark what changed since the baseline, and record what was seen."""
        old = baseline.load(self.target)
        old_units = old['units'] if old else {}
        if self.skip_untranslated is None:
            self.skip_untranslated = bool(old and old.get('skip_untranslated'))
        keys = {key for key, _unit in self.units()}
        # Kept until a save, so a rename still carries over on a later open.
        units = {key: e for key, e in old_units.items() if key not in keys}
        renamed = {e.get('source_hash'): key for key, e in units.items()}
        values = self.converter.values(self.target) if renamed else {}
        for key, unit in self.units():
            previous = old_units.get(key)
            if old is None:
                units[key] = self._first_seen(key, unit)
            elif not unit.target:
                units[key] = self._untranslated(unit, renamed, values)
            elif previous is None:
                # Translated outside Virtaal along with the new key.
                self._mark(key, unit, _Review(outside=True))
                units[key] = baseline.entry(unit.source, _target_text(unit), FUZZY, outside=True)
            else:
                units[key] = self._known_key(key, unit, previous)
        data = dict(
            old or {}, template=self.template, units=units, skip_untranslated=self.skip_untranslated
        )
        if data != old:
            baseline.save(self.target, data)

    def _first_seen(self, key, unit):
        """With nothing to compare with, every translation needs review."""
        target = _target_text(unit)
        if not target:
            return baseline.entry(unit.source, None, UNTRANSLATED)
        self._mark(key, unit, _Review())
        return baseline.entry(unit.source, target, FUZZY)

    def _untranslated(self, unit, renamed, values):
        old_key = renamed.get(text_hash(unit.source))
        if old_key and values.get(old_key):
            unit.target = values[old_key]
            self.carried += 1
        # What the file holds: the carried translation isn't in it yet.
        return baseline.entry(unit.source, None, UNTRANSLATED)

    def _known_key(self, key, unit, previous):
        target = _target_text(unit)
        if previous.get('state') == UNTRANSLATED and previous.get('target_hash') == text_hash(
            target
        ):
            # The template's text, written in for an untranslated key.
            unit.target = ''
            return baseline.entry(unit.source, target, UNTRANSLATED)
        review = _Review(previous.get('previous_source'), previous.get('outside', False))
        fuzzy = previous.get('state') == FUZZY
        if previous.get('source_hash') != text_hash(unit.source):
            review.previous_source = review.previous_source or previous.get('source')
            fuzzy = True
        if review.previous_source == unit.source:
            review.previous_source = None
        if previous.get('target_hash') != text_hash(target):
            review.outside = fuzzy = True
        if fuzzy:
            self._mark(key, unit, review)
        return baseline.entry(
            unit.source,
            target,
            FUZZY if fuzzy else TRANSLATED,
            review.previous_source,
            review.outside,
        )

    def _mark(self, key, unit, review):
        unit.markfuzzy(True)
        self.reviews[key] = review
        if review.previous_source is not None:
            unit.addnote(
                _(
                    # l10n: Shown below a translation whose source text changed
                    # since it was translated; %s is the source text it had.
                    "Previous source: %s"
                )
                % review.previous_source,
                'translator',
            )
        if review.outside:
            unit.addnote(_("Translation changed outside Virtaal"), 'translator')

    def save(self, path=None):
        """Write the target (or C{path}) in its own format, and record
        what it now holds as the baseline."""
        path = os.path.abspath(path or self.target)
        translations = {key: _target_text(unit) for key, unit in self.units() if unit.target}
        data = self.converter.to_monolingual(
            self.template, self.target, translations, self.skip_untranslated
        )
        with open(path, 'wb') as f:
            f.write(data)
        old = baseline.load(self.target) if path == self.target else None
        self.target = path
        self.store.filename = path
        saved = self.converter.values(path)
        units = {}
        for key, unit in self.units():
            target = saved.get(key)
            if not unit.target:
                state = UNTRANSLATED
            else:
                state = FUZZY if unit.isfuzzy() else TRANSLATED
            if state != FUZZY:
                self.reviews.pop(key, None)
            review = self.reviews.get(key, _Review())
            units[key] = baseline.entry(
                unit.source, target, state, review.previous_source, review.outside
            )
        baseline.save(
            path,
            dict(
                old or {},
                template=self.template,
                units=units,
                skip_untranslated=self.skip_untranslated,
            ),
        )
