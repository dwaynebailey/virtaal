#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from virtaal.views.backendselect import select_backends


class _FakeSelectDialog:
    """Stands in for a real SelectDialog - captures what it was built
    and run with, without touching GTK at all."""

    def __init__(self, title=None, message=None, parent=None, size=None):
        self.title = title
        self.message = message
        self.parent = parent
        self.size = size
        self.icon = None
        self.handlers = {}
        self.items = None
        self.sview = _FakeSelectView()

    def set_icon(self, icon):
        self.icon = icon

    def connect(self, signal_name, handler):
        self.handlers[signal_name] = handler

    def run(self, items=None):
        self.items = items


class _FakeSelectView:
    def __init__(self):
        self.configs = []

    def set_item_config(self, data, config):
        self.configs.append((data, config))


class _FakePlugin:
    def __init__(self, configure_func=None):
        self.configure_func = configure_func


class _FakePluginController:
    def __init__(self, plugin_names, infos, plugins=None):
        self._plugin_names = plugin_names
        self._infos = infos
        self.plugins = plugins or {}
        self.enabled = []
        self.disabled = []

    def _find_plugin_names(self):
        return self._plugin_names

    def get_plugin_info(self, name):
        return self._infos[name]

    def enable_plugin(self, name):
        self.enabled.append(name)
        self.plugins.setdefault(name, _FakePlugin(configure_func=print))

    def disable_plugin(self, name):
        self.disabled.append(name)


class _FakeMainWindow:
    def get_icon(self):
        return 'the-app-icon'


class _FakeMainController:
    class view:
        main_window = _FakeMainWindow()


def test_base_model_name_is_excluded_from_the_item_list(monkeypatch):
    pc = _FakePluginController(
        ['basemodel', 'realbackend'],
        {'realbackend': {'display_name': 'Real Backend', 'description': 'desc'}},
    )
    captured = {}

    def fake_select_dialog(**kwargs):
        dlg = _FakeSelectDialog(**kwargs)
        captured['dlg'] = dlg
        return dlg

    monkeypatch.setattr('virtaal.views.widgets.selectdialog.SelectDialog', fake_select_dialog)

    select_backends(
        _FakeMainController(),
        pc,
        {'disabled_models': []},
        'basemodel',
        title='Title',
        message='Message',
        size=(1, 2),
    )

    names = [item['data']['internal_name'] for item in captured['dlg'].items]
    assert names == ['realbackend']


def test_item_config_is_the_configure_func_not_the_whole_plugin(monkeypatch):
    def real_configure(parent):
        return None

    pc = _FakePluginController(
        ['backend1'],
        {'backend1': {'display_name': 'Backend 1', 'description': 'desc'}},
        plugins={'backend1': _FakePlugin(configure_func=real_configure)},
    )
    captured = {}

    def fake_select_dialog(**kwargs):
        dlg = _FakeSelectDialog(**kwargs)
        captured['dlg'] = dlg
        return dlg

    monkeypatch.setattr('virtaal.views.widgets.selectdialog.SelectDialog', fake_select_dialog)

    select_backends(
        _FakeMainController(),
        pc,
        {'disabled_models': []},
        'basemodel',
        title='Title',
        message='Message',
        size=(1, 2),
    )

    items = captured['dlg'].items
    assert len(items) == 1
    assert items[0]['config'] is real_configure


def test_disabled_backend_has_no_config_even_with_a_configure_func(monkeypatch):
    pc = _FakePluginController(
        ['backend1'], {'backend1': {'display_name': 'Backend 1', 'description': 'desc'}}, plugins={}
    )  # not enabled
    captured = {}

    def fake_select_dialog(**kwargs):
        dlg = _FakeSelectDialog(**kwargs)
        captured['dlg'] = dlg
        return dlg

    monkeypatch.setattr('virtaal.views.widgets.selectdialog.SelectDialog', fake_select_dialog)

    select_backends(
        _FakeMainController(),
        pc,
        {'disabled_models': []},
        'basemodel',
        title='Title',
        message='Message',
        size=(1, 2),
    )

    items = captured['dlg'].items
    assert items[0]['enabled'] is False
    assert items[0]['config'] is None


def test_item_enabled_removes_the_backend_from_disabled_models(monkeypatch):
    pc = _FakePluginController(
        ['backend1'], {'backend1': {'display_name': 'B', 'description': 'd'}}
    )
    captured = {}

    def fake_select_dialog(**kwargs):
        dlg = _FakeSelectDialog(**kwargs)
        captured['dlg'] = dlg
        return dlg

    monkeypatch.setattr('virtaal.views.widgets.selectdialog.SelectDialog', fake_select_dialog)
    config = {'disabled_models': ['backend1']}

    select_backends(
        _FakeMainController(),
        pc,
        config,
        'basemodel',
        title='Title',
        message='Message',
        size=(1, 2),
    )
    item = captured['dlg'].items[0]
    captured['dlg'].handlers['item-enabled'](captured['dlg'], item)

    assert 'backend1' in pc.enabled
    assert 'backend1' not in config['disabled_models']
    # Its Configure… appears without reopening the dialog (#4715).
    assert captured['dlg'].sview.configs == [({'internal_name': 'backend1'}, print)]


def test_item_disabled_adds_the_backend_to_disabled_models(monkeypatch):
    pc = _FakePluginController(
        ['backend1'],
        {'backend1': {'display_name': 'B', 'description': 'd'}},
        plugins={'backend1': _FakePlugin()},
    )
    captured = {}

    def fake_select_dialog(**kwargs):
        dlg = _FakeSelectDialog(**kwargs)
        captured['dlg'] = dlg
        return dlg

    monkeypatch.setattr('virtaal.views.widgets.selectdialog.SelectDialog', fake_select_dialog)
    config = {'disabled_models': []}

    select_backends(
        _FakeMainController(),
        pc,
        config,
        'basemodel',
        title='Title',
        message='Message',
        size=(1, 2),
    )
    item = captured['dlg'].items[0]
    captured['dlg'].handlers['item-disabled'](captured['dlg'], item)

    assert 'backend1' in pc.disabled
    assert 'backend1' in config['disabled_models']
    assert captured['dlg'].sview.configs == [({'internal_name': 'backend1'}, None)]


def _items_for(monkeypatch, infos):
    pc = _FakePluginController(list(infos), infos)
    captured = {}

    def fake_select_dialog(**kwargs):
        captured['dlg'] = _FakeSelectDialog(**kwargs)
        return captured['dlg']

    monkeypatch.setattr('virtaal.views.widgets.selectdialog.SelectDialog', fake_select_dialog)
    select_backends(
        _FakeMainController(),
        pc,
        {'disabled_models': []},
        'basemodel',
        title='Title',
        message='Message',
        size=(1, 2),
    )
    return {item['data']['internal_name']: item for item in captured['dlg'].items}


def test_an_online_source_says_where_its_text_goes(monkeypatch):
    items = _items_for(
        monkeypatch,
        {
            'mt': {
                'display_name': 'MT',
                'description': 'd',
                'online_service': 'Example MT',
                'terms_url': 'https://example.org/terms',
            },
            'tm': {'display_name': 'TM', 'description': 'd', 'online_service': 'Example TM'},
        },
    )

    assert items['mt']['notice'] == 'Sends the text you translate to Example MT.'
    assert items['mt']['link'] == ('Terms of service', 'https://example.org/terms')
    assert items['tm']['notice'] == 'Sends the text you translate to Example TM.'
    assert 'link' not in items['tm']


def test_an_offline_source_has_no_notice(monkeypatch):
    items = _items_for(monkeypatch, {'local': {'display_name': 'Local', 'description': 'd'}})

    assert 'notice' not in items['local']
    assert 'link' not in items['local']


def test_a_downloading_source_says_it_sends_no_text(monkeypatch):
    items = _items_for(
        monkeypatch,
        {
            'terms': {
                'display_name': 'Terms',
                'description': 'd',
                'downloads_from': 'Example Corp',
                'terms_url': 'https://example.org/terms',
            }
        },
    )

    assert items['terms']['notice'] == 'Downloads from Example Corp, sending none of your text.'
    assert items['terms']['link'] == ('Terms of service', 'https://example.org/terms')


def test_an_online_source_that_also_downloads_says_where_its_text_goes(monkeypatch):
    items = _items_for(
        monkeypatch,
        {
            'mt': {
                'display_name': 'MT',
                'description': 'd',
                'online_service': 'Example MT',
                'downloads_from': 'Example Corp',
            }
        },
    )

    assert items['mt']['notice'] == 'Sends the text you translate to Example MT.'


def test_terms_without_a_notice_give_no_link(monkeypatch):
    items = _items_for(
        monkeypatch,
        {'local': {'display_name': 'L', 'description': 'd', 'terms_url': 'https://x.org'}},
    )

    assert 'link' not in items['local']


def test_a_backend_without_plugin_info_is_left_out(monkeypatch):
    class _Unreadable(dict):
        def __getitem__(self, name):
            if name == 'broken':
                raise ImportError(name)
            return dict.__getitem__(self, name)

    infos = _Unreadable(broken={}, good={'display_name': 'Good', 'description': 'd'})

    assert list(_items_for(monkeypatch, infos)) == ['good']


def test_a_source_that_names_its_button_passes_the_name_on(monkeypatch):
    pc = _FakePluginController(
        ['localtm', 'deepl'],
        {
            'localtm': {'display_name': 'L', 'description': 'd', 'configure_label': 'Manage…'},
            'deepl': {'display_name': 'D', 'description': 'd', 'configure_label': None},
        },
    )
    captured = {}

    def fake_select_dialog(**kwargs):
        captured['dlg'] = _FakeSelectDialog(**kwargs)
        return captured['dlg']

    monkeypatch.setattr('virtaal.views.widgets.selectdialog.SelectDialog', fake_select_dialog)

    select_backends(
        _FakeMainController(), pc, {'disabled_models': []}, 'basemodel', 'T', 'M', (1, 2)
    )

    local, online = captured['dlg'].items
    assert local['config_label'] == 'Manage…'
    assert 'config_label' not in online


def test_the_local_sources_open_a_window_to_manage_them():
    from virtaal.plugins.terminology.models import localfile
    from virtaal.plugins.tm.models import deepl, localtm

    assert localtm.TMModel.configure_label == 'Manage…'
    assert localfile.TerminologyModel.configure_label == 'Manage…'
    assert deepl.TMModel.configure_label is None
