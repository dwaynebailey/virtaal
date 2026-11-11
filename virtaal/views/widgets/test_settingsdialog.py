#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest
from gi.repository import Gtk

from virtaal.views.widgets.settingsdialog import (
    BOOL,
    PORT,
    SECRET,
    Field,
    SettingsDialog,
    api_key_field,
    valid,
)

_SERVER = (Field('host', '_Host:', required=True), Field('port', '_Port:', PORT))


@pytest.mark.parametrize(
    'text, ok',
    [('1', True), ('65535', True), ('0', False), ('65536', False), ('', False), ('8o', False)]
    + [('-1', False), ('٣', False)],
)
def test_a_port_is_a_number_from_1_to_65535(text, ok):
    assert valid(Field('port', '', PORT), text) is ok


def test_an_empty_api_key_is_valid():
    # It turns the service off.
    assert valid(api_key_field(), '')


def test_ok_is_only_sensitive_while_every_field_is_valid():
    dialog = SettingsDialog(None, 'Server', _SERVER, {'host': 'localhost', 'port': '55555'})
    assert dialog.btn_ok.get_sensitive()

    dialog.entries['port'].set_text('99999')
    assert not dialog.btn_ok.get_sensitive()

    dialog.entries['port'].set_text('8080')
    dialog.entries['host'].set_text('  ')
    assert not dialog.btn_ok.get_sensitive()


def test_a_problem_is_shown_and_keeps_ok_insensitive():
    class Picky(SettingsDialog):
        def problem(self):
            return 'Not that one.' if self.get_values()['host'] == 'bad' else None

    dialog = Picky(None, 'Server', _SERVER, {'host': 'localhost', 'port': '55555'})
    assert not dialog.problem_label.get_visible()

    dialog.entries['host'].set_text('bad')
    assert not dialog.btn_ok.get_sensitive()
    assert dialog.problem_label.get_visible()
    assert dialog.problem_label.get_text() == 'Not that one.'


def test_values_are_stripped():
    dialog = SettingsDialog(None, 'Server', _SERVER, {'host': ' tm.example.org ', 'port': 8080})

    assert dialog.get_values() == {'host': 'tm.example.org', 'port': '8080'}


def test_a_secret_is_hidden_until_shown():
    dialog = SettingsDialog(None, 'Google', (api_key_field(),), {'api_key': 'k'})
    entry = dialog.entries['api_key']
    show = next(w for w in entry.get_parent().get_children() if isinstance(w, Gtk.CheckButton))
    assert api_key_field().kind == SECRET

    assert not entry.get_visibility()
    show.set_active(True)
    assert entry.get_visibility()


@pytest.mark.parametrize(
    'response, expected',
    [(Gtk.ResponseType.OK, {'api_key': 'new'}), (Gtk.ResponseType.CANCEL, None)],
)
def test_run_returns_the_values_only_on_ok(monkeypatch, response, expected):
    dialog = SettingsDialog(None, 'Google', (api_key_field(),), {})
    dialog.entries['api_key'].set_text('new')
    monkeypatch.setattr(dialog.dialog, 'present', lambda: None)
    monkeypatch.setattr(dialog.dialog, 'show', lambda: None)
    monkeypatch.setattr(dialog.dialog, 'run', lambda: response)

    assert dialog.run() == expected


def test_a_bool_is_a_check_box_and_always_valid():
    fields = (Field('url', '_URL:', required=True), Field('send', '_Send as you go', BOOL))
    dialog = SettingsDialog(None, 'Server', fields, {'url': 'x', 'send': True})

    assert dialog.get_values() == {'url': 'x', 'send': True}
    dialog.switches['send'].set_active(False)
    assert dialog.get_values() == {'url': 'x', 'send': False}
    assert dialog.btn_ok.get_sensitive()
    assert dialog.switches['send'].get_label() == '_Send as you go'
    dialog.dialog.destroy()
