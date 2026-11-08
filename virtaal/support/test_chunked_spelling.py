#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from gi.repository import Gtk

from virtaal.support import chunked_spelling
from virtaal.support.chunked_spelling import ChunkedChecker, checked, words
from virtaal.support.enchant_compat import import_enchant, marked_words


class _Dictionary:
    """Knows every word but those in bad; records what it was asked."""

    def __init__(self, bad=('tset', 'blorft', 'zorp')):
        self.bad = set(bad)
        self.asked = []
        self.added = []
        self.session = []
        self.replaced = []

    def check(self, word):
        self.asked.append(word)
        return word not in self.bad

    def suggest(self, word):
        return ['%s%d' % (word, i) for i in range(12)] if word != 'zorp' else []

    def add(self, word):
        self.added.append(word)
        self.bad.discard(word)

    def add_to_session(self, word):
        self.session.append(word)
        self.bad.discard(word)

    def store_replacement(self, word, replacement):
        self.replaced.append((word, replacement))


def _checker(text, dictionary=None):
    view = Gtk.TextView()
    view.get_buffer().set_text(text)
    checker = ChunkedChecker(view, dictionary or _Dictionary())
    checker.run_until_done()
    return view, checker


def _type(view, checker, text):
    buffer = view.get_buffer()
    for char in text:
        buffer.begin_user_action()
        buffer.insert_interactive_at_cursor(char, -1, True)
        buffer.end_user_action()
        checker.run_until_done()


@pytest.mark.parametrize('text, expected', [
    ('one two', ['one', 'two']),
    ("Don't stop", ["Don't", 'stop']),
    ('Rachel’s notes', ['Rachel’s', 'notes']),
    ("users' files", ['users', 'files']),
    ('3rd e-mail', ['3rd', 'e', 'mail']),
    ('', []),
])
def test_words(text, expected):
    assert [text[s:e] for s, e in words(text)] == expected


def test_numbers_and_placeable_widgets_are_not_checked():
    assert checked('word')
    assert not checked('3rd')
    assert not checked('a￼b')
    assert not checked('')


def test_marks_the_misspelt_words():
    view, checker = _checker('This is a tset, zorp there.')

    assert marked_words(view.get_buffer()) == ['tset', 'zorp']
    assert not checker.pending


def test_long_text_is_checked_a_chunk_at_a_time(monkeypatch):
    text = 'a tset of words\n' * 1000
    view = Gtk.TextView()
    view.get_buffer().set_text(text)
    checker = ChunkedChecker(view, _Dictionary())
    steps = 0
    while checker.pending:
        checker._step(0)  # one chunk per step
        steps += 1

    assert steps >= len(text) // chunked_spelling.CHUNK
    assert marked_words(view.get_buffer()) == ['tset'] * 1000


def _can_compare_with_gtkspell():
    enchant = import_enchant()
    if enchant is None or not enchant.dict_exists('en_US'):
        return None
    try:
        import gi
        gi.require_version('GtkSpell', '3.0')
        from gi.repository import GtkSpell
    except (ImportError, ValueError):
        return None
    return enchant, GtkSpell


def test_marks_the_same_words_as_gtkspell():
    found = _can_compare_with_gtkspell()
    if found is None:
        pytest.skip("needs GtkSpell and an en_US dictionary")
    enchant, GtkSpell = found
    text = ("Don't worry, it's 3rd-party qwyx: the users' files (and Rachel’s notes) "
            "are kept 42 times... e-mail re-enter, naive cafe, x-ray, zorp blorft!\n") * 20
    view = Gtk.TextView()
    view.get_buffer().set_text(text)
    gtkspell = GtkSpell.Checker()
    gtkspell.set_language('en_US')
    gtkspell.attach(view)

    ours, _checker_ = _checker(text, enchant.Dict('en_US'))

    assert marked_words(ours.get_buffer()) == marked_words(view.get_buffer())


def test_an_edit_rechecks_only_around_it():
    view, checker = _checker('This is a tset. ' * 200)
    buffer = view.get_buffer()
    checker.dictionary.asked = []
    buffer.place_cursor(buffer.get_iter_at_offset(10))

    _type(view, checker, 'blorft ')

    assert len(checker.dictionary.asked) < 20
    assert marked_words(buffer)[:2] == ['blorft', 'tset']
    assert len(marked_words(buffer)) == 201


def test_a_word_being_typed_is_marked_once_the_cursor_leaves_it():
    view, checker = _checker('Some text ')
    buffer = view.get_buffer()
    buffer.place_cursor(buffer.get_end_iter())

    _type(view, checker, 'blorft')
    assert marked_words(buffer) == []

    _type(view, checker, ' ')
    assert marked_words(buffer) == ['blorft']


def test_deleting_rechecks_the_joined_word():
    view, checker = _checker('a ts et here')
    buffer = view.get_buffer()
    assert marked_words(buffer) == []

    buffer.delete(buffer.get_iter_at_offset(4), buffer.get_iter_at_offset(5))
    checker.run_until_done()

    assert marked_words(buffer) == ['tset']


def test_stop_cancels_whats_pending():
    view = Gtk.TextView()
    view.get_buffer().set_text('a tset ' * 2000)
    checker = ChunkedChecker(view, _Dictionary())
    checker._step(0)
    marked = marked_words(view.get_buffer())

    checker.stop()
    view.get_buffer().insert(view.get_buffer().get_start_iter(), 'blorft ')

    assert not checker.pending and checker._idle is None
    assert marked_words(view.get_buffer()) == marked


def test_stop_can_clear_the_marks():
    view, checker = _checker('a tset')
    checker.stop(clear=True)
    assert marked_words(view.get_buffer()) == []


def test_a_new_dictionary_rechecks_everything():
    view, checker = _checker('a tset and a blorft')

    checker.set_dictionary(_Dictionary(bad=['and']))
    checker.run_until_done()

    assert marked_words(view.get_buffer()) == ['and']


def test_an_apply_tag_handler_can_leave_a_word_unmarked():
    # As the spell checker plugin does for placeables.
    view = Gtk.TextView()
    buffer = view.get_buffer()
    buffer.set_text('tset blorft')

    def in_placeable(buffer, tag, start, end):
        if start.get_offset() == 0:
            buffer.stop_emission_by_name('apply-tag')
    buffer.connect('apply-tag', in_placeable)
    checker = ChunkedChecker(view, _Dictionary())
    checker.run_until_done()

    assert marked_words(buffer) == ['blorft']


def test_a_very_long_line_is_marked_around_the_cursor_only():
    line = 'a tset ' * 4000  # 28,000 characters
    view = Gtk.TextView()
    buffer = view.get_buffer()
    buffer.set_text(line)
    buffer.place_cursor(buffer.get_iter_at_offset(14000))
    checker = ChunkedChecker(view, _Dictionary())
    checker.run_until_done()

    tag = buffer.get_tag_table().lookup(chunked_spelling.TAG_NAME)
    marked = [i for i in range(0, len(line), 7) if buffer.get_iter_at_offset(i + 2).has_tag(tag)]
    assert abs(len(marked) - chunked_spelling.WINDOW // 7) <= 1
    assert marked[0] >= 14000 - chunked_spelling.WINDOW // 2 - 7

    buffer.place_cursor(buffer.get_iter_at_offset(100))
    checker.run_until_done()
    assert buffer.get_iter_at_offset(2).has_tag(tag)
    assert not buffer.get_iter_at_offset(14002).has_tag(tag)


def _popup(view, checker, offset):
    buffer = view.get_buffer()
    buffer.move_mark(checker._click, buffer.get_iter_at_offset(offset))
    menu = Gtk.Menu()
    checker.populate_popup(menu, lambda msgid: '[%s]' % msgid)
    return menu


def _labels(menu):
    labels = []
    for item in menu.get_children():
        if isinstance(item, Gtk.SeparatorMenuItem):
            labels.append('---')
        elif item.get_label() is not None:
            labels.append(item.get_label())
        else:
            labels.append(item.get_child().get_label())
    return labels


def test_the_context_menu_on_a_marked_word():
    view, checker = _checker('This is a tset')

    menu = _popup(view, checker, 11)

    labels = _labels(menu)
    assert labels[:10] == ['tset%d' % i for i in range(10)]
    assert labels[10:] == ['[More...]', '[Add "%s" to Dictionary]' % 'tset', '[Ignore All]', '---']
    more = menu.get_children()[10].get_submenu()
    assert _labels(more) == ['tset10', 'tset11']


def test_the_context_menu_with_no_suggestions():
    view, checker = _checker('zorp there')

    labels = _labels(_popup(view, checker, 2))

    assert labels == ['[<i>(no suggestions)</i>]', '[Add "%s" to Dictionary]' % 'zorp', '[Ignore All]', '---']


def test_no_context_menu_items_on_a_correct_word():
    view, checker = _checker('This is a tset')

    assert _labels(_popup(view, checker, 1)) == []


def test_the_context_menu_replaces_adds_and_ignores():
    view, checker = _checker('a tset and a blorft')
    buffer = view.get_buffer()

    menu = _popup(view, checker, 3)
    menu.get_children()[0].activate()
    checker.run_until_done()
    assert buffer.get_text(*buffer.get_bounds(), True) == 'a tset0 and a blorft'
    assert checker.dictionary.replaced == [('tset', 'tset0')]

    menu = _popup(view, checker, 16)
    menu.get_children()[11].activate()  # Add to Dictionary
    checker.run_until_done()
    assert checker.dictionary.added == ['blorft']
    assert marked_words(buffer) == []


def test_ignore_all_from_the_context_menu():
    view, checker = _checker('a tset')

    menu = _popup(view, checker, 3)
    menu.get_children()[12].activate()
    checker.run_until_done()

    assert checker.dictionary.session == ['tset']
    assert marked_words(view.get_buffer()) == []


def test_the_plugin_puts_chunked_items_in_the_menu():
    from virtaal.plugins import spellchecker
    view, checker = _checker('This is a tset')
    view.spell_chunked = checker
    plugin = spellchecker.Plugin.__new__(spellchecker.Plugin)
    view.get_buffer().move_mark(checker._click, view.get_buffer().get_iter_at_offset(11))
    menu = Gtk.Menu()

    plugin._on_populate_popup_start(view, menu)
    plugin._on_populate_popup(view, menu)

    labels = _labels(menu)
    assert labels[0] == 'tset0'
    assert spellchecker._gtkspell_text('Ignore All') in labels


def test_suggestions_replace_the_word_even_after_an_edit_elsewhere():
    view, checker = _checker('a tset')
    buffer = view.get_buffer()
    menu = _popup(view, checker, 3)
    buffer.insert(buffer.get_end_iter(), ' more')

    menu.get_children()[1].activate()

    assert buffer.get_text(*buffer.get_bounds(), True) == 'a tset1 more'


def test_a_destroyed_word_isnt_replaced():
    view, checker = _checker('a tset')
    buffer = view.get_buffer()
    menu = _popup(view, checker, 3)
    buffer.set_text('other')

    menu.get_children()[1].activate()

    assert buffer.get_text(*buffer.get_bounds(), True) == 'other'


def test_scrolling_a_very_long_line_moves_the_marks_there():
    line = 'a tset ' * 4000
    view = Gtk.TextView()
    window = Gtk.ScrolledWindow()
    window.add(view)
    buffer = view.get_buffer()
    buffer.set_text(line)
    buffer.place_cursor(buffer.get_start_iter())
    checker = ChunkedChecker(view, _Dictionary())
    checker.run_until_done()
    tag = buffer.get_tag_table().lookup(chunked_spelling.TAG_NAME)
    assert not buffer.get_iter_at_offset(20002).has_tag(tag)

    checker._set_focus(20000)
    checker.run_until_done()

    assert buffer.get_iter_at_offset(20002).has_tag(tag)
    assert checker._vadjustment is not None
    checker.stop()
    assert checker._vadjustment is None
