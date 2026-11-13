#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The translation server dialogs and menu item with Weblate, against
the fake server."""

import socket
from types import SimpleNamespace

import pytest
from gi.repository import Gtk

from devsupport.testing.fakeweblate import TOKEN, FakeWeblate, demo, run_until
from virtaal.common import pan_app
from virtaal.plugins import weblate as plugin
from virtaal.plugins.weblate import backend
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.plugins.weblate.client import Client
from virtaal.plugins.weblate.config import KeyFile
from virtaal.support.platforms import registry
from virtaal.support.platforms.browsedialog import (
    COL_INFO,
    COL_KIND,
    COL_NAME,
    LANGUAGE,
    PENDING,
    BrowseDialog,
)
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.serversdialog import ServerDialog, ServersDialog


@pytest.fixture(autouse=True)
def _english_numbers(monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')


@pytest.fixture
def fake():
    server = demo().start()
    yield server
    server.stop()
    assert server.schema_problems == []


def _server_list(tmp_path):
    platform = WeblatePlatform(str(tmp_path / 'weblate.ini'))
    return ServerList([platform], str(tmp_path / 'platforms.ini'))


def _saved(servers):
    """The (URL, token) pairs weblate.ini holds."""
    return KeyFile(servers.platforms[0].keys.path).servers


@pytest.fixture
def weblate(tmp_path):
    return WeblatePlatform(str(tmp_path / 'weblate.ini'))


@pytest.fixture
def servers(tmp_path, fake):
    servers = _server_list(tmp_path)
    servers.put(servers.platforms[0], fake.url, {'token': TOKEN})
    return servers


@pytest.fixture
def no_wlc(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, 'wlc_config_path', lambda: None)


def _rows(model, parent=None):
    it = model.iter_children(parent)
    rows = []
    while it is not None:
        rows.append(it)
        it = model.iter_next(it)
    return rows


def _names(model, parent=None):
    return [model[it][COL_NAME] for it in _rows(model, parent)]


# The server's address and token #


@pytest.mark.parametrize(
    'url, token, ok',
    [
        ('https://weblate.example.org', 'wlu_x', True),
        ('https://weblate.example.org', '', False),
        ('http://weblate.example.org', 'wlu_x', False),
        ('', 'wlu_x', False),
    ],
)
def test_ok_needs_a_usable_address_and_a_token(weblate, url, token, ok):
    dialog = ServerDialog(None, weblate, url, {'token': token})

    assert dialog.btn_ok.get_sensitive() is ok


@pytest.mark.parametrize('url, label', [('', '_Add'), ('https://weblate.example.org', '_Save')])
def test_ok_adds_a_new_server_and_saves_a_known_one(weblate, url, label):
    dialog = ServerDialog(None, weblate, url, {'token': 'wlu_x'})

    assert dialog.btn_ok.get_label() == label


def test_the_token_has_its_own_show_check_box(weblate):
    dialog = ServerDialog(None, weblate, '', {'token': 'wlu_x'})
    labels = []

    def walk(widget):
        if isinstance(widget, Gtk.CheckButton):
            labels.append(widget.get_label())
        elif isinstance(widget, Gtk.Container):
            widget.foreach(walk)

    walk(dialog.dialog)

    assert 'S_how token' in labels
    assert '_Show key' not in labels


def test_a_plain_http_address_says_why_it_is_refused(weblate):
    dialog = ServerDialog(None, weblate, 'http://weblate.example.org', {'token': 'wlu_x'})

    assert not dialog.btn_ok.get_sensitive()
    assert dialog.problem_label.get_visible()
    assert dialog.problem_label.get_text() == 'Use https: the token would be sent unencrypted.'

    dialog.entries['url'].set_text('https://weblate.example.org')
    assert dialog.btn_ok.get_sensitive()
    assert not dialog.problem_label.get_visible()


@pytest.mark.parametrize('url', ['http://localhost:8080', 'http://127.0.0.1:8765', ''])
def test_plain_http_to_this_machine_needs_no_message(weblate, url):
    assert not ServerDialog(None, weblate, url, {'token': 'x'}).problem_label.get_visible()


def test_the_token_is_hidden(weblate, no_wlc):
    dialog = ServerDialog(None, weblate, 'https://w.example.org', {'token': 'wlu_x'})

    assert not dialog.entries['token'].get_visibility()
    assert dialog.entries['url'].get_visibility()


# The server list #


def _answer(monkeypatch, values):
    monkeypatch.setattr(ServerDialog, 'run', lambda self: values)


def _status(dialog):
    run_until(lambda: not dialog.spinner.get_visible())
    return dialog.status.get_text()


def test_the_list_shows_each_server(servers, no_wlc):
    dialog = ServersDialog(servers)

    assert [(row[0], row[1]) for row in dialog.store] == [
        ('Weblate', s.url) for s in servers.servers
    ]
    assert dialog.btn_test.get_sensitive()


def test_adding_a_server_saves_it_and_tests_it(tmp_path, fake, monkeypatch, no_wlc):
    servers = _server_list(tmp_path)
    dialog = ServersDialog(servers)
    assert not dialog.btn_test.get_sensitive()
    _answer(monkeypatch, {'url': fake.url, 'token': TOKEN})

    dialog.btn_add.clicked()

    assert _saved(servers) == [(fake.api_url, TOKEN)]
    assert _status(dialog) == 'Connected: 14 projects'


def test_one_project_is_singular(tmp_path, monkeypatch, no_wlc):
    with FakeWeblate() as one:
        one.add_project('only')
        servers = _server_list(tmp_path)
        servers.put(servers.platforms[0], one.url, {'token': TOKEN})
        dialog = ServersDialog(servers)
        dialog.btn_test.clicked()

        assert _status(dialog) == 'Connected: 1 project'


def test_the_test_says_how_many_requests_are_left_when_the_server_says(
    servers, fake, monkeypatch, no_wlc
):
    from virtaal.support.platforms import serversdialog

    monkeypatch.setattr(serversdialog, 'clock_time', lambda seconds: '08:40')
    fake.rate_limit = 5000
    servers.put(servers.platforms[0], fake.url, {'token': TOKEN})
    dialog = ServersDialog(servers)
    dialog.treeview.get_selection().select_path(
        Gtk.TreePath.new_from_indices([len(dialog.store) - 1])
    )

    dialog.btn_test.clicked()

    assert _status(dialog) == 'Connected: 14 projects\n4,999 of 5,000 requests left until 08:40.'


def test_editing_a_server_replaces_it(servers, fake, monkeypatch, no_wlc):
    dialog = ServersDialog(servers)
    _answer(monkeypatch, {'url': fake.url, 'token': 'wlu_new'})

    dialog.btn_edit.clicked()

    assert _saved(servers) == [(fake.api_url, 'wlu_new')]


def test_cancelling_the_edit_changes_nothing(servers, monkeypatch, no_wlc):
    dialog = ServersDialog(servers)
    _answer(monkeypatch, None)

    dialog.btn_edit.clicked()

    assert servers.servers[0].credentials == {'token': TOKEN}


def test_removing_a_server(servers, no_wlc):
    dialog = ServersDialog(servers)

    dialog.btn_remove.clicked()

    assert len(dialog.store) == 0
    assert _saved(servers) == []
    assert not dialog.btn_remove.get_sensitive()


def test_a_refused_token_says_so(servers, fake, no_wlc):
    servers.put(servers.platforms[0], fake.url, {'token': 'wlu_wrong'})
    dialog = ServersDialog(servers)

    dialog.btn_test.clicked()

    assert _status(dialog) == 'The server refused the token.'


def test_no_reply_says_why(tmp_path, no_wlc):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    servers = _server_list(tmp_path)
    servers.put(servers.platforms[0], 'http://127.0.0.1:%d/' % port, {'token': TOKEN})
    dialog = ServersDialog(servers)

    dialog.btn_test.clicked()

    assert _status(dialog).startswith("Couldn’t connect to the server: ")


def test_another_site_is_not_weblate(servers, fake, no_wlc):
    fake.not_weblate = True
    dialog = ServersDialog(servers)

    dialog.btn_test.clicked()

    assert _status(dialog) == 'No Weblate server answered at this address.'


def test_other_errors_give_the_status_and_the_servers_words(servers, fake, no_wlc):
    fake.fail('/api/projects/', 403, body=fake.error(403, 'permission_denied', 'Not allowed.'))
    dialog = ServersDialog(servers)

    dialog.btn_test.clicked()

    assert _status(dialog) == 'The server replied with error 403.\nNot allowed.'


def test_import_from_wlc_is_offered_only_when_wlc_has_a_file(servers, tmp_path, monkeypatch):
    monkeypatch.setattr(backend, 'wlc_config_path', lambda: None)
    weblate = servers.platforms[0]
    assert not ServersDialog(servers).import_buttons[weblate].get_sensitive()

    wlc = tmp_path / 'weblate'
    wlc.write_text('[keys]\nhttps://weblate.example.org/api/ = wlu_wlc\n', encoding='utf-8')
    monkeypatch.setattr(backend, 'wlc_config_path', lambda: str(wlc))
    monkeypatch.setattr('virtaal.plugins.weblate.config.wlc_config_path', lambda: str(wlc))
    dialog = ServersDialog(servers)
    button = dialog.import_buttons[weblate]
    assert button.get_sensitive()
    assert button.get_label() == 'Import from _Weblate Client'

    button.clicked()

    assert 'https://weblate.example.org/api/' in [row[1] for row in dialog.store]
    assert ('https://weblate.example.org/api/', 'wlu_wlc') in _saved(servers)


def test_the_privacy_notice_names_what_is_sent(servers, no_wlc):
    dialog = ServersDialog(servers)
    labels = [w for w in _all(dialog.dialog) if isinstance(w, Gtk.Label)]

    assert any('sends your token' in label.get_text() for label in labels)


def _all(widget):
    found = [widget]
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found += _all(child)
    return found


# Browsing #


def _browse(servers, monkeypatch, page_size=Client.PAGE_SIZE):
    monkeypatch.setattr(Client, 'PAGE_SIZE', page_size)
    dialog = BrowseDialog(servers)
    run_until(lambda: not dialog.spinner.get_visible())
    return dialog


def _expand(dialog, it):
    path = dialog.filtered.get_path(it)
    dialog.treeview.expand_row(path, False)
    run_until(lambda: not dialog.spinner.get_visible())
    return dialog.filtered.get_iter(path)


def _find(model, name, parent=None):
    return next(it for it in _rows(model, parent) if model[it][COL_NAME] == name)


def test_projects_load_across_pages(servers, fake, monkeypatch):
    dialog = _browse(servers, monkeypatch, page_size=6)

    names = _names(dialog.filtered)
    assert names[:2] == ['Hello', 'Documentation']
    assert len(names) == 14
    assert [r['query'].get('page') for r in fake.requests_to('/api/projects/')] == [None, '2', '3']


def _pages_asked(fake):
    return [r['query'].get('page') for r in fake.requests_to('/api/projects/')]


def test_projects_load_only_as_far_as_needed(servers, fake, monkeypatch):
    monkeypatch.setattr(BrowseDialog, 'ROWS_WANTED', 4)

    dialog = _browse(servers, monkeypatch, page_size=3)

    assert len(_names(dialog.filtered)) == 6
    assert _pages_asked(fake) == [None, '2']


def test_scrolling_near_the_end_loads_the_next_page(servers, fake, monkeypatch):
    monkeypatch.setattr(BrowseDialog, 'ROWS_WANTED', 1)
    scrolled = []
    monkeypatch.setattr(
        BrowseDialog, '_near_the_end', lambda self: bool(scrolled and scrolled.pop())
    )
    dialog = _browse(servers, monkeypatch, page_size=3)
    assert _pages_asked(fake) == [None]

    scrolled.append(True)
    dialog.vadjustment.emit('value-changed')
    run_until(lambda: not dialog.spinner.get_visible())

    assert _pages_asked(fake) == [None, '2']
    assert len(_names(dialog.filtered)) == 6


@pytest.mark.parametrize(
    'value, near', [(0, False), (60, False), (79, False), (80, True), (90, True)]
)
def test_near_the_end_is_within_a_screen_of_it(servers, monkeypatch, value, near):
    dialog = _browse(servers, monkeypatch)
    dialog.vadjustment = Gtk.Adjustment(
        value=value, lower=0, upper=100, step_increment=1, page_increment=10, page_size=10
    )

    assert dialog._near_the_end() is near


def _filter(dialog, text):
    dialog.filter_entry.set_text(text)
    run_until(lambda: dialog._filter_timer is None and not dialog.spinner.get_visible())


def test_the_filter_loads_more_projects_to_find_matches(servers, fake, monkeypatch):
    monkeypatch.setattr(BrowseDialog, 'ROWS_WANTED', 3)
    dialog = _browse(servers, monkeypatch, page_size=3)
    assert _pages_asked(fake) == [None]

    _filter(dialog, 'app 12')

    assert _names(dialog.filtered) == ['App 12']
    assert _pages_asked(fake) == [None, '2', '3', '4', '5']


def test_the_filter_waits_for_typing_to_stop(servers, monkeypatch):
    dialog = _browse(servers, monkeypatch)
    applied = []
    monkeypatch.setattr(dialog.filtered, 'refilter', lambda: applied.append(True))

    dialog.filter_entry.set_text('d')
    dialog.filter_entry.set_text('do')
    assert applied == []
    run_until(lambda: dialog._filter_timer is None)

    assert applied == [True]


def test_a_project_shows_its_components_with_glossaries_marked(servers, monkeypatch):
    dialog = _browse(servers, monkeypatch)
    model = dialog.filtered
    hello = _find(model, 'Hello')
    assert model[_rows(model, hello)[0]][COL_KIND] == PENDING

    hello = _expand(dialog, hello)

    assert _names(model, hello) == ['Program', 'Glossary', 'Website']
    glossary = _find(model, 'Glossary', hello)
    assert model[glossary][COL_INFO] == 'Glossary'
    assert model[_find(model, 'Program', hello)][COL_INFO] == ''


def test_a_component_shows_its_languages_with_the_translated_percent(servers, monkeypatch):
    dialog = _browse(servers, monkeypatch)
    model = dialog.filtered
    hello = _expand(dialog, _find(model, 'Hello'))
    program = _expand(dialog, _find(model, 'Program', hello))

    rows = [(model[it][COL_NAME], model[it][COL_INFO]) for it in _rows(model, program)]
    assert rows == [('Afrikaans', '98.0%'), ('German', '100.0%'), ('Zulu', '41.0%')]
    assert all(model[it][COL_KIND] == LANGUAGE for it in _rows(model, program))


def test_a_language_in_a_category_component_can_be_chosen(servers, monkeypatch):
    dialog = _browse(servers, monkeypatch)
    model = dialog.filtered
    docs = _expand(dialog, _find(model, 'Documentation'))
    manual = _expand(dialog, _find(model, 'User manual', docs))

    dialog.treeview.get_selection().select_iter(_rows(model, manual)[0])

    assert dialog.selected() == ('docs', 'guides/manual', 'pt_BR')


def test_only_a_language_is_a_choice(servers, monkeypatch):
    dialog = _browse(servers, monkeypatch)

    dialog.treeview.get_selection().select_iter(_find(dialog.filtered, 'Hello'))

    assert dialog.selected() is None


def test_rows_load_once(servers, fake, monkeypatch):
    dialog = _browse(servers, monkeypatch)
    hello = _expand(dialog, _find(dialog.filtered, 'Hello'))
    dialog.treeview.collapse_row(dialog.filtered.get_path(hello))

    _expand(dialog, hello)

    assert len(fake.requests_to('/api/projects/hello/components/')) == 1


def test_a_failed_load_is_asked_again_on_the_next_expand(servers, fake, monkeypatch):
    dialog = _browse(servers, monkeypatch)
    fake.fail('/api/projects/hello/components/', 500)
    hello = _expand(dialog, _find(dialog.filtered, 'Hello'))
    assert dialog.status.get_text() == 'The server replied with error 500.'
    dialog.treeview.collapse_row(dialog.filtered.get_path(hello))

    hello = _expand(dialog, hello)

    assert _names(dialog.filtered, hello) == ['Program', 'Glossary', 'Website']


def test_the_filter_matches_project_names_and_slugs(servers, monkeypatch):
    dialog = _browse(servers, monkeypatch)

    _filter(dialog, 'docum')
    assert _names(dialog.filtered) == ['Documentation']

    _filter(dialog, 'APP-1')
    assert _names(dialog.filtered) == ['App 10', 'App 11', 'App 12']


def test_an_error_is_shown(servers, fake, monkeypatch):
    fake.tokens.clear()

    dialog = _browse(servers, monkeypatch)

    assert dialog.status.get_text() == 'The server refused the token.'
    assert _names(dialog.filtered) == []


def test_the_last_server_used_is_remembered(servers, monkeypatch, tmp_path):
    with FakeWeblate() as other:
        other.add_project('elsewhere', 'Elsewhere')
        servers.put(servers.platforms[0], other.url, {'token': TOKEN})
        dialog = _browse(servers, monkeypatch)
        assert dialog.server_combo.get_visible()
        assert dialog.dialog.get_title() == 'Open from Weblate'
        assert _names(dialog.filtered)[0] == 'Hello'

        dialog.server_combo.set_active(1)
        run_until(lambda: not dialog.spinner.get_visible())

        assert _names(dialog.filtered) == ['Elsewhere']
        again = _browse(_server_list(tmp_path), monkeypatch)
        assert again.server_combo.get_active() == 1
        assert _names(again.filtered) == ['Elsewhere']
        # weblate.ini, wlc's shape, has it as the default server too.
        assert KeyFile(servers.platforms[0].keys.path).last_url == other.api_url


def test_one_server_needs_no_choice(servers, monkeypatch):
    assert not _browse(servers, monkeypatch).server_combo.get_visible()


# The plugin #


def _main_controller():
    menu = Gtk.Menu()
    items = {}
    for name in ('mnu_open', 'mnu_open_folder', 'mnu_save', 'mnu_saveas'):
        items[name] = Gtk.MenuItem(label=name)
        menu.append(items[name])
    objects = {'menu_file': menu, **items}
    view = SimpleNamespace(
        gui=SimpleNamespace(get_object=objects.get), main_window=None, sync_menubar=lambda: None
    )
    return SimpleNamespace(view=view), menu


def test_the_menu_items_follow_open_folder_and_save_as_and_go_with_the_plugin():
    main_controller, menu = _main_controller()

    weblate = plugin.Plugin('weblate', main_controller)
    labels = [item.get_label() for item in menu.get_children()]
    assert labels == [
        'mnu_open',
        'mnu_open_folder',
        'Open from Tra_nslation Server…',
        'mnu_save',
        'mnu_saveas',
        'Sub_mit to Server…',
        'Up_date from Server…',
    ]
    assert registry.platforms == [weblate.platform]

    weblate.destroy()
    assert len(menu.get_children()) == 4
    assert registry.platforms == []


def test_browsing_with_no_server_asks_for_one_first(monkeypatch, tmp_path):
    main_controller, _menu = _main_controller()
    weblate = plugin.Plugin('weblate', main_controller)
    weblate.platform.keys = KeyFile(str(tmp_path / 'weblate.ini'))
    monkeypatch.setattr(pan_app, 'get_config_dir', lambda: str(tmp_path))
    shown = []
    monkeypatch.setattr(
        registry, 'configure', lambda parent=None, servers=None: shown.append('servers')
    )
    monkeypatch.setattr(BrowseDialog, 'run', lambda self: shown.append('browse'))

    registry.browse()

    assert shown == ['servers']
    weblate.destroy()


def test_the_plugin_is_off_until_enabled():
    assert pan_app.Settings.plugin_state['weblate'] == 'disabled'
