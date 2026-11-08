#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import time
from types import SimpleNamespace

from gi.repository import Atk, Gtk

from virtaal.views.widgets.completionpopup import CompletionPopup, popup_position


def test_popup_position_is_below_the_anchor():
    assert popup_position((100, 200, 1, 20), (80, 60), (0, 0, 1000, 1000)) == (100, 220)


def test_popup_position_flips_above_when_below_does_not_fit():
    assert popup_position((100, 950, 1, 20), (80, 60), (0, 0, 1000, 1000)) == (100, 890)


def test_popup_position_stays_below_when_above_does_not_fit_either():
    assert popup_position((100, 30, 1, 20), (80, 990), (0, 0, 1000, 1000)) == (100, 50)


def test_popup_position_is_kept_inside_the_bounds_horizontally():
    assert popup_position((980, 200, 1, 20), (80, 60), (0, 0, 1000, 1000)) == (920, 220)


def test_popup_position_right_aligns_to_the_anchor_for_rtl():
    assert popup_position((500, 200, 1, 20), (80, 60), (0, 0, 1000, 1000), rtl=True) == (420, 220)


def test_popup_position_without_bounds():
    assert popup_position((100, 200, 1, 20), (80, 60), None) == (100, 220)


def _flush(timeout=1.0):
    # Bounded: a shown window can keep events pending (e.g. cursor blink).
    deadline = time.monotonic() + timeout
    while Gtk.events_pending() and time.monotonic() < deadline:
        Gtk.main_iteration_do(False)


def _textbox_in_window(text=''):
    textbox = Gtk.TextView()
    # Offscreen, so the tests don't need a display.
    window = Gtk.OffscreenWindow()
    window.add(textbox)
    window.show_all()
    textbox.get_buffer().set_text(text)
    _flush()
    return textbox


def _show(textbox, offset=0, candidates=('argief', 'dokument', 'lêer')):
    accepted = []
    popup = CompletionPopup()
    popup.show_at(textbox, offset, list(candidates), accepted.append)
    return popup, accepted


def test_show_at_selects_the_first_candidate():
    popup, accepted = _show(_textbox_in_window())

    assert popup.is_showing()
    assert popup.selected_index() == 0
    assert [row.get_child().get_label() for row in popup.listbox.get_children()] == [
        'argief',
        'dokument',
        'lêer',
    ]


def test_show_at_without_candidates_does_not_show():
    popup, accepted = _show(_textbox_in_window(), candidates=())

    assert not popup.is_showing()


def test_move_selection_is_clamped_to_the_list():
    popup, accepted = _show(_textbox_in_window())

    popup.move_selection(-1)
    assert popup.selected_index() == 0
    popup.move_selection(5)
    assert popup.selected_index() == 2


def test_accept_passes_the_selected_candidate_and_dismisses():
    popup, accepted = _show(_textbox_in_window())
    popup.move_selection(1)

    popup.accept()

    assert accepted == ['dokument']
    assert not popup.is_showing()


def test_dismiss_does_not_accept():
    popup, accepted = _show(_textbox_in_window())

    popup.dismiss()
    popup.accept()

    assert accepted == []
    assert not popup.is_showing()


def test_clicking_a_row_accepts_it():
    popup, accepted = _show(_textbox_in_window())

    popup.listbox.emit('row-activated', popup.listbox.get_row_at_index(2))

    assert accepted == ['lêer']


def test_reshowing_with_a_shorter_list_shrinks_the_popup():
    textbox = _textbox_in_window()
    popup, accepted = _show(textbox, candidates=['word%d' % i for i in range(12)])
    _flush()
    long_height = popup.get_size().height

    popup.show_at(textbox, 0, ['a', 'b'], accepted.append)
    _flush()

    _minimum, natural = popup.listbox.get_preferred_size()
    assert natural.height > 0
    assert popup.get_size().height < long_height


def _configure(popup, x=10, y=20, width=800, height=600):
    event = SimpleNamespace(x=x, y=y, width=width, height=height)
    popup._on_configure_toplevel(None, event)


def test_moving_the_main_window_hides_the_list_until_it_settles(monkeypatch):
    timers = []
    monkeypatch.setattr(
        'virtaal.views.widgets.completionpopup.GLib.timeout_add',
        lambda ms, func: timers.append((ms, func)) or len(timers),
    )
    monkeypatch.setattr('virtaal.views.widgets.completionpopup.GLib.source_remove', lambda i: True)
    popup, accepted = _show(_textbox_in_window())
    popup._toplevel_geometry = (10, 20, 800, 600)  # Where it starts.

    _configure(popup, x=50)
    _configure(popup, x=90)

    assert not popup.get_visible()
    assert popup.is_showing()  # Still taking its keys.
    assert [ms for ms, _func in timers] == [CompletionPopup.SETTLE_MS] * 2
    timers[-1][1]()
    assert popup.get_visible()
    assert accepted == []
    popup.dismiss()


def test_a_configure_without_a_move_leaves_the_list_alone():
    popup, _accepted = _show(_textbox_in_window())
    popup._toplevel_geometry = (10, 20, 800, 600)
    visible = popup.get_visible()

    _configure(popup)

    assert popup.get_visible() == visible
    popup.dismiss()


def test_dismiss_soon_waits_for_idle_and_showing_again_cancels_it():
    textbox = _textbox_in_window()
    popup, accepted = _show(textbox)

    popup.dismiss_soon()
    assert popup.is_showing()
    popup.show_at(textbox, 0, ['argief'], accepted.append)
    _flush()
    assert popup.is_showing()

    popup.dismiss_soon()
    _flush()
    assert not popup.is_showing()


def test_selecting_another_row_is_reported():
    selected = []
    popup = CompletionPopup()
    popup.show_at(_textbox_in_window(), 0, ['a', 'b', 'c'], print, selected.append)

    popup.move_selection(1)
    popup.move_selection(1)
    popup.move_selection(1)

    assert selected == [1, 2]
    popup.dismiss()


def _notifications(textbox):
    said = []
    textbox.get_accessible().connect(
        'notification', lambda accessible, text, live: said.append((text, live))
    )
    return said


def test_the_first_candidate_is_announced_when_the_list_shows():
    textbox = _textbox_in_window()
    said = _notifications(textbox)

    popup, _accepted = _show(textbox)

    assert said == [('argief, 1 of 3', Atk.Live.POLITE)]
    popup.dismiss()


def test_each_newly_selected_candidate_is_announced_once():
    textbox = _textbox_in_window()
    said = _notifications(textbox)
    popup, accepted = _show(textbox)

    popup.move_selection(1)
    popup.move_selection(5)
    popup.move_selection(1)  # Already the last: nothing new to say.
    popup.show_at(textbox, 0, ['lêer', 'argief'], accepted.append)  # Typing refills it.

    assert [text for text, _live in said] == [
        'argief, 1 of 3',
        'dokument, 2 of 3',
        'lêer, 3 of 3',
    ]
    popup.dismiss()


def test_announce_falls_back_to_the_older_signal(monkeypatch):
    from virtaal.views.widgets import completionpopup

    said = []
    accessible = Atk.NoOpObject.new(Gtk.Label())
    accessible.connect('announcement', lambda accessible, text: said.append(text))
    real_lookup = completionpopup.GObject.signal_lookup
    monkeypatch.setattr(
        completionpopup.GObject,
        'signal_lookup',
        lambda name, instance: 0 if name == 'notification' else real_lookup(name, instance),
    )

    assert completionpopup.announce(accessible, 'Herroep, 1 of 2')
    assert said == ['Herroep, 1 of 2']


def test_show_at_can_select_another_row_first():
    popup = CompletionPopup()
    popup.show_at(_textbox_in_window(), 0, ['a', 'b', 'c'], print, selected=2)

    assert popup.selected_index() == 2
    popup.dismiss()


def test_the_list_and_its_rows_are_named_for_screen_readers():
    popup, _accepted = _show(_textbox_in_window())

    listbox = popup.listbox.get_accessible()
    assert listbox.get_name() == 'Suggestions'
    assert listbox.get_role() == Atk.Role.LIST
    rows = [row.get_accessible().get_name() for row in popup.listbox.get_children()]
    assert rows == ['argief', 'dokument', 'lêer']
    popup.dismiss()


def test_popup_on_an_empty_target_is_placed_below_the_cursor_line(monkeypatch):
    # #988: on an empty target the picker used to sit too high, because
    # its position came from an inline widget rather than the text line.
    textbox = _textbox_in_window('')
    moves = []
    monkeypatch.setattr(CompletionPopup, 'move', lambda self, x, y: moves.append((x, y)))

    _show(textbox, offset=0)

    rect = textbox.get_iter_location(textbox.get_buffer().get_start_iter())
    _wx, wy = textbox.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x, rect.y)
    origin = textbox.get_window(Gtk.TextWindowType.WIDGET).get_origin()
    assert rect.height > 0
    assert moves
    _x, y = moves[-1]
    line_bottom = origin.y + wy + rect.height
    line_top = origin.y + wy
    # Below the line, or flipped to end exactly at its top.
    assert y == line_bottom or y < line_top


def test_showing_the_popup_does_not_change_the_text_or_line_height():
    # #3915: the old inline combo changed the row's height as it opened.
    textbox = _textbox_in_window('drink')
    height_before = textbox.get_iter_location(textbox.get_buffer().get_start_iter()).height

    _show(textbox, offset=5)
    _flush()

    assert textbox.get_buffer().props.text == 'drink'
    assert textbox.get_children() == []
    assert textbox.get_iter_location(textbox.get_buffer().get_start_iter()).height == height_before


def test_dismiss_releases_the_native_window():
    # macOS can re-show a hidden transient window when its parent is moved.
    popup, accepted = _show(_textbox_in_window())
    assert popup.get_realized()

    popup.dismiss()

    assert not popup.get_realized()


def test_announce_works_with_an_atk_without_live(monkeypatch):
    # Windows' GTK ships an ATK older than 2.50: no Atk.Live, no notification.
    from virtaal.views.widgets import completionpopup

    said = []
    accessible = Atk.NoOpObject.new(Gtk.Label())
    accessible.connect('announcement', lambda accessible, text: said.append(text))
    monkeypatch.setattr(completionpopup, 'Atk', SimpleNamespace(), raising=True)

    assert completionpopup.announce(accessible, 'Herroep, 1 of 2')
    assert said == ['Herroep, 1 of 2']
