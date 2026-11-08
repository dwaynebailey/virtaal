#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from types import SimpleNamespace

from gi.repository import Gtk
from translate.storage.placeables import general

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.support import formatplaceables
from virtaal.support.invisiblechars import InvisiblePlaceable
from virtaal.views import placeablesguiinfo


class _FakeTextbox:
    def __init__(self, calls, name, visible=True, parent_visible=True):
        self.props = SimpleNamespace(visible=visible)
        self._parent_visible = parent_visible
        self._calls = calls
        self.name = name

    def is_visible(self):
        return self.props.visible and self._parent_visible

    def refresh(self):
        self._calls.append(('refresh', self.name))


def _controller(sources=(), targets=()):
    controller = PlaceablesController.__new__(PlaceablesController)
    unitview = SimpleNamespace(
        sources=list(sources),
        targets=list(targets),
        disable_signals=lambda names: None,
        enable_signals=lambda names: None,
    )
    controller.main_controller = SimpleNamespace(unit_controller=SimpleNamespace(view=unitview))
    return controller


def _record_update_style(monkeypatch, calls):
    monkeypatch.setattr(
        placeablesguiinfo, 'update_style', lambda widget: calls.append(('update_style', widget))
    )


def test_style_set_with_no_file_open_still_updates_the_placeable_colours(monkeypatch):
    # #3964: a dark theme applied at startup, before any text box exists,
    # left the light defaults in place.
    calls = []
    _record_update_style(monkeypatch, calls)

    _controller()._on_style_set(None)

    assert len(calls) == 1
    assert isinstance(calls[0][1], Gtk.TextView)


def test_style_set_updates_the_colours_before_redrawing_the_text(monkeypatch):
    calls = []
    _record_update_style(monkeypatch, calls)
    source = _FakeTextbox(calls, 'source')
    target = _FakeTextbox(calls, 'target')

    _controller(sources=[source], targets=[target])._on_style_set(None)

    assert calls == [('update_style', source), ('refresh', 'source'), ('refresh', 'target')]


def test_style_set_ignores_hidden_text_boxes(monkeypatch):
    calls = []
    _record_update_style(monkeypatch, calls)
    hidden = _FakeTextbox(calls, 'hidden', visible=False)
    target = _FakeTextbox(calls, 'target')

    _controller(sources=[hidden], targets=[target])._on_style_set(None)

    assert calls == [('update_style', target), ('refresh', 'target')]


def test_style_set_ignores_text_boxes_in_a_hidden_container(monkeypatch):
    # Unused plural targets: UnitView hides their container, and they still
    # hold the last plural unit's text, so refreshing one edits the unit.
    calls = []
    _record_update_style(monkeypatch, calls)
    target = _FakeTextbox(calls, 'target')
    unused_plural = _FakeTextbox(calls, 'unused plural', parent_visible=False)

    _controller(targets=[target, unused_plural])._on_style_set(None)

    assert calls == [('update_style', target), ('refresh', 'target')]


def test_adding_a_parser_keeps_the_parsers_added_before_it():
    controller = _controller()
    controller.parsers = []
    controller.emit = lambda signal: None

    def first(text):
        return None

    def second(text):
        return None

    controller.add_parsers(first)
    controller.add_parsers(second, general.NumberPlaceable.parse)
    controller.add_parsers(general.NewlinePlaceable.parse, second)

    assert controller.parsers == [
        general.NewlinePlaceable.parse,
        general.NumberPlaceable.parse,
        first,
        second,
    ]


def _gating_controller(store=None, current_unit=None):
    controller = _controller()
    controller.main_controller.unit_controller.current_unit = current_unit
    controller.main_controller.store_controller = SimpleNamespace(get_store=lambda: store)
    controller.parsers = []
    controller.emit = lambda signal: None
    controller.add_parsers(*general.parsers, *controller.own_parsers)
    return controller


def _fluent_store():
    pair = SimpleNamespace(converter=SimpleNamespace(id='fluent'), target='af.ftl')
    return SimpleNamespace(pair=pair)


def test_the_format_parsers_go_before_the_toolkits():
    parsers = _gating_controller().parsers

    assert parsers[: len(formatplaceables.parsers)] == formatplaceables.parsers
    assert parsers[-1] == InvisiblePlaceable.parse


def test_get_parsers_gives_a_format_only_its_own_parsers():
    po_parsers = _gating_controller().get_parsers()
    fluent_parsers = _gating_controller(_fluent_store()).get_parsers()

    assert formatplaceables.FluentPlaceable.parse not in po_parsers
    assert formatplaceables.BracePlaceable.parse not in po_parsers
    assert formatplaceables.ApplePlaceable.parse in po_parsers
    assert formatplaceables.FluentPlaceable.parse in fluent_parsers
    assert general.XMLTagPlaceable.parse in fluent_parsers


def test_get_parsers_follows_the_units_gettext_flags():
    flagged = SimpleNamespace(typecomments=['#, python-brace-format\n'])
    controller = _gating_controller(current_unit=SimpleNamespace(typecomments=[]))

    assert formatplaceables.BracePlaceable.parse not in controller.get_parsers()
    assert formatplaceables.BracePlaceable.parse in controller.get_parsers(flagged)


def test_get_parsers_leaves_out_a_disabled_format_parser():
    controller = _gating_controller(_fluent_store())
    controller.remove_parsers(formatplaceables.FluentPlaceable.parse)

    assert formatplaceables.FluentPlaceable.parse not in controller.get_parsers()


def test_every_format_parser_has_a_label():
    controller = _controller()
    controller.parser_info = {}
    controller._init_format_parser_descriptions()

    assert set(controller.parser_info) == set(formatplaceables.parsers)
