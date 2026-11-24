#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The notice, on opening a file, of the online services the text is sent to (#4903)."""

from types import SimpleNamespace

import pytest

from virtaal.plugins.tm.models.basetmmodel import BaseTMModel, outside_host
from virtaal.plugins.tm.models.localtm import TMModel as LocalTMModel
from virtaal.plugins.tm.models.moses import TMModel as MosesModel
from virtaal.plugins.tm.models.remotetm import TMModel as RemoteTMModel
from virtaal.plugins.tm.tmcontroller import TMController
from virtaal.plugins.tm.tmview import TMView


class _Model:
    def __init__(self, *names):
        self.names = names

    def sends_text_to(self):
        return self.names


class _Plugin:
    def __init__(self, config):
        self.config = config
        self.saved = []
        self.configured = []

    def save_config(self):
        self.saved.append(list(self.config['told_online']))

    def configure(self, parent):
        self.configured.append(parent)


def _controller(models, told=()):
    controller = TMController.__new__(TMController)
    controller.config = {'told_online': list(told)}
    controller.plugin_controller = SimpleNamespace(
        plugins={str(n): model for n, model in enumerate(models)}
    )
    controller.notices = []
    controller.view = SimpleNamespace(
        show_online_notice=lambda services, on_sources, on_never: controller.notices.append(
            (services, on_sources, on_never)
        )
    )
    plugin = _Plugin(controller.config)
    controller.main_controller = SimpleNamespace(
        plugin_controller=SimpleNamespace(plugins={'tm': plugin}),
        view=SimpleNamespace(main_window='window'),
    )
    return controller, plugin


def _shown(controller):
    return [services for services, _sources, _never in controller.notices]


def test_names_each_online_service_once_sorted():
    controller, _plugin = _controller(
        [_Model('Google'), _Model(), _Model('amaGama'), _Model('Google', 'tm.example.org')]
    )

    controller.tell_online_services()

    assert _shown(controller) == [['amaGama', 'Google', 'tm.example.org']]


def test_shown_once_a_session():
    controller, _plugin = _controller([_Model('amaGama')])

    controller.tell_online_services()
    controller.tell_online_services()

    assert _shown(controller) == [['amaGama']]


def test_not_shown_without_online_sources():
    controller, _plugin = _controller([_Model(), _Model()])

    controller.tell_online_services()

    assert controller.notices == []


def test_shown_again_when_another_service_is_switched_on():
    models = [_Model('amaGama')]
    controller, _plugin = _controller(models)
    controller.tell_online_services()

    models.append(_Model('DeepL'))
    controller.plugin_controller.plugins['1'] = models[1]
    controller.tell_online_services()

    assert _shown(controller) == [['amaGama'], ['amaGama', 'DeepL']]


def test_dont_show_again_is_remembered():
    controller, plugin = _controller([_Model('amaGama'), _Model('Google')])
    controller.tell_online_services()
    _services, _sources, never = controller.notices[0]

    never()

    assert plugin.saved == [['amaGama', 'Google']]
    later, _plugin = _controller([_Model('Google'), _Model('amaGama')], told=plugin.saved[0])
    later.tell_online_services()
    assert later.notices == []


def test_shown_again_after_dont_show_again_for_a_new_service():
    controller, _plugin = _controller([_Model('amaGama'), _Model('DeepL')], told=['amaGama'])

    controller.tell_online_services()

    assert _shown(controller) == [['amaGama', 'DeepL']]


def test_select_sources_opens_the_sources_dialog_and_remembers_those_left_on():
    models = [_Model('amaGama'), _Model('Google')]
    controller, plugin = _controller(models)
    controller.tell_online_services()
    _services, sources, _never = controller.notices[0]
    # The user switches Google off in the dialog.
    plugin.configure = lambda parent: (
        plugin.configured.append(parent),
        controller.plugin_controller.plugins.pop('1'),
    )

    sources()

    assert plugin.configured == ['window']
    assert plugin.saved == [['amaGama']]


def test_switching_the_plugin_off_closes_the_notice():
    controller, _plugin = _controller([])
    hidden = []
    controller.main_controller.view.hide_online_notice = lambda: hidden.append(True)
    controller.view.hide = controller.view.destroy = lambda: None
    controller.concordance = None

    controller._destroy_windows()

    assert hidden == [True]


def test_the_notice_names_the_services():
    shown = []
    view = TMView.__new__(TMView)
    view.controller = SimpleNamespace(
        main_controller=SimpleNamespace(
            view=SimpleNamespace(show_online_notice=lambda text, *_args: shown.append(text))
        )
    )

    view.show_online_notice(['amaGama', 'Google'], None, None)

    assert shown == [
        'The source text of each unit you visit is sent to amaGama, Google for suggestions.'
    ]


@pytest.mark.parametrize(
    ('address', 'host'),
    [
        ('localhost', None),
        ('LocalHost', None),
        ('127.0.0.1', None),
        ('::1', None),
        ('[::1]', None),
        ('', None),
        ('http://localhost:8080', None),
        ('http://127.0.1.1:8080/RPC2', None),
        ('tm.example.org', 'tm.example.org'),
        ('http://MT.example.org:8080', 'mt.example.org'),
        ('192.0.2.7', '192.0.2.7'),
    ],
)
def test_outside_host(address, host):
    assert outside_host(address) == host


def test_a_source_sends_to_its_online_service():
    assert BaseTMModel.sends_text_to(SimpleNamespace(online_service=None)) == ()
    assert BaseTMModel.sends_text_to(SimpleNamespace(online_service='amaGama')) == ('amaGama',)


def test_remote_tm_sends_only_to_a_server_elsewhere():
    model = SimpleNamespace(config=dict(RemoteTMModel.default_config))

    assert RemoteTMModel.sends_text_to(model) == ()
    model.config['host'] = 'tm.example.org'
    assert RemoteTMModel.sends_text_to(model) == ('tm.example.org',)


def test_the_local_tm_sends_nothing_outside():
    model = SimpleNamespace(config={'host': 'tm.example.org'})

    assert LocalTMModel.sends_text_to(model) == ()


def test_moses_sends_to_its_servers_elsewhere():
    model = SimpleNamespace(config=dict(MosesModel.default_config))

    assert MosesModel.sends_text_to(model) == ()
    model.config['de->en'] = 'http://mt.example.org:8080'
    model.config['es->en'] = 'http://mt.example.org:8080'
    assert MosesModel.sends_text_to(model) == ('mt.example.org',)
