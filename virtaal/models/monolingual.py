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

from translate.storage import base, xliff

from virtaal.support import baseline, converters, dismissedchecks
from virtaal.support.baseline import (
    NEEDS_REVIEW,
    NEEDS_WORK,
    REVIEW_STATES,
    TRANSLATED,
    UNTRANSLATED,
    text_hash,
)
from virtaal.support.converters import PLURAL_SEPARATOR, unit_text


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

    if not _target_text(unit):
        return UNTRANSLATED
    state = unit.get_state_n()
    if state >= StateEnum.UNREVIEWED:
        return TRANSLATED
    return NEEDS_REVIEW if state >= StateEnum.NEEDS_REVIEW else NEEDS_WORK


def unit_key(unit):
    return unit.getid() or None


def _working_store(bilingual):
    """An XLIFF store with C{bilingual}'s units, keyed by their first
    location: XLIFF can hold Needs review, PO can't. A plural message
    becomes a L{PluralUnit}."""
    store = xliff.xlifffile()
    for unit in bilingual.units:
        locations = unit.getlocations()
        if unit.isheader() or not locations:
            continue
        if getattr(unit, 'plural_tags', None):
            working = PluralUnit(
                unit.source,
                unit.plural_tags,
                unit.file_source,
                unit.form_keys,
                getattr(unit, 'plural_ordinal', False),
            )
            store.addunit(working)
        else:
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
    return unit_text(unit.target, unit)


def _source_text(unit):
    """The source as the baseline and the history compare it: a plural
    message's template forms as L{plural_text()}."""
    return getattr(unit, 'file_source', None) or str(unit.source)


def _shown(text):
    """A plural message's L{plural_text()} as a note shows it."""
    return text.replace(PLURAL_SEPARATOR, '\n')


def _empty_target(unit):
    return [''] * len(unit.plural_tags) if getattr(unit, 'plural_tags', None) else ''


class PluralUnit(xliff.xliffunit):
    """A plural message of a monolingual file in the working XLIFF store.
    Its source forms (singular, plural) and target forms (one per
    C{plural_tags}, the target language's CLDR categories) are kept
    here; the XLIFF element keeps its id, state and notes."""

    def __init__(
        self, source, plural_tags=(), file_source=None, form_keys=(), ordinal=False, **kwargs
    ):
        sources = [str(form) for form in getattr(source, 'strings', [source or ''])]
        self._targets = []
        self.plural_tags = list(plural_tags)
        self.plural_ordinal = ordinal
        self.file_source = file_source
        self.form_keys = list(form_keys)
        # The element's source is set through the source property.
        super().__init__(sources[0], **kwargs)
        self._sources = sources

    def hasplural(self):
        return True

    @property
    def source(self):
        from translate.misc.multistring import multistring

        return multistring(self._sources)

    @source.setter
    def source(self, source):
        self._sources = [str(form) for form in getattr(source, 'strings', [source or ''])]
        self._rich_source = None
        self.setsource(self._sources[0])

    @property
    def target(self):
        from translate.misc.multistring import multistring

        return multistring(self._targets or [''])

    @target.setter
    def target(self, target):
        if isinstance(target, (list, tuple)):
            forms = list(target)
        else:
            forms = getattr(target, 'strings', [target or ''])
        self._targets = [str(form) if form else '' for form in forms]
        self._rich_target = None
        # The element's target holds the forms only so its state does.
        self.settarget('\n'.join(self._targets) if any(self._targets) else None)

    # Plain text, as other formats' units are: XLIFF's own rich text
    # would read the forms as XML.
    @property
    def rich_source(self):
        if self._rich_source is None:
            self._rich_source = base.TranslationUnit.multistring_to_rich(self, self.source)
        return self._rich_source

    @rich_source.setter
    def rich_source(self, value):
        self.source = base.TranslationUnit.rich_to_multistring(value)
        self._rich_source = list(value)

    @property
    def rich_target(self):
        if self._rich_target is None:
            self._rich_target = base.TranslationUnit.multistring_to_rich(self, self.target)
        return self._rich_target

    @rich_target.setter
    def rich_target(self, value):
        self.target = base.TranslationUnit.rich_to_multistring(value)
        self._rich_target = list(value)


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
        # Given when the path doesn't name it: plural forms follow it.
        self.language = None if self.source_only else language
        self.store = _working_store(
            self.converter.to_bilingual(self.template, self.target, self.language)
        )
        self.store.filename = self.target
        if not self.source_only:
            self._read_dismissals()
        if self.source_only:
            language = ''
            for _key, unit in self.units():
                unit.target = _empty_target(unit)
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

    def _read_dismissals(self):
        """The dismissed checks the target keeps in comments, as its
        units' notes."""
        if not os.path.isfile(self.target):
            return
        found = self.converter.dismissals(converters._read(self.target))
        for key, unit in self.units():
            if key in found:
                unit.addnote('\n'.join(found[key]), 'translator')

    @property
    def name(self):
        return self.converter.name

    @property
    def nplurals(self):
        """How many forms the target's plural messages have: its
        language's CLDR categories, not gettext's plural forms. C{None}
        without plural messages. An ordinal message has its own
        (L{PluralUnit.plural_tags})."""
        tags = [
            unit.plural_tags for _key, unit in self.units() if getattr(unit, 'plural_tags', None)
        ]
        if not tags:
            return None
        categories = converters.plural_categories(self.store.gettargetlanguage())
        return len(categories) if categories else len(tags[0])

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
        values = (
            self.converter.target_values(self.template, self.target, self.language)
            if renamed
            else {}
        )
        recalled = self._recalled() if not old_units else {}
        for key, unit in self.units():
            previous = old_units.get(key)
            if not old_units:
                units[key] = self._first_seen(key, unit, recalled.get(key))
            elif not _target_text(unit):
                units[key] = self._untranslated(key, unit, renamed, values)
            elif previous is None and self._migrated(unit, old_units):
                units[key] = self._known_key(key, unit, self._migrated(unit, old_units))
            elif previous is None:
                # Translated outside Virtaal along with the new key.
                self._mark(key, unit, _Review(outside=True), NEEDS_REVIEW)
                units[key] = baseline.entry(
                    _source_text(unit), _target_text(unit), NEEDS_REVIEW, outside=True
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
        targets = {key: _target_text(unit) for key, unit in self.units() if _target_text(unit)}
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
        current = _source_text(unit)
        if not target:
            return baseline.entry(current, None, UNTRANSLATED)
        if source == current:
            return baseline.entry(current, target, TRANSLATED)
        if source is not None:
            self._mark(key, unit, _Review(source), NEEDS_WORK)
            return baseline.entry(current, target, NEEDS_WORK, source)
        self._mark(key, unit, _Review(), NEEDS_REVIEW)
        return baseline.entry(current, target, NEEDS_REVIEW)

    def _untranslated(self, key, unit, renamed, values):
        old_key = renamed.get(text_hash(_source_text(unit)))
        carried = values.get(old_key) if old_key else None
        if carried and getattr(unit, 'plural_tags', None):
            forms = carried.split(PLURAL_SEPARATOR)
            carried = forms if len(forms) == len(unit.plural_tags) else None
        if carried:
            unit.target = carried
            self.carried += 1
            # The key may have been renamed for a change of meaning.
            self._mark(key, unit, _Review(), NEEDS_REVIEW)
        # What the file holds: the carried translation isn't in it yet.
        return baseline.entry(_source_text(unit), None, UNTRANSLATED)

    @staticmethod
    def _migrated(unit, old_units):
        """The baseline entry of a plural message whose forms an earlier
        Virtaal kept each under its own key, if it did: translated if
        every form was and is as recorded, else needing review."""
        keys = getattr(unit, 'form_keys', None)
        if not keys or not all(key in old_units for key in keys):
            return None
        entries = [old_units[key] for key in keys]
        forms = list(unit.target.strings) + [''] * len(keys)
        sources = set((unit.file_source or '').split(PLURAL_SEPARATOR))
        same = all(
            entry.get('target_hash') == text_hash(form) and entry.get('source') in sources
            for entry, form in zip(entries, forms, strict=False)
        )
        settled = same and all(entry.get('state') == TRANSLATED for entry in entries)
        state = TRANSLATED if settled else NEEDS_REVIEW
        return baseline.entry(_source_text(unit), _target_text(unit), state)

    def _known_key(self, key, unit, previous):
        target = _target_text(unit)
        if previous.get('state') == UNTRANSLATED and previous.get('target_hash') == text_hash(
            target
        ):
            # The template's text, written in for an untranslated key.
            unit.target = _empty_target(unit)
            return baseline.entry(_source_text(unit), target, UNTRANSLATED)
        review = _Review(previous.get('previous_source'), previous.get('outside', False))
        state = previous.get('state') if previous.get('state') in REVIEW_STATES else TRANSLATED
        if previous.get('target_hash') != text_hash(target):
            review.outside = True
            if state == TRANSLATED:
                state = NEEDS_REVIEW
        current = _source_text(unit)
        if previous.get('source_hash') != text_hash(current):
            # The translation may no longer fit.
            review.previous_source = review.previous_source or previous.get('source')
            state = NEEDS_WORK
        if review.previous_source == current:
            review.previous_source = None
        if state != TRANSLATED:
            self._mark(key, unit, review, state)
        return baseline.entry(current, target, state, review.previous_source, review.outside)

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
                % _shown(review.previous_source),
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
        translations = {
            key: list(unit.target.strings)
            if getattr(unit, 'plural_tags', None)
            else _target_text(unit)
            for key, unit in self.units()
            if _target_text(unit)
        }
        data = self.converter.to_monolingual(
            self.template, self.target, translations, self.skip_untranslated, self.language
        )
        dismissals = {key: dismissedchecks.lines(unit) for key, unit in self.units()}
        data = self.converter.with_dismissals(data, dismissals)
        with open(path, 'wb') as f:
            f.write(data)
        old = baseline.load(self.target) if path == self.target else None
        self.source_only = False
        self.target = path
        self.store.filename = path
        saved = self.converter.target_values(self.template, path, self.language)
        units = {}
        for key, unit in self.units():
            target = saved.get(key)
            state = state_name(unit)
            if state not in REVIEW_STATES:
                self.reviews.pop(key, None)
            review = self.reviews.get(key, _Review())
            units[key] = baseline.entry(
                _source_text(unit), target, state, review.previous_source, review.outside
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
