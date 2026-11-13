#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Transifex as a second real platform beside Weblate: the plug-in is
experimental and off until enabled, both kinds share the server list
(each in its own file) and its dialog, and the browser opens a
Transifex resource's language from the fake server with no change to
the platform layer."""

import os

import pytest

from devsupport.testing.faketransifex import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.transifex import Plugin, backend, client
from virtaal.plugins.transifex.config import KeyFile
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.plugins.weblate.config import KeyFile as WeblateKeys
from virtaal.support.platforms import workcopy
from virtaal.support.platforms.browsedialog import COL_INFO, COL_NAME, BrowseDialog
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.serversdialog import ServerDialog, ServersDialog


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(client.Client, 'POLL_FIRST', 0.01)


@pytest.fixture
def fake():
    server = demo().start()
    yield server
    server.stop()
    assert server.schema_problems == []


@pytest.fixture
def platforms(tmp_path):
    return (
        WeblatePlatform(str(tmp_path / 'weblate.ini')),
        backend.TransifexPlatform(str(tmp_path / 'transifex.ini')),
    )


def test_the_plug_in_is_experimental_and_off_until_enabled(tmp_path):
    assert Plugin.display_name == 'Transifex (experimental)'
    assert Plugin.description == 'Translate projects hosted on Transifex'
    path = tmp_path / 'virtaal.ini'
    path.write_text('', encoding='utf-8')
    settings = pan_app.Settings(str(path))
    assert settings.plugin_state['transifex'] == 'disabled'


def test_each_platform_keeps_its_servers_in_its_own_file(tmp_path, platforms):
    weblate, transifex = platforms
    servers = ServerList(platforms, str(tmp_path / 'platforms.ini'))
    servers.put(transifex, '', {'token': 'tx'})
    servers.put(transifex, transifex.default_url, {'token': 'tx'})
    servers.put(weblate, 'https://weblate.example.org', {'token': 'wlu_x'})
    servers.save()

    assert KeyFile(transifex.keys.path).servers == [('https://rest.api.transifex.com/', 'tx')]
    assert WeblateKeys(weblate.keys.path).servers == [('https://weblate.example.org/api/', 'wlu_x')]
    assert [(s.platform.id, s.url) for s in ServerList(platforms, servers.path).servers] == [
        ('weblate', 'https://weblate.example.org/api/'),
        ('transifex', 'https://rest.api.transifex.com/'),
    ]


def test_add_offers_transifex_with_its_address_filled_in(tmp_path, platforms, monkeypatch):
    asked = []

    def answer(self):
        asked.append((self.platform.name, self.get_values()['url'], [f.key for f in self.fields]))
        return None

    monkeypatch.setattr(ServerDialog, 'run', answer)
    dialog = ServersDialog(ServerList(platforms, str(tmp_path / 'platforms.ini')))
    dialog.btn_add.clicked()
    items = dialog.add_menu.get_children()
    assert [item.get_label() for item in items] == ['Weblate', 'Transifex']

    items[1].activate()

    # No "Submit as suggestions": Transifex has no suggestions here.
    assert asked == [
        ('Transifex', 'https://rest.api.transifex.com/', ['url', 'token', 'send_finished'])
    ]


def _rows(model, parent=None):
    it = model.iter_children(parent)
    while it is not None:
        yield it
        it = model.iter_next(it)


def _expand(dialog, it):
    path = dialog.filtered.get_path(it)
    dialog.treeview.expand_row(path, False)
    run_until(lambda: not dialog.spinner.get_visible(), timeout=20)
    return dialog.filtered.get_iter(path)


def test_the_browser_opens_a_resources_language(fake, tmp_path, platforms):
    _weblate, transifex = platforms
    servers = ServerList([transifex], str(tmp_path / 'platforms.ini'))
    servers.put(transifex, fake.url, {'token': TOKEN})
    dialog = BrowseDialog(servers)
    run_until(lambda: not dialog.spinner.get_visible(), timeout=20)
    model = dialog.filtered

    assert dialog.dialog.get_title() == 'Transifex'
    assert [model[it][COL_NAME] for it in _rows(model)] == [
        'Hello Org / Program',
        'Tools / Toolbar',
    ]
    program = _expand(dialog, next(_rows(model)))
    # Each resource with its format.
    assert [(model[it][COL_NAME], model[it][COL_INFO]) for it in _rows(model, program)] == [
        ('Messages', 'PO'),
        ('Website', 'KEYVALUEJSON'),
    ]
    messages = _expand(dialog, next(_rows(model, program)))
    languages = list(_rows(model, messages))
    assert [(model[it][COL_NAME], model[it][COL_INFO]) for it in languages] == [
        ('Afrikaans', '71.4%'),
        ('German', '0.0%'),
    ]
    dialog.treeview.get_selection().select_iter(languages[0])
    assert dialog.selected() == ('hello-org/program', 'messages', 'af')

    dialog.open_selected()
    run_until(lambda: dialog.opened is not None, timeout=20)

    copy = workcopy.for_file(dialog.opened)
    assert copy.platform_name() == 'Transifex'
    assert copy.location() == 'Program/Messages'
    assert dialog.opened.endswith(os.path.join('messages', 'af.po'))


def test_the_browser_opens_a_docx_resource_as_any_other(fake, tmp_path, platforms):
    from devsupport.testing.faketransifex import add_formats

    add_formats(fake)
    _weblate, transifex = platforms
    servers = ServerList([transifex], str(tmp_path / 'platforms.ini'))
    servers.put(transifex, fake.url, {'token': TOKEN})
    dialog = BrowseDialog(servers)
    run_until(lambda: not dialog.spinner.get_visible(), timeout=20)
    model = dialog.filtered
    project = next(it for it in _rows(model) if model[it][COL_NAME] == 'Hello Org / Formats')
    project = _expand(dialog, project)
    guide = next(it for it in _rows(model, project) if model[it][COL_NAME] == 'User guide')
    assert model[guide][COL_INFO] == 'DOCX'
    guide = _expand(dialog, guide)
    afrikaans = next(_rows(model, guide))
    dialog.treeview.get_selection().select_iter(afrikaans)

    dialog.open_selected()
    run_until(lambda: dialog.opened is not None, timeout=20)

    assert dialog.opened.endswith(os.path.join('guide', 'af.xlf'))
    assert workcopy.for_file(dialog.opened).record['origin'] == workcopy.BUILT
