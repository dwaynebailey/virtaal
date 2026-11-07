#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from gi.repository import Gtk

from virtaal.common import pan_app
from virtaal.plugins.tm.amagamadialog import (
    COL_LICENCE,
    COL_NAME,
    COL_TITLE,
    COL_USE,
    COL_WEIGHT,
    AmagamaSourcesDialog,
    parse_percent,
)
from virtaal.plugins.tm.models.amagama import parse_sources

SOURCES = parse_sources(
    {
        'sources': [
            {
                'name': 'gnome-49',
                'displayTitle': 'GNOME 49',
                'licence': 'GPL-2.0-or-later',
                'weight': 1.0,
                'sourceLanguage': 'en',
                'targetLanguages': ['af'],
            },
            {
                'name': 'hosted-alpha',
                'displayTitle': 'Alpha',
                'licence': 'MIT',
                'weight': 0.8,
                'sourceLanguage': 'en',
                'targetLanguages': ['af', 'de'],
            },
            {
                'name': 'kde-de',
                'displayTitle': 'KDE',
                'weight': 1.0,
                'sourceLanguage': 'en',
                'targetLanguages': ['de'],
            },
        ]
    }
)


class _FakeModel:
    """The amaGama model as the dialog uses it, with the server's answer
    given by the test."""

    source_lang = 'en'
    target_lang = 'af'

    def __init__(self, sources=None, excluded=(), weights=None):
        self.sources = sources
        self.excluded = frozenset(excluded)
        self.weights = dict(weights or {})
        self.fetches = []
        self.choices = []
        self.saved = 0

    def fetch_sources(self, on_done, on_error):
        self.fetches.append((on_done, on_error))

    def set_choices(self, excluded, weights):
        self.excluded, self.weights = frozenset(excluded), dict(weights)
        self.choices.append((set(excluded), dict(weights)))

    def save_config(self):
        self.saved += 1


@pytest.fixture
def dialogs():
    made = []
    yield made
    for dialog in made:
        if not dialog._closed:
            dialog.dialog.destroy()


def _dialog(dialogs, model):
    dialog = AmagamaSourcesDialog(model, None)
    dialogs.append(dialog)
    dialog.show()
    return dialog


def _rows(dialog):
    return [
        (row[COL_TITLE], row[COL_LICENCE], row[COL_USE], row[COL_WEIGHT])
        for row in dialog.liststore
    ]


def _path(dialog, name):
    return next(str(row.path) for row in dialog.liststore if row[COL_NAME] == name)


def test_the_known_sources_for_the_languages_show_at_once_and_are_fetched_again(dialogs):
    model = _FakeModel(SOURCES, excluded={'gnome-49'}, weights={'hosted-alpha': 2.0})

    dialog = _dialog(dialogs, model)

    assert _rows(dialog) == [
        ('Alpha', 'MIT', True, 2.0),
        ('GNOME 49', 'GPL-2.0-or-later', False, 1.0),
    ]
    assert not dialog.status.get_visible()
    assert len(model.fetches) == 1


def test_the_list_follows_the_fetched_sources(dialogs):
    model = _FakeModel()
    dialog = _dialog(dialogs, model)
    assert dialog.status.get_visible()
    assert len(dialog.liststore) == 0

    model.fetches[0][0](SOURCES)

    assert [row[0] for row in _rows(dialog)] == ['Alpha', 'GNOME 49']
    assert not dialog.status.get_visible()
    assert dialog.scrolled.get_visible()


def test_projects_are_in_alphabetical_order_whatever_their_case(dialogs):
    sources = [dict(SOURCES[0], name=t, title=t) for t in ('blivet', 'Avogadro', 'Catima')]

    dialog = _dialog(dialogs, _FakeModel(sources))

    assert [row[0] for row in _rows(dialog)] == ['Avogadro', 'blivet', 'Catima']


def test_without_sources_or_a_server_the_dialog_says_so(dialogs):
    model = _FakeModel()
    dialog = _dialog(dialogs, model)

    model.fetches[0][1]()

    assert dialog.status.get_visible()
    assert dialog.status.get_text() == 'Could not get the list of projects from Amagama.'
    assert not dialog.scrolled.get_visible()


def test_offline_the_known_sources_stay(dialogs):
    model = _FakeModel(SOURCES)
    dialog = _dialog(dialogs, model)

    model.fetches[0][1]()

    assert len(dialog.liststore) == 2
    assert not dialog.status.get_visible()


def test_unticking_a_source_leaves_it_out_and_keeps_choices_for_other_languages(dialogs):
    model = _FakeModel(SOURCES, excluded={'kde-de'}, weights={'kde-de': 3.0})
    dialog = _dialog(dialogs, model)

    dialog._on_toggled(None, _path(dialog, 'gnome-49'))

    assert model.choices == [({'gnome-49', 'kde-de'}, {'kde-de': 3.0})]

    dialog._on_toggled(None, _path(dialog, 'gnome-49'))

    assert model.choices[-1] == ({'kde-de'}, {'kde-de': 3.0})


@pytest.mark.parametrize(
    'text, weights',
    [
        ('150', {'hosted-alpha': 1.5}),
        ('80', {}),  # the server's own weight
        ('٢٠٠', {'hosted-alpha': 2.0}),
        ('5000', {'hosted-alpha': 10.0}),
    ],
)
def test_a_weight_typed_in_applies_unless_it_is_the_servers(dialogs, text, weights):
    model = _FakeModel(SOURCES)
    dialog = _dialog(dialogs, model)

    dialog._on_weight_edited(None, _path(dialog, 'hosted-alpha'), text)

    assert model.choices == [(set(), weights)]


def test_a_weight_that_is_no_number_is_ignored(dialogs):
    model = _FakeModel(SOURCES)
    dialog = _dialog(dialogs, model)

    dialog._on_weight_edited(None, _path(dialog, 'hosted-alpha'), 'much')

    assert model.choices == []


def test_editing_a_weight_starts_from_its_percentage(dialogs):
    dialog = _dialog(dialogs, _FakeModel(SOURCES))
    spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(lower=0, upper=1000, step_increment=10))

    dialog._on_weight_editing_started(None, spin, _path(dialog, 'hosted-alpha'))

    assert spin.get_value() == 80


def test_weights_show_as_percentages_in_the_ui_language(dialogs, monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'de')
    dialog = _dialog(dialogs, _FakeModel(SOURCES))
    renderer = Gtk.CellRendererText()
    itr = dialog.liststore.get_iter(_path(dialog, 'hosted-alpha'))

    dialog._weight_data_func(None, renderer, dialog.liststore, itr, None)

    assert renderer.get_property('text') == '80\xa0%'


def test_closing_saves_the_choices_and_ignores_a_late_answer(dialogs):
    model = _FakeModel()
    dialog = _dialog(dialogs, model)

    dialog.dialog.response(Gtk.ResponseType.CLOSE)
    model.fetches[0][0](SOURCES)
    model.fetches[0][1]()

    assert model.saved == 1
    assert len(dialog.liststore) == 0


@pytest.mark.parametrize(
    'text, expected', [('100', 1.0), ('85 %', 0.85), ('%120', 1.2), ('', None), ('-', None)]
)
def test_parse_percent(text, expected):
    assert parse_percent(text) == expected
