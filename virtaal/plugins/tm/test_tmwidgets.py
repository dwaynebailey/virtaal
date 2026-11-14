#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import time
from types import SimpleNamespace

import pytest
from gi.repository import Gdk, GObject, Gtk, Pango

from virtaal.common import pan_app
from virtaal.plugins.tm.tmwidgets import TMMatchRenderer, TMSourceColRenderer, TMWindow


class _FakeCellRenderer:
    def __init__(self):
        self.properties = {}

    def set_property(self, name, value):
        self.properties[name] = value


def _percent(match_data):
    tree_model = type('M', (), {'get_value': staticmethod(lambda iter, col: match_data)})()
    cell_renderer = _FakeCellRenderer()
    TMWindow._percent_data_func(None, None, cell_renderer, tree_model, None, None)
    return cell_renderer.properties


@pytest.mark.parametrize(
    'lang, expected',
    [
        ('en', '75%'),
        ('fr', '75\u00a0%'),
        ('tr', '%75'),
        ('bn_IN', '৭৫%'),
    ],
)
def test_percent_data_func_shows_the_quality_as_the_ui_language_writes_it(
    monkeypatch, lang, expected
):
    monkeypatch.setattr(pan_app, 'ui_language', lang)

    properties = _percent({'quality': 75, 'source': 'a', 'target': 'b'})

    assert properties['value'] == 75
    assert properties['text'] == expected


def test_percent_data_func_shows_a_question_mark_for_a_match_with_no_quality():
    properties = _percent({'source': 'a', 'target': 'b'})
    assert properties['value'] == 0
    assert properties['text'] == '?'


# TMSourceColRenderer: no-op when the match has no tmsource to show
# (e.g. a plain MT suggestion).


def test_do_get_size_returns_zero_without_a_tm_source():
    renderer = TMSourceColRenderer(view=None)
    renderer.matchdata = {'source': 'a'}

    assert renderer.do_get_size(widget=None, cell_area=None) == (0, 0, 0, 0)


def test_do_render_does_nothing_without_a_tm_source():
    renderer = TMSourceColRenderer(view=None)
    renderer.matchdata = {'source': 'a'}

    renderer.do_render(window=None, widget=None, _background_area=None, cell_area=None, _flags=None)


# update_geometry(): real widgets, real positions - translate/virtaal#1366
# (a unit rendered near the bottom of the screen could push the popup
# off-screen, or, once clamped, over the widget it's anchored to).


def _process_events():
    while Gtk.events_pending():
        Gtk.main_iteration()


# Tall enough to be representative of a real (large/maximized) Virtaal
# window - deliberately much taller than the popup itself, so "is there
# room" is being tested by the target's position within the window, not
# by the window being too short to ever fit the popup at all.
WINDOW_HEIGHT = 700


def _make_source_and_target(position, height=WINDOW_HEIGHT, comment=None):
    """Source and target, each in its own ScrolledWindow as in the real unit
    view, pinned to the 'top' or 'bottom' of the window by a filler, and
    an optional translator comment below the target."""
    window = Gtk.Window()
    window.set_decorated(False)
    vbox = Gtk.VBox()
    textviews = []
    for _ in range(2):
        textview = Gtk.TextView()
        textview.set_size_request(400, 30)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        scrolled.add(textview)
        textviews.append((textview, scrolled))
    (source, source_scrolled), (target, target_scrolled) = textviews
    target.selector_textbox = source
    if position == 'bottom':
        vbox.pack_start(Gtk.Label(), True, True, 0)
    vbox.pack_start(source_scrolled, False, False, 0)
    vbox.pack_start(target_scrolled, False, False, 0)
    if comment is not None:
        label = Gtk.Label(label=comment)
        label.set_size_request(-1, 40)
        vbox.pack_start(label, False, False, 0)
        target.below_widgets = (label,)
    if position == 'top':
        vbox.pack_start(Gtk.Label(), True, True, 0)
    window.add(vbox)
    window.set_default_size(400, height)
    window.show_all()
    _process_events()
    window.move(*_workarea_origin(target))
    _process_events()
    return window, source, target


def _workarea_origin(widget):
    display = Gdk.Display.get_default()
    workarea = display.get_monitor_at_window(
        widget.get_window(Gtk.TextWindowType.WIDGET)
    ).get_workarea()
    return workarea.x + 10, workarea.y + 10


def _popup_span(tmwindow):
    popup_window = tmwindow.get_window()
    top = popup_window.get_root_origin().y
    return top, top + popup_window.get_height()


def _top(textview):
    return textview.get_window(Gtk.TextWindowType.WIDGET).get_origin().y


def _bottom(textview):
    return _top(textview) + textview.get_parent().get_allocation().height


def _fake_view():
    """Just enough of a TMView for the match renderer to size real rows."""
    lang = SimpleNamespace(
        source_lang=SimpleNamespace(code='en'), target_lang=SimpleNamespace(code='af')
    )
    return SimpleNamespace(
        get_target_width=lambda: 400,
        controller=SimpleNamespace(main_controller=SimpleNamespace(lang_controller=lang)),
    )


def _make_tmwindow_with_matches(n=1, show=True):
    tmwindow = TMWindow(_fake_view())
    for i in range(n):
        match = {
            'quality': 90 - i,
            'tmsource': 'Current file',
            'query_str': 'query',
            'source': f'source text {i}',
            'target': f'target text {i}',
        }
        tmwindow.liststore.append([match, f'match {i}'])
    tmwindow.treeview.columns_autosize()
    if show:
        tmwindow.show_all()
        _process_events()
    return tmwindow


def test_rows_height_is_known_before_the_window_is_shown():
    # update_geometry() places the window before it is first shown.
    tmwindow = _make_tmwindow_with_matches(n=3, show=False)
    before = tmwindow.rows_height()

    tmwindow.show_all()
    _process_events()

    assert before > 3 * 20
    assert tmwindow.rows_height() == before

    tmwindow.destroy()


def test_update_geometry_pops_down_when_there_is_room_below():
    window, _source, target = _make_source_and_target('top')
    tmwindow = _make_tmwindow_with_matches()

    tmwindow.update_geometry(target)
    _process_events()

    top, _ = _popup_span(tmwindow)
    assert top >= _bottom(target)

    tmwindow.destroy()
    window.destroy()


def test_update_geometry_pops_down_below_the_translator_comment():
    window, _source, target = _make_source_and_target('top', comment='A translator comment')
    tmwindow = _make_tmwindow_with_matches()

    tmwindow.update_geometry(target)
    _process_events()

    top, _ = _popup_span(tmwindow)
    assert top >= _bottom(target) + 40

    tmwindow.destroy()
    window.destroy()


def test_update_geometry_skips_below_widgets_that_are_not_shown():
    # A plural unit's first form anchors below the last form and the
    # comment row; either can be missing.
    window, _source, target = _make_source_and_target('top', comment='A translator comment')
    target.below_widgets = (Gtk.Label(), target.below_widgets[0])
    tmwindow = _make_tmwindow_with_matches()

    tmwindow.update_geometry(target)
    _process_events()

    top, _ = _popup_span(tmwindow)
    assert top >= _bottom(target) + 40

    tmwindow.destroy()
    window.destroy()


def test_update_geometry_ignores_a_hidden_translator_comment():
    window, _source, target = _make_source_and_target('top', comment='')
    target.below_widgets[0].hide()
    _process_events()
    tmwindow = _make_tmwindow_with_matches()

    tmwindow.update_geometry(target)
    _process_events()

    top, _ = _popup_span(tmwindow)
    assert _bottom(target) <= top < _bottom(target) + 40

    tmwindow.destroy()
    window.destroy()


def test_update_geometry_flips_above_the_source_when_there_is_no_room_below():
    window, source, target = _make_source_and_target('bottom')
    tmwindow = _make_tmwindow_with_matches(n=3)

    tmwindow.update_geometry(target)
    _process_events()

    _, bottom = _popup_span(tmwindow)
    assert bottom <= _top(source)

    tmwindow.destroy()
    window.destroy()


def _make_unit_above_a_panel():
    """Source and target in the middle of the window, a panel the
    height of the window's lower half under them."""
    window = Gtk.Window()
    window.set_decorated(False)
    vbox = Gtk.VBox()
    vbox.pack_start(Gtk.Label(), True, True, 0)
    boxes = []
    for _ in range(2):
        textview = Gtk.TextView()
        textview.set_size_request(400, 30)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        scrolled.add(textview)
        vbox.pack_start(scrolled, False, False, 0)
        boxes.append(textview)
    source, target = boxes
    target.selector_textbox = source
    panel = Gtk.Label(label='panel')
    panel.set_size_request(-1, WINDOW_HEIGHT // 2)
    vbox.pack_start(panel, False, False, 0)
    window.add(vbox)
    window.set_default_size(400, WINDOW_HEIGHT)
    window.show_all()
    _process_events()
    window.move(*_workarea_origin(target))
    _process_events()
    return window, source, target, panel


def test_update_geometry_keeps_above_a_panel_under_the_unit():
    window, source, target, panel = _make_unit_above_a_panel()
    tmwindow = _make_tmwindow_with_matches(n=3)

    tmwindow.update_geometry(target)
    _process_events()
    top, _bottom_ = _popup_span(tmwindow)
    assert top >= _bottom(target)

    tmwindow.update_geometry(target, keep_above=panel)
    _process_events()
    _top_, bottom = _popup_span(tmwindow)
    assert bottom <= _top(source)

    tmwindow.destroy()
    window.destroy()


# Too many matches to fit above or below a unit in a short window: the
# popup must shrink rather than cover the unit.


def test_update_geometry_shrinks_above_when_neither_side_fits():
    window, source, target = _make_source_and_target('bottom', height=250)
    tmwindow = _make_tmwindow_with_matches(n=20)
    assert tmwindow.rows_height() > tmwindow.MAX_HEIGHT

    tmwindow.update_geometry(target)
    _process_events()

    top, bottom = _popup_span(tmwindow)
    assert bottom <= _top(source)
    assert bottom - top < tmwindow.MAX_HEIGHT

    tmwindow.destroy()
    window.destroy()


def test_update_geometry_shrinks_below_when_neither_side_fits():
    window, _source, target = _make_source_and_target('top', height=250)
    tmwindow = _make_tmwindow_with_matches(n=20)
    assert tmwindow.rows_height() > tmwindow.MAX_HEIGHT

    tmwindow.update_geometry(target)
    _process_events()

    top, bottom = _popup_span(tmwindow)
    assert top >= _bottom(target)
    assert bottom - top < tmwindow.MAX_HEIGHT

    tmwindow.destroy()
    window.destroy()


def test_update_geometry_sizes_the_popup_from_the_real_theme_border(monkeypatch):
    # rows_height() is fixed here rather than measured from real rows -
    # its own real value isn't stable across a full suite run (a known,
    # separate GTK cross-test state issue - #3738), and isn't what this
    # test is about. What matters here is that the *border* added on
    # top of it is queried from the real theme, not a hardcoded guess.
    tmwindow = TMWindow(None)
    monkeypatch.setattr(tmwindow, 'rows_height', lambda: 100)
    tmwindow.show_all()
    _process_events()

    window = Gtk.Window()
    target = Gtk.TextView()
    target.set_size_request(200, 30)
    window.add(target)
    window.show_all()
    _process_events()

    border = tmwindow.scrolled_window.get_style_context().get_border(Gtk.StateFlags.NORMAL)
    expected_height = 100 + border.top + border.bottom

    tmwindow.update_geometry(target)
    _process_events()

    assert tmwindow.get_window().get_height() == expected_height

    tmwindow.destroy()
    window.destroy()


def test_spare_width_goes_to_the_matches_not_the_tm_source_column():
    # update_geometry() sizes the window from the TM Source column's
    # width; if that column took the spare width, each update would make
    # the window wider. GTK gives spare width to the last column unless
    # another one expands.
    tmwindow = TMWindow(None)

    assert tmwindow.tvc_match.get_expand() is True
    assert tmwindow.tvc_tm_source.get_expand() is False


def test_update_geometry_shrinks_back_when_the_target_narrows():
    # #4149: after fullscreen and back, the Matches column kept its
    # fullscreen width.
    window, _source, target = _make_source_and_target('top')
    tmwindow = TMWindow(_fake_view())
    tmwindow.view.get_target_width = lambda: target.get_parent().get_allocation().width
    tmwindow.liststore.append(
        [
            {
                'quality': 90,
                'tmsource': 'Current file',
                'query_str': 'query',
                'source': 'source text',
                'target': 'target text',
            },
            'match',
        ]
    )
    tmwindow.show_all()
    _process_events()

    def update_at(window_width):
        window.resize(window_width, WINDOW_HEIGHT)
        for _ in range(100):
            _process_events()
            if target.get_parent().get_allocation().width == window_width:
                break
            time.sleep(0.01)
        tmwindow.update_geometry(target)
        for _ in range(10):
            _process_events()
            time.sleep(0.01)
        return tmwindow.get_window().get_width(), tmwindow.tvc_match.get_width()

    update_at(400)
    wide_width, wide_match_width = update_at(900)
    width, match_width = update_at(400)

    assert width < wide_width - 400
    assert match_width < wide_match_width - 400

    tmwindow.destroy()
    window.destroy()


def test_update_geometry_waits_until_the_popup_is_realized():
    window, _source, target = _make_source_and_target('top')
    tmwindow = _make_tmwindow_with_matches(show=False)

    tmwindow.update_geometry(target)

    assert not tmwindow.get_realized()
    tmwindow.destroy()
    window.destroy()


def test_update_geometry_waits_until_the_target_is_shown():
    tmwindow = _make_tmwindow_with_matches()
    width = tmwindow.get_window().get_width()
    target = Gtk.TextView()
    scrolled = Gtk.ScrolledWindow()
    scrolled.add(target)

    tmwindow.update_geometry(target)
    _process_events()

    assert tmwindow.get_window().get_width() == width
    tmwindow.destroy()


def test_update_geometry_keeps_to_the_monitor_without_the_windows_frame(monkeypatch):
    window, _source, target = _make_source_and_target('top')
    tmwindow = _make_tmwindow_with_matches()
    monkeypatch.setattr(target, 'get_toplevel', lambda: SimpleNamespace(get_window=lambda: None))

    tmwindow.update_geometry(target)
    _process_events()

    top, bottom = _popup_span(tmwindow)
    workarea = (
        Gdk.Display.get_default()
        .get_monitor_at_window(target.get_window(Gtk.TextWindowType.WIDGET))
        .get_workarea()
    )
    assert top >= _bottom(target)
    assert bottom <= workarea.y + workarea.height
    tmwindow.destroy()
    window.destroy()


def _drawn_pixels(renderer, widget):
    """The pixels C{renderer} draws for its matchdata in a realized view
    like C{widget}, beyond those of a row it draws nothing for."""

    def render(matchdata):
        liststore = Gtk.ListStore(GObject.TYPE_PYOBJECT)
        liststore.append([matchdata])
        view = Gtk.TreeView(model=liststore)
        view.set_direction(widget.get_direction())
        view.set_headers_visible(False)
        view.append_column(Gtk.TreeViewColumn('', renderer, matchdata=0))
        window = Gtk.OffscreenWindow()
        window.set_size_request(400, 200)
        window.add(view)
        window.show_all()
        _process_events()
        pixels = window.get_pixbuf().get_pixels()
        window.destroy()
        return pixels

    matchdata = renderer.matchdata
    blank = render({'source': '', 'target': '', 'query_str': '', 'quality': 0})
    drawn = render(matchdata)
    renderer.matchdata = matchdata
    return sum(1 for a, b in zip(drawn, blank, strict=True) if a != b)


@pytest.mark.parametrize('direction', [Gtk.TextDirection.LTR, Gtk.TextDirection.RTL])
def test_tm_source_is_drawn_in_either_direction(direction):
    widget = Gtk.TreeView()
    widget.set_direction(direction)
    renderer = TMSourceColRenderer(view=None)
    renderer.matchdata = {'tmsource': 'Current file'}

    assert _drawn_pixels(renderer, widget) > 0
    assert renderer.get_property('matchdata') == {'tmsource': 'Current file'}


def test_tm_source_with_markup_characters_is_shown():
    # An XLIFF alt-trans origin attribute goes into the TM source as is.
    widget = Gtk.TreeView()
    renderer = TMSourceColRenderer(view=None)
    renderer.matchdata = {'tmsource': 'Tom and Jerry'}
    plain = renderer.do_get_size(widget, None)
    renderer.matchdata = {'tmsource': 'Tom & Jerry'}

    assert renderer.do_get_size(widget, None)[3] > plain[3] * 3 // 4
    assert _drawn_pixels(renderer, widget) > 0


def _match_renderer(**match):
    renderer = TMMatchRenderer(_fake_view())
    renderer.matchdata = dict(
        {'quality': 90, 'query_str': 'query', 'source': 'source text', 'target': 'target text'},
        **match,
    )
    return renderer


def test_a_machine_translation_shows_only_its_target():
    widget = Gtk.TreeView()
    tm_height = _match_renderer().do_get_size(widget, None)[3]
    mt = _match_renderer(quality=0, source='query')

    mt_height = mt.do_get_size(widget, None)[3]

    assert mt.source_layout is None
    assert mt_height < tm_height
    assert _drawn_pixels(mt, widget) > 0
    assert mt.get_property('matchdata')['quality'] == 0


def test_a_matchs_inline_codes_are_shown_as_the_editor_shows_them():
    widget = Gtk.TreeView()
    renderer = _match_renderer(
        source='Click here now',
        target='Klik hier nou',
        source_markup='Click <g id="1">here</g> now<x id="2"/>',
        target_markup='Klik <g id="1">hier</g> nou<x id="2"/>',
    )

    renderer.do_get_size(widget, None)

    assert renderer.source_layout.get_text() == 'Click <1|here> now[2]'
    assert renderer.target_layout.get_text() == 'Klik <1|hier> nou[2]'
    assert _drawn_pixels(renderer, widget) > 0


def test_a_match_whose_text_isnt_its_markups_shows_the_text():
    # An adapted target no longer holds the TM's markup.
    renderer = _match_renderer(target='Klik daar', target_markup='Klik <g id="1">hier</g>')

    renderer.do_get_size(Gtk.TreeView(), None)

    assert renderer.target_layout.get_text() == 'Klik daar'


ADAPTED_MATCH = {
    'source': 'Save 5 files',
    'target': 'Stoor 3 lêers',
    'source_markup': 'Save <g id="1">5</g> files',
    'target_markup': 'Stoor <g id="1">5</g> lêers',
    'target_shown_markup': 'Stoor <g id="1">3</g> lêers',
}


def test_an_adapted_match_shows_its_codes():
    renderer = _match_renderer(**ADAPTED_MATCH)

    renderer.do_get_size(Gtk.TreeView(), None)

    assert renderer.source_layout.get_text() == 'Save <1|5> files'
    assert renderer.target_layout.get_text() == 'Stoor <1|3> lêers'


def test_an_adapted_matchs_codes_keep_its_difference_shown():
    unchanged = _renderer_with_existing('', **ADAPTED_MATCH)
    renderer = _renderer_with_existing('Stoor 5 lêers', **ADAPTED_MATCH)

    assert renderer.target_layout.get_text() == 'Stoor <1|3> lêers'
    # "3", at byte 9, replacing the existing translation's "5".
    (start, end), *others = [
        span
        for span in _attribute_spans(renderer.target_layout)
        if span not in _attribute_spans(unchanged.target_layout)
    ]
    assert start <= 9 < end
    assert others == []


def test_match_text_is_right_aligned_in_a_right_to_left_view():
    widget = Gtk.TreeView()
    widget.set_direction(Gtk.TextDirection.RTL)
    renderer = _match_renderer()

    renderer.do_get_size(widget, None)

    assert renderer.target_layout.get_alignment() == Pango.Alignment.RIGHT
    assert renderer.source_layout.get_alignment() == Pango.Alignment.RIGHT


# update_geometry() with stand-ins for every widget it asks: where the
# popup goes for each side the room is on.


def _rect(x, y, width, height):
    return SimpleNamespace(x=x, y=y, width=width, height=height)


def _fake_widget(top, *, rtl=False, source_top=None, below=(), frame=None):
    """A target at y=top (40 high, 300 wide) in a window covering frame
    (1000x800 by default), its source at source_top."""
    frame = frame or _rect(0, 0, 1000, 800)
    origin = SimpleNamespace(x=200, y=top)
    widget = SimpleNamespace(
        get_parent=lambda: SimpleNamespace(get_allocation=lambda: _rect(0, 0, 300, 40)),
        get_window=lambda kind: SimpleNamespace(get_origin=lambda: origin),
        get_direction=lambda: Gtk.TextDirection.RTL if rtl else Gtk.TextDirection.LTR,
        get_toplevel=lambda: SimpleNamespace(
            get_window=lambda: SimpleNamespace(get_frame_extents=lambda: frame)
        ),
        below_widgets=below,
    )
    if source_top is not None:
        source_origin = SimpleNamespace(x=200, y=source_top)
        widget.selector_textbox = SimpleNamespace(
            get_window=lambda kind: SimpleNamespace(get_origin=lambda: source_origin)
        )
    return widget


def _fake_below(height, offset, mapped=True):
    return SimpleNamespace(
        get_mapped=lambda: mapped,
        get_allocated_height=lambda: height,
        translate_coordinates=lambda widget, x, y: (x, offset + y),
    )


def _place(widget, rows_height=100, tm_source_width=60, scrollbar=False):
    calls = []
    border = SimpleNamespace(top=1, bottom=1)
    window = SimpleNamespace(
        move_resize=lambda *args: calls.append(('window', *args)),
        get_toplevel=lambda: SimpleNamespace(
            move_resize=lambda *args: calls.append(('toplevel', *args))
        ),
    )
    popup = SimpleNamespace(
        MAX_HEIGHT=TMWindow.MAX_HEIGHT,
        MIN_HEIGHT=TMWindow.MIN_HEIGHT,
        get_realized=lambda: True,
        tvc_match=SimpleNamespace(queue_resize=lambda: None),
        tvc_perc=SimpleNamespace(get_width=lambda: 40),
        tvc_tm_source=SimpleNamespace(get_width=lambda: tm_source_width),
        scrolled_window=SimpleNamespace(
            get_vscrollbar=lambda: SimpleNamespace(
                props=SimpleNamespace(visible=scrollbar),
                get_allocation=lambda: _rect(0, 0, 12, 0),
            ),
            get_style_context=lambda: SimpleNamespace(get_border=lambda state: border),
            set_size_request=lambda *args: calls.append(('scrolled', *args)),
        ),
        rows_height=lambda: rows_height,
        move=lambda *args: calls.append(('move', *args)),
        resize=lambda *args: calls.append(('resize', *args)),
        get_window=lambda: window,
    )
    TMWindow.update_geometry(popup, widget)
    return calls


@pytest.mark.parametrize(
    ('widget', 'kwargs', 'expected'),
    [
        # Room below.
        (_fake_widget(100), {}, (160, 142, 400, 102)),
        # Below a shown comment, not a hidden one.
        (
            _fake_widget(100, below=(_fake_below(30, 50), _fake_below(30, 200, mapped=False))),
            {},
            (160, 182, 400, 102),
        ),
        # No room below: above the source.
        (_fake_widget(700, source_top=650), {}, (160, 546, 400, 102)),
        # Without a source, above the target itself.
        (_fake_widget(700), {}, (160, 596, 400, 102)),
        # Room on neither side: shrink into the roomier.
        (_fake_widget(300, frame=_rect(0, 0, 1000, 400)), {'rows_height': 300}, (160, 0, 400, 298)),
        (
            _fake_widget(60, frame=_rect(0, 0, 1000, 400)),
            {'rows_height': 300},
            (160, 102, 400, 298),
        ),
        # Too little room anywhere: clamped over the unit.
        (_fake_widget(30, frame=_rect(0, 0, 1000, 120)), {'rows_height': 300}, (160, 0, 400, 302)),
        # Right to left, with a scroll bar; a too-wide source column.
        (_fake_widget(100, rtl=True), {'scrollbar': True}, (127, 142, 413, 102)),
        (_fake_widget(100), {'tm_source_width': 150}, (160, 142, 380, 102)),
        # Kept inside the window's frame.
        (_fake_widget(100, frame=_rect(300, 0, 400, 800)), {}, (300, 142, 400, 102)),
    ],
)
def test_update_geometry_places_the_popup(widget, kwargs, expected):
    x, y, width, height = expected

    assert _place(widget, **kwargs) == [
        ('move', x, y),
        ('resize', width, height),
        ('scrolled', width, height),
        ('window', 0, 0, width, height),
        ('toplevel', x, y, width, height),
    ]


def _renderer_with_existing(existing, **match):
    renderer = _match_renderer(**match)
    renderer.view.controller.existing_target = lambda: existing
    renderer.do_get_size(Gtk.TreeView(), None)
    return renderer


def _attribute_spans(layout):
    """The (start, end) byte ranges with a background colour set."""
    attributes = layout.get_attributes()
    return [
        (attribute.start_index, attribute.end_index)
        for attribute in attributes.get_attributes()
        if attribute.klass.type == Pango.AttrType.BACKGROUND
    ]


def test_a_tm_matchs_target_shows_how_it_differs_from_the_existing_translation():
    renderer = _renderer_with_existing('Klik hier', target='Klik nou')

    # "nou", replacing the existing translation's "hier", is highlighted.
    assert renderer.target_layout.get_text() == 'Klik nou'
    assert _attribute_spans(renderer.target_layout) == [(5, 8)]


def test_text_a_tm_match_leaves_out_of_the_existing_translation_is_struck_out():
    renderer = _renderer_with_existing('Klik hier nou', target='Klik hier')

    assert renderer.target_layout.get_text() == 'Klik hier nou'
    assert _attribute_spans(renderer.target_layout) == [(9, 13)]


@pytest.mark.parametrize(
    'existing, match',
    [
        ('', {'target': 'Klik daar'}),  # nothing to compare with
        ('Klik daar', {'target': 'Klik daar'}),  # the same translation
        ('Klik hier', {'target': 'Klik daar', 'quality': 0, 'source': 'query'}),  # MT
    ],
)
def test_a_matchs_target_shows_no_difference(existing, match):
    renderer = _renderer_with_existing(existing, **match)

    assert renderer.target_layout.get_text() == 'Klik daar'
    assert not _attribute_spans(renderer.target_layout)
