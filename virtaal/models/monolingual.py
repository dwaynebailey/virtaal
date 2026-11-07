#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""A monolingual translation file paired with its template as one
bilingual store in memory. No GTK here."""

import logging
import os

from virtaal.support import baseline, converters
from virtaal.support.baseline import (
    NEEDS_REVIEW,
    NEEDS_WORK,
    REVIEW_STATES,
    TRANSLATED,
    UNTRANSLATED,
    text_hash,
)


def pairing(match, recall=None):
    """The L{StoreModel} arguments that open a monolingual file paired as
    the L{TemplateMatch} C{match} says, recalling earlier sources with
    C{recall} (L{sourcehistory.recall} if not given)."""
    if recall is None:
        from virtaal.support.sourcehistory import recall
    return {
        'template': match.template,
        'language': match.language,
        'skip_untranslated': match.skip_untranslated,
        'recall': recall,
    }


def _state_numbers():
    from translate.storage.workflow import StateEnum

    return {NEEDS_WORK: StateEnum.NEEDS_WORK, NEEDS_REVIEW: StateEnum.NEEDS_REVIEW}


def state_name(unit):
    """The baseline's name for C{unit}'s state."""
    from translate.storage.workflow import StateEnum

    if not unit.target:
        return UNTRANSLATED
    state = unit.get_state_n()
    if state >= StateEnum.UNREVIEWED:
        return TRANSLATED
    return NEEDS_REVIEW if state >= StateEnum.NEEDS_REVIEW else NEEDS_WORK


def unit_key(unit):
    return unit.getid() or None


def _working_store(bilingual):
    """An XLIFF store with C{bilingual}'s units, keyed by their first
    location: XLIFF can hold Needs review, PO can't."""
    from translate.storage import xliff

    store = xliff.xlifffile()
    for unit in bilingual.units:
        locations = unit.getlocations()
        if unit.isheader() or not locations:
            continue
        working = store.addsourceunit(unit.source)
        working.setid(locations[0])
        working.target = unit.target
        notes = unit.getnotes('developer')
        if notes:
            working.addnote(notes, 'developer')
    # Without a file name, XLIFF's ids are the bare keys.
    for node in store.document.getroot().iterchildren(store.namespaced('file')):
        node.set('original', '')
    return store


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
    given, what the baseline last recorded, else the format's habit).

    With no baseline, C{recall(pair, {key: translation})} may give
    {key: source} of the source each translation was made for: one that
    differs from the template's now needs work.

    A template paired with itself is the source-language file: its text
    as source, no translations, and it can't be saved over."""

    def __init__(self, target, template, language=None, skip_untranslated=None, recall=None):
        from virtaal.models.fileset import path_language

        self.converter = converters.monolingual_converter(target)
        if self.converter is None:
            raise ValueError('Not a monolingual file: %s' % target)
        self.target = os.path.abspath(target)
        self.template = os.path.abspath(template)
        self.source_only = os.path.normcase(self.target) == os.path.normcase(self.template)
        self.store = _working_store(self.converter.to_bilingual(self.template, self.target))
        self.store.filename = self.target
        if self.source_only:
            language = ''
            for _key, unit in self.units():
                unit.target = ''
        self.store.settargetlanguage(
            path_language(self.target) or '' if language is None else language
        )
        self.store.setsourcelanguage(path_language(self.template) or 'en')
        self.reviews = {}
        """{key: _Review} of the units marked for review on open."""
        self.carried = 0
        """Translations carried over from a renamed key on open."""
        self.skip_untranslated = skip_untranslated
        self._recall = recall
        if not self.source_only:
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
        if self.skip_untranslated is None and old and 'skip_untranslated' in old:
            self.skip_untranslated = bool(old['skip_untranslated'])
        elif self.skip_untranslated is None:
            self.skip_untranslated = self.converter.leaves_out_untranslated
        keys = {key for key, _unit in self.units()}
        # Kept until a save, so a rename still carries over on a later open.
        units = {key: e for key, e in old_units.items() if key not in keys}
        renamed = {e.get('source_hash'): key for key, e in units.items()}
        values = self.converter.values(self.target) if renamed else {}
        recalled = self._recalled() if not old_units else {}
        for key, unit in self.units():
            previous = old_units.get(key)
            if not old_units:
                units[key] = self._first_seen(key, unit, recalled.get(key))
            elif not unit.target:
                units[key] = self._untranslated(unit, renamed, values)
            elif previous is None:
                # Translated outside Virtaal along with the new key.
                self._mark(key, unit, _Review(outside=True), NEEDS_REVIEW)
                units[key] = baseline.entry(
                    unit.source, _target_text(unit), NEEDS_REVIEW, outside=True
                )
            else:
                units[key] = self._known_key(key, unit, previous)
        data = dict(
            old or {}, template=self.template, units=units, skip_untranslated=self.skip_untranslated
        )
        if data != old:
            baseline.save(self.target, data)

    def _recalled(self):
        if self._recall is None:
            return {}
        targets = {key: _target_text(unit) for key, unit in self.units() if unit.target}
        try:
            return self._recall(self, targets)
        except Exception:
            logging.exception("Couldn't recall the sources of %s", self.target)
            return {}

    def _first_seen(self, key, unit, source=None):
        """With nothing to compare with, a translation whose source is
        recalled is translated if that C{source} is the same now, and
        needs work if it changed since; any other needs review."""
        target = _target_text(unit)
        if not target:
            return baseline.entry(unit.source, None, UNTRANSLATED)
        if source == unit.source:
            return baseline.entry(unit.source, target, TRANSLATED)
        if source is not None:
            self._mark(key, unit, _Review(source), NEEDS_WORK)
            return baseline.entry(unit.source, target, NEEDS_WORK, source)
        self._mark(key, unit, _Review(), NEEDS_REVIEW)
        return baseline.entry(unit.source, target, NEEDS_REVIEW)

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
        state = previous.get('state') if previous.get('state') in REVIEW_STATES else TRANSLATED
        if previous.get('target_hash') != text_hash(target):
            review.outside = True
            if state == TRANSLATED:
                state = NEEDS_REVIEW
        if previous.get('source_hash') != text_hash(unit.source):
            # The translation may no longer fit.
            review.previous_source = review.previous_source or previous.get('source')
            state = NEEDS_WORK
        if review.previous_source == unit.source:
            review.previous_source = None
        if state != TRANSLATED:
            self._mark(key, unit, review, state)
        return baseline.entry(unit.source, target, state, review.previous_source, review.outside)

    def _mark(self, key, unit, review, state):
        unit.set_state_n(_state_numbers()[state])
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
        if os.path.normcase(path) == os.path.normcase(self.template):
            raise OSError(_('This file is read-only.'))
        translations = {key: _target_text(unit) for key, unit in self.units() if unit.target}
        data = self.converter.to_monolingual(
            self.template, self.target, translations, self.skip_untranslated
        )
        with open(path, 'wb') as f:
            f.write(data)
        old = baseline.load(self.target) if path == self.target else None
        self.source_only = False
        self.target = path
        self.store.filename = path
        saved = self.converter.values(path)
        units = {}
        for key, unit in self.units():
            target = saved.get(key)
            state = state_name(unit)
            if state not in REVIEW_STATES:
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
