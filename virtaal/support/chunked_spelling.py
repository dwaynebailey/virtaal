#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Spell checking for text too long for GtkSpell (#4443), a chunk at a
time on idle.

GTK 3.24 finds a word's ends from its whole line, recomputing the line's
Pango attributes for every word once the text has changed, so GtkSpell
takes time growing with the square of a line's length. This checker
finds words in a fresh, small buffer per chunk instead, with GtkSpell's
own rules, marks them with GtkSpell's tag, and builds GtkSpell's menu
items for a marked word."""

import time

from gi.repository import GLib, Gtk, Pango

TAG_NAME = 'gtkspell-misspelled'
# Characters checked at a time, and the time one idle step may take.
CHUNK = 2000
STEP_SECONDS = 0.01
# GTK lays out a line with many marks in time growing faster than their
# number (a keystroke in a line of 100,000 characters takes over a second
# with 10,000 marks, 0.25 s with 1,100 on a CI Mac): in a longer line
# only this many characters around the cursor, or what's in view, are
# marked.
WINDOW = 4000
# Suggestions shown before the rest go into a "More..." submenu, as GtkSpell.
SUGGESTIONS_PER_MENU = 10
_APOSTROPHES = ("'", '\u2019')
# How far back and on an edit's recheck looks for its words' ends.
WORD_REACH = 200
_OBJECT = '\ufffc'


def _forward_word_end(it):
    """GtkSpell's: a word goes on past an apostrophe followed by a letter."""
    if not it.forward_word_end():
        return False
    if it.get_char() not in _APOSTROPHES:
        return True
    after = it.copy()
    if after.forward_char() and after.get_char().isalpha():
        return it.forward_word_end()
    return True


def _backward_word_start(it):
    if not it.backward_word_start():
        return False
    before = it.copy()
    if it.get_char().isalpha() and before.backward_char() and before.get_char() in _APOSTROPHES:
        return it.backward_word_start()
    return True


def words(text):
    """The (start, end) offsets of the words in text, as GtkSpell finds
        them: Pango's word boundaries, in a buffer of its own so that GTK's
        boundary cache is valid."""
    buffer = Gtk.TextBuffer()
    buffer.set_text(text)
    start, end = buffer.get_bounds()
    if not start.starts_word():
        if start.inside_word() or start.ends_word():
            _backward_word_start(start)
        elif _forward_word_end(start):
            _backward_word_start(start)
    # An iter at offset 0 always seems to be inside a word (GtkSpell).
    if start.get_offset() == 0:
        _forward_word_end(start)
        _backward_word_start(start)
    spans = []
    while start.compare(end) < 0:
        word_end = start.copy()
        _forward_word_end(word_end)
        if word_end.equal(start):
            break
        spans.append((start.get_offset(), word_end.get_offset()))
        _forward_word_end(word_end)
        _backward_word_start(word_end)
        if word_end.equal(start):
            break
        start = word_end
    return spans


def checked(word):
    """Whether GtkSpell would check word: not a number, nor a placeable's
        widget."""
    return bool(word) and not word[0].isdigit() and _OBJECT not in word


def _chunk_start(text, end, size=CHUNK):
    """Where a chunk ending at end (an index into text) should start: at
        whitespace about size characters back, so no word is split."""
    if end <= size:
        return 0
    for i in range(end - size, max(-1, end - 2 * size), -1):
        if text[i].isspace():
            return i
    return end - size


class ChunkedChecker:
    """Checks text_view's spelling with dictionary (a pyenchant Dict) in
        idle steps, rechecking around each edit, and offers GtkSpell's
        context menu items for a marked word."""

    def __init__(self, text_view, dictionary):
        self.view = text_view
        self.buffer = text_view.get_buffer()
        self.dictionary = dictionary
        tags = self.buffer.get_tag_table()
        self.tag = tags.lookup(TAG_NAME) or self.buffer.create_tag(TAG_NAME, underline=Pango.Underline.ERROR)
        # Ranges still to check, as (start mark, end mark, every word):
        # edits first. A range is checked from its end back.
        self._ranges = []
        self._deferred = None
        self._idle = None
        self._scroll_timeout = None
        # Where marks go in a long line, and the window marked around it in
        # the focus's own line, if long.
        self._focus = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        self._window = None
        self._click = self.buffer.create_mark(None, self.buffer.get_start_iter(), True)
        self._handlers = [
            (self.buffer, self.buffer.connect_after('insert-text', self._on_insert_text)),
            (self.buffer, self.buffer.connect_after('delete-range', self._on_delete_range)),
            (self.buffer, self.buffer.connect('mark-set', self._on_mark_set)),
            (self.view, self.view.connect('button-press-event', self._on_button_press)),
            (self.view, self.view.connect('popup-menu', self._on_popup_menu)),
        ]
        self._vadjustment = None
        self._handlers.append((self.view, self.view.connect('notify::vadjustment', self._on_vadjustment)))
        self._on_vadjustment(self.view, None)
        self.check_all()

    # Checking

    def check_all(self):
        """Check every word again, from scratch."""
        self._drop_ranges()
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(self.tag, start, end)
        self._add_range(start, end, every_word=True)

    def set_dictionary(self, dictionary):
        self.dictionary = dictionary
        self.check_all()

    @property
    def pending(self):
        return bool(self._ranges)

    def run_until_done(self, seconds=60):
        """Check everything still pending now, for tests and timings."""
        deadline = time.perf_counter() + seconds
        while self._ranges and time.perf_counter() < deadline:
            self._step(deadline)

    def stop(self, clear=False):
        """Stop checking; clear takes the marks off too."""
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        if self._vadjustment is not None:
            self._vadjustment[0].disconnect(self._vadjustment[1])
            self._vadjustment = None
        self._drop_ranges()
        self._drop_deferred()
        self._drop_window()
        if self._scroll_timeout is not None:
            GLib.source_remove(self._scroll_timeout)
            self._scroll_timeout = None
        if not self._click.get_deleted():
            self.buffer.delete_mark(self._click)
        if clear:
            self.buffer.remove_tag(self.tag, *self.buffer.get_bounds())

    def _add_range(self, start, end, every_word=False, first=False):
        marks = (self.buffer.create_mark(None, start, True), self.buffer.create_mark(None, end, False), every_word)
        if first:
            self._ranges.insert(0, marks)
        else:
            self._ranges.append(marks)
        if self._idle is None:
            self._idle = GLib.idle_add(self._on_idle)

    def _drop_ranges(self):
        for start, end, _every_word in self._ranges:
            self.buffer.delete_mark(start)
            self.buffer.delete_mark(end)
        self._ranges = []
        if self._idle is not None:
            GLib.source_remove(self._idle)
            self._idle = None

    def _drop_deferred(self):
        if self._deferred is not None:
            for mark in self._deferred:
                self.buffer.delete_mark(mark)
            self._deferred = None

    def _drop_window(self):
        if self._window is not None:
            for mark in self._window:
                self.buffer.delete_mark(mark)
            self._window = None

    def _line_window(self, offset):
        """offset's line's start and end, and the start and end of the
            part that may be marked: all of it, or WINDOW characters around
            the focus."""
        it = self.buffer.get_iter_at_offset(offset)
        line_start = offset - it.get_line_offset()
        line_end = line_start + it.get_chars_in_line()
        if line_end - line_start <= WINDOW:
            return line_start, line_end, line_start, line_end
        centre = min(max(self._focus, line_start), line_end)
        start = min(max(centre - WINDOW // 2, line_start), line_end - WINDOW)
        if line_start <= self._focus <= line_end:
            self._drop_window()
            self._window = (self.buffer.create_mark(None, self.buffer.get_iter_at_offset(start), True),
                            self.buffer.create_mark(None, self.buffer.get_iter_at_offset(start + WINDOW), False))
        return line_start, line_end, start, start + WINDOW

    def _set_focus(self, offset):
        """Moves the long line's marked window to offset, once it gets near
            the window's edge."""
        self._focus = offset
        if self._window is None:
            return
        start = self.buffer.get_iter_at_mark(self._window[0])
        end = self.buffer.get_iter_at_mark(self._window[1])
        if start.get_offset() + WINDOW // 4 <= offset <= end.get_offset() - WINDOW // 4:
            return
        self._drop_window()
        self.buffer.remove_tag(self.tag, start, end)
        first, last = self._line_window(offset)[2:]
        self._add_range(self.buffer.get_iter_at_offset(first), self.buffer.get_iter_at_offset(last), every_word=True)

    def _on_vadjustment(self, view, pspec):
        if self._vadjustment is not None:
            self._vadjustment[0].disconnect(self._vadjustment[1])
            self._vadjustment = None
        adjustment = view.get_property('vadjustment')
        if adjustment is not None:
            self._vadjustment = (adjustment, adjustment.connect('value-changed', self._on_scrolled))

    def _on_scrolled(self, adjustment):
        if self._scroll_timeout is not None:
            GLib.source_remove(self._scroll_timeout)
        self._scroll_timeout = GLib.timeout_add(150, self._on_scroll_settled)

    def _on_scroll_settled(self):
        self._scroll_timeout = None
        rect = self.view.get_visible_rect()
        found, it = self.view.get_iter_at_location(rect.x + rect.width // 2, rect.y + rect.height // 2)
        if found:
            self._set_focus(it.get_offset())
        return False

    def _on_idle(self):
        self._step(time.perf_counter() + STEP_SECONDS)
        if self._ranges:
            return True
        self._idle = None
        return False

    def _step(self, deadline):
        """Checks chunks until deadline, at least one."""
        while self._ranges:
            start_mark, end_mark, every_word = self._ranges[0]
            start = self.buffer.get_iter_at_mark(start_mark)
            end = self.buffer.get_iter_at_mark(end_mark)
            first = start.get_offset()
            if start.compare(end) < 0:
                begin = self._check_chunk(start, end, every_word)
                self.buffer.move_mark(end_mark, self.buffer.get_iter_at_offset(begin))
            else:
                begin = first
            if begin <= first:
                self._ranges.pop(0)
                self.buffer.delete_mark(start_mark)
                self.buffer.delete_mark(end_mark)
            if time.perf_counter() >= deadline:
                break

    def _check_chunk(self, start, end, every_word):
        """Checks the words in the last chunk of start..end, which begin
            and end at whitespace or the text's ends; returns the chunk's
            start offset."""
        first, last = start.get_offset(), end.get_offset()
        low = max(first, last - 2 * CHUNK)
        text = self.buffer.get_slice(self.buffer.get_iter_at_offset(low), end, True)
        rel = _chunk_start(text, len(text))
        offset, chunk = low + rel, text[rel:]
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        marked = []
        line = None
        for word_start, word_end in words(chunk):
            word = chunk[word_start:word_end]
            if not checked(word):
                continue
            abs_start, abs_end = offset + word_start, offset + word_end
            if line is None or not line[0] <= abs_start < line[1]:
                line = self._line_window(abs_start)
            if not line[2] <= abs_start < line[3]:
                continue
            if not every_word and abs_start < cursor <= abs_end and not self._is_marked(abs_start):
                # Being typed: checked once the cursor leaves it (GtkSpell).
                self._defer(abs_start, abs_end)
                continue
            if not self.dictionary.check(word):
                marked.append((abs_start, abs_end))
        self.buffer.remove_tag(self.tag, self.buffer.get_iter_at_offset(offset), end)
        # From the end: finding an offset walks the line's tag toggles before it.
        for abs_start, abs_end in reversed(marked):
            self.buffer.apply_tag(self.tag, self.buffer.get_iter_at_offset(abs_start),
                                  self.buffer.get_iter_at_offset(abs_end))
        return offset

    def _is_marked(self, offset):
        return self.buffer.get_iter_at_offset(offset).has_tag(self.tag)

    def _defer(self, start, end):
        self._drop_deferred()
        self._deferred = (self.buffer.create_mark(None, self.buffer.get_iter_at_offset(start), True),
                          self.buffer.create_mark(None, self.buffer.get_iter_at_offset(end), False))

    # Edits

    def _around(self, offset_start, offset_end):
        """Iters around the words touching offset_start..offset_end."""
        start = self.buffer.get_iter_at_offset(offset_start)
        end = self.buffer.get_iter_at_offset(offset_end)
        for _ in range(WORD_REACH):
            if start.is_start():
                break
            before = start.copy()
            before.backward_char()
            if before.get_char().isspace():
                break
            start = before
        for _ in range(WORD_REACH):
            if end.is_end() or end.get_char().isspace():
                break
            end.forward_char()
        return start, end

    def _on_insert_text(self, buffer, iter, text, length):
        if len(text) >= CHUNK:
            # A paste: from scratch, since moving through text already
            # marked costs more than marking it afresh.
            self.check_all()
            return
        end = iter.get_offset()
        start, end = self._around(end - len(text), end)
        self._add_range(start, end, first=True)

    def _on_delete_range(self, buffer, start, end):
        start, end = self._around(start.get_offset(), start.get_offset())
        self._add_range(start, end, first=True)

    def _on_mark_set(self, buffer, iter, mark):
        if mark is not buffer.get_insert():
            return
        self._set_focus(iter.get_offset())
        if self._deferred is None:
            return
        start = buffer.get_iter_at_mark(self._deferred[0])
        end = buffer.get_iter_at_mark(self._deferred[1])
        if start.get_offset() < iter.get_offset() <= end.get_offset():
            return
        self._drop_deferred()
        self._add_range(start, end, every_word=True, first=True)

    # The context menu

    def _on_button_press(self, view, event):
        if event.button == 3:
            x, y = view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
            found, it = view.get_iter_at_location(x, y)
            if found:
                self.buffer.move_mark(self._click, it)
        return False

    def _on_popup_menu(self, view):
        self.buffer.move_mark(self._click, self.buffer.get_iter_at_mark(self.buffer.get_insert()))
        return False

    def word_at(self, it):
        """(start offset, end offset, word) of the word at it, or None."""
        offset = it.get_offset()
        first = max(0, offset - WORD_REACH)
        text = self.buffer.get_slice(self.buffer.get_iter_at_offset(first),
                                     self.buffer.get_iter_at_offset(min(offset + WORD_REACH,
                                                                        self.buffer.get_char_count())),
                                     True)
        for start, end in words(text):
            if first + start <= offset <= first + end:
                return first + start, first + end, text[start:end]
        return None

    def populate_popup(self, menu, gettext):
        """Puts GtkSpell's items for the marked word clicked on at the top
            of menu, with gtkspell3's labels (gettext): suggestions, Add to
            Dictionary, Ignore All, then a separator."""
        found = self.word_at(self.buffer.get_iter_at_mark(self._click))
        if found is None or not self._is_marked(found[0]):
            return
        start, end, word = found
        items = []
        suggestions = self.dictionary.suggest(word)
        if not suggestions:
            label = Gtk.Label()
            label.set_markup(gettext('<i>(no suggestions)</i>'))
            item = Gtk.MenuItem()
            item.add(label)
            items.append(item)
        submenu = None
        for i, suggestion in enumerate(suggestions):
            if i and i % SUGGESTIONS_PER_MENU == 0:
                more = Gtk.MenuItem.new_with_label(gettext('More...'))
                more.show()
                (items.append if submenu is None else submenu.append)(more)
                submenu = Gtk.Menu()
                more.set_submenu(submenu)
            item = Gtk.MenuItem.new_with_label(suggestion)
            item.show()
            item.connect('activate', self._replace, start, word, suggestion)
            (items.append if submenu is None else submenu.append)(item)
        add = Gtk.MenuItem.new_with_label(gettext('Add "%s" to Dictionary') % word)
        add.connect('activate', lambda _item: (self.dictionary.add(word), self.check_all()))
        ignore = Gtk.MenuItem.new_with_label(gettext('Ignore All'))
        ignore.connect('activate', lambda _item: (self.dictionary.add_to_session(word), self.check_all()))
        items += [add, ignore, Gtk.SeparatorMenuItem()]
        for position, item in enumerate(items):
            item.show_all()
            menu.insert(item, position)

    def _replace(self, item, start, word, suggestion):
        found = self.word_at(self.buffer.get_iter_at_offset(start))
        if found is None or found[2] != word:
            return
        self.buffer.begin_user_action()
        it_start = self.buffer.get_iter_at_offset(found[0])
        it_end = self.buffer.get_iter_at_offset(found[1])
        self.buffer.delete(it_start, it_end)
        self.buffer.insert(it_start, suggestion)
        self.buffer.end_user_action()
        self.dictionary.store_replacement(word, suggestion)
