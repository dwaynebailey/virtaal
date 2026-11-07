#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from fnmatch import fnmatchcase

import pytest
from gi.repository import Gtk

from virtaal.common import pan_app
from virtaal.plugins.tm.amagamadialog import (
    ANY,
    COL_LICENCE,
    COL_NAME,
    COL_TITLE,
    COL_USE,
    COL_WEIGHT,
    COPYLEFT,
    CUSTOM,
    PERMISSIVE,
    AmagamaSourcesDialog,
    licence_presets,
    parse_percent,
    preset_of,
)
from virtaal.plugins.tm.models.amagama import parse_licence, parse_sources

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

    def __init__(self, sources=None, excluded=(), weights=None, licence=''):
        self.sources = sources
        self.excluded = frozenset(excluded)
        self.weights = dict(weights or {})
        self.licence = licence
        self.licences = []
        self.fetches = []
        self.choices = []
        self.saved = 0

    def set_licence(self, text):
        self.licences.append(text)

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


def _matches(identifier, globs):
    """Whether amaGama's licence= of globs keeps a source under the one
    SPDX identifier: a glob matches the whole identifier, any case."""
    return any(fnmatchcase(identifier.lower(), glob.lower()) for glob in globs)


# The licences amaGama's /sources/ listed (2026-11-07), and the choices
# that keep each.
FREE_AND_PERMISSIVE = (
    '0BSD Apache-2.0 BSD-2-Clause BSD-3-Clause BSL-1.0 CC-BY-3.0-US CC-BY-4.0'
    ' CC0-1.0 ISC MIT MIT-0 MIT-Modern-Variant Unlicense Zlib'
).split()
FREE_ONLY = (
    'AGPL-3.0-only AGPL-3.0-or-later CC-BY-SA-3.0 CC-BY-SA-4.0 EUPL-1.2'
    ' GPL-2.0-only GPL-2.0-or-later GPL-3.0-only GPL-3.0-or-later LGPL-2.0-only'
    ' LGPL-2.0-or-later LGPL-2.1-only LGPL-2.1-or-later LGPL-3.0-only'
    ' LGPL-3.0-or-later MPL-2.0 OSL-3.0 GFDL-1.1-or-later'
    # Not listed, but free.
    ' GFDL-1.3-or-later GFDL-1.3-no-invariants-only GFDL-1.2-no-invariants-or-later'
).split()
NEITHER = (
    'CC-BY-NC-4.0 CC-BY-ND-4.0 CC-BY-NC-SA-4.0 GFDL-1.1-invariants-only'
    ' GFDL-1.3-invariants-or-later GFDL-1.3-only'
).split()


@pytest.mark.parametrize('identifier', FREE_AND_PERMISSIVE + FREE_ONLY + NEITHER)
def test_the_licence_choices_keep_the_licences_they_name(identifier):
    globs = {key: preset for key, _label, preset in licence_presets()}

    assert _matches(identifier, globs['free']) is (identifier not in NEITHER)
    assert _matches(identifier, globs['permissive']) is (identifier in FREE_AND_PERMISSIVE)


@pytest.mark.parametrize(
    'licence, key',
    [
        ('', ANY),
        (','.join(PERMISSIVE), 'permissive'),
        (','.join(reversed(PERMISSIVE)).lower(), 'permissive'),
        (','.join(PERMISSIVE + COPYLEFT), 'free'),
        ('GPL-*,MIT', CUSTOM),
        (','.join(PERMISSIVE[1:]), CUSTOM),
    ],
)
def test_a_saved_licence_shows_as_its_choice(dialogs, licence, key):
    dialog = _dialog(dialogs, _FakeModel(SOURCES, licence=licence))

    assert preset_of(licence) == key
    assert dialog.licence_combo.get_active_id() == key
    assert dialog.licence.get_visible() is (key == CUSTOM)
    assert dialog.licence_note.get_visible() is bool(licence)
    assert preset_of(parse_licence(dialog.licence_text())) == key
    if key == CUSTOM:
        assert parse_licence(dialog.licence_text()) == licence


def test_a_custom_licence_shows_in_its_field(dialogs):
    dialog = _dialog(dialogs, _FakeModel(SOURCES, licence='GPL-*,MIT'))

    assert dialog.licence.get_text() == 'GPL-*, MIT'
    assert dialog.licence.get_placeholder_text() == 'Any license'


def test_choosing_a_preset_applies_it_at_once(dialogs):
    model = _FakeModel(SOURCES)
    dialog = _dialog(dialogs, model)
    assert not dialog.licence_note.get_visible()

    dialog.licence_combo.set_active_id('permissive')

    assert model.licences == [','.join(PERMISSIVE)]
    assert dialog.licence_note.get_visible()
    assert 'MIT, MIT-*' in dialog.licence_combo.get_tooltip_text()

    dialog.licence_combo.set_active_id(ANY)

    assert model.licences[-1] == ''
    assert not dialog.licence_note.get_visible()
    assert dialog.licence_combo.get_tooltip_text() is None


def test_custom_starts_from_the_last_preset_and_applies_on_enter_and_close(dialogs):
    model = _FakeModel(SOURCES, licence=','.join(PERMISSIVE))
    dialog = _dialog(dialogs, model)

    dialog.licence_combo.set_active_id(CUSTOM)

    assert dialog.licence.get_visible()
    assert dialog.licence.get_text() == ', '.join(PERMISSIVE)
    # Ready to add to, not to type over.
    assert dialog.licence.get_selection_bounds() == ()
    assert model.licences == [', '.join(PERMISSIVE)]

    dialog.licence.set_text('GPL-*')
    assert len(model.licences) == 1
    dialog.licence.activate()
    dialog.licence.set_text(' , ')
    assert not dialog.licence_note.get_visible()
    dialog.licence.set_text('GPL-*, MIT')
    assert dialog.licence_note.get_visible()
    dialog.dialog.response(Gtk.ResponseType.CLOSE)

    assert model.licences[1:] == ['GPL-*', 'GPL-*, MIT']
    assert model.saved == 1


def test_a_preset_applies_on_close_whatever_the_hidden_field_holds(dialogs):
    model = _FakeModel(SOURCES, licence='GPL-*')
    dialog = _dialog(dialogs, model)
    dialog.licence_combo.set_active_id('free')
    dialog.licence.set_text('MIT')

    dialog.dialog.response(Gtk.ResponseType.CLOSE)

    assert model.licences == [','.join(PERMISSIVE + COPYLEFT)] * 2


@pytest.mark.parametrize(
    'text, expected', [('100', 1.0), ('85 %', 0.85), ('%120', 1.2), ('', None), ('-', None)]
)
def test_parse_percent(text, expected):
    assert parse_percent(text) == expected
