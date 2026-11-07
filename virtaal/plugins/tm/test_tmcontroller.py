#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from types import SimpleNamespace

import pytest
from translate.storage import xliff

from virtaal.plugins.tm.tmcontroller import TMController


def _bare_controller(**overrides):
    controller = TMController.__new__(TMController)
    for name, value in overrides.items():
        setattr(controller, name, value)
    return controller


class _FakePlugin:
    """A plain, hashable stand-in for a plugin - unlike SimpleNamespace,
    whose own __eq__ makes it unusable as a dict key."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _make_controller(matches=None, max_matches=5, current_query='hello', storecursor=True):
    controller = TMController.__new__(TMController)
    controller.storecursor = storecursor
    controller.current_query = current_query
    controller.matches = matches if matches is not None else []
    controller.max_matches = max_matches
    controller.displayed = []
    controller.view = SimpleNamespace(
        display_matches=lambda matches: controller.displayed.append(list(matches))
    )
    return controller


def _tmmodel(display_name='Some TM'):
    return SimpleNamespace(display_name=display_name)


def test_accept_response_ignores_a_response_after_the_file_was_closed():
    controller = _make_controller(storecursor=None)

    controller.accept_response(_tmmodel(), 'hello', [{'target': 'hallo', 'quality': 90}])

    assert controller.matches == []
    assert controller.displayed == []


def test_accept_response_ignores_a_stale_query():
    controller = _make_controller(current_query='goodbye')

    controller.accept_response(_tmmodel(), 'hello', [{'target': 'hallo', 'quality': 90}])

    assert controller.matches == []


def test_accept_response_ignores_an_empty_matches_list():
    controller = _make_controller()

    controller.accept_response(_tmmodel(), 'hello', [])

    assert controller.matches == []
    assert controller.displayed == []


def test_accept_response_coerces_a_string_quality_to_int():
    controller = _make_controller()

    controller.accept_response(_tmmodel(), 'hello', [{'target': 'hallo', 'quality': '90'}])

    assert controller.matches[0]['quality'] == 90


_XLIFF = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
<file original="a" source-language="en" target-language="af" datatype="plaintext"><body>
<trans-unit id="1"><source>%s</source></trans-unit>
</body></file></xliff>"""


def _tagged_response(unit_source, match):
    controller = _make_controller(current_query='Click here')
    controller._queried_unit = xliff.xlifffile.parsestring(
        (_XLIFF % unit_source).encode('utf-8')
    ).units[0]
    match = dict({'source': 'Click here', 'target': 'Klik hier', 'quality': 100}, **match)

    controller.accept_response(_tmmodel(), 'Click here', [match])

    return controller.matches[0]['quality']


@pytest.mark.parametrize(
    ('unit_source', 'source_markup', 'quality'),
    [
        # #4708: the same text with other tags isn't the same - 100 less
        # one for each code not shared, a pair counting once.
        ('Click <g id="1">here</g>', 'Click <x id="1"/>here', 98),
        ('Click <g id="1">here</g>', 'Click <g id="1">here</g><x id="2"/>', 99),
        ('Click here', 'Click <g id="1">here</g>', 99),
        ('Click here', 'Click <g id="1">here</g><x id="2"/>', 98),
        ('Click <g id="1">here</g>', 'Click here', 99),
        # The same kinds of code, whatever their ids.
        ('Click <g id="1">here</g>', 'Click <g id="7">here</g>', 100),
        ('Click <x id="1"/>here', 'Click <ph id="7"></ph>here', 100),
    ],
)
def test_accept_response_scores_a_match_with_other_codes_less(unit_source, source_markup, quality):
    match = {'source_markup': source_markup, 'target_markup': 'Klik hier'}

    assert _tagged_response(unit_source, match) == quality


@pytest.mark.parametrize(
    ('unit_source', 'quality'),
    [
        # A TMX <b> holds its native code as text; an XLIFF <g> doesn't.
        ('<g id="1">Save</g> %s files', 99),
        ('<bpt id="1">&lt;b&gt;</bpt>Save<ept id="1">&lt;/b&gt;</ept> %s files', 99),
        # No codes: 99 for the codes it lacks.
        ('Save %s files', 99),
    ],
)
def test_accept_response_scores_a_match_with_codes_without_their_native_code(unit_source, quality):
    unit = xliff.xlifffile.parsestring((_XLIFF % unit_source).encode('utf-8')).units[0]
    controller = _make_controller(current_query=unit.source)
    controller.abstract_placeables = True
    controller._queried_unit = unit
    controller._accel_markers = lambda: ()
    match = {
        'source': '<b>Save</b> %d files',
        'target': '<b>Stoor</b> %d lêers',
        'quality': 99,
        'raw_quality': 92,
        'source_markup': '<bpt id="1">&lt;b&gt;</bpt>Save<ept id="1">&lt;/b&gt;</ept> %d files',
        'target_markup': '<bpt id="1">&lt;b&gt;</bpt>Stoor<ept id="1">&lt;/b&gt;</ept> %d lêers',
    }

    controller.accept_response(_tmmodel(), unit.source, [match])

    assert controller.matches[0]['quality'] == quality


def test_accept_response_adapts_a_matchs_plain_target_too():
    # What picking a match whose codes don't map inserts (#4708).
    unit = xliff.xlifffile.parsestring(
        (_XLIFF % '<g id="1">Delete</g> 3 files<x id="2"/>').encode('utf-8')
    ).units[0]
    controller = _make_controller(current_query=unit.source)
    controller.abstract_placeables = True
    controller._queried_unit = unit
    controller._accel_markers = lambda: ()
    match = {
        'source': '<b>Delete</b> 5 files',
        'target': '<b>Skrap</b> 5 lêers',
        'quality': 81,
        'source_markup': '<bpt id="1">&lt;b&gt;</bpt>Delete<ept id="1">&lt;/b&gt;</ept> 5 files',
        'target_markup': '<bpt id="1">&lt;b&gt;</bpt>Skrap<ept id="1">&lt;/b&gt;</ept> 5 lêers',
    }

    controller.accept_response(_tmmodel(), unit.source, [match])

    match = controller.matches[0]
    assert match['target'] == '<b>Skrap</b> 3 lêers'
    assert match['plain_target'] == 'Skrap 3 lêers'
    assert TMController._rich_target(unit, match) is None
    assert TMController._plain_target(unit, match) == 'Skrap 3 lêers'


@pytest.mark.parametrize(
    ('match', 'expected'),
    [
        # A TMX it code's <b> is native code, not text.
        (
            {
                'target': '<b>Skrap dit nou',
                'target_markup': '<it id="tmx1" pos="open">&lt;b&gt;</it>Skrap dit nou',
            },
            'Skrap dit nou',
        ),
        # An XLIFF g holds text.
        ({'target': 'Skrap dit nou', 'target_markup': '<g id="1">Skrap</g> dit nou'}, None),
        ({'target': 'Skrap dit nou'}, None),
        # Markup that can't be read leaves the target alone.
        ({'target': '<b>Skrap', 'target_markup': '<b>Skrap'}, None),
    ],
)
def test_plain_target_leaves_out_the_native_code_of_codes(match, expected):
    unit = xliff.xlifffile.parsestring((_XLIFF % 'Delete it now').encode('utf-8')).units[0]

    assert TMController._plain_target(unit, match) == (expected or match['target'])


def test_plain_target_of_a_po_unit_keeps_the_code_text():
    from translate.storage import po

    match = {
        'target': '<b>Skrap dit nou',
        'target_markup': '<it id="tmx1" pos="open">&lt;b&gt;</it>Skrap dit nou',
    }

    assert TMController._plain_target(po.pounit('Delete it now'), match) == '<b>Skrap dit nou'


def _numbered_match(unit_source, **match):
    unit = xliff.xlifffile.parsestring((_XLIFF % unit_source).encode('utf-8')).units[0]
    controller = _make_controller(current_query=unit.source)
    controller.abstract_placeables = True
    controller._queried_unit = unit
    controller._accel_markers = lambda: ()
    match = dict(
        {
            'source': 'Save 5 files',
            'target': 'Stoor 5 lêers',
            'quality': 90,
            'source_markup': 'Save <g id="1">5</g> files',
            'target_markup': 'Stoor <g id="1">5</g> lêers',
        },
        **match,
    )

    controller.accept_response(_tmmodel(), unit.source, [match])

    return controller.matches[0]


def test_an_adapted_match_shows_the_codes_picking_it_gives():
    match = _numbered_match('Save <g id="4">3</g> files')

    assert match['target'] == 'Stoor 3 lêers'
    assert match['target_shown_markup'] == 'Stoor <g id="4">3</g> lêers'
    assert match['target_markup'] == 'Stoor <g id="1">5</g> lêers'


@pytest.mark.parametrize(
    ('unit_source', 'match'),
    [
        # Not adapted: its own markup holds its text.
        ('Save <g id="4">5</g> files', {}),
        # Codes that don't map: picking it gives the text.
        ('Save <x id="4"/>3 files', {}),
        ('Save 3 files', {'source_markup': None, 'target_markup': None}),
    ],
)
def test_a_match_shows_its_own_codes_unless_adapted(unit_source, match):
    assert 'target_shown_markup' not in _numbered_match(unit_source, **match)


def test_accept_response_leaves_a_match_without_markup_at_100():
    assert _tagged_response('Click <g id="1">here</g>', {}) == 100


def test_accept_response_leaves_a_po_units_match_at_100():
    from translate.storage import po

    controller = _make_controller(current_query='Click here')
    controller._queried_unit = po.pounit('Click here')
    match = {
        'source': 'Click here',
        'target': 'Klik hier',
        'quality': 100,
        'source_markup': 'Click <g id="1">here</g>',
        'target_markup': 'Klik <g id="1">hier</g>',
    }

    controller.accept_response(_tmmodel(), 'Click here', [match])

    assert controller.matches[0]['quality'] == 100


# A plural unit's suggestions for the form being edited (#1392).

PLURAL_MATCH = {
    'source': 'Delete %d file',
    'target': 'Skrap %d lêer',
    'quality': 100,
    'plural_source': 'Delete %d files',
    'plural_targets': ['Skrap %d lêer', 'Skrap %d lêers'],
}


def _plural_response(
    match=PLURAL_MATCH, target_n=1, nplurals=2, source=None, plural=True, abstract=True
):
    from translate.misc.multistring import multistring
    from translate.storage import po

    source = source or ['Delete %d file', 'Delete %d files']
    controller = _make_controller(current_query=source[0])
    controller.min_quality = 75
    controller.abstract_placeables = abstract
    controller.target_n = target_n
    controller.main_controller = SimpleNamespace(
        lang_controller=SimpleNamespace(get_target_nplurals=lambda: nplurals)
    )
    unit = po.pounit(multistring(source) if plural else source[0])
    controller._queried_unit = unit
    controller.accept_response(_tmmodel(), source[0], [dict(match)])
    return controller.matches


def test_accept_response_offers_a_plural_matchs_form_for_the_form_edited():
    (match,) = _plural_response()

    assert (match['source'], match['target'], match['quality']) == (
        'Delete %d files',
        'Skrap %d lêers',
        100,
    )
    assert match['query_str'] == 'Delete %d files'


def test_accept_response_scores_a_later_form_on_the_source_plurals():
    (match,) = _plural_response(source=['Delete %d file', 'Delete %d items'])

    assert match['target'] == 'Skrap %d lêers'
    assert 75 <= match['quality'] < 100


def test_accept_response_gives_a_later_form_the_units_placeables():
    (match,) = _plural_response(source=['Delete %s file', 'Delete %s files'])

    assert match['target'] == 'Skrap %s lêers'


@pytest.mark.parametrize(
    'kwargs',
    [
        {'target_n': 0},
        # Another plural equation: its forms are other numbers' (#1392).
        {'nplurals': 3},
        {'plural': False},
        # The source plurals are too far apart.
        {'source': ['Delete %d file', 'Remove every one of the files']},
        {'match': {k: v for k, v in PLURAL_MATCH.items() if not k.startswith('plural')}},
        # A reply of another shape from a remote server.
        {'match': {**PLURAL_MATCH, 'plural_targets': 'Skrap %d lêers'}},
        {'match': {**PLURAL_MATCH, 'plural_targets': ['Skrap %d lêer', 2]}},
        {'match': {**PLURAL_MATCH, 'plural_source': None}},
    ],
)
def test_accept_response_offers_the_first_form_otherwise(kwargs):
    (match,) = _plural_response(**kwargs)

    assert (match['source'], match['target']) == ('Delete %d file', 'Skrap %d lêer')


def test_accept_response_offers_a_plural_matchs_form_without_abstraction():
    (match,) = _plural_response(abstract=False)

    assert (match['target'], match['quality']) == ('Skrap %d lêers', 100)


def test_accept_response_defaults_a_missing_tmsource_to_the_models_display_name():
    controller = _make_controller()

    controller.accept_response(_tmmodel('Amagama'), 'hello', [{'target': 'hallo', 'quality': 90}])

    assert controller.matches[0]['tmsource'] == 'Amagama'


def test_accept_response_adds_a_genuinely_new_match_and_displays_it():
    controller = _make_controller()

    controller.accept_response(_tmmodel(), 'hello', [{'target': 'hallo', 'quality': 90}])

    assert len(controller.matches) == 1
    assert controller.displayed == [controller.matches]


def test_accept_response_keeps_an_existing_qualified_match_over_a_duplicate_target():
    """A duplicate suggestion (same normalized target) that already has
    quality information is trusted over a new one - only a
    qualityless existing match (e.g. from MT) gets replaced."""
    existing = {'target': 'hallo', 'quality': 95, 'tmsource': 'Local TM', 'query_str': 'hello'}
    controller = _make_controller(matches=[existing])

    controller.accept_response(_tmmodel(), 'hello', [{'target': 'hallo', 'quality': 80}])

    assert controller.matches == [existing]
    assert controller.displayed == []  # nothing new, so display_matches is never called


def test_accept_response_replaces_an_unqualified_duplicate_with_a_qualified_one():
    existing = {
        'target': 'hallo',
        'quality': None,
        'tmsource': 'Machine Translation',
        'query_str': 'hello',
    }
    controller = _make_controller(matches=[existing])

    controller.accept_response(_tmmodel(), 'hello', [{'target': 'hallo', 'quality': 90}])

    assert len(controller.matches) == 1
    assert controller.matches[0]['quality'] == 90
    assert existing not in controller.matches


def test_accept_response_sorts_by_quality_descending_and_truncates_to_max_matches():
    controller = _make_controller(max_matches=2)

    controller.accept_response(
        _tmmodel(),
        'hello',
        [
            {'target': 'laag', 'quality': 60},
            {'target': 'hoog', 'quality': 95},
            {'target': 'middel', 'quality': 80},
        ],
    )

    assert [m['target'] for m in controller.matches] == ['hoog', 'middel']


def _weighted_tmmodel(weights):
    return SimpleNamespace(
        display_name='Amagama', match_weight=lambda match: weights[match['target']]
    )


def test_accept_response_sorts_by_quality_times_weight_keeping_quality():
    local = {'target': 'plaaslik', 'quality': 85, 'tmsource': 'Local TM', 'query_str': 'hello'}
    controller = _make_controller(matches=[local])

    controller.accept_response(
        _weighted_tmmodel({'kde': 1.0, 'gnome': 1.5, 'hosted': 0.5}),
        'hello',
        [
            {'target': 'kde', 'quality': 90},
            {'target': 'gnome', 'quality': 80},
            {'target': 'hosted', 'quality': 95},
        ],
    )

    assert [(m['target'], m['quality']) for m in controller.matches] == [
        ('gnome', 80),
        ('kde', 90),
        ('plaaslik', 85),
        ('hosted', 95),
    ]


def test_accept_response_puts_exact_matches_first_whatever_their_weight():
    controller = _make_controller()

    controller.accept_response(
        _weighted_tmmodel({'fuzzy': 2.0, 'exact': 0.5, 'trusted': 1.5}),
        'hello',
        [
            {'target': 'fuzzy', 'quality': 90},
            {'target': 'exact', 'quality': 100},
            {'target': 'trusted', 'quality': 100},
        ],
    )

    assert [m['target'] for m in controller.matches] == ['trusted', 'exact', 'fuzzy']


def test_accept_response_keeps_the_order_of_equally_ranked_matches():
    first = {'target': 'een', 'quality': 80, 'tmsource': 'Local TM', 'query_str': 'hello'}
    controller = _make_controller(matches=[first])

    controller.accept_response(
        _weighted_tmmodel({'twee': 1.0, 'drie': 2.0}),
        'hello',
        [{'target': 'twee', 'quality': 80}, {'target': 'drie', 'quality': 40}],
    )

    assert [m['target'] for m in controller.matches] == ['een', 'twee', 'drie']


def test_accept_response_tidies_each_match():
    controller = _make_controller()
    matches = [
        {'target': 'een', 'quality': None, 'tmsource': None},
        {'target': 'twee'},
        {'target': 'drie', 'quality': 70.6, 'tmsource': 'Elsewhere'},
    ]

    controller.accept_response(_tmmodel('Some TM'), b'hello', matches)

    assert [(m['target'], m.get('quality'), m['tmsource']) for m in controller.matches] == [
        ('drie', 70, 'Elsewhere'),
        ('een', 0, 'Some TM'),
        ('twee', None, 'Some TM'),
    ]
    assert {m['query_str'] for m in controller.matches} == {'hello'}


def test_accept_response_keeps_the_first_of_two_matches_with_one_target():
    controller = _make_controller()

    controller.accept_response(
        _tmmodel(),
        'hello',
        [{'target': 'hallo', 'quality': 0}, {'target': 'hallo', 'quality': 90}],
    )

    # The first has no quality, so the second replaces it.
    assert [m['quality'] for m in controller.matches] == [90]
    assert len(controller.displayed) == 1


# __init__() #


def test_init_computes_config_and_delegates_to_helpers(monkeypatch):
    calls = []
    monkeypatch.setattr(TMController, '_load_models', lambda self: calls.append('load_models'))
    monkeypatch.setattr(
        TMController, '_connect_plugin', lambda self: calls.append('connect_plugin')
    )
    fake_view = SimpleNamespace()
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmview.TMView', lambda controller, max_matches: fake_view
    )
    main_controller = object()

    controller = TMController(
        main_controller, config={'max_matches': 3, 'min_quality': 50, 'disabled_models': ['foo']}
    )

    assert controller.main_controller is main_controller
    assert controller.max_matches == 3
    assert controller.min_quality == 50
    assert controller.disabled_model_names == ['basetmmodel', 'foo']
    assert controller.storecursor is None
    assert controller.view is fake_view
    assert calls == ['load_models', 'connect_plugin']


def test_init_defaults_config_when_none_given(monkeypatch):
    monkeypatch.setattr(TMController, '_load_models', lambda self: None)
    monkeypatch.setattr(TMController, '_connect_plugin', lambda self: None)
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmview.TMView', lambda controller, max_matches: SimpleNamespace()
    )

    controller = TMController(object())

    assert controller.config == {}
    assert controller.max_matches == 5
    assert controller.min_quality == 75
    assert controller.disabled_model_names == ['basetmmodel']


# _load_models() #


def test_load_models_builds_the_plugin_controller_and_loads_plugins(monkeypatch):
    controller = _bare_controller(disabled_model_names=['x', 'y'], _signal_ids={})
    captured = {}

    def fake_for_backend(owner, classname, category, interface, get_disabled):
        captured['args'] = (owner, classname, category, interface, get_disabled)
        return SimpleNamespace(
            connect=lambda signal, handler: f'sig-{signal}',
            load_plugins=lambda: calls.append('loaded'),
        )

    calls = []
    monkeypatch.setattr(
        'virtaal.controllers.plugincontroller.PluginController.for_backend', fake_for_backend
    )

    controller._load_models()

    owner, classname, category, interface, get_disabled = captured['args']
    assert (owner, classname, category) == (controller, 'TMModel', 'tm')
    assert get_disabled() == ['x', 'y']
    assert calls == ['loaded']
    assert controller._signal_ids['plugin-enabled'] == 'sig-plugin-enabled'
    assert controller._signal_ids['plugin-disabled'] == 'sig-plugin-disabled'


def test_load_models_on_plugin_enabled_connects_match_found_to_accept_response(monkeypatch):
    controller = _bare_controller(disabled_model_names=[], _signal_ids={})
    handlers = {}
    monkeypatch.setattr(
        'virtaal.controllers.plugincontroller.PluginController.for_backend',
        lambda *a: SimpleNamespace(
            connect=lambda signal, handler: (
                handlers.__setitem__(signal, handler) or f'sig-{signal}'
            ),
            load_plugins=lambda: None,
        ),
    )
    controller._load_models()
    plugin = _FakePlugin(connect=lambda signal, handler: 'plugin-sig')

    handlers['plugin-enabled'](None, plugin)

    assert controller._model_signal_ids[plugin] == 'plugin-sig'


def test_load_models_on_plugin_disabled_disconnects_the_model(monkeypatch):
    controller = _bare_controller(disabled_model_names=[], _signal_ids={})
    handlers = {}
    monkeypatch.setattr(
        'virtaal.controllers.plugincontroller.PluginController.for_backend',
        lambda *a: SimpleNamespace(
            connect=lambda signal, handler: (
                handlers.__setitem__(signal, handler) or f'sig-{signal}'
            ),
            load_plugins=lambda: None,
        ),
    )
    controller._load_models()
    disconnected = []
    plugin = _FakePlugin(disconnect=disconnected.append)
    controller._model_signal_ids[plugin] = 'plugin-sig'

    stopped = []
    controller._fetches = {plugin: SimpleNamespace(cancel=lambda: stopped.append(plugin))}

    handlers['plugin-disabled'](None, plugin)

    assert disconnected == ['plugin-sig']
    # A file fetch from a service just turned off stops (#1679).
    assert stopped == [plugin]


# _connect_plugin() #


def _fake_store_controller(store=None):
    connected = []

    def connect(signal, handler):
        connected.append(signal)
        return f'sig-{signal}'

    sc = SimpleNamespace(connect=connect, get_store=lambda: store)
    sc._connected = connected
    return sc


def test_connect_plugin_subscribes_to_store_loaded_and_closed():
    controller = _bare_controller()
    store_controller = _fake_store_controller(store=None)
    controller.main_controller = SimpleNamespace(
        store_controller=store_controller, mode_controller=None
    )

    controller._connect_plugin()

    assert store_controller._connected == ['store-loaded', 'store-closed']
    assert controller._store_loaded_id == 'sig-store-loaded'
    assert controller._store_closed_id == 'sig-store-closed'


def test_connect_plugin_immediately_loads_an_already_open_store():
    controller = _bare_controller()
    calls = []
    controller._on_store_loaded = lambda sc: calls.append(sc)
    store_controller = _fake_store_controller(store=object())
    controller.main_controller = SimpleNamespace(
        store_controller=store_controller, mode_controller=None
    )
    controller.view = SimpleNamespace(_should_show_tmwindow=False)

    controller._connect_plugin()

    assert calls == [store_controller]
    assert controller.view._should_show_tmwindow is True


def test_connect_plugin_subscribes_to_mode_selected_when_a_mode_controller_exists():
    controller = _bare_controller()
    store_controller = _fake_store_controller()
    mode_controller = SimpleNamespace(connect=lambda signal, handler: 'sig-' + signal)
    controller.main_controller = SimpleNamespace(
        store_controller=store_controller, mode_controller=mode_controller
    )

    controller._connect_plugin()

    assert controller._mode_selected_id == 'sig-mode-selected'
    assert controller._context_selected_id == 'sig-context-selected'


def test_connect_plugin_skips_mode_selected_without_a_mode_controller():
    controller = _bare_controller()
    store_controller = _fake_store_controller()
    controller.main_controller = SimpleNamespace(
        store_controller=store_controller, mode_controller=None
    )

    controller._connect_plugin()

    assert not hasattr(controller, '_mode_selected_id')


# destroy() #


def test_destroy_tears_down_the_view_and_all_registered_signals():
    controller = _bare_controller()
    calls = []
    controller.view = SimpleNamespace(
        hide=lambda: calls.append('hide'), destroy=lambda: calls.append('destroy')
    )
    store_controller = SimpleNamespace(disconnect=lambda sid: calls.append(('store', sid)))
    store_controller.cursor = SimpleNamespace(disconnect=lambda sid: calls.append(('cursor', sid)))
    mode_controller = SimpleNamespace(disconnect=lambda sid: calls.append(('mode', sid)))
    unit_controller = SimpleNamespace(
        view=SimpleNamespace(disconnect=lambda sid: calls.append(('target', sid)))
    )
    controller.main_controller = SimpleNamespace(
        store_controller=store_controller,
        mode_controller=mode_controller,
        unit_controller=unit_controller,
        lang_controller=SimpleNamespace(disconnect=lambda sid: calls.append(('lang', sid))),
    )
    controller._store_loaded_id = 'store-id'
    controller._cursor_changed_id = 'cursor-id'
    controller._mode_selected_id = 'mode-id'
    controller._context_selected_id = 'context-id'
    controller._target_focused_id = 'target-id'
    controller._popup_toggled_id = 'popup-id'
    controller._lang_changed_ids = ['source-id', 'target-id']
    controller.plugin_controller = SimpleNamespace(shutdown=lambda: calls.append('shutdown'))

    controller.destroy()

    assert calls == [
        'hide',
        'destroy',
        ('store', 'store-id'),
        ('cursor', 'cursor-id'),
        ('mode', 'mode-id'),
        ('mode', 'context-id'),
        ('target', 'target-id'),
        ('lang', 'source-id'),
        ('lang', 'target-id'),
        ('target', 'popup-id'),
        'shutdown',
    ]


def test_destroy_skips_optional_signals_that_were_never_connected():
    controller = _bare_controller()
    calls = []
    controller.view = SimpleNamespace(
        hide=lambda: calls.append('hide'), destroy=lambda: calls.append('destroy')
    )
    controller.main_controller = SimpleNamespace(
        store_controller=SimpleNamespace(disconnect=lambda sid: calls.append(('store', sid))),
    )
    controller._store_loaded_id = 'store-id'
    controller.plugin_controller = SimpleNamespace(shutdown=lambda: calls.append('shutdown'))

    controller.destroy()

    assert calls == ['hide', 'destroy', ('store', 'store-id'), 'shutdown']


# select_match() #


def test_select_match_pushes_undo_and_sets_the_focused_target():
    controller = _bare_controller()
    calls = []
    view = SimpleNamespace(
        focused_target_n=1, get_target_n=lambda n: 'old text', targets=['textbox0', 'textbox1']
    )
    unit_controller = SimpleNamespace(
        view=view, set_unit_target=lambda n, text: calls.append(('set', n, text))
    )
    controller.main_controller = SimpleNamespace(
        unit_controller=unit_controller,
        undo_controller=SimpleNamespace(
            push_current_text=lambda textbox: calls.append(('push', textbox))
        ),
    )

    controller.select_match({'target': 'hallo'})

    assert calls == [('push', 'textbox1'), ('set', 1, 'hallo')]


# insert_match() #


class _FakeTarget:
    def __init__(self):
        self.calls = []

    def insert_text(self, text):
        self.calls.append(('insert', text))

    def grab_focus(self):
        self.calls.append('focus')


def _insert_controller(focused_target_n):
    controller = _bare_controller()
    targets = [_FakeTarget(), _FakeTarget()]
    view = SimpleNamespace(focused_target_n=focused_target_n, targets=targets)
    controller.main_controller = SimpleNamespace(unit_controller=SimpleNamespace(view=view))
    return controller, targets


def test_insert_match_inserts_into_the_focused_target_and_focuses_it():
    # #2014
    controller, targets = _insert_controller(1)

    controller.insert_match({'target': 'hallo'})

    assert targets[0].calls == []
    assert targets[1].calls == [('insert', 'hallo'), 'focus']


def test_insert_match_does_nothing_without_a_focused_target():
    controller, targets = _insert_controller(None)

    controller.insert_match({'target': 'hallo'})

    assert targets[0].calls == targets[1].calls == []


# send_tm_query() #


def test_send_tm_query_uses_the_given_unit_and_clears_previous_matches():
    controller = _bare_controller(matches=['stale'])
    calls = []
    controller.view = SimpleNamespace(clear=lambda: calls.append('clear'))
    emitted = []
    controller.emit = lambda signal, unit: emitted.append((signal, unit))
    unit = SimpleNamespace(source='hello')

    controller.send_tm_query(unit)

    assert controller.unit is unit
    assert controller.current_query == 'hello'
    assert controller.matches == []
    assert calls == ['clear']
    assert emitted == [('start-query', unit)]


def test_send_tm_query_reuses_the_existing_unit_when_none_given():
    controller = _bare_controller(unit=SimpleNamespace(source='bye'))
    controller.view = SimpleNamespace(clear=lambda: None)
    controller.emit = lambda *a: None

    controller.send_tm_query()

    assert controller.current_query == 'bye'


@pytest.mark.parametrize(
    'unit_view, expected',
    [
        (None, 0),
        (SimpleNamespace(focused_target_n=None), 0),
        (SimpleNamespace(focused_target_n=2), 2),
    ],
)
def test_send_tm_query_records_the_form_being_edited(unit_view, expected):
    # Models offer a plural unit's other forms for this one (#874).
    controller = _bare_controller(unit=SimpleNamespace(source='bye'))
    if unit_view is not None:
        controller.unit_view = unit_view
    controller.view = SimpleNamespace(clear=lambda: None)
    controller.emit = lambda *a: None

    controller.send_tm_query()

    assert controller.target_n == expected
    assert controller._queried_unit is controller.unit


# start_query() #


def test_start_query_does_nothing_without_a_store_cursor():
    controller = _bare_controller(storecursor=None)

    controller.start_query()  # must not raise


def _controller_for_start_query(**overrides):
    overrides.setdefault('_delay_id', None)
    controller = _bare_controller(
        storecursor=SimpleNamespace(deref=lambda: pytest.fail('must not re-derive a cached unit')),
        **overrides,
    )
    controller.view = SimpleNamespace(hide=lambda: None)
    return controller


def test_start_query_uses_the_cached_unit_when_already_set(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmcontroller.GLib.timeout_add',
        lambda delay, cb: scheduled.append((delay, cb)) or 'timeout-id',
    )
    controller = _controller_for_start_query(unit=SimpleNamespace(source='x'))
    controller.main_controller = SimpleNamespace(
        unit_controller=SimpleNamespace(
            view=SimpleNamespace(connect=lambda signal, handler: 'sig-' + signal)
        )
    )

    controller.start_query()

    assert controller._target_focused_id == 'sig-target-focused'
    assert controller._popup_toggled_id == 'sig-editor-popup-toggled'
    assert scheduled and scheduled[0][0] == TMController.QUERY_DELAY


def test_start_query_derives_the_unit_from_the_cursor_when_none_cached(monkeypatch):
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmcontroller.GLib.timeout_add', lambda delay, cb: 'timeout-id'
    )
    controller = _bare_controller(
        storecursor=SimpleNamespace(deref=lambda: 'derived-unit'), _delay_id=None
    )
    controller.main_controller = SimpleNamespace(
        unit_controller=SimpleNamespace(view=SimpleNamespace(connect=lambda *a: 'sig'))
    )
    controller.view = SimpleNamespace(hide=lambda: None)

    controller.start_query()

    assert controller.unit == 'derived-unit'


def test_start_query_disconnects_a_previous_target_focused_subscription(monkeypatch):
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmcontroller.GLib.timeout_add', lambda delay, cb: 'timeout-id'
    )
    disconnected = []
    unit_view = SimpleNamespace(
        connect=lambda signal, handler: 'new-sig', disconnect=disconnected.append
    )
    controller = _controller_for_start_query(unit='cached', _target_focused_id='old-sig')
    controller.main_controller = SimpleNamespace(unit_controller=SimpleNamespace(view=unit_view))

    controller.start_query()

    assert disconnected == ['old-sig']
    assert controller._target_focused_id == 'new-sig'


def test_start_query_cancels_a_pending_delayed_query_before_scheduling_a_new_one(monkeypatch):
    removed = []
    monkeypatch.setattr('virtaal.plugins.tm.tmcontroller.GLib.source_remove', removed.append)
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmcontroller.GLib.timeout_add', lambda delay, cb: 'new-timeout-id'
    )
    controller = _controller_for_start_query(unit='cached', _delay_id='old-timeout-id')
    controller.main_controller = SimpleNamespace(
        unit_controller=SimpleNamespace(view=SimpleNamespace(connect=lambda *a: 'sig'))
    )

    controller.start_query()

    assert removed == ['old-timeout-id']
    assert controller._delay_id == 'new-timeout-id'


def test_start_query_schedules_send_tm_query_after_the_delay(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmcontroller.GLib.timeout_add',
        lambda delay, cb: scheduled.append(cb) or 'id',
    )
    controller = _controller_for_start_query(unit='cached')
    controller.main_controller = SimpleNamespace(
        unit_controller=SimpleNamespace(view=SimpleNamespace(connect=lambda *a: 'sig'))
    )
    sent = []
    controller.send_tm_query = lambda: sent.append(True)

    controller.start_query()
    result = scheduled[0]()  # invoke the deferred start_query() closure

    assert sent == [True]
    assert controller._delay_id is None
    assert result is False


# _on_cursor_changed() #


def test_on_cursor_changed_clears_storecursor_and_returns_when_none():
    controller = _bare_controller()
    controller.start_query = lambda: pytest.fail('must not query without a cursor')

    result = controller._on_cursor_changed(None)

    assert controller.storecursor is None
    assert result is None


def test_on_cursor_changed_returns_when_the_dereffed_unit_is_none():
    cursor = SimpleNamespace(deref=lambda: None)
    controller = _bare_controller()
    controller.start_query = lambda: pytest.fail('must not query without a unit')

    controller._on_cursor_changed(cursor)

    assert controller.unit is None


def _suggestions_controller(unit, active=True, summoned=False, isvisible=False):
    events = []
    controller = _bare_controller(unit=unit, _delay_id=None, storecursor=object())
    controller.start_query = lambda: events.append('query')
    controller.view = SimpleNamespace(
        active=active,
        summoned=summoned,
        isvisible=isvisible,
        hide=lambda: events.append('hide'),
        mnu_suggestions=SimpleNamespace(
            set_active=lambda v: pytest.fail('must not change the toggle')
        ),
    )
    return controller, events


@pytest.mark.parametrize(
    'translated, active, expected',
    [
        (False, True, ['query']),
        (True, True, ['hide']),  # translated units only get suggestions on request
        (False, False, ['hide']),
        (True, False, ['hide']),
    ],
)
def test_on_cursor_changed_never_changes_the_toggle(translated, active, expected):
    unit = SimpleNamespace(istranslated=lambda: translated)
    controller, events = _suggestions_controller(unit, active=active, summoned=True)

    controller._on_cursor_changed(SimpleNamespace(deref=lambda: unit))

    assert events == expected
    assert controller.view.summoned is False


def test_a_locked_trados_segment_gets_no_suggestions_unasked():
    data = (
        b'<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2"'
        b' xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0" version="1.2">'
        b'<file original="a" datatype="x-sdlfilterframework2" source-language="en"'
        b' target-language="af"><body><trans-unit id="1"><source>Open</source>'
        b'<sdl:seg-defs><sdl:seg id="1" locked="true"/></sdl:seg-defs>'
        b'</trans-unit></body></file></xliff>'
    )
    unit = xliff.xlifffile.parsestring(data).units[0]
    controller, events = _suggestions_controller(unit)

    controller.update_suggestions()

    assert events == ['hide']


@pytest.mark.parametrize('active', [True, False])
def test_summon_queries_a_translated_unit_whether_or_not_suggestions_are_on(active):
    unit = SimpleNamespace(istranslated=lambda: True)
    controller, events = _suggestions_controller(None, active=active)
    controller.storecursor = SimpleNamespace(deref=lambda: unit)

    controller.summon()

    assert controller.unit is unit
    assert controller.view.summoned is True
    assert events == ['query']


def test_summon_with_suggestions_already_shown_does_not_requery():
    unit = SimpleNamespace(istranslated=lambda: False)
    controller, events = _suggestions_controller(None, isvisible=True)
    controller.storecursor = SimpleNamespace(deref=lambda: unit)

    controller.summon()

    assert events == []


def test_summon_without_a_unit_does_nothing():
    controller, events = _suggestions_controller(None)
    controller.storecursor = SimpleNamespace(deref=lambda: None)

    controller.summon()

    assert controller.view.summoned is False
    assert events == []


def test_summon_without_a_file_does_nothing():
    controller, events = _suggestions_controller(None)
    controller.storecursor = None

    controller.summon()

    assert events == []


# _on_mode_selected() / _on_target_focused() #


@pytest.mark.parametrize(
    'translated, active, summoned, queries',
    [
        (False, True, False, [True]),
        (True, True, False, []),  # translated units don't show suggestions by themselves
        (False, False, False, []),
        (True, False, True, [True]),  # asked for with F9
    ],
)
def test_a_context_change_queries_again_where_suggestions_apply(
    translated, active, summoned, queries
):
    unit = SimpleNamespace(istranslated=lambda: translated)
    calls = []
    controller = _bare_controller(storecursor=SimpleNamespace(deref=lambda: unit))
    controller.view = SimpleNamespace(active=active, summoned=summoned)
    controller.start_query = lambda: calls.append(True)

    controller._on_context_selected(None, 1)

    assert calls == queries


def test_a_context_change_without_a_file_does_nothing():
    controller = _bare_controller(storecursor=None)
    controller.start_query = lambda: pytest.fail('must not query')

    controller._on_context_selected(None, 1)


def test_connect_plugin_subscribes_to_language_changes():
    controller = _bare_controller()
    lang_controller = SimpleNamespace(connect=lambda signal, handler: 'sig-' + signal)
    controller.main_controller = SimpleNamespace(
        store_controller=_fake_store_controller(),
        mode_controller=None,
        lang_controller=lang_controller,
    )

    controller._connect_plugin()

    assert controller._lang_changed_ids == ['sig-source-lang-changed', 'sig-target-lang-changed']


@pytest.mark.parametrize(
    'summoned, queries, updates',
    [
        (False, [], [True]),
        (True, [True], []),  # asked for with F9
    ],
)
def test_a_language_change_clears_the_suggestions_and_queries_again(summoned, queries, updates):
    calls = []
    controller = _bare_controller(storecursor=object())
    controller.view = SimpleNamespace(summoned=summoned, clear=lambda: calls.append('clear'))
    controller.start_query = lambda: queries.remove(True) or calls.append('query')
    controller.update_suggestions = lambda: updates.remove(True) or calls.append('update')

    controller._on_lang_changed(None, 'de')

    assert calls[0] == 'clear' and len(calls) == 2
    assert queries == [] and updates == []


def test_a_language_change_without_a_file_only_clears():
    calls = []
    controller = _bare_controller(storecursor=None)
    controller.view = SimpleNamespace(clear=lambda: calls.append('clear'))
    controller.update_suggestions = lambda: pytest.fail('must not query')

    controller._on_lang_changed(None, 'de')

    assert calls == ['clear']


def test_on_mode_selected_updates_the_view_geometry():
    controller = _bare_controller()
    calls = []
    controller.view = SimpleNamespace(update_geometry=lambda: calls.append(True))

    controller._on_mode_selected(None, None)

    assert calls == [True]


def test_editor_popup_toggled_suspends_and_resumes_the_view():
    # The TM window would otherwise cover the completion list or a context menu.
    controller = _bare_controller()
    calls = []
    controller.view = SimpleNamespace(
        suspend=lambda: calls.append('suspend'), resume=lambda: calls.append('resume')
    )

    controller._on_editor_popup_toggled(None, True)
    controller._on_editor_popup_toggled(None, False)

    assert calls == ['suspend', 'resume']


def test_on_target_focused_updates_the_view_geometry():
    controller = _bare_controller()
    calls = []
    controller.view = SimpleNamespace(update_geometry=lambda: calls.append(True))

    controller._on_target_focused(None, 0)

    assert calls == [True]


def _focus_controller(plural=True, target_n=0, queried=True, **view):
    unit = SimpleNamespace(hasplural=lambda: plural, istranslated=lambda: True)
    controller, events = _suggestions_controller(unit, **view)
    controller.target_n = target_n
    if queried:
        controller._queried_unit = unit
    controller.storecursor = SimpleNamespace(deref=lambda: unit)
    controller.view.update_geometry = lambda: None
    return controller, events


@pytest.mark.parametrize(
    'kwargs',
    [
        # Partly typed: the unit says it's translated, but it was queried.
        {},
        {'active': False, 'summoned': True},
        {'queried': False, 'summoned': True},
    ],
)
def test_on_target_focused_requeries_for_another_plural_form(kwargs):
    # The other forms are suggestions for this one (#874).
    controller, events = _focus_controller(**kwargs)

    controller._on_target_focused(None, 1)

    assert events == ['query']


@pytest.mark.parametrize(
    'kwargs',
    [
        {'plural': False},
        {'target_n': 1},  # the form already queried
        {'active': False},  # suggestions off and not summoned
        {'queried': False},  # translated when selected: only on request
    ],
)
def test_on_target_focused_does_not_requery(kwargs):
    controller, events = _focus_controller(**kwargs)

    controller._on_target_focused(None, 1)

    assert events == []


def test_on_target_focused_does_not_requery_without_a_file():
    controller, events = _focus_controller()
    controller.storecursor = None

    controller._on_target_focused(None, 1)

    assert events == []


# _on_store_closed() #


def test_on_store_closed_disconnects_the_cursor_and_hides_the_view():
    controller = _bare_controller()
    disconnected = []
    controller.storecursor = SimpleNamespace(disconnect=disconnected.append)
    controller._cursor_changed_id = 'sig-id'
    calls = []
    controller.view = SimpleNamespace(hide=lambda: calls.append('hide'))

    controller._on_store_closed(None)

    assert disconnected == ['sig-id']
    assert controller.storecursor is None
    assert controller._cursor_changed_id == 0
    assert calls == ['hide']


def test_on_store_closed_is_safe_without_a_prior_cursor_subscription():
    controller = _bare_controller()
    controller.view = SimpleNamespace(hide=lambda: None)

    controller._on_store_closed(None)  # must not raise

    assert controller.storecursor is None


# _on_store_loaded() #


def test_on_store_loaded_connects_the_new_cursor_and_schedules_the_first_unit(monkeypatch):
    idle_calls = []
    monkeypatch.setattr('virtaal.plugins.tm.tmcontroller.GLib.idle_add', idle_calls.append)
    new_cursor = SimpleNamespace(connect=lambda signal, handler: 'new-sig')
    controller = _bare_controller()

    controller._on_store_loaded(SimpleNamespace(cursor=new_cursor))

    assert controller.storecursor is new_cursor
    assert controller._cursor_changed_id == 'new-sig'
    assert len(idle_calls) == 1


def test_on_store_loaded_disconnects_the_previous_cursor_first(monkeypatch):
    monkeypatch.setattr('virtaal.plugins.tm.tmcontroller.GLib.idle_add', lambda f: None)
    disconnected = []
    old_cursor = SimpleNamespace(disconnect=disconnected.append)
    new_cursor = SimpleNamespace(connect=lambda signal, handler: 'new-sig')
    controller = _bare_controller(storecursor=old_cursor, _cursor_changed_id='old-sig')

    controller._on_store_loaded(SimpleNamespace(cursor=new_cursor))

    assert disconnected == ['old-sig']


def test_on_store_loaded_handles_the_first_unit_via_the_idle_callback(monkeypatch):
    idle_calls = []
    monkeypatch.setattr('virtaal.plugins.tm.tmcontroller.GLib.idle_add', idle_calls.append)
    new_cursor = SimpleNamespace(connect=lambda signal, handler: 'new-sig')
    controller = _bare_controller()
    seen = []
    controller._on_cursor_changed = lambda cursor: seen.append(cursor)

    controller._on_store_loaded(SimpleNamespace(cursor=new_cursor))
    result = idle_calls[0]()

    assert seen == [new_cursor]
    assert result is False


def test_update_suggestions_before_a_file_is_open_does_nothing():
    # TMView's constructor ticks the toggle before TMController has a view.
    controller = _bare_controller(storecursor=None)

    controller.update_suggestions()  # must not raise


# Preloading the units ahead #


class _PrefetchModel(_FakePlugin):
    def __init__(self, depth, ceiling=2, in_flight=0, cached=()):
        super().__init__(PREFETCH_MAX=ceiling)
        self.depth = depth
        self.in_flight = in_flight
        self.cached = set(cached)
        self.prefetched = []

    def prefetch_depth(self):
        return self.depth

    def requests_in_flight(self):
        return self.in_flight

    def prefetch(self, unit):
        self.prefetched.append(unit.source)

    def has_cached(self, query_str):
        return query_str in self.cached


def _store_unit(source, translated=False):
    return SimpleNamespace(source=source, istranslated=lambda: translated)


def _prefetch_controller(*models, units=None, upcoming=(1, 2)):
    units = units or [_store_unit(s) for s in ('now', 'next', 'after', 'later')]
    asked = []

    def upcoming_units(n):
        asked.append(n)
        return list(upcoming)[:n]

    controller = _bare_controller(
        storecursor=SimpleNamespace(model=units, upcoming=upcoming_units),
        plugin_controller=SimpleNamespace(
            plugins={'m%d' % i: model for i, model in enumerate(models)}
        ),
    )
    return controller, asked


def test_a_query_preloads_each_backends_units_ahead():
    deep, shallow, off = _PrefetchModel(2), _PrefetchModel(1), _PrefetchModel(0)
    controller, asked = _prefetch_controller(deep, shallow, off)

    controller._prefetch_upcoming()

    assert asked == [2]  # as far as the deepest backend
    assert deep.prefetched == ['next', 'after']
    assert shallow.prefetched == ['next']
    assert off.prefetched == []


def test_preloading_skips_translated_units():
    model = _PrefetchModel(2)
    units = [_store_unit('now'), _store_unit('done', translated=True), _store_unit('after')]
    controller, _asked = _prefetch_controller(model, units=units)

    controller._prefetch_upcoming()

    assert model.prefetched == ['after']


def test_preloading_leaves_room_for_real_queries():
    busy = _PrefetchModel(2, in_flight=TMController.PREFETCH_BUSY + 1)
    idle = _PrefetchModel(2, in_flight=TMController.PREFETCH_BUSY)
    controller, _asked = _prefetch_controller(busy, idle)

    controller._prefetch_upcoming()

    assert busy.prefetched == []
    assert idle.prefetched == ['next', 'after']


def test_nothing_is_preloaded_without_a_backend_that_preloads():
    controller, asked = _prefetch_controller(_PrefetchModel(0))

    controller._prefetch_upcoming()

    assert asked == []


def test_nothing_is_preloaded_without_a_file():
    controller = _bare_controller(storecursor=None)

    controller._prefetch_upcoming()  # must not raise


def test_send_tm_query_preloads_after_querying():
    controller = _bare_controller(unit=SimpleNamespace(source='bye'))
    controller.view = SimpleNamespace(clear=lambda: None)
    calls = []
    controller.emit = lambda *a: calls.append('query')
    controller._prefetch_upcoming = lambda: calls.append('prefetch')

    controller.send_tm_query()

    assert calls == ['query', 'prefetch']


@pytest.mark.parametrize(
    'cached, expected',
    [
        ({'a': ['hello'], 'b': ['hello']}, True),
        ({'a': ['hello'], 'b': []}, False),
    ],
)
def test_answers_ready_when_every_network_backend_has_them(cached, expected):
    models = [_PrefetchModel(2, cached=cached['a']), _PrefetchModel(0, cached=cached['b'])]
    # Backends with nothing to preload answer at once.
    models.append(_PrefetchModel(0, ceiling=0))
    controller, _asked = _prefetch_controller(*models)

    assert controller._answers_ready(SimpleNamespace(source='hello')) is expected


def _fast_controller(monkeypatch, ready, rested):
    scheduled = []
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmcontroller.GLib.timeout_add',
        lambda delay, cb: scheduled.append(delay) or 'timeout-id',
    )
    monkeypatch.setattr(
        'virtaal.plugins.tm.tmcontroller.GLib.idle_add',
        lambda cb: scheduled.append('idle') or 'idle-id',
    )
    controller = _controller_for_start_query(unit=SimpleNamespace(source='x'), _rested=rested)
    controller.main_controller = SimpleNamespace(
        unit_controller=SimpleNamespace(view=SimpleNamespace(connect=lambda *a: 'sig'))
    )
    controller._answers_ready = lambda unit: ready
    return controller, scheduled


@pytest.mark.parametrize(
    'ready, rested, expected',
    [
        (True, True, 'idle'),
        # After unit-done, so the "This file" TM knows the unit just left.
        (False, True, TMController.QUERY_DELAY),
        (True, False, TMController.QUERY_DELAY),  # moving fast: keep quiet
    ],
)
def test_start_query_without_a_wait_when_the_answers_are_ready(
    monkeypatch, ready, rested, expected
):
    controller, scheduled = _fast_controller(monkeypatch, ready, rested)

    controller.start_query()

    assert scheduled == [expected]


@pytest.mark.parametrize(
    'gap, rested',
    [
        (TMController.QUERY_DELAY / 1000, True),
        (TMController.QUERY_DELAY / 1000 - 0.01, False),
    ],
)
def test_on_cursor_changed_tells_a_rest_from_moving_on(monkeypatch, gap, rested):
    now = [10.0]
    monkeypatch.setattr('virtaal.plugins.tm.tmcontroller.time.monotonic', lambda: now[0])
    unit = _store_unit('x')
    controller = _bare_controller()
    controller.view = SimpleNamespace(summoned=False)
    controller.update_suggestions = lambda: None
    cursor = SimpleNamespace(deref=lambda: unit)

    controller._on_cursor_changed(cursor)
    assert controller._rested  # the first unit
    now[0] += gap
    controller._on_cursor_changed(cursor)

    assert controller._rested is rested


# The existing translation suggestions are compared with (#1291) #


def _po_unit(text):
    from translate.storage import po

    return po.pofile.parsestring(text.encode('utf-8')).units[1]


@pytest.mark.parametrize(
    'po, target_n, expected',
    [
        ('msgid "Open"\nmsgstr "Oop"\n', 0, 'Oop'),
        ('msgid "Open"\nmsgstr ""\n', 0, ''),
        ('#, fuzzy\nmsgid "Open"\nmsgstr "Maak oop"\n', 0, 'Maak oop'),
        (
            'msgid "%d file"\nmsgid_plural "%d files"\nmsgstr[0] "%d lêer"\nmsgstr[1] "%d lêers"\n',
            1,
            '%d lêers',
        ),
        ('msgid "%d file"\nmsgid_plural "%d files"\nmsgstr[0] "%d lêer"\n', 1, ''),
    ],
)
def test_existing_target_is_the_edited_forms_translation_on_arrival(po, target_n, expected):
    unit = _po_unit('msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n' + po)
    controller, _events = _suggestions_controller(unit)

    controller._on_cursor_changed(SimpleNamespace(deref=lambda: unit))
    unit.target = 'Something typed since'
    controller.target_n = target_n

    assert controller.existing_target() == expected


def test_existing_target_before_any_unit_is_empty():
    assert _bare_controller().existing_target() == ''


# Suggestions for translated units (#1396) #


def _state_unit(translated=True, state=100):
    return SimpleNamespace(istranslated=lambda: translated, get_state_n=lambda: state)


@pytest.mark.parametrize(
    'translated, state, mode, expected',
    [
        (False, 0, 'never', ['query']),
        (True, 100, 'never', ['hide']),
        (False, 0, 'always', ['query']),
        (True, 100, 'always', ['query']),
        (True, 120, 'always', ['query']),
        (True, 100, 'attention', ['hide']),
        (True, 120, 'attention', ['hide']),
        (True, 30, 'attention', ['query']),  # needs work
        (True, 60, 'attention', ['query']),  # rejected
        (True, 80, 'attention', ['query']),  # needs review
        (True, 80, 'never', ['hide']),
    ],
)
def test_translated_units_get_suggestions_unasked_as_chosen(translated, state, mode, expected):
    unit = _state_unit(translated, state)
    controller, events = _suggestions_controller(unit)
    controller.translated_suggestions = mode
    controller.failing_units = lambda: frozenset()

    controller._on_cursor_changed(SimpleNamespace(deref=lambda: unit))

    assert events == expected


@pytest.mark.parametrize('index, expected', [(3, ['query']), (4, ['hide'])])
def test_a_translated_unit_failing_a_check_needs_attention(index, expected):
    unit = _state_unit()
    controller, events = _suggestions_controller(unit)
    controller.storecursor = SimpleNamespace(index=index)
    controller.translated_suggestions = 'attention'
    controller.failing_units = lambda: frozenset({1, 3})

    controller.update_suggestions()

    assert events == expected


def test_suggestions_for_translated_units_still_skip_a_locked_trados_segment():
    data = (
        b'<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2"'
        b' xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0" version="1.2">'
        b'<file original="a" datatype="x-sdlfilterframework2" source-language="en"'
        b' target-language="af"><body><trans-unit id="1"><source>Open</source>'
        b'<target>Oop</target>'
        b'<sdl:seg-defs><sdl:seg id="1" locked="true"/></sdl:seg-defs>'
        b'</trans-unit></body></file></xliff>'
    )
    unit = xliff.xlifffile.parsestring(data).units[0]
    controller, events = _suggestions_controller(unit)
    controller.translated_suggestions = 'always'

    controller.update_suggestions()

    assert events == ['hide']


@pytest.mark.parametrize(
    'translated, summoned, mode, expected',
    [
        (True, False, 'always', ['query']),
        (True, False, 'never', ['hide']),
        (True, False, 'attention', ['hide']),
        (True, True, 'never', []),  # asked for with F9
        (False, False, 'never', []),  # unchanged for a unit still to translate
    ],
)
def test_set_translated_suggestions_updates_the_current_units_suggestions(
    translated, summoned, mode, expected
):
    unit = _state_unit(translated)
    controller, events = _suggestions_controller(unit, summoned=summoned)
    controller.config = {}
    controller.failing_units = lambda: frozenset()

    controller.set_translated_suggestions(mode)

    assert controller.translated_suggestions == mode
    assert controller.config == {'translated_suggestions': mode}
    assert events == expected


@pytest.mark.parametrize(
    'config, expected',
    [
        ({}, 'never'),
        ({'show_translated': 'True'}, 'always'),
        ({'show_translated': 'False'}, 'never'),
        ({'translated_suggestions': 'attention'}, 'attention'),
        ({'translated_suggestions': ' Always '}, 'always'),
        ({'translated_suggestions': 'never', 'show_translated': 'True'}, 'never'),
        ({'translated_suggestions': 'sometimes', 'show_translated': 'True'}, 'always'),
    ],
)
def test_init_reads_translated_suggestions_or_the_older_show_translated(
    monkeypatch, config, expected
):
    monkeypatch.setattr(TMController, '_load_timing', lambda self: None)
    monkeypatch.setattr(TMController, '_load_models', lambda self: None)
    monkeypatch.setattr(TMController, '_connect_plugin', lambda self: None)
    monkeypatch.setattr('virtaal.plugins.tm.tmview.TMView', lambda *args: None)

    controller = TMController(SimpleNamespace(), dict(config))

    assert controller.translated_suggestions == expected
    # Saved in its new form only.
    assert 'show_translated' not in controller.config
    if 'show_translated' in config:
        assert controller.config['translated_suggestions'] == expected


@pytest.mark.parametrize(
    'mode, expected',
    [('always', ['done', 'review', 'after']), ('attention', ['review', 'after'])],
)
def test_preloading_includes_translated_units_as_chosen(mode, expected):
    model = _PrefetchModel(3)
    units = [
        _store_unit('now'),
        _store_unit('done', translated=True),
        SimpleNamespace(source='review', istranslated=lambda: True, get_state_n=lambda: 80),
        _store_unit('after'),
    ]
    units[1].get_state_n = lambda: 100
    controller, _asked = _prefetch_controller(model, units=units, upcoming=(1, 2, 3))
    controller.translated_suggestions = mode
    controller.failing_units = lambda: frozenset()

    controller._prefetch_upcoming()

    assert model.prefetched == expected


def test_preloading_a_translated_unit_failing_a_check_needing_attention():
    model = _PrefetchModel(2)
    units = [_store_unit('now'), _store_unit('done', translated=True), _store_unit('ok', True)]
    for unit in units:
        unit.get_state_n = lambda: 100
    controller, _asked = _prefetch_controller(model, units=units)
    controller.translated_suggestions = 'attention'
    controller.failing_units = lambda: frozenset({1})

    controller._prefetch_upcoming()

    assert model.prefetched == ['done']


def test_preloads_are_marked_as_asked_ahead_of_need_and_queries_are_not(monkeypatch, tmp_path):
    from virtaal.plugins.tm.models import test_deepl

    model = test_deepl._ready(monkeypatch, tmp_path)
    model.config['prefetch'] = 1
    model.requests_in_flight = lambda: 0
    controller, _asked = _prefetch_controller(model)

    controller._prefetch_upcoming()
    test_deepl._query(model, 'now')

    preload, query = model.client.added[1:]
    assert 'Sec-Purpose: prefetch' in preload.headers
    assert 'Sec-Purpose: prefetch;bulk' not in preload.headers
    assert not any(h.startswith('Sec-Purpose') for h in query.headers)
