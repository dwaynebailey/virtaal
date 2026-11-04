#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from types import SimpleNamespace

import pytest

from .basetmmodel import BaseTMModel, unescape_html_entities


def test_unescape_html_entities():
    """Test the unescaping of &amp; and &#39; type HTML escapes"""
    assert unescape_html_entities("This &amp; That") == "This & That"
    assert unescape_html_entities("&#39;n Vertaler") == "'n Vertaler"
    assert (
        unescape_html_entities("Copyright &copy; 2009 Virtaa&#7741;") == "Copyright © 2009 Virtaaḽ"
    )


def test_unescape_html_entities_keeps_what_isnt_an_entity():
    # Machine translation results can contain text like "AT&T;".
    assert unescape_html_entities("AT&T; and &nosuch;") == "AT&T; and &nosuch;"
    assert unescape_html_entities("&#99999999; &amp;") == "&#99999999; &"


def test_a_new_checker_drops_the_cached_matches():
    # amaGama's matches depend on the project style sent with the query.
    model = SimpleNamespace(checker='gnome', cache={'File': None}, set_checker=lambda checker: None)

    BaseTMModel._set_checker(model, None, 'mozilla')

    assert model.checker == 'mozilla'
    assert model.cache == {}


def _backends():
    import importlib
    import pkgutil

    from virtaal.plugins.tm import models

    for info in pkgutil.iter_modules(models.__path__):
        if info.name.startswith('test_') or info.name == 'basetmmodel':
            continue
        module = importlib.import_module('virtaal.plugins.tm.models.' + info.name)
        yield info.name, module.TMModel


def test_every_backend_decides_whether_it_may_preload():
    # Inheriting a decision isn't making one.
    assert 'PREFETCH_MAX' not in BaseTMModel.__dict__
    backends = dict(_backends())
    assert len(backends) == 9
    for name, cls in backends.items():
        assert 'PREFETCH_MAX' in cls.__dict__, name
        assert 0 <= cls.PREFETCH_MAX <= 2, name
        assert 'prefetch' in cls.__dict__.get('default_config', {}), name
        assert 0 <= int(cls.default_config['prefetch']) <= cls.PREFETCH_MAX, name


def test_preload_policy_per_backend():
    policy = {
        name: (cls.PREFETCH_MAX, int(cls.default_config['prefetch'])) for name, cls in _backends()
    }

    assert policy == {
        '_dummytm': (0, 0),
        'amagama': (2, 2),
        'apertium': (1, 0),
        'currentfile': (0, 0),
        'deepl': (2, 0),
        'google_translate': (2, 0),
        'localtm': (2, 2),
        'moses': (2, 2),
        'remotetm': (2, 2),
    }


class _Backend(BaseTMModel):
    __gtype_name__ = 'TestPrefetchBackend'
    PREFETCH_MAX = 2
    default_config = {'prefetch': 2}


def _backend(config=None, cache=None):
    model = _Backend.__new__(_Backend)
    model.config = dict(_Backend.default_config, **(config or {}))
    model.cache = cache if cache is not None else {}
    return model


@pytest.mark.parametrize(
    'value, expected',
    [
        ('2', 2),
        ('1', 1),
        ('0', 0),
        ('5', 2),  # clamped to the backend's ceiling
        ('-1', 0),
        ('lots', 2),  # not a number: the default
        ('', 2),
    ],
)
def test_prefetch_depth_is_the_setting_within_the_ceiling(value, expected):
    assert _backend({'prefetch': value}).prefetch_depth() == expected


def test_prefetch_depth_never_exceeds_a_lower_ceiling(monkeypatch):
    monkeypatch.setattr(_Backend, 'PREFETCH_MAX', 1)

    assert _backend({'prefetch': '2'}).prefetch_depth() == 1


def test_prefetch_depth_without_the_setting_is_the_default():
    model = _backend()
    model.config = {}

    assert model.prefetch_depth() == 2


def test_prefetch_queries_a_unit_not_yet_cached():
    model = _backend(cache={'cached': []})
    queried = []
    model.controller = 'controller'
    model.query = lambda controller, unit: queried.append((controller, unit.source))

    model.prefetch(SimpleNamespace(source='cached'))
    model.prefetch(SimpleNamespace(source='new'))

    assert queried == [('controller', 'new')]


def test_prefetch_waits_while_the_languages_are_unknown():
    # A held query must not be replaced by a preload.
    model = _backend()
    model.languages_known = False
    model.query = lambda *a: pytest.fail('must not query')

    model.prefetch(SimpleNamespace(source='new'))


def test_has_cached():
    model = _backend(cache={'none found': None})

    assert model.has_cached('none found')
    assert not model.has_cached('other')


def test_a_model_without_a_client_has_nothing_in_flight():
    assert _backend().requests_in_flight() == 0


def test_init_keeps_a_config_loaded_before_it(monkeypatch):
    # localtm and apertium load theirs first.
    from virtaal.plugins.tm._testhelpers import fake_controller

    model = _Backend.__new__(_Backend)
    model.config = {'prefetch': '1'}
    BaseTMModel.__init__(model, fake_controller())

    assert model.config == {'prefetch': '1'}


# configure() (#1519)


@pytest.mark.parametrize('values', [None, {'api_key': 'new'}])
def test_configure_saves_only_what_the_dialog_returns(monkeypatch, values):
    from virtaal.views.widgets import settingsdialog

    monkeypatch.setattr(settingsdialog.SettingsDialog, 'run', lambda self: values)
    updated = []
    model = SimpleNamespace(
        display_name='Some MT',
        settings=(settingsdialog.api_key_field(),),
        config={'api_key': ''},
        update_settings=updated.append,
    )

    BaseTMModel.configure(model, None)

    assert updated == ([values] if values else [])
