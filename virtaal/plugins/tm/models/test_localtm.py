#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import os
import signal
import socket
import sys
from types import SimpleNamespace

import pytest
from gi.repository import GObject, Gtk
from translate.storage import pypo

from virtaal.common import SignalTracker, pan_app
from virtaal.common.platform import Platform
from virtaal.controllers.baseplugin import PluginUnsupported
from virtaal.plugins.tm._testhelpers import fake_controller
from virtaal.plugins.tm.models import localtm
from virtaal.plugins.tm.models.localtm import TMModel, find_free_port
from virtaal.support import helperprocess, tmdb

# localtm.test_port is a production function, not a test - even a
# module-level alias here would make pytest try to collect and run it
# as one, since collection matches on the name "test_port" itself
# regardless of how it's bound. Call it as port_is_free() instead.
port_is_free = localtm.test_port


def test_test_port_true_for_a_free_port():
    # Bind a real socket to get an actually-unused ephemeral port,
    # then immediately free it - it's very unlikely (not proven
    # impossible) to be grabbed by anything else before test_port()
    # gets to it.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('localhost', 0))
    port = s.getsockname()[1]
    s.close()

    assert port_is_free('localhost', port) is True


def test_test_port_false_for_an_occupied_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('localhost', 0))
    port = s.getsockname()[1]
    try:
        assert port_is_free('localhost', port) is False
    finally:
        s.close()


def test_find_free_port_skips_an_occupied_port(monkeypatch):
    # Whether a real neighbouring port is free depends on the machine (it
    # made this flaky on Windows CI), so which ports are busy is set here;
    # test_port() itself is covered against real sockets above.
    monkeypatch.setattr(localtm, 'test_port', lambda host, port: port != 5000)
    # Try the occupied port first, rather than a random order.
    monkeypatch.setattr('random.shuffle', lambda ports: None)

    assert find_free_port('localhost', 5000, 5002) == 5001


def test_find_free_port_is_none_when_every_port_is_occupied(monkeypatch):
    monkeypatch.setattr(localtm, 'test_port', lambda host, port: False)

    assert find_free_port('localhost', 5000, 5002) is None


def test_destroy_uses_sigterm_on_posix(monkeypatch):
    monkeypatch.setattr(localtm, 'platform', Platform(os_name='posix'))
    killed = []
    monkeypatch.setattr(localtm.os, 'kill', lambda pid, sig: killed.append((pid, sig)))

    model = TMModel.__new__(TMModel)
    model.tmserver = SimpleNamespace(pid=12345)
    model._signal_tracker = SignalTracker()

    model.destroy()

    assert killed == [(12345, signal.SIGTERM)]


def test_destroy_uses_terminateprocess_on_windows(monkeypatch):
    monkeypatch.setattr(localtm, 'platform', Platform(os_name='nt'))
    terminated = []

    class _FakeKernel32:
        def TerminateProcess(self, handle, exit_code):
            terminated.append((handle, exit_code))

    class _FakeCtypes(SimpleNamespace):
        windll = SimpleNamespace(kernel32=_FakeKernel32())

    monkeypatch.setitem(sys.modules, 'ctypes', _FakeCtypes())

    model = TMModel.__new__(TMModel)
    model.tmserver = SimpleNamespace(_handle=999)
    model._signal_tracker = SignalTracker()

    model.destroy()

    assert terminated == [(999, -1)]


# _pick_port() #


def _model_with_config(bind='localhost', port=55555, tmdb='/tmp/tm.db'):
    model = TMModel.__new__(TMModel)
    model.config = {'tmserver_bind': bind, 'tmserver_port': port, 'tmdb': tmdb}
    return model


def test_pick_port_uses_the_configured_port_when_free(monkeypatch):
    monkeypatch.setattr(localtm, 'test_port', lambda host, port: True)
    model = _model_with_config(port=55555)

    assert model._pick_port() == 55555


def test_pick_port_falls_back_to_a_free_port_when_the_configured_one_is_taken(monkeypatch):
    monkeypatch.setattr(localtm, 'test_port', lambda host, port: False)
    calls = []
    monkeypatch.setattr(
        localtm, 'find_free_port', lambda host, lo, hi: calls.append((host, lo, hi)) or 12345
    )
    model = _model_with_config(bind='localhost', port=55555)

    assert model._pick_port() == 12345
    assert calls == [('localhost', 49152, 65535)]


def test_no_free_port_leaves_the_local_tm_off_without_starting_a_server(monkeypatch, caplog):
    monkeypatch.setattr(
        TMModel, 'load_config', lambda self: setattr(self, 'config', dict(TMModel.default_config))
    )
    monkeypatch.setattr(TMModel, '_check_config', lambda self: None)
    monkeypatch.setattr(localtm, 'test_port', lambda host, port: False)
    monkeypatch.setattr(localtm, 'find_free_port', lambda host, lo, hi: None)
    started = []
    monkeypatch.setattr('subprocess.Popen', lambda command, env: started.append(command))

    with caplog.at_level(logging.WARNING), pytest.raises(PluginUnsupported):
        TMModel('localtm', SimpleNamespace(min_quality=75, max_matches=3))

    assert started == []
    assert 'No free port' in caplog.text


# _build_tmserver_command() #


def _fake_controller(min_quality=75, max_matches=3):
    return SimpleNamespace(min_quality=min_quality, max_matches=max_matches)


def test_build_tmserver_command_uses_run_module_when_frozen(monkeypatch):
    monkeypatch.setattr(helperprocess, 'platform', Platform(frozen=True))
    model = _model_with_config()

    command = model._build_tmserver_command(55555, _fake_controller())

    assert command[:3] == [sys.executable, '--run-module', 'virtaal.support.tmserver']


def test_build_tmserver_command_uses_module_flag_when_not_frozen(monkeypatch):
    monkeypatch.setattr(helperprocess, 'platform', Platform(frozen=False))
    model = _model_with_config()

    command = model._build_tmserver_command(55555, _fake_controller())

    assert command[:3] == [sys.executable, '-m', 'virtaal.support.tmserver']


def test_build_tmserver_command_includes_bind_port_db_and_controller_settings(monkeypatch):
    monkeypatch.setattr(helperprocess, 'platform', Platform(frozen=False))
    model = _model_with_config(bind='127.0.0.1', port=55555, tmdb='/tmp/tm.db')

    command = model._build_tmserver_command(55555, _fake_controller(min_quality=80, max_matches=5))

    assert '-b' in command and command[command.index('-b') + 1] == '127.0.0.1'
    assert '-p' in command and command[command.index('-p') + 1] == '55555'
    assert '-d' in command and command[command.index('-d') + 1] == '/tmp/tm.db'
    assert '--min-similarity=80' in command
    assert '--max-candidates=5' in command


def test_build_tmserver_command_keeps_to_raw_text_without_abstract_placeables(monkeypatch):
    monkeypatch.setattr(helperprocess, 'platform', Platform(frozen=False))
    model = _model_with_config()
    controller = _fake_controller()

    assert '--raw-quality' not in model._build_tmserver_command(55555, controller)
    controller.abstract_placeables = False
    assert '--raw-quality' in model._build_tmserver_command(55555, controller)


def test_build_tmserver_command_appends_debug_flag_when_pan_app_debug(monkeypatch):
    monkeypatch.setattr(helperprocess, 'platform', Platform(frozen=False))
    monkeypatch.setattr(pan_app, 'DEBUG', True)
    model = _model_with_config()

    command = model._build_tmserver_command(55555, _fake_controller())

    assert command[-1] == '--debug'


def test_build_tmserver_command_omits_debug_flag_by_default(monkeypatch):
    monkeypatch.setattr(helperprocess, 'platform', Platform(frozen=False))
    monkeypatch.setattr(pan_app, 'DEBUG', False)
    model = _model_with_config()

    command = model._build_tmserver_command(55555, _fake_controller())

    assert '--debug' not in command


# _launch_tmserver() #


def test_launch_tmserver_starts_the_process_and_builds_a_tmclient(monkeypatch):
    monkeypatch.setattr(helperprocess, 'platform', Platform(frozen=False))
    model = _model_with_config(bind='localhost', port=55555)
    monkeypatch.setattr(helperprocess, 'env', lambda: {'PYTHONPATH': '/x'})
    popen_calls = []

    class _FakePopen:
        def __init__(self, command, env):
            popen_calls.append((command, env))

    monkeypatch.setattr('subprocess.Popen', _FakePopen)
    tmclient_urls = []
    monkeypatch.setattr(
        'virtaal.support.tmclient.TMClient', lambda url: tmclient_urls.append(url) or 'the-client'
    )

    model._launch_tmserver(['the', 'command'], 55555)

    assert popen_calls == [(['the', 'command'], {'PYTHONPATH': '/x'})]
    assert isinstance(model.tmserver, _FakePopen)
    assert tmclient_urls == ['http://localhost:55555/tmserver']
    assert model.tmclient == 'the-client'


def test_launch_tmserver_logs_and_reraises_on_oserror(monkeypatch):
    model = _model_with_config()

    def raise_oserror(command, env):
        raise OSError('boom')

    monkeypatch.setattr('subprocess.Popen', raise_oserror)

    with pytest.raises(OSError):
        model._launch_tmserver(['the', 'command'], 55555)


def _unchecked_model(**config):
    model = TMModel.__new__(TMModel)
    model.config = dict(TMModel.default_config, **config)
    return model


@pytest.mark.parametrize(
    'host', ['', '  ', 'no-such-host.invalid', 'h\x00st', 'hôst.\u202e', '1.5', '8.8.8.8']
)
def test_check_config_replaces_a_host_that_cant_be_used(host, caplog):
    # #4340: each port tried looked the host up again, blocking start-up.
    model = _unchecked_model(tmserver_bind=host)

    model._check_config()

    assert model.config['tmserver_bind'] == 'localhost'
    assert "can't be used" in caplog.text


def test_check_config_keeps_a_usable_host():
    model = _unchecked_model(tmserver_bind='127.0.0.1')

    model._check_config()

    assert model.config['tmserver_bind'] == '127.0.0.1'


def test_check_config_puts_a_relative_tm_database_in_the_config_folder():
    model = _unchecked_model(tmdb='tm.db')

    model._check_config()

    assert model.config['tmdb'] == os.path.join(pan_app.get_config_dir(), 'tm.db')


def test_check_config_keeps_an_absolute_tm_database(tmp_path):
    model = _unchecked_model(tmdb=str(tmp_path / 'tm.db'))

    model._check_config()

    assert model.config['tmdb'] == str(tmp_path / 'tm.db')


@pytest.mark.parametrize('port', ['', 'abc', '0', '-1', '65536', '99999999999999999999'])
def test_check_config_replaces_an_unusable_port(port):
    model = _unchecked_model(tmserver_port=port)

    model._check_config()

    assert model.config['tmserver_port'] == 55555


# push_store(): a save syncs the file into the local TM.


def _pushing_model():
    model = TMModel.__new__(TMModel)
    GObject.GObject.__init__(model)
    model.source_lang = 'en'
    model.target_lang = 'af'
    model.cache = {'stale': 'entry'}
    model._excluded = {}
    model.sent = []
    model.tmclient = SimpleNamespace(add_store=lambda *a: model.sent.append(a))
    return model


def _store_controller(po, filename, bundle=None, member=None):
    store = pypo.pofile.parsestring(po)
    return SimpleNamespace(
        store=SimpleNamespace(get_units=lambda: store.units, get_filename=lambda: filename),
        get_bundle_filename=lambda: bundle,
        get_store_filename=lambda: '%s:%s' % (bundle, member) if bundle else filename,
    )


PO = (
    b'msgid "Open"\nmsgstr "Maak oop"\n\n'
    b'#, fuzzy\nmsgid "Close"\nmsgstr "Maak toe"\n\n'
    b'msgctxt "menu"\nmsgid "Quit"\nmsgstr "Verlaat"\n\n'
    b'msgid "Save"\nmsgstr ""\n'
)


def test_push_store_syncs_the_files_translated_units_by_key(tmp_path):
    model = _pushing_model()
    (tmp_path / 'sub').mkdir()

    model.push_store(_store_controller(PO, os.path.join(str(tmp_path), 'sub', '..', 'af.po')))

    ((path, body, source_lang, target_lang),) = model.sent
    assert path == os.path.realpath(tmp_path / 'af.po')
    assert (source_lang, target_lang) == ('en', 'af')
    assert body['replace'] is True
    assert body['member'] == ''
    assert body['units'] == [
        {'source': 'Open', 'target': 'Maak oop', 'context': '', 'key': 'Open'},
        {'source': 'Quit', 'target': 'Verlaat', 'context': 'menu', 'key': 'menu\x04Quit'},
    ]
    assert model.cache == {}


def test_push_store_keys_a_bundles_file_by_the_bundle_and_its_name(tmp_path):
    model = _pushing_model()
    bundle = str(tmp_path / 'project.zip')

    model.push_store(_store_controller(PO, '/tmp/extracted/doc.po', bundle=bundle, member='doc.po'))

    ((path, body, _source_lang, _target_lang),) = model.sent
    assert path == os.path.realpath(bundle)
    assert body['member'] == 'doc.po'


def test_saving_twice_replaces_a_changed_translation_on_the_server(tmp_path, monkeypatch):
    from virtaal.support import test_tmserver, tmclient

    app, port = test_tmserver._start_server(tmp_path / 'tm.db')
    try:
        model = _pushing_model()
        model.tmclient = tmclient.TMClient('http://127.0.0.1:%d' % port)
        requests = []
        monkeypatch.setattr(model.tmclient, 'add', lambda request: requests.append(request) or True)
        filename = str(tmp_path / 'af.po')

        for po in (PO, PO.replace(b'"Maak oop"', b'"Open dit"').replace(b'"Verlaat"', b'""')):
            model.push_store(_store_controller(po, filename))
            requests[-1].curl.perform()

        matches = app.tmdb.translate_unit('Open', 'en', 'af')
        assert [m['target'] for m in matches] == ['Open dit']
        assert app.tmdb.translate_unit('Quit', 'en', 'af') == []
        assert app.tmdb.store_stats(os.path.realpath(filename), 'en', 'af')['units'] == 1
    finally:
        while test_tmserver._servers:
            test_tmserver._servers.pop().stop()


# import_file(): a file chosen by the user goes into the local TM.

TMX = b'''<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4"><header srclang="en" adminlang="en" datatype="plaintext"
 segtype="sentence" creationtool="test" creationtoolversion="1" o-tmf="test"/>
<body>
<tu><tuv xml:lang="en"><seg>Open file</seg></tuv><tuv xml:lang="de"><seg>Datei offnen</seg></tuv></tu>
<tu><tuv xml:lang="en"><seg>Close file</seg></tuv><tuv xml:lang="de"><seg>Datei schliessen</seg></tuv></tu>
</body></tmx>
'''


@pytest.fixture
def importing_model(tmp_path, monkeypatch):
    """A model talking to a real server; returns (model, app, send) where
    send() completes the request import_file() queued."""
    from virtaal.support import test_tmserver, tmclient

    app, port = test_tmserver._start_server(tmp_path / 'tm.db')
    model = _pushing_model()
    model._generation = 0
    model.tmclient = tmclient.TMClient('http://127.0.0.1:%d' % port)
    requests = []
    monkeypatch.setattr(model.tmclient, 'add', lambda request: requests.append(request) or True)

    def send():
        request = requests.pop()
        try:
            request.curl.perform()
        except Exception as e:
            request.emit('http-failed', str(e))
            return
        request.handle_result()

    yield model, app, send
    while test_tmserver._servers:
        test_tmserver._servers.pop().stop()


def _import(model, path):
    results = []
    model.import_file(
        str(path),
        lambda path, added: results.append(('done', path, added)),
        lambda path: results.append(('error', path)),
    )
    return results


def test_import_file_adds_a_tmx_under_its_own_languages(tmp_path, importing_model):
    model, app, send = importing_model
    (tmp_path / 'sub').mkdir()
    tmx = tmp_path / 'memory.tmx'
    tmx.write_bytes(TMX)

    results = _import(model, tmp_path / 'sub' / '..' / 'memory.tmx')
    send()

    assert results == [('done', os.path.realpath(tmx), 2)]
    assert app.tmdb.translate_unit('Open file', 'en', 'de')[0]['target'] == 'Datei offnen'
    assert app.tmdb.translate_unit('Open file', 'en', 'af') == []
    stats = app.tmdb.store_stats(os.path.realpath(tmx), 'en', 'de')
    assert stats['kind'] == tmdb.STORE_IMPORTED
    assert model.cache == {}
    assert model._generation == 1


def test_import_file_files_a_po_without_languages_under_the_current_pair(tmp_path, importing_model):
    model, app, send = importing_model
    po = tmp_path / 'af.po'
    po.write_bytes(PO)

    results = _import(model, po)
    send()

    assert results == [('done', os.path.realpath(po), 2)]
    assert app.tmdb.translate_unit('Quit', 'en', 'af')[0]['target'] == 'Verlaat'


def test_importing_a_file_again_adds_nothing(tmp_path, importing_model):
    model, _app, send = importing_model
    po = tmp_path / 'af.po'
    po.write_bytes(PO)
    _import(model, po)
    send()

    results = _import(model, po)
    send()

    assert results == [('done', os.path.realpath(po), 0)]


def test_importing_a_changed_file_counts_its_new_translations(tmp_path, importing_model):
    model, app, send = importing_model
    po = tmp_path / 'af.po'
    po.write_bytes(PO)
    _import(model, po)
    send()
    po.write_bytes(PO.replace(b'"Maak oop"', b'"Open dit"'))

    results = _import(model, po)
    send()

    assert results == [('done', os.path.realpath(po), 1)]
    assert [m['target'] for m in app.tmdb.translate_unit('Open', 'en', 'af')] == ['Open dit']


@pytest.mark.parametrize(
    'content', [None, b'', b'<tmx>broken'], ids=['missing', 'empty', 'unreadable']
)
def test_import_file_reports_a_file_it_cant_import(tmp_path, importing_model, content):
    model, app, send = importing_model
    path = tmp_path / 'memory.tmx'
    if content is not None:
        path.write_bytes(content)

    results = _import(model, path)
    if content:
        send()

    assert results == [('error', os.path.realpath(path))]
    assert model.cache == {'stale': 'entry'}


def test_import_file_reports_a_server_that_doesnt_answer(tmp_path, importing_model):
    model, _app, _send = importing_model
    from virtaal.support import tmclient

    model.tmclient = tmclient.TMClient('http://127.0.0.1:%d' % _closed_port())
    requests = []
    model.tmclient.add = lambda request: requests.append(request) or True
    po = tmp_path / 'af.po'
    po.write_bytes(PO)

    results = _import(model, po)
    try:
        requests[0].curl.perform()
    except Exception as e:
        requests[0].emit('http-failed', str(e))

    assert results == [('error', os.path.realpath(po))]


def test_import_file_reports_a_full_queue(tmp_path):
    model = _pushing_model()
    model.tmclient = SimpleNamespace(import_file=lambda *a: None)
    po = tmp_path / 'af.po'
    po.write_bytes(PO)

    assert _import(model, po) == [('error', os.path.realpath(po))]


def _closed_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


# list_stores(), remove_store(), export_tmx(): the Local Translation Memory dialog's calls.


def _results():
    results = []
    return (
        results,
        (lambda *args: results.append(('done', *args))),
        (lambda: results.append(('error',))),
    )


def test_list_stores_gives_each_store_and_its_pairs(importing_model):
    model, app, send = importing_model
    app.tmdb.add_dict({'source': 'dog', 'target': 'hond', 'context': ''}, 'en', 'af')
    app.tmdb.sync_store('/gone/af.po', [{'source': 'cat', 'target': 'kat'}], 'en', 'af')
    results, done, error = _results()

    model.list_stores(done, error)
    send()

    ((_done, stores),) = results
    assert [(s['path'], s['exists'], s['pairs'][0]['units']) for s in stores] == [
        ('', None, 1),
        ('/gone/af.po', False, 1),
    ]


def test_remove_store_keeps_what_another_store_holds(importing_model):
    model, app, send = importing_model
    app.tmdb.sync_store('/a/af.po', [{'source': 'cat', 'target': 'kat'}], 'en', 'af')
    app.tmdb.sync_store(
        '/a/b.po',
        [{'source': 'cat', 'target': 'kat'}, {'source': 'dog', 'target': 'hond'}],
        'en',
        'af',
    )
    results, done, error = _results()

    model.remove_store('/a/b.po', '', 'en', 'af', done, error)
    send()

    assert results == [('done', 2)]
    assert app.tmdb.translate_unit('dog', 'en', 'af') == []
    assert app.tmdb.translate_unit('cat', 'en', 'af')[0]['target'] == 'kat'
    assert model.cache == {}
    assert model._generation == 1


def test_remove_store_reports_an_unknown_store(importing_model):
    model, _app, send = importing_model
    results, done, error = _results()

    model.remove_store('/nowhere.po', '', 'en', 'af', done, error)
    send()

    assert results == [('error',)]
    assert model.cache == {'stale': 'entry'}


def test_export_tmx_gives_the_pair_or_one_stores_entries(importing_model):
    from translate.storage import tmx

    model, app, send = importing_model
    app.tmdb.sync_store('/a/af.po', [{'source': 'cat', 'target': 'kat'}], 'en', 'af')
    app.tmdb.sync_store('/a/b.po', [{'source': 'dog', 'target': 'hond'}], 'en', 'af')
    results, done, error = _results()

    model.export_tmx('en', 'af', done, error)
    send()
    model.export_tmx('en', 'af', done, error, path='/a/b.po')
    send()

    assert [sorted(u.target for u in tmx.tmxfile(data).units) for _done, data in results] == [
        ['hond', 'kat'],
        ['hond'],
    ]


@pytest.mark.parametrize(
    'call, args',
    [
        ('list_stores', ()),
        ('remove_store', ('/a.po', '', 'en', 'af')),
        ('export_tmx', ('en', 'af')),
    ],
)
def test_store_calls_report_a_server_that_doesnt_answer(importing_model, call, args):
    model, _app, _send = importing_model
    from virtaal.support import tmclient

    model.tmclient = tmclient.TMClient('http://127.0.0.1:%d' % _closed_port())
    requests = []
    model.tmclient.add = lambda request: requests.append(request) or True
    results, done, error = _results()

    getattr(model, call)(*args, done, error)
    try:
        requests[0].curl.perform()
    except Exception as e:
        requests[0].emit('http-failed', str(e))

    assert results == [('error',)]


@pytest.mark.parametrize('call', ['list_stores', 'remove_store', 'export_tmx'])
def test_store_calls_report_a_full_queue(call):
    model = _pushing_model()
    model.tmclient = SimpleNamespace(**{call: lambda *a: None})
    results, done, error = _results()
    args = {
        'list_stores': (),
        'remove_store': ('/a.po', '', 'en', 'af'),
        'export_tmx': ('en', 'af'),
    }

    getattr(model, call)(*args[call], done, error)

    assert results == [('error',)]


# store_status(), set_exclude(): File > Add to Local TM on Save.


def _status(model, store_controller):
    results, done, error = _results()
    model.store_status(store_controller, done, error)
    return results


def test_store_status_of_a_file_the_tm_doesnt_know(tmp_path, importing_model):
    model, _app, send = importing_model
    store_controller = _store_controller(PO, str(tmp_path / 'af.po'))

    results = _status(model, store_controller)
    send()

    assert results == [('done', False, 0)]


def test_store_status_counts_what_a_saved_file_added(tmp_path, importing_model):
    model, app, send = importing_model
    filename = str(tmp_path / 'af.po')
    app.tmdb.sync_store(filename, [{'source': 'cat', 'target': 'kat'}], 'en', 'af')
    app.tmdb.sync_store(filename, [{'source': 'cat', 'target': 'Katze'}], 'en', 'de')

    results = _status(model, _store_controller(PO, filename))
    send()

    assert results == [('done', False, 1)]


def test_excluding_a_file_removes_what_it_added_and_stops_its_saves(tmp_path, importing_model):
    model, app, send = importing_model
    filename = str(tmp_path / 'af.po')
    store_controller = _store_controller(PO, filename)
    app.tmdb.sync_store(filename, [{'source': 'Open', 'target': 'Maak oop'}], 'en', 'af')
    results, done, error = _results()

    model.set_exclude(store_controller, True, lambda: done(), error)
    send()

    assert results == [('done',)]
    assert app.tmdb.translate_unit('Open', 'en', 'af') == []
    assert model.cache == {}
    assert model._generation == 1
    results = _status(model, store_controller)
    send()
    assert results == [('done', True, 0)]
    model.tmclient.add_store = lambda *a: model.sent.append(a)
    model.push_store(store_controller)
    assert model.sent == []


def test_a_bundles_file_is_excluded_on_its_own(tmp_path, importing_model):
    model, app, send = importing_model
    bundle = str(tmp_path / 'project.zip')
    doc = _store_controller(PO, '/tmp/x/doc.po', bundle=bundle, member='doc.po')
    other = _store_controller(PO, '/tmp/x/other.po', bundle=bundle, member='other.po')

    model.set_exclude(doc, True, lambda: None, lambda: None)
    send()

    assert app.tmdb.store_stats(os.path.realpath(bundle), 'en', 'af', 'doc.po')['exclude']
    results = _status(model, other)
    send()
    assert results == [('done', False, 0)]
    results = _status(model, doc)
    send()
    assert results == [('done', True, 0)]


def test_a_file_let_in_again_is_added_by_its_next_save(tmp_path, importing_model, monkeypatch):
    model, app, send = importing_model
    filename = str(tmp_path / 'af.po')
    store_controller = _store_controller(PO, filename)
    model.set_exclude(store_controller, True, lambda: None, lambda: None)
    send()

    model.set_exclude(store_controller, False, lambda: None, lambda: None)
    send()
    model.push_store(store_controller)
    send()

    assert model._excluded == {(os.path.realpath(filename), ''): False}
    assert app.tmdb.translate_unit('Open', 'en', 'af')[0]['target'] == 'Maak oop'
    assert app.tmdb.store_stats(os.path.realpath(filename), 'en', 'af')['units'] == 2


def test_push_store_sends_when_the_files_state_is_unknown(tmp_path):
    model = _pushing_model()
    model._excluded = {(os.path.realpath(tmp_path / 'other.po'), ''): True}

    model.push_store(_store_controller(PO, str(tmp_path / 'af.po')))

    assert len(model.sent) == 1


@pytest.mark.parametrize('call', ['store_status', 'set_exclude'])
def test_exclude_calls_report_a_server_that_doesnt_answer(tmp_path, importing_model, call):
    model, _app, _send = importing_model
    from virtaal.support import tmclient

    model.tmclient = tmclient.TMClient('http://127.0.0.1:%d' % _closed_port())
    requests = []
    model.tmclient.add = lambda request: requests.append(request) or True
    results, done, error = _results()
    args = () if call == 'store_status' else (True,)

    getattr(model, call)(_store_controller(PO, str(tmp_path / 'af.po')), *args, done, error)
    try:
        requests[0].curl.perform()
    except Exception as e:
        requests[0].emit('http-failed', str(e))

    assert results == [('error',)]
    assert model._excluded == {}


@pytest.mark.parametrize('call, method', [('store_status', 'store_info'), ('set_exclude',) * 2])
def test_exclude_calls_report_a_full_queue(tmp_path, call, method):
    model = _pushing_model()
    model.tmclient = SimpleNamespace(**{method: lambda *a: None})
    results, done, error = _results()
    args = () if call == 'store_status' else (True,)

    getattr(model, call)(_store_controller(PO, str(tmp_path / 'af.po')), *args, done, error)

    assert results == [('error',)]


# Waiting for the server: a file opened with Virtaal is looked up at start.


@pytest.fixture
def starting_server(importing_model, monkeypatch):
    """The importing_model with its server 'starting': returns (model,
    app, send, server) where server.up lets connections through,
    server.exited ends the process and server.check() runs the wait's
    next poll, returning whether it keeps polling."""
    model, app, send = importing_model
    port = int(model.tmclient.base_url.rsplit(':', 1)[1])
    model.config = {'tmserver_bind': '127.0.0.1'}
    server = SimpleNamespace(up=False, exited=False, checks=[])
    model.tmserver = SimpleNamespace(poll=lambda: 1 if server.exited else None)
    real_connect = socket.create_connection

    def connect(address, timeout):
        if not server.up:
            raise ConnectionRefusedError
        return real_connect(address, timeout)

    monkeypatch.setattr(localtm.socket, 'create_connection', connect)
    monkeypatch.setattr(
        localtm.GLib, 'timeout_add', lambda ms, func: server.checks.append(func) or 7
    )
    model._wait_for_server(port)
    server.check = lambda: server.checks[0]()
    return model, app, send, server


def test_a_file_looked_up_before_the_server_is_up_waits_for_it(tmp_path, starting_server, caplog):
    model, _app, send, server = starting_server
    caplog.set_level(logging.DEBUG)
    store_controller = _store_controller(PO, str(tmp_path / 'af.po'))

    results = _status(model, store_controller)
    assert server.check() is True
    assert results == []

    server.up = True
    assert server.check() is False
    send()

    assert results == [('done', False, 0)]
    assert model._wait_id is None
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_save_before_the_server_is_up_sends_the_file_as_saved(tmp_path, starting_server):
    model, app, send, server = starting_server
    filename = str(tmp_path / 'af.po')

    model.push_store(_store_controller(PO, filename))
    server.up = True
    server.check()
    send()

    assert app.tmdb.store_stats(os.path.realpath(filename), 'en', 'af')['units'] == 2


@pytest.mark.parametrize('how', ['exited', 'timed out'])
def test_a_server_that_never_comes_up_warns_once(
    tmp_path, starting_server, caplog, monkeypatch, how
):
    model, _app, _send, server = starting_server
    caplog.set_level(logging.DEBUG)
    from virtaal.support import tmclient

    model.tmclient = tmclient.TMClient('http://127.0.0.1:%d' % _closed_port())
    requests = []
    model.tmclient.add = lambda request: requests.append(request) or True
    if how == 'exited':
        server.exited = True
    else:
        later = localtm.time.monotonic() + model.SERVER_WAIT + 1
        monkeypatch.setattr(localtm, 'time', SimpleNamespace(monotonic=lambda: later))
    results = _status(model, _store_controller(PO, str(tmp_path / 'af.po')))
    assert server.check() is False
    try:
        requests[0].curl.perform()
    except Exception as e:
        requests[0].emit('http-failed', str(e))

    assert results == [('error',)]
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "didn't start" in warnings[0]


def test_destroy_stops_waiting_for_the_server(monkeypatch):
    model = _pushing_model()
    model._signal_tracker = SignalTracker()
    model._wait_id = 42
    model.tmserver = SimpleNamespace(pid=12345)
    removed = []
    monkeypatch.setattr(localtm.GLib, 'source_remove', removed.append)
    monkeypatch.setattr(localtm.os, 'kill', lambda pid, sig: None)
    monkeypatch.setattr(localtm, 'platform', Platform(os_name='posix'))

    model.destroy()

    assert removed == [42]
    assert model._wait_id is None


# Opening it: Configure… on the local TM in the sources dialog.


def test_the_local_tm_model_opens_the_dialog_from_configure(monkeypatch):
    monkeypatch.setattr(
        TMModel,
        'load_config',
        lambda self: setattr(self, 'config', dict(TMModel.default_config)),
    )
    monkeypatch.setattr(TMModel, '_check_config', lambda self: None)
    monkeypatch.setattr(TMModel, '_pick_port', lambda self: 1)
    monkeypatch.setattr(TMModel, '_launch_tmserver', lambda self, command, port: None)
    controller = fake_controller()
    controller.min_quality, controller.max_matches = 75, 3
    controller.main_controller.store_controller = controller
    controller.main_controller.view = SimpleNamespace(main_window=Gtk.Window())
    model = TMModel('localtm', controller)
    shown = []
    from virtaal.plugins.tm.localtmdialog import LocalTMDialog

    monkeypatch.setattr(LocalTMDialog, 'show', lambda self: shown.append(self))
    parent = Gtk.Window()

    model.configure_func(parent)

    (dialog,) = shown
    assert dialog.model is model
    assert dialog.dialog.get_transient_for() is parent
    dialog.dialog.destroy()


def test_the_sources_dialog_offers_configure_only_while_the_local_tm_is_on(monkeypatch):
    from virtaal.views import backendselect

    model = SimpleNamespace(configure_func=lambda parent: None)
    runs = []

    class _SelectDialog:
        def __init__(self, **kwargs):
            pass

        def set_icon(self, icon):
            pass

        def connect(self, *args):
            pass

        def run(self, items):
            runs.append({item['data']['internal_name']: item['config'] for item in items})

    monkeypatch.setattr('virtaal.views.widgets.selectdialog.SelectDialog', _SelectDialog)
    for plugins in ({'localtm': model}, {}):
        plugin_controller = SimpleNamespace(
            _find_plugin_names=lambda: ['localtm'],
            get_plugin_info=lambda name: {'display_name': 'Local TM', 'description': ''},
            plugins=plugins,
        )
        backendselect.select_backends(
            SimpleNamespace(view=SimpleNamespace(main_window=Gtk.Window())),
            plugin_controller,
            {'disabled_models': []},
            'basetmmodel',
            'title',
            'message',
            (1, 1),
        )

    assert runs == [{'localtm': model.configure_func}, {'localtm': None}]
