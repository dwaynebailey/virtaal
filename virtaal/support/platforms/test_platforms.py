#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The translation platform layer with a second kind of platform, a
dummy one here, beside Weblate: it plugs in with no change to the
server list, its dialog, the browser or Submit."""

import configparser
from types import SimpleNamespace

import pytest
from gi.repository import GLib, Gtk

from devsupport.testing.fakeplatform import run_until
from virtaal.common import pan_app
from virtaal.plugins.weblate.backend import WeblatePlatform
from virtaal.plugins.weblate.config import KeyFile
from virtaal.support.platforms import (
    AUTH,
    Download,
    Item,
    Platform,
    PlatformError,
    Registry,
    RemoteUnit,
    Session,
    workcopy,
)
from virtaal.support.platforms.browsedialog import COL_INFO, COL_KIND, COL_NAME, BrowseDialog
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.serversdialog import ServerDialog, ServersDialog, error_message
from virtaal.views.widgets.settingsdialog import SECRET, Field

PROJECTS = {
    'alpha': ('Alpha', {'af': ('Afrikaans', 0.5), 'zu': ('Zulu', 1.0)}),
    'beta': ('Beta', {'de': ('German', 0.25)}),
    'gamma': ('Gamma', {}),
}


class DummySession(Session):
    """Answers from PROJECTS on the next main loop iteration, two
    projects a page, filtering them as a server would."""

    def __init__(self, platform, credentials):
        self.platform = platform
        self.credentials = credentials
        self.cancelled = False

    def _later(self, callback, *args):
        def call():
            if not self.cancelled:
                callback(*args)
            return False

        GLib.idle_add(call)

    def test_connection(self, on_done, on_error):
        if self.credentials.get('password') != 'secret':
            self._later(on_error, PlatformError(AUTH, 401))
        else:
            self._later(on_done, len(PROJECTS))

    def projects(self, query, cursor, on_page, on_error):
        self.platform.queries.append(query)
        found = [Item(name, slug) for slug, (name, _l) in PROJECTS.items() if query in slug]
        start = cursor or 0
        following = start + 2 if start + 2 < len(found) else None
        self._later(on_page, found[start : start + 2], following)

    def components(self, project, on_done, on_error):
        raise AssertionError('The dummy platform has no components')

    def languages(self, project, component, on_done, on_error):
        assert component is None
        languages = PROJECTS[project][1]
        self._later(on_done, [Item(n, code, done) for code, (n, done) in languages.items()])

    def download(self, project, component, language, on_done, on_error):
        assert component is None
        data = b'msgid "Alpha"\nmsgstr "Alfa"\n\nmsgid "Beta"\nmsgstr ""\n'
        units = list(self.platform.held.values())
        names = {'project': PROJECTS[project][0], 'component': None}
        self._later(on_done, Download('%s.po' % project, data, None, units, names))

    def locked(self, project, component, on_done, on_error):
        assert component is None
        self._later(on_done, False)

    def unit_count(self, project, component, language, on_done, on_error):
        self._later(on_done, len(self.platform.held))

    def changed_since(self, project, component, language, since, on_done, on_error):
        held = self.platform.held
        self._later(on_done, [held[i] for i in sorted(self.platform.changed)])

    def submit_unit(self, unit_id, target, code, on_done, on_error):
        unit = self.platform.held[unit_id]
        self.platform.held[unit_id] = unit._replace(target=list(target), code=code)
        self.platform.changed.add(unit_id)
        self._later(on_done, code)

    def cancel(self):
        self.cancelled = True


class DummyPlatform(Platform):
    """Servers in memory, each with a user name and a password."""

    id = 'dummy'
    name = 'Dummy'
    has_components = False
    supports_search = True

    can_submit = True

    def __init__(self):
        self.saved = ([], '')
        self.queries = []
        self.held = {
            'a': RemoteUnit('a', 1, '', ['Alpha'], ['Alfa'], 120, 'final', None, False),
            'b': RemoteUnit('b', 2, '', ['Beta'], [''], 0, 'new', None, False),
        }
        """The server's units."""
        self.changed = set()

    def state_code(self, state, target):
        if not any(target):
            return 'new'
        return 'final' if state >= 120 else 'draft'

    def credential_fields(self):
        return (Field('user', '_User:', required=True), Field('password', '_Password:', SECRET))

    def normalise_url(self, text):
        text = (text or '').strip()
        return text if text.startswith('dummy://') else None

    def not_platform_message(self):
        return 'No dummy here.'

    def load_servers(self):
        return list(self.saved[0]), self.saved[1]

    def save_servers(self, servers, last_url):
        self.saved = (list(servers), last_url)

    def session(self, url, credentials):
        return DummySession(self, credentials)


@pytest.fixture(autouse=True)
def _english_numbers(monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')


@pytest.fixture
def dummy():
    return DummyPlatform()


@pytest.fixture
def weblate(tmp_path):
    return WeblatePlatform(str(tmp_path / 'weblate.ini'))


@pytest.fixture
def servers(tmp_path, weblate, dummy):
    servers = ServerList([weblate, dummy], str(tmp_path / 'platforms.ini'))
    servers.put(dummy, 'dummy://one', {'user': 'me', 'password': 'secret'})
    servers.put(weblate, 'https://weblate.example.org', {'token': 'wlu_x'})
    servers.save()
    return servers


def _rows(model, parent=None):
    it = model.iter_children(parent)
    while it is not None:
        yield it
        it = model.iter_next(it)


def _names(model, parent=None):
    return [model[it][COL_NAME] for it in _rows(model, parent)]


# The server list #


def test_each_platform_saves_its_own_servers(servers, weblate, dummy):
    assert [(s.platform.id, s.url) for s in servers.servers] == [
        ('weblate', 'https://weblate.example.org/api/'),
        ('dummy', 'dummy://one'),
    ]
    assert KeyFile(weblate.keys.path).servers == [('https://weblate.example.org/api/', 'wlu_x')]
    assert dummy.saved == ([('dummy://one', {'user': 'me', 'password': 'secret'})], '')


def test_the_last_server_used_survives_a_round_trip(servers, tmp_path, weblate, dummy):
    servers.last = servers.servers[1]
    servers.save()

    again = ServerList([weblate, dummy], servers.path)

    assert (again.last.platform, again.last.url) == (dummy, 'dummy://one')
    parser = configparser.RawConfigParser()
    parser.read(servers.path, encoding='utf-8')
    assert dict(parser.items('last')) == {'platform': 'dummy', 'url': 'dummy://one'}
    assert dummy.saved[1] == 'dummy://one'


def test_sending_finished_units_is_kept_per_server_in_platforms_ini(
    servers, tmp_path, weblate, dummy
):
    servers.servers[0].send_finished = True
    servers.save()

    again = ServerList([weblate, dummy], servers.path)

    assert [s.send_finished for s in again.servers] == [True, False]
    with open(servers.path, encoding='utf-8') as f:
        assert '[send_finished]\nweblate https://weblate.example.org/api/ = true\n' in f.read()
    # Changing the server keeps it unless told; it holds no secret.
    changed = again.put(
        weblate, 'https://weblate.example.org', {'token': 'wlu_y'}, again.servers[0]
    )
    assert changed.send_finished
    assert not again.put(weblate, 'https://other.example.org', {'token': 'wlu_z'}).send_finished


def test_editing_a_server_sets_its_option(servers, weblate, monkeypatch):
    dialog = ServersDialog(servers)
    seen = []

    def answer(self):
        seen.append(self.switches['send_finished'].get_active())
        return {'url': self.get_values()['url'], 'token': 'wlu_x', 'send_finished': True}

    monkeypatch.setattr(ServerDialog, 'run', answer)

    dialog.btn_edit.clicked()

    assert seen == [False]
    assert servers.find(weblate, 'https://weblate.example.org/api/').send_finished
    assert ServerList(servers.platforms, servers.path).servers[0].send_finished


def test_without_platforms_ini_a_platforms_own_last_server_is_used(tmp_path, weblate, dummy):
    dummy.saved = ([('dummy://one', {}), ('dummy://two', {})], 'dummy://two')

    servers = ServerList([weblate, dummy], str(tmp_path / 'platforms.ini'))

    assert servers.last.url == 'dummy://two'


def test_the_servers_of_a_platform_turned_off_stay_in_its_own_file(servers, weblate, dummy):
    before = dummy.saved
    only_weblate = ServerList([weblate], servers.path)
    only_weblate.save()

    assert [s.url for s in only_weblate.servers] == ['https://weblate.example.org/api/']
    assert dummy.saved == before


def test_a_new_server_goes_after_its_platforms_others(servers, weblate):
    servers.put(weblate, 'https://other.example.org', {'token': 'wlu_y'})

    assert [s.url for s in servers.servers] == [
        'https://weblate.example.org/api/',
        'https://other.example.org/api/',
        'dummy://one',
    ]


def test_an_address_the_platform_refuses_is_not_added(servers, dummy):
    assert servers.put(dummy, 'https://not-dummy', {}) is None
    assert len(servers.servers) == 2


# The dialogs #


def test_the_list_names_each_servers_type(servers):
    dialog = ServersDialog(servers)

    assert [(row[0], row[1]) for row in dialog.store] == [
        ('Weblate', 'https://weblate.example.org/api/'),
        ('Dummy', 'dummy://one'),
    ]
    assert [c.get_title() for c in dialog.treeview.get_columns()] == ['Type', 'URL']


def test_add_asks_which_platform_then_for_its_fields(servers, dummy, monkeypatch):
    asked = []

    def answer(self):
        asked.append((self.platform, [f.key for f in self.fields]))
        return {'url': 'dummy://two', 'user': 'you', 'password': 'secret'}

    monkeypatch.setattr(ServerDialog, 'run', answer)
    dialog = ServersDialog(servers)
    dialog.btn_add.clicked()
    items = dialog.add_menu.get_children()
    assert [item.get_label() for item in items] == ['Weblate', 'Dummy']

    items[1].activate()

    # A platform that submits has the option to send finished units as you go.
    assert asked == [(dummy, ['url', 'user', 'password', 'send_finished'])]
    assert [url for url, _creds in dummy.saved[0]] == ['dummy://one', 'dummy://two']
    run_until(lambda: not dialog.spinner.get_visible())
    assert dialog.status.get_text() == 'Connected: 3 projects'


def test_with_one_platform_add_asks_no_question(tmp_path, dummy, monkeypatch):
    servers = ServerList([dummy], str(tmp_path / 'platforms.ini'))
    asked = []
    monkeypatch.setattr(ServerDialog, 'run', lambda self: asked.append(self.platform))

    ServersDialog(servers).btn_add.clicked()

    assert asked == [dummy]


def test_errors_are_worded_by_kind_and_platform(dummy):
    assert error_message(PlatformError(AUTH, 401), dummy) == 'The server refused the token.'
    assert error_message(PlatformError('not-platform', 200), dummy) == 'No dummy here.'


def _browse(servers):
    dialog = BrowseDialog(servers)
    run_until(lambda: not dialog.spinner.get_visible())
    return dialog


def test_the_browser_follows_the_platform(servers, dummy):
    servers.last = servers.servers[1]
    dialog = _browse(servers)
    model = dialog.filtered

    assert dialog.dialog.get_title() == 'Dummy'
    assert _names(model) == ['Alpha', 'Beta', 'Gamma']
    path = model.get_path(next(_rows(model)))
    dialog.treeview.expand_row(path, False)
    run_until(lambda: not dialog.spinner.get_visible())
    alpha = model.get_iter(path)

    languages = list(_rows(model, alpha))
    assert [(model[it][COL_NAME], model[it][COL_INFO]) for it in languages] == [
        ('Afrikaans', '50.0%'),
        ('Zulu', '100.0%'),
    ]
    assert {model[it][COL_KIND] for it in languages} == {'language'}
    dialog.treeview.get_selection().select_iter(languages[1])
    assert dialog.selected() == ('alpha', None, 'zu')


def test_a_platform_without_components_opens_a_language(servers, dummy, tmp_path, monkeypatch):
    from translate.storage import po

    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    servers.last = servers.servers[1]
    dialog = _browse(servers)
    model = dialog.filtered
    path = model.get_path(next(_rows(model)))
    dialog.treeview.expand_row(path, False)
    run_until(lambda: not dialog.spinner.get_visible())
    dialog.treeview.get_selection().select_iter(next(_rows(model, model.get_iter(path))))

    dialog.open_selected()
    run_until(lambda: dialog.opened is not None)

    folder = tmp_path / 'state' / 'platforms' / 'dummy' / 'one' / 'alpha' / 'af'
    assert dialog.opened == str(folder / 'alpha.po')
    copy = workcopy.for_file(dialog.opened)
    assert copy.location() == 'Alpha'
    assert copy.platform_name() == 'Dummy'
    units = [u for u in po.pofile.parsefile(dialog.opened).units if not u.isheader()]
    copy.attach(SimpleNamespace(get_units=lambda: units, pair=None))
    assert [u.get_state_n() for u in units] == [120, 0]


def test_the_filter_searches_on_a_server_that_can(servers, dummy):
    servers.last = servers.servers[1]
    dialog = _browse(servers)
    assert dummy.queries == ['', '']

    dialog.filter_entry.set_text('bet')
    run_until(lambda: dialog._filter_timer is None and not dialog.spinner.get_visible())

    assert dummy.queries[2:] == ['bet']
    assert _names(dialog.filtered) == ['Beta']


def test_the_server_choice_shows_types_when_there_are_several(servers, tmp_path, dummy):
    assert len(_browse(servers).server_combo.get_cells()) == 2

    dummy.saved = ([('dummy://one', {}), ('dummy://two', {})], '')
    only_dummy = ServerList([dummy], str(tmp_path / 'platforms.ini'))
    assert len(_browse(only_dummy).server_combo.get_cells()) == 1


# The menu item #


def _main_controller():
    menu = Gtk.Menu()
    items = {}
    for name in ('mnu_open', 'mnu_open_folder', 'mnu_save', 'mnu_saveas'):
        items[name] = Gtk.MenuItem(label=name)
        menu.append(items[name])
    view = SimpleNamespace(
        gui=SimpleNamespace(get_object={'menu_file': menu, **items}.get),
        main_window=None,
        sync_menubar=lambda: None,
    )
    return SimpleNamespace(view=view), menu


def test_one_menu_item_serves_every_platform(weblate, dummy):
    registry = Registry()
    main_controller, menu = _main_controller()

    registry.add(weblate, main_controller)
    registry.add(dummy, main_controller)
    labels = [item.get_label() for item in menu.get_children()]
    assert labels == [
        'mnu_open',
        'mnu_open_folder',
        'Open from Tra_nslation Server…',
        'mnu_save',
        'mnu_saveas',
        'Su_bmit to Server…',
        'Up_date from Server…',
    ]

    registry.remove(weblate)
    assert len(menu.get_children()) == 7
    registry.remove(dummy)
    assert len(menu.get_children()) == 4


def test_the_registry_follows_the_network_while_a_platform_is_on(weblate, monkeypatch):
    from virtaal.plugins.weblate.test_trickle import FakeMonitor
    from virtaal.support.platforms import trickle

    monitor = FakeMonitor()
    monkeypatch.setattr(trickle, 'network_monitor', lambda: monitor)
    registry = Registry()
    seen = []
    monkeypatch.setattr(registry.trickler, 'network_changed', seen.append)
    main_controller, _menu = _main_controller()

    registry.add(weblate, main_controller)
    monitor.emit('network-changed', True)
    registry.remove(weblate)
    monitor.emit('network-changed', True)

    assert seen == [True]


def test_the_registry_works_without_a_network_monitor(weblate, monkeypatch):
    from virtaal.support.platforms import trickle

    monkeypatch.setattr(trickle, 'network_monitor', lambda: None)
    registry = Registry()
    main_controller, menu = _main_controller()

    registry.add(weblate, main_controller)
    registry.remove(weblate)

    assert len(menu.get_children()) == 4


def test_browse_lists_the_servers_of_every_platform_on(weblate, dummy, tmp_path, monkeypatch):
    registry = Registry()
    main_controller, _menu = _main_controller()
    registry.add(weblate, main_controller)
    registry.add(dummy, main_controller)
    dummy.saved = ([('dummy://one', {})], '')
    monkeypatch.setattr(pan_app, 'get_config_dir', lambda: str(tmp_path))
    seen = []
    monkeypatch.setattr(BrowseDialog, 'run', lambda self: seen.append(self.servers.servers))

    registry.browse()

    assert [s.url for s in seen[0]] == ['dummy://one']


# Submit #


def test_a_platform_without_components_submits_through_the_interface(
    servers, dummy, tmp_path, monkeypatch
):
    from translate.storage import po

    from virtaal.support.platforms import submit

    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    download = []
    DummySession(dummy, {}).download('alpha', None, 'af', download.append, None)
    run_until(lambda: download)
    copy = workcopy.create(dummy, 'dummy://one', 'alpha', None, 'af', download[0])
    store = po.pofile.parsefile(copy.path)
    units = [u for u in store.units if not u.isheader()]
    copy.attach(SimpleNamespace(get_units=lambda: units, pair=None))
    units[1].target = 'Beta'
    store.save()
    copy.saved()
    assert copy.pending() == [1]
    done = []

    submission = submit.Submission(
        copy, dummy, DummySession(dummy, {}), lambda *a: None, None, done.append
    )
    submission.start()
    run_until(lambda: done)

    assert done[0].sent == 1 and done[0].stop is None
    assert dummy.held['b'].target == ['Beta'] and dummy.held['b'].code == 'draft'
    assert copy.pending() == [] and copy.submitted()


def test_a_platform_without_components_updates_through_the_interface(
    servers, dummy, tmp_path, monkeypatch
):
    from translate.storage import po

    from virtaal.support.platforms import update

    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    download = []
    DummySession(dummy, {}).download('alpha', None, 'af', download.append, None)
    run_until(lambda: download)
    copy = workcopy.create(dummy, 'dummy://one', 'alpha', None, 'af', download[0])
    store = po.pofile.parsefile(copy.path)
    units = [u for u in store.units if not u.isheader()]
    copy.attach(SimpleNamespace(get_units=lambda: units, pair=None))
    dummy.held['b'] = dummy.held['b']._replace(
        target=['Beta!'], state=100, code='draft', last_updated='2'
    )
    dummy.changed.add('b')
    done = []

    update.Update(copy, dummy, DummySession(dummy, {}), None, None, done.append).start()
    run_until(lambda: done)

    assert done[0].taken == 1 and done[0].stop is None
    assert str(units[1].target) == 'Beta!'
    assert copy.units[1]['target_hash'] == workcopy.text_hash(['Beta!'])
