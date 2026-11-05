#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""TM matches scored without their placeables and rebuilt with the
current unit's own, for every TM backend."""

from types import SimpleNamespace

import pytest
from gi.repository import GObject
from translate.storage import po

from virtaal.plugins.tm.models import amagama, currentfile, localtm
from virtaal.plugins.tm.tmcontroller import TMController


def _controller(query, accelmarkers=(), abstract_placeables=True):
    controller = TMController.__new__(TMController)
    controller.storecursor = True
    controller.current_query = query
    controller.matches = []
    controller.max_matches = 5
    controller.abstract_placeables = abstract_placeables
    checker = SimpleNamespace(config=SimpleNamespace(accelmarkers=list(accelmarkers)))
    controller.main_controller = SimpleNamespace(
        checks_controller=SimpleNamespace(get_checker=lambda: checker)
    )
    controller.displayed = []
    controller.view = SimpleNamespace(
        display_matches=lambda matches: controller.displayed.append(list(matches))
    )
    return controller


def _remote(model_class, controller):
    model = model_class.__new__(model_class)
    GObject.GObject.__init__(model)
    model.source_lang, model.target_lang, model.checker = 'en', 'af', None
    model._generation = 0
    model.cache = {}
    model.shortname = model_class.__module__.rsplit('.', 1)[-1]
    model.display_name = model.shortname
    model.connect('match-found', controller.accept_response)
    return model


def _accept(controller, matches, query=None):
    controller.accept_response(
        SimpleNamespace(display_name='TM'), query or controller.current_query, matches
    )
    return controller.matches


@pytest.mark.parametrize('model_class', [amagama.TMModel, localtm.TMModel])
def test_a_server_match_gets_the_unit_placeables(model_class):
    controller = _controller('Delete %(count)s files')
    model = _remote(model_class, controller)
    reply = [{'source': 'Delete {0} files', 'target': 'Verwyder {0} lêers', 'quality': 59}]

    model._handle_matches(SimpleNamespace(), 'Delete %(count)s files', reply)

    (match,) = controller.matches
    assert match['target'] == 'Verwyder %(count)s lêers'
    assert match['tm_target'] == 'Verwyder {0} lêers'
    assert (match['quality'], match['raw_quality']) == (99, 59)
    assert match['source'] == 'Delete {0} files'
    # The backend's cache keeps what the server said.
    assert model.cache['Delete %(count)s files'][0]['target'] == 'Verwyder {0} lêers'


def test_a_local_tm_match_keeps_the_servers_raw_quality():
    controller = _controller('Delete %(count)s files')
    model = _remote(localtm.TMModel, controller)
    reply = [
        {
            'source': 'Delete {0} files',
            'target': 'Verwyder {0} lêers',
            'quality': 99,
            'raw_quality': 59,
        }
    ]

    model._handle_matches(SimpleNamespace(), 'Delete %(count)s files', reply)

    (match,) = controller.matches
    assert match['target'] == 'Verwyder %(count)s lêers'
    assert (match['quality'], match['raw_quality']) == (99, 59)


def test_a_current_file_match_gets_the_unit_placeables():
    store = po.pofile()
    done = store.addsourceunit('Click <strong>here</strong>')
    done.target = 'Klik <strong>hier</strong>'
    current = store.addsourceunit('Click <b>here</b>')
    controller = _controller(current.source)
    model = currentfile.TMModel.__new__(currentfile.TMModel)
    GObject.GObject.__init__(model)
    model.controller = SimpleNamespace(min_quality=50, max_matches=5)
    model.cache = {}
    model.matcher = currentfile._UnitMatcher(
        store, max_length=1000, max_candidates=5, min_similarity=50
    )
    model.connect('match-found', controller.accept_response)

    model.query(controller, current)

    (match,) = controller.matches
    assert match['target'] == 'Klik <b>hier</b>'
    assert match['tm_target'] == 'Klik <strong>hier</strong>'
    assert match['raw_quality'] < 60
    assert match['quality'] == 98


def test_matches_are_sorted_on_the_new_quality():
    controller = _controller('Delete %(count)s files')

    matches = _accept(
        controller,
        [
            {'source': 'Delete the files', 'target': 'Verwyder die lêers', 'quality': 80},
            {'source': 'Delete {0} files', 'target': 'Verwyder {0} lêers', 'quality': 59},
        ],
    )

    assert [m['target'] for m in matches] == ['Verwyder %(count)s lêers', 'Verwyder die lêers']


def test_a_rebuilt_duplicate_is_shown_once():
    controller = _controller('Delete %(count)s files')

    matches = _accept(
        controller,
        [
            {
                'source': 'Delete %(count)s files',
                'target': 'Verwyder %(count)s lêers',
                'quality': 100,
            },
            {'source': 'Delete {0} files', 'target': 'Verwyder {0} lêers', 'quality': 59},
        ],
    )

    assert [(m['target'], m['quality']) for m in matches] == [('Verwyder %(count)s lêers', 100)]
    assert 'tm_target' not in matches[0]


def test_a_raw_score_above_the_adjusted_one_is_kept():
    controller = _controller('5 of 6 and 7')

    (match,) = _accept(
        controller, [{'source': '5 of 6 and 7 ok', 'target': '8 van 9 en 10 goed', 'quality': 90}]
    )

    assert match['quality'] == 90
    assert match['target'] == '8 van 9 en 10 goed'


def test_only_an_identical_source_scores_100():
    controller = _controller('Delete %s files')

    (match,) = _accept(
        controller, [{'source': 'Delete %d files', 'target': 'Verwyder %d lêers', 'quality': 100}]
    )

    assert match['quality'] == 100, 'max(raw, adjusted) keeps the backend score'
    assert match['target'] == 'Verwyder %s lêers'

    controller = _controller('Delete %s files')
    (match,) = _accept(
        controller, [{'source': 'Delete %d files', 'target': 'Verwyder %d lêers', 'quality': 70}]
    )
    assert match['quality'] == 99


def test_an_unresolved_placeable_costs_more_than_a_rebuilt_one():
    controller = _controller('Version 3.5 of 10')

    (match,) = _accept(
        controller,
        [{'source': 'Version 2.1 of 12', 'target': 'Weergawe 2,1 van 12', 'quality': 82}],
    )

    assert match['target'] == 'Weergawe 2,1 van 10'
    assert match['quality'] == 100 - 1 - 5


def test_the_accelerator_takes_the_style_marker():
    controller = _controller('&Open file', accelmarkers=['&'])

    (match,) = _accept(
        controller, [{'source': '_Open file', 'target': 'Maak lêer _oop', 'quality': 80}]
    )

    assert match['target'] == 'Maak lêer &oop'
    assert match['tm_target'] == 'Maak lêer _oop'
    assert match['quality'] == 99


def test_without_style_markers_the_accelerator_stays():
    controller = _controller('&Open file')

    (match,) = _accept(
        controller, [{'source': '_Open file', 'target': 'Maak lêer _oop', 'quality': 80}]
    )

    assert match['target'] == 'Maak lêer _oop'
    assert 'tm_target' not in match
    assert match['quality'] == 99


def test_the_switch_turns_it_off():
    controller = _controller('Delete %(count)s files', abstract_placeables=False)

    (match,) = _accept(
        controller, [{'source': 'Delete {0} files', 'target': 'Verwyder {0} lêers', 'quality': 59}]
    )

    assert match == {
        'source': 'Delete {0} files',
        'target': 'Verwyder {0} lêers',
        'quality': 59,
        'tmsource': 'TM',
        'query_str': 'Delete %(count)s files',
    }


@pytest.mark.parametrize(
    ('setting', 'expected'),
    [(None, True), ('True', True), ('False', False), ('false', False), ('0', False), ('yes', True)],
)
def test_the_switch_is_read_from_the_config(monkeypatch, setting, expected):
    monkeypatch.setattr(TMController, '_load_timing', lambda self: None)
    monkeypatch.setattr(TMController, '_load_models', lambda self: None)
    monkeypatch.setattr(TMController, '_connect_plugin', lambda self: None)
    monkeypatch.setattr('virtaal.plugins.tm.tmview.TMView', lambda *args: None)
    config = {} if setting is None else {'abstract_placeables': setting}

    assert TMController(SimpleNamespace(), config).abstract_placeables is expected


def test_machine_translation_is_left_alone():
    controller = _controller('Delete %(count)s files')
    mt = {'source': 'Delete %(count)s files', 'target': 'Verwyder %(telling)s lêers'}

    (match,) = _accept(controller, [mt])

    assert match is mt
    assert match['target'] == 'Verwyder %(telling)s lêers'


def test_a_match_with_nothing_to_adapt_is_kept_as_it_came():
    controller = _controller('Open the file')
    reply = {'source': 'Open the file', 'target': 'Maak die lêer oop', 'quality': 100}

    (match,) = _accept(controller, [reply])

    assert match is reply
