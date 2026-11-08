#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""How long a long paste into a spell-checked text box takes (#4443),
through the spell checker plugin and through GtkSpell alone - for the
packaged builds' CI:
    virtaal --run-module virtaal.support.spelling_timing LANG [OUTPUT]"""

import sys
import time

FILLER = 'Lorem ipsum dolor sit amet. '
SIZES = (2000, 10000, 100000)


def _pump(seconds=300):
    """Runs the main loop until nothing is pending; returns how long that
        took and the longest single iteration."""
    from gi.repository import GLib
    context = GLib.MainContext.default()
    start = time.perf_counter()
    longest = 0
    while context.pending() and time.perf_counter() - start < seconds:
        before = time.perf_counter()
        context.iteration(False)
        longest = max(longest, time.perf_counter() - before)
    return time.perf_counter() - start, longest


def _paste(view, text):
    """Inserts text as a paste does: the insert's time, the longest time
        the app is then busy at once, and the time until it's idle with
        the text fully checked."""
    buffer = view.get_buffer()
    buffer.set_text('')
    _pump()
    start = time.perf_counter()
    buffer.begin_user_action()
    buffer.insert_interactive_at_cursor(text, -1, True)
    buffer.end_user_action()
    inserted = time.perf_counter() - start
    idle, longest = _pump()
    return inserted, max(inserted, longest), inserted + idle


def _type(view, text, clear=True):
    """The longest single keystroke, typing text."""
    buffer = view.get_buffer()
    if clear:
        buffer.set_text('')
    _pump()
    longest = 0
    for char in text:
        start = time.perf_counter()
        buffer.begin_user_action()
        buffer.insert_interactive_at_cursor(char, -1, True)
        buffer.end_user_action()
        _pump()
        longest = max(longest, time.perf_counter() - start)
    return longest


def _marked(view):
    from virtaal.support.enchant_compat import marked_words
    return len(marked_words(view.get_buffer()))


def _plugin_view(lang):
    """A text view the spell checker plugin checks in lang."""
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GObject, Gtk

    from virtaal.plugins.spellchecker import Plugin

    signal = (GObject.SignalFlags.RUN_FIRST, None, (object, object))

    class UnitView(GObject.Object):
        __gsignals__ = {'textview-language-changed': signal, 'sources-created': signal,
                        'targets-created': signal}

    class LangController(GObject.Object):
        __gsignals__ = {'dictionary-installed': (GObject.SignalFlags.RUN_FIRST, None, (object,))}

    view = Gtk.TextView()
    unit_view = UnitView()
    unit_view.sources, unit_view.targets = [], [view]
    lang_controller = LangController()
    lang_controller.source_lang = lang_controller.target_lang = type('Lang', (), {'code': lang})
    controller = type('MainController', (), {})()
    controller.unit_controller = type('UnitController', (), {'view': unit_view})
    controller.lang_controller = lang_controller
    start = time.perf_counter()
    plugin = Plugin('spellchecker', controller)
    # Until it checks: it may wait for macOS's spell checker to start.
    while getattr(view, 'spell_lang', None) is None and time.perf_counter() - start < 20:
        _pump()
        time.sleep(0.01)
    _, longest = _pump()
    view.spell_ready = time.perf_counter() - start, longest
    return plugin, view


def _gtkspell_view(lang):
    import gi
    gi.require_version('GtkSpell', '3.0')
    from gi.repository import Gtk, GtkSpell
    view = Gtk.TextView()
    checker = GtkSpell.Checker()
    checker.set_language(lang)
    checker.attach(view)
    return checker, view


def timings(lang, sizes=SIZES):
    """Report lines: per size, the insert's time, the longest the app is
        busy at once and the time until it's idle with the text checked,
        through the plugin and through GtkSpell alone; and the longest
        keystroke typing with the plugin, in short and in long text."""
    import gi
    gi.require_version('Gtk', '3.0')
    lines = []
    plugin, view = _plugin_view(lang)
    if getattr(view, 'spell_lang', None):
        lines.append('plugin: checking, ready in %.3f s, busy %.3f s' % view.spell_ready)
    else:
        lines.append('plugin: not checking')
    from virtaal.support.enchant_compat import marked_words, sample_text
    view.get_buffer().set_text(sample_text(lang))
    _pump()
    lines.append('sample marked: %s' % ' '.join(marked_words(view.get_buffer())))
    lines.append('typing: longest keystroke %.3f s, %d marked' % (_type(view, 'This is a tset of typing'), _marked(view)))
    for size in sizes:
        text = (FILLER * (size // len(FILLER) + 1))[:size]
        inserted, busy, idle = _paste(view, text)
        lines.append('plugin %d: insert %.3f s, busy %.3f s, idle %.3f s, %d marked'
                     % (size, inserted, busy, idle, _marked(view)))
    # Typing in the middle of the longest text.
    buffer = view.get_buffer()
    buffer.place_cursor(buffer.get_iter_at_offset(buffer.get_char_count() // 2))
    lines.append('typing in %d: longest keystroke %.3f s' % (sizes[-1], _type(view, ' blorft tset ', clear=False)))
    plugin.destroy()
    # GtkSpell alone takes minutes for 100k characters.
    checker, view = _gtkspell_view(lang)
    for size in [s for s in sizes if s <= 10000]:
        text = (FILLER * (size // len(FILLER) + 1))[:size]
        inserted, busy, idle = _paste(view, text)
        lines.append('gtkspell %d: insert %.3f s, busy %.3f s, idle %.3f s, %d marked'
                     % (size, inserted, busy, idle, _marked(view)))
    checker.detach()
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    # The plugin's labels, outside the app.
    import builtins
    builtins.__dict__.setdefault('_', str)
    result = timings(sys.argv[1] if len(sys.argv) > 1 else 'en_US')
    if len(sys.argv) > 2:
        with open(sys.argv[2], 'w', encoding='utf-8') as f:
            f.write(result)
    else:
        sys.stdout.write(result)
