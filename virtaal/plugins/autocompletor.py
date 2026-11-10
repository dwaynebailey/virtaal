#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Contains the AutoCompletor class."""

import itertools
import re
import unicodedata
from collections import Counter, defaultdict

from gi.repository import GLib
from translate.storage.placeables import StringElem, general
from translate.storage.placeables import parse as parse_placeables
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.controllers.baseplugin import BasePlugin
from virtaal.support.invisiblechars import InvisiblePlaceable
from virtaal.support.libi18n.numbers import localise_number_forms
from virtaal.views.widgets.textbox import TextBox


def _combining_marks():
    """A regular expression character class body for every combining mark
    (Mn, Mc, Me) below U+20000 and the zero width (non-)joiner: vowel
    signs, viramas and ZWNJ are part of words, though not \\w."""
    ranges = []
    for code in range(0x20000):
        if unicodedata.category(chr(code))[0] == 'M':
            if ranges and ranges[-1][1] == code - 1:
                ranges[-1][1] = code
            else:
                ranges.append([code, code])
    return '\u200c\u200d' + ''.join(
        re.escape(chr(a)) + ('-' + re.escape(chr(b)) if b > a else '') for a, b in ranges
    )


def _fold_table():
    """str.translate() table for AutoCompletor.fold()."""
    table = {ord(c): ' ' for c in '\u00a0\u2007\u2009\u202f'}
    for code in range(0x20000):
        char = chr(code)
        digit = unicodedata.decimal(char, None)
        if digit is not None and not 0x30 <= code <= 0x39:
            table[code] = str(digit)
        elif len(char.lower()) == 1 and char.lower() != char:
            table[code] = char.lower()
    # Turkish İ: lower() gives i and a combining dot above.
    table[0x130] = 'i'
    return table


_FOLD = _fold_table()
_WORD_CHARS = r'\w' + _combining_marks()
# What ends a sentence: ., !, ?, … and the Armenian, Arabic, Urdu,
# Devanagari, Ethiopic, CJK and full-width ones.
_SENTENCE_ENDS = '.!?…։؟۔।॥።。！？'
# What may open a sentence before its first word (Spanish ¿Dónde?, ¡Hola!).
_OPENING_MARKS = '¿¡'
# The Afrikaans indefinite article: "'n Lêer is oop." capitalises the
# word after it.
_AF_ARTICLES = ("'n", '\u2019n', '\u0149')
# Languages whose i and ı are İ and I in capitals.
_DOTTED_I = frozenset(('tr', 'az', 'crh', 'gag'))


def _upper(text, language):
    """text in capitals as language writes them."""
    if language in _DOTTED_I:
        text = text.replace('i', '\u0130')
    return text.upper()


def _capitalised(word, language):
    """word with a capital first letter as language writes it: Dutch
    capitalises an initial ij as IJ."""
    if language == 'nl' and word[:2].lower() == 'ij':
        return 'IJ' + word[2:]
    if not word[:1].islower():
        return word
    return _upper(word[0], language) + word[1:]


class NameForms:
    """How the translator writes the source's names in capitals or
    CamelCase in this file: kept in Latin letters, or in another script.

    The other script's form of a name is the one word of another script
    in the target of every unit with the name in its source and not in
    its target, and in the target of no other unit. It is learnt from
    its second such unit: one unit alone is often too little to tell."""

    name_res = (general.CapsPlaceable.regex, general.CamelCasePlaceable.regex)
    CHUNK = 2000  # Units read per call of the L{load()} generator.
    USES = 2  # Units with the other form before it is offered.

    def __init__(self, word_re):
        self.word_re = word_re
        self.clear()

    def clear(self):
        self._seq = 0
        self._counts = Counter()  # Units with each other-script target word.
        self._units = {}  # id(unit): [seq, names, names kept in Latin, words]
        self._by_name = defaultdict(set)
        self._read = set()  # id() of every unit read
        self._entered = None  # (unit, its target, its words if read) since L{enter()}

    @classmethod
    def names(cls, texts):
        return {m.group() for text in texts for regex in cls.name_res for m in regex.finditer(text)}

    def enter(self, unit):
        """Remember unit's target as it is before editing."""
        words = self._words(unit) if id(unit) in self._read else None
        self._entered = (unit, self._target(unit), words)

    def forms(self, name):
        """name's forms, the preferred one first: as the translator last
        wrote it in a unit of this file, kept in Latin or another form."""
        units = [self._units[key] for key in self._by_name.get(name, ())]
        translated = [u for u in units if u[3] or u[2]]
        other = [u for u in translated if name not in u[2]]
        if len(other) < self.USES:
            return [name]
        common = set.intersection(*(u[3] for u in other))
        with_name = Counter(w for u in units for w in u[3])
        forms = [w for w in common if self._counts[w] == with_name[w]]
        if len(forms) != 1:
            return [name]
        if name in max(translated, key=lambda u: u[0])[2]:
            return [name, forms[0]]
        return [forms[0], name]

    def leave(self, unit):
        """Read unit's target again if it changed since L{enter()}."""
        entered, self._entered = self._entered, None
        if unit is None or entered is None or entered[0] is not unit:
            return
        if entered[2] is not None:
            if self._target(unit) == entered[1]:
                return
            self._counts.subtract(entered[2])
            self._forget(unit)
        self._read_unit(unit)

    def load(self, units):
        """Forget the previous file's units and return a generator that
        reads these, L{CHUNK} per step, for running from the main loop."""
        self.clear()

        def read():
            for start in range(0, len(units), self.CHUNK):
                for unit in units[start : start + self.CHUNK]:
                    if id(unit) in self._read:
                        continue
                    if self._entered is None or self._entered[0] is not unit:
                        self._read_unit(unit)
                yield True

        return read()

    def _forget(self, unit):
        record = self._units.pop(id(unit), None)
        if record:
            for name in record[1]:
                self._by_name[name].discard(id(unit))

    def _read_unit(self, unit):
        self._read.add(id(unit))
        words = self._words(unit)
        self._counts.update(words)
        names = self.names(AutoCompletor._strings(unit.source))
        if not names:
            return
        target = self._target(unit)
        self._seq += 1
        self._units[id(unit)] = [self._seq, names, {n for n in names if n in target}, words]
        for name in names:
            self._by_name[name].add(id(unit))

    @staticmethod
    def _target(unit):
        return '\n'.join(AutoCompletor._strings(unit.target))

    def _words(self, unit):
        """The words of unit's target in another script than Latin."""
        words = set()
        for text in AutoCompletor._strings(unit.target):
            for word in self.word_re.findall(text):
                letter = next((c for c in word if c.isalpha()), '')
                if letter and not unicodedata.name(letter, '').startswith('LATIN'):
                    words.add(word)
        return words


class AutoCompletor:
    """
    Does auto-completion of registered words in registered widgets.

    The words come from the current unit only (see L{set_unit()}), so
    nothing from other units is offered.
    """

    wordsep_re = re.compile(r'[^%s]+' % _WORD_CHARS)
    word_re = re.compile(r'[%s]+' % _WORD_CHARS)

    DEFAULT_COMPLETION_LENGTH = 4  # The default minimum length of a word that may
    # be auto-completed.
    FAST_COMPLETION_LENGTH = 2  # The minimum typed length that completes a
    # placeable or term translation from the current unit's source.
    SIGILS = '%${<&'  # These and digits complete a placeable from one character.
    MAX_CANDIDATES = 8  # The most completions listed at once.

    def __init__(self, main_controller, word_list=None, comp_len=DEFAULT_COMPLETION_LENGTH):
        """Constructor.

        @type  word_list: iterable
        @param word_list: A list of words that should be auto-completed."""
        self.main_controller = main_controller
        if word_list is None:
            word_list = []
        assert isinstance(word_list, list)
        self.comp_len = comp_len
        self.names = NameForms(self.word_re)
        self._unit = None
        self.clear_words()
        self.add_words(word_list)
        self.widgets = set()

    def add_widget(self, widget):
        """Add a widget to the list of widgets to do auto-completion for."""
        if widget in self.widgets:
            return  # Widget already added

        if isinstance(widget, TextBox):
            self._add_text_box(widget)
            return

        raise ValueError("Widget type %s not supported." % (type(widget)))

    def add_words(self, words, update=True):
        """Add a word or words to the list of words to auto-complete."""
        for word in words:
            if self.isusable(word):
                self._word_freq[word] += 1
        if update:
            self._update_word_list()

    def autocomplete(self, word, target='', at_start=False):
        """Complete word to one of the current unit's words, or else to a
        long word typed elsewhere in C{target}, ignoring case: see
        L{_cased()}."""
        return next(self._word_completions(word, target, at_start), (None, ''))

    def _word_completions(self, word, target, at_start):
        """(completed word, the part to add) for each word that word
        completes to, best first, as L{autocomplete()}."""
        folded = self.fold(word)
        typed = [w for w in self.wordsep_re.split(target) if self.isusable(w)]
        for w in itertools.chain(self._word_list, typed):
            if (self._word_folds.get(w) or self.fold(w)).startswith(folded):
                if w not in self._proper and not self._proper_in(w, target):
                    w = self._cased(w, word, at_start)
                yield w, w[len(word) :]

    def autocomplete_fast(self, prefix, target=''):
        """Complete the end of C{prefix} to one of the current unit's
        placeables or term translations, which may contain spaces and
        punctuation. Those not yet in C{target} as often as in the source
        come first, each group in source order."""
        return next(self._fast_completions(prefix, target), (None, ''))

    def _fast_completions(self, prefix, target):
        """(completed placeable or term, the part to add) for each that
        the end of prefix completes to, in L{autocomplete_fast()}'s order."""
        folded_prefix = self.fold(prefix)
        for candidate in self._ordered_fast_candidates(target):
            typed = self._fast_typed(candidate, prefix, folded_prefix)
            if typed is None:
                continue
            if candidate in self._cased_candidates:
                before = prefix[: len(prefix) - len(typed)]
                candidate = self._cased(candidate, typed, self.at_start(before))
            yield candidate, candidate[len(typed) :]

    def _fast_typed(self, candidate, prefix, folded_prefix):
        """The end of prefix that starts candidate, or None."""
        for folded in self._folded[candidate]:
            first = max(0, len(prefix) - min(len(folded), len(candidate)) + 1)
            for start in range(first, len(prefix)):
                typed = prefix[start:]
                if len(typed) < self.FAST_COMPLETION_LENGTH and not self._is_sigil(typed[0]):
                    continue
                if not folded.startswith(folded_prefix[start:]):
                    continue
                # Don't complete "ab" in "tab" to "about".
                if start > 0 and self._isword(prefix[start - 1]) and self._isword(folded[0]):
                    continue
                return typed
        return None

    def at_start(self, before):
        """Whether a word after C{before} in the target starts a sentence:
        after a full stop or the like, or first in a target whose source
        starts with a capital; an opening ¿ or ¡, or Afrikaans 'n, may
        come between."""
        end = self._sentence_lead(before, len(before))
        if end >= 0:
            return before[end] in _SENTENCE_ENDS
        return self._capitalised

    def _sentence_lead(self, text, end):
        """The index of the last character before text[end] that tells
        whether a word there starts a sentence, or -1: skipping spaces,
        opening ¿ and ¡ (Q39) and the Afrikaans article 'n, whose next
        word takes the capital."""
        i = end - 1
        while i >= 0 and (text[i].isspace() or text[i] in _OPENING_MARKS):
            i -= 1
        if i < 1 or self._language() != 'af':
            return i
        for article in _AF_ARTICLES:
            start = i + 1 - len(article)
            if text[max(start, 0) : i + 1] == article and (
                start == 0 or not self._isword(text[start - 1])
            ):
                return self._sentence_lead(text, start)
        return i

    @staticmethod
    def fold(text):
        """text as matched: in lower case, digits of every script as 0-9 and
        no-break or thin spaces as spaces, one character for one."""
        return text.translate(_FOLD)

    def clear_widgets(self):
        """Release all registered widgets from the spell of auto-completion."""
        for w in set(self.widgets):
            self.remove_widget(w)

    def clear_words(self):
        """Remove all registered words; effectively turns off auto-completion."""
        self._word_list = []
        self._word_folds = {}
        self._word_freq = defaultdict(lambda: 0)
        self._fast_candidates = []
        self._cased_candidates = set()
        self._proper = set()
        self._capitalised = False
        self._folded = {}
        self._source_counts = Counter()

    def isusable(self, word):
        """Returns a value indicating if the given word should be kept as a
        suggestion for autocomplete."""
        return len(word) > self.comp_len + 2

    def set_unit(self, unit):
        """Replace all words with those of the given unit: its source and
        target words, plus the placeables and term translations in its
        source for L{autocomplete_fast()}."""
        self.names.leave(self._unit)
        self._unit = unit
        self.clear_words()
        if unit is None:
            return
        self.names.enter(unit)
        sources = self._strings(unit.source)
        for text in sources + self._strings(unit.target):
            self.add_words(self.wordsep_re.split(text), update=False)
            self._proper.update(self._proper_words(text))
        self._update_word_list()
        first = next((c for c in ''.join(sources) if c.isalpha()), '')
        self._capitalised = first.isupper()
        names = NameForms.names(sources)
        candidates = []
        for text, cased in self._placeable_candidates(sources, unit):
            forms = self.names.forms(text) if text in names else [text]
            candidates.append((forms[0], cased))
            self._folded.setdefault(forms[0], [self.fold(f) for f in forms])
        self._source_counts = Counter(c for c, _cased in candidates)
        self._fast_candidates = list(dict.fromkeys(c for c, _cased in candidates))
        self._cased_candidates = {c for c, cased in candidates if cased and not self._is_name(c)}

    def remove_widget(self, widget):
        """Remove a widget (currently only L{TextBox}s are accepted) from
        the list of widgets to do auto-correction for.
        """
        if isinstance(widget, TextBox) and widget in self.widgets:
            self._remove_textbox(widget)

    def remove_words(self, words):
        """Remove a word or words from the list of words to auto-complete."""
        if isinstance(words, str):
            words = [words]
        for w in words:
            try:
                del self._word_freq[w]
            except KeyError:
                pass
            try:
                self._word_list.remove(w)
            except ValueError:
                pass

    def _add_text_box(self, textbox):
        """Add the given L{TextBox} to the list of widgets to do auto-
        correction on."""
        if not hasattr(self, '_textbox_insert_ids'):
            self._textbox_insert_ids = {}
        handler_id = textbox.connect('text-inserted', self._on_insert_text)
        self._textbox_insert_ids[textbox] = handler_id
        self.widgets.add(textbox)

    def _cased(self, candidate, typed, at_start):
        """candidate as completed from typed: in capitals after two typed
        capitals, otherwise with the case of the typed letters, capitalised
        at the start of a sentence, as the target language does (Q39)."""
        language = self._language()
        if len(typed) > 1 and typed.isupper():
            return _upper(candidate, language)
        cased = (
            ''.join(
                t if t.lower() == c.lower() or (t.isalpha() and self.fold(t) == self.fold(c)) else c
                for t, c in zip(typed, candidate, strict=False)
            )
            + candidate[len(typed) :]
        )
        if at_start:
            cased = _capitalised(cased, language)
        return cased

    def _language(self):
        """The target language's code without its country or script."""
        languages = self._languages()
        if languages is None:
            return None
        return re.split('[_@-]', languages[1])[0].lower()

    @staticmethod
    def _is_name(word):
        """Whether word is in capitals or CamelCase (NASA, GitHub, iPod)."""
        return any(c.isupper() for c in word[1:]) or (len(word) > 1 and word.isupper())

    def _proper_in(self, word, text):
        return word[:1].isupper() and word in self._proper_words(text)

    def _proper_words(self, text):
        """The words of text whose case is their own: names, and words with
        a capital other than at the start of a sentence."""
        proper = set()
        for match in self.word_re.finditer(text):
            word = match.group()
            if not word[:1].isupper():
                continue
            before = self._sentence_lead(text, match.start())
            if self._is_name(word) or (before >= 0 and text[before] not in _SENTENCE_ENDS):
                proper.add(word)
        return proper

    def _is_sigil(self, char):
        return char in self.SIGILS or char.isdecimal()

    def _isword(self, char):
        return not self.wordsep_re.match(char)

    def _completion(self, prefix, postfix, text):
        """(completed word, the part of it to add) for text just typed
        between prefix and postfix."""
        return next(iter(self._completions(prefix, postfix, text, 1)), (None, ''))

    def _completions(self, prefix, postfix, text, limit=MAX_CANDIDATES):
        """Up to limit (completed word, the part of it to add), best first,
        for text just typed between prefix and postfix: placeables and
        term translations, then words."""
        found = []
        seen = set()

        def add(pairs):
            for word, rest in pairs:
                if len(found) >= limit:
                    return
                if rest and word not in seen:
                    seen.add(word)
                    found.append((word, rest))

        add(self._fast_completions(prefix, prefix + postfix))
        if self.wordsep_re.match(text):
            return found
        lastword = self.wordsep_re.split(prefix)[-1]
        if len(lastword) >= self.comp_len:
            before = prefix[: len(prefix) - len(lastword)]
            add(self._word_completions(lastword, before + ' ' + postfix, self.at_start(before)))
        return found

    def _on_insert_text(self, textbox, text, offset, elem):
        if not isinstance(text, str) or text.isspace():
            return
        # Placeables may start with or contain punctuation (%s, <b>, URLs).
        if not self._fast_candidates and self.wordsep_re.match(text):
            return
        # We are only interested in single character insertions, otherwise we
        # react similarly for paste and similar events
        if len(text) != 1:
            return

        prefix = str(textbox.get_text(0, offset) + text)
        postfix = str(textbox.get_text(offset))

        # Quick fix to check that we don't autocomplete in the middle of a word.
        right_lim = len(postfix) > 0 and postfix[0] or ' '
        if not self.wordsep_re.match(right_lim):
            return

        completions = self._completions(prefix, postfix, text)
        if completions:
            # Updating of the buffer is deferred until after this signal
            # and its side effects are taken care of. We abuse
            # GLib.idle_add for that.
            insert_offset = offset + 1  # len(text) == 1
            candidates = [
                self._suggestion(word, rest, prefix, insert_offset) for word, rest in completions
            ]
            suggestion = dict(candidates[0], candidates=candidates)

            def suggest_completion():
                if getattr(textbox, '_render_pending', False):
                    # A typed placeable (a digit, say) re-renders the text
                    # first, which would drop the suggestion's selection.
                    GLib.idle_add(suggest_completion, priority=GLib.PRIORITY_DEFAULT_IDLE + 10)
                    return False
                textbox.handler_block(self._textbox_insert_ids[textbox])
                textbox.suggestion = suggestion
                textbox.handler_unblock(self._textbox_insert_ids[textbox])

                buffer = textbox.buffer
                rest = suggestion['text']
                sel_iter_start = buffer.get_iter_at_offset(insert_offset)
                sel_iter_end = buffer.get_iter_at_offset(insert_offset + len(rest))
                buffer.select_range(sel_iter_start, sel_iter_end)
                return False

            GLib.idle_add(suggest_completion, priority=GLib.PRIORITY_HIGH)

    @staticmethod
    def _suggestion(word, rest, prefix, offset):
        """The text box suggestion that completes prefix to word by adding
        rest at offset; or, if word doesn't start with the typed part
        (another case, digits or script), shows word itself in place of
        the typed part (Q35)."""
        typed_len = len(word) - len(rest)
        if word[:typed_len] != prefix[len(prefix) - typed_len :]:
            return {'text': word, 'offset': offset, 'word': word, 'typed': typed_len}
        return {'text': rest, 'offset': offset, 'word': word}

    def _parsers(self, unit):
        """The placeables parsers for unit's format, but the invisible
        characters', as a single one is never offered."""
        placeables_controller = getattr(self.main_controller, 'placeables_controller', None)
        get_parsers = getattr(placeables_controller, 'get_parsers', None)
        if get_parsers is not None:
            parsers = get_parsers(unit)
        else:
            parsers = getattr(placeables_controller, 'parsers', None) or ()
        return [p for p in parsers if p != InvisiblePlaceable.parse]

    def _placeable_candidates(self, sources, unit=None):
        parsers = self._parsers(unit)
        if not parsers:
            return []
        languages = self._languages()
        candidates = []
        shown = self._shown_sources(unit)
        for text in sources:
            found = []  # (start, end, candidate, cased)
            tree = shown.get(text) or parse_placeables(text, parsers)
            for node, start in self._with_offsets(tree):
                end = start + len(str(node))
                if isinstance(node, TerminologyPlaceable):
                    found.extend((start, end, t, True) for t in node.translations)
                elif isinstance(node, general.NumberPlaceable):
                    numbers = self._numbers(text, start, str(node), languages)
                    found.extend((start, end, number, False) for number in numbers)
                elif type(node) is not StringElem and not isinstance(
                    node, (general.PunctuationPlaceable, InvisiblePlaceable)
                ):
                    found.append((start, end, str(node), False))
            candidates.extend(f[2:] for f in sorted(found, key=lambda f: f[0]))
        return [
            (c, cased)
            for c, cased in candidates
            if len(c) > self.FAST_COMPLETION_LENGTH or (len(c) > 1 and self._is_sigil(c[0]))
        ]

    def _shown_sources(self, unit):
        """{text: placeables tree} of unit's sources as the unit editor
        parsed them, if it shows unit: parsing a long text twice took
        seconds. Its invisible characters (which make no candidate) may
        not be parsed yet."""
        unit_controller = getattr(self.main_controller, 'unit_controller', None)
        view = getattr(unit_controller, 'view', None)
        if unit is None or getattr(view, 'unit', None) is not unit:
            return {}
        trees = (textbox.elem for textbox in getattr(view, 'sources', ()))
        return {str(tree): tree for tree in trees if tree is not None}

    @classmethod
    def _with_offsets(cls, elem, offset=0):
        """Every element of the tree elem, depth first, with its offset."""
        yield elem, offset
        for sub in elem.sub:
            if isinstance(sub, StringElem):
                yield from cls._with_offsets(sub, offset)
            offset += len(str(sub))

    def _languages(self):
        lang_controller = getattr(self.main_controller, 'lang_controller', None)
        source = getattr(lang_controller, 'source_lang', None)
        target = getattr(lang_controller, 'target_lang', None)
        if source is None or target is None:
            return None
        return source.code, target.code

    @staticmethod
    def _numbers(text, start, number, languages):
        """number, found at start in text, in the target language's form
        and then in its native digits if it has others (Q36), unless it
        is part of a version, code, date or time or has no target
        language."""
        end = start + len(number)
        before = text[start - 1] if start else ''
        after = text[end : end + 2]
        if (
            languages is None
            or before.isalnum()
            or before in '_.#/\\:'
            or after[:1].isalnum()
            or after[:1] == '_'
            or (after[:1] in '.,-/:' and after[1:].isdecimal())
        ):
            return [number]
        return localise_number_forms(number, *languages) or [number]

    def _ordered_fast_candidates(self, target):
        if not target:
            return self._fast_candidates
        target = self.fold(target)
        missing, present = [], []
        for candidate in self._fast_candidates:
            if target.count(self._folded[candidate][0]) >= self._source_counts[candidate]:
                present.append(candidate)
            else:
                missing.append(candidate)
        return missing + present

    def _remove_textbox(self, textbox):
        """Remove the given L{TextBox} from the list of widgets to do
        auto-correction on.
        """
        if not hasattr(self, '_textbox_insert_ids'):
            return
        # Disconnect the "insert-text" event handler
        textbox.disconnect(self._textbox_insert_ids[textbox])

        self.widgets.remove(textbox)

    @staticmethod
    def _strings(text):
        """The non-empty forms of a source or target: one, or every plural
        form of a multistring."""
        if not text:
            return []
        return [str(s) for s in getattr(text, 'strings', [text]) if s]

    def _update_word_list(self):
        """Update and sort found words according to frequency."""
        wordlist = list(self._word_freq.items())
        wordlist.sort(key=lambda x: x[1], reverse=True)
        self._word_list = [items[0] for items in wordlist]
        self._word_folds = {w: self.fold(w) for w in self._word_list}


class Plugin(BasePlugin):
    description = _('Automatically complete long words while you type')
    display_name = _('AutoCompletor')
    version = 0.1

    # INITIALIZERS #
    def __init__(self, internal_name, main_controller):
        self.internal_name = internal_name
        self.main_controller = main_controller

        self._init_plugin()

    def _init_plugin(self):
        self.autocomp = AutoCompletor(self.main_controller)

        self._store_loaded_id = self.main_controller.store_controller.connect(
            'store-loaded', self._on_store_loaded
        )

        if self.main_controller.store_controller.get_store():
            # Connect to already loaded store. This happens when the plug-in is enabled after loading a store.
            self._on_store_loaded(self.main_controller.store_controller)

        self._unitview_id = None
        unitview = self.main_controller.unit_controller.view
        if unitview.targets:
            self._connect_to_textboxes(unitview, unitview.targets)
        else:
            self._unitview_id = unitview.connect('targets-created', self._connect_to_textboxes)

    def _connect_to_textboxes(self, unitview, textboxes):
        for target in textboxes:
            self.autocomp.add_widget(target)

    # METHODS #
    def destroy(self):
        """Remove all signal-connections."""
        self.autocomp.clear_words()
        self.autocomp.clear_widgets()
        self.main_controller.store_controller.disconnect(self._store_loaded_id)
        if getattr(self, '_cursor_changed_id', None):
            self.store_cursor.disconnect(self._cursor_changed_id)
        if self._unitview_id:
            self.main_controller.unit_controller.view.disconnect(self._unitview_id)
        if getattr(self, '_names_id', None):
            GLib.source_remove(self._names_id)

    # EVENT HANDLERS #
    def _on_cursor_change(self, cursor):
        def load_unit():
            # deref() is None while the cursor has no unit (a search with
            # no matches).
            self.autocomp.set_unit(cursor.deref())

        GLib.idle_add(load_unit)

    def _on_store_loaded(self, storecontroller):
        if hasattr(self, '_cursor_changed_id'):
            self.store_cursor.disconnect(self._cursor_changed_id)
        self.store_cursor = storecontroller.cursor
        self._cursor_changed_id = self.store_cursor.connect(
            'cursor-changed', self._on_cursor_change
        )
        self._on_cursor_change(self.store_cursor)
        self._read_names(storecontroller)

    def _read_names(self, storecontroller):
        """Read the file's names for AutoCompletor.names in the background."""
        if getattr(self, '_names_id', None):
            GLib.source_remove(self._names_id)
        self._names_id = None
        store = getattr(storecontroller, 'get_store', lambda: None)()
        if store is None:
            return
        reader = self.autocomp.names.load(store.get_units())

        def read():
            if next(reader, None) is None:
                self._names_id = None
                return False
            return True

        self._names_id = GLib.idle_add(read)
