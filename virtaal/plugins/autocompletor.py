#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Contains the AutoCompletor class."""

import re
import unicodedata
from collections import defaultdict

from gi.repository import GLib
from translate.storage.placeables import StringElem, general
from translate.storage.placeables import parse as parse_placeables
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.controllers.baseplugin import BasePlugin
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


class AutoCompletor:
    """
    Does auto-completion of registered words in registered widgets.

    The words come from the current unit only (see L{set_unit()}), so
    nothing from other units is offered.
    """

    wordsep_re = re.compile(r'[^\w%s]+' % _combining_marks())

    DEFAULT_COMPLETION_LENGTH = 4  # The default minimum length of a word that may
    # be auto-completed.
    FAST_COMPLETION_LENGTH = 2  # The minimum typed length that completes a
    # placeable or term translation from the current unit's source.

    def __init__(self, main_controller, word_list=None, comp_len=DEFAULT_COMPLETION_LENGTH):
        """Constructor.

        @type  word_list: iterable
        @param word_list: A list of words that should be auto-completed."""
        self.main_controller = main_controller
        if word_list is None:
            word_list = []
        assert isinstance(word_list, list)
        self.comp_len = comp_len
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

    def autocomplete(self, word):
        for w in self._word_list:
            if w.startswith(word):
                return w, w[len(word) :]
        return None, ''

    def autocomplete_fast(self, prefix):
        """Complete the end of C{prefix} to one of the current unit's
        placeables or term translations, which may contain spaces and
        punctuation."""
        for candidate in self._fast_candidates:
            first = max(0, len(prefix) - len(candidate) + 1)
            last = len(prefix) - self.FAST_COMPLETION_LENGTH
            for start in range(first, last + 1):
                typed = prefix[start:]
                if not candidate.startswith(typed):
                    continue
                # Don't complete "ab" in "tab" to "about".
                if start > 0 and self._isword(prefix[start - 1]) and self._isword(candidate[0]):
                    continue
                return candidate, candidate[len(typed) :]
        return None, ''

    def clear_widgets(self):
        """Release all registered widgets from the spell of auto-completion."""
        for w in set(self.widgets):
            self.remove_widget(w)

    def clear_words(self):
        """Remove all registered words; effectively turns off auto-completion."""
        self._word_list = []
        self._word_freq = defaultdict(lambda: 0)
        self._fast_candidates = []

    def isusable(self, word):
        """Returns a value indicating if the given word should be kept as a
        suggestion for autocomplete."""
        return len(word) > self.comp_len + 2

    def set_unit(self, unit):
        """Replace all words with those of the given unit: its source and
        target words, plus the placeables and term translations in its
        source for L{autocomplete_fast()}."""
        self.clear_words()
        if unit is None:
            return
        sources = self._strings(unit.source)
        for text in sources + self._strings(unit.target):
            self.add_words(self.wordsep_re.split(text), update=False)
        self._update_word_list()
        self._fast_candidates = self._placeable_candidates(sources)

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

    def _isword(self, char):
        return not self.wordsep_re.match(char)

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
        buffer = textbox.buffer

        # Quick fix to check that we don't autocomplete in the middle of a word.
        right_lim = len(postfix) > 0 and postfix[0] or ' '
        if not self.wordsep_re.match(right_lim):
            return

        completed_word, word_postfix = self.autocomplete_fast(prefix)
        if not completed_word:
            if self.wordsep_re.match(text):
                return
            lastword = self.wordsep_re.split(prefix)[-1]
            if len(lastword) < self.comp_len:
                return
            completed_word, word_postfix = self.autocomplete(lastword)
            if completed_word == lastword:
                return

        if completed_word:
            # Updating of the buffer is deferred until after this signal
            # and its side effects are taken care of. We abuse
            # GLib.idle_add for that.
            insert_offset = offset + 1  # len(text) == 1

            def suggest_completion():
                textbox.handler_block(self._textbox_insert_ids[textbox])
                # logging.debug("textbox.suggestion = {'text': u'%s', 'offset': %d}" % (word_postfix, insert_offset))
                textbox.suggestion = {'text': word_postfix, 'offset': insert_offset}
                textbox.handler_unblock(self._textbox_insert_ids[textbox])

                sel_iter_start = buffer.get_iter_at_offset(insert_offset)
                sel_iter_end = buffer.get_iter_at_offset(insert_offset + len(word_postfix))
                buffer.select_range(sel_iter_start, sel_iter_end)

                return False

            GLib.idle_add(suggest_completion, priority=GLib.PRIORITY_HIGH)

    def _placeable_candidates(self, sources):
        placeables_controller = getattr(self.main_controller, 'placeables_controller', None)
        parsers = getattr(placeables_controller, 'parsers', None)
        if not parsers:
            return []
        candidates = []
        for text in sources:
            for node in parse_placeables(text, parsers).depth_first():
                if isinstance(node, TerminologyPlaceable):
                    candidates.extend(node.translations)
                elif type(node) is not StringElem and not isinstance(
                    node, general.PunctuationPlaceable
                ):
                    candidates.append(str(node))
        candidates = [c for c in candidates if len(c) > self.FAST_COMPLETION_LENGTH]
        return list(dict.fromkeys(candidates))

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
