#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""File > Add to Local TM on Save"""

import os
from types import SimpleNamespace

import pytest
from gi.repository import GObject, Gtk

from virtaal.plugins.tm.test_tm_import import _ImportingPlugin, _MainView, _Plugin
from virtaal.plugins.tm.tmview import TMView

LABEL = 'Add to Local _TM on Save'


class _StoreController:
    def __init__(self, filename='/home/user/af.po', modified=False):
        self.filename = filename
        self.modified = modified

    def get_store(self):
        return object() if self.filename else None

    def get_store_filename(self):
        return self.filename or ''

    def is_modified(self):
        return self.modified


class _Model:
    """The local TM's exclude calls, answering at once from C{status}
    (file name -> (excluded, units), or None for an error)."""

    def __init__(self, status=None, fail_set=False):
        self.status = status or {}
        self.fail_set = fail_set
        self.asked = []
        self.set = []
        self.pushed = []

    def store_status(self, store_controller, on_done, on_error):
        self.asked.append(store_controller.filename)
        answer = self.status.get(store_controller.filename, (False, 0))
        if answer is None:
            on_error()
        else:
            on_done(*answer)

    def set_exclude(self, store_controller, exclude, on_done, on_error):
        self.set.append((store_controller.filename, exclude))
        if self.fail_set:
            on_error()
        else:
            on_done()

    def push_store(self, store_controller):
        self.pushed.append(store_controller.filename)


def _labels(mainview):
    return [item.get_label() for item in mainview.menu_file.get_children()]


def _tmview(store_controller=None):
    view = TMView.__new__(TMView)
    view.controller = SimpleNamespace(
        main_controller=SimpleNamespace(
            view=_MainView(), store_controller=store_controller or _StoreController()
        )
    )
    view.mnu_suggestions = Gtk.CheckMenuItem()
    view.mnu_summon = Gtk.MenuItem()
    view.hide = lambda: None
    view.confirmed = []
    view.confirm_answer = True

    def confirm(name, units):
        view.confirmed.append((name, units))
        return view.confirm_answer

    view.confirm_exclude = confirm
    return view, view.controller.main_controller.view


@pytest.fixture(autouse=True)
def run_idle(monkeypatch):
    """Run what the view queues with GLib.idle_add() at once."""
    from virtaal.plugins.tm import tmview

    monkeypatch.setattr(tmview.GLib, 'idle_add', lambda func, *args: func(*args))


# The menu item follows the local TM.


def test_the_item_goes_after_import_ticked_with_its_own_access_key():
    view, mainview = _tmview()
    model = _Model()
    view.add_import_menu_item(model)

    view.add_exclude_menu_item(model)

    assert _labels(mainview) == [
        '_Update from Template',
        '_Export',
        '_Import into Translation Memory…',
        LABEL,
        '_Recent Files',
    ]
    item = view.mnu_add_on_save
    assert isinstance(item, Gtk.CheckMenuItem)
    assert item.get_use_underline() and item.get_visible()
    assert item.get_active() and item.get_sensitive()
    assert model.asked == ['/home/user/af.po']
    assert model.set == []
    assert mainview.synced == 2


def test_the_item_is_insensitive_without_a_file():
    view, _mainview = _tmview(_StoreController(filename=None))
    model = _Model()

    view.add_exclude_menu_item(model)

    assert not view.mnu_add_on_save.get_sensitive()
    assert view.mnu_add_on_save.get_active()
    assert model.asked == []


def test_adding_the_item_twice_keeps_one_and_removing_takes_it_away():
    view, mainview = _tmview()
    view.add_exclude_menu_item(_Model())
    view.add_exclude_menu_item(_Model())
    assert _labels(mainview).count(LABEL) == 1

    view.remove_exclude_menu_item()

    assert LABEL not in _labels(mainview)
    assert view.mnu_add_on_save is None
    view.remove_exclude_menu_item()  # once more is harmless


def _controller_handlers(monkeypatch):
    from virtaal.plugins.tm.tmcontroller import TMController

    controller = TMController.__new__(TMController)
    controller.disabled_model_names = []
    controller._signal_ids = {}
    controller.view, mainview = _tmview()
    handlers = {}
    monkeypatch.setattr(
        'virtaal.controllers.plugincontroller.PluginController.for_backend',
        lambda *a: SimpleNamespace(
            connect=lambda signal, handler: handlers.__setitem__(signal, handler),
            load_plugins=lambda: None,
        ),
    )
    controller._load_models()
    return controller, mainview, handlers


class _LocalTM(_ImportingPlugin, _Model):
    def __init__(self):
        _Model.__init__(self)


def test_the_local_tm_brings_the_item_and_takes_it_away(monkeypatch):
    controller, mainview, handlers = _controller_handlers(monkeypatch)
    local_tm = _LocalTM()

    handlers['plugin-enabled'](None, local_tm)
    assert LABEL in _labels(mainview)
    assert controller.view._exclude_model is local_tm

    handlers['plugin-disabled'](None, local_tm)
    assert LABEL not in _labels(mainview)


def test_no_item_without_the_local_tm(monkeypatch):
    _controller, mainview, handlers = _controller_handlers(monkeypatch)

    handlers['plugin-enabled'](None, _Plugin())

    assert LABEL not in _labels(mainview)


def test_only_the_local_tm_model_can_exclude():
    from virtaal.plugins.tm.models import amagama, localtm, remotetm

    assert hasattr(localtm.TMModel, 'set_exclude')
    assert not hasattr(remotetm.TMModel, 'set_exclude')
    assert not hasattr(amagama.TMModel, 'set_exclude')


# Opening and closing files.


def test_opening_an_excluded_file_unticks_the_item_without_changing_anything():
    store_controller = _StoreController()
    view, mainview = _tmview(store_controller)
    model = _Model({'/home/user/af.po': (True, 0)})
    view.add_exclude_menu_item(model)
    assert not view.mnu_add_on_save.get_active()

    store_controller.filename = '/home/user/de.po'
    view._on_store_loaded(store_controller)

    assert view.mnu_add_on_save.get_active()
    assert model.asked == ['/home/user/af.po', '/home/user/de.po']
    assert model.set == view.confirmed == []
    assert mainview.notices == mainview.errors == []


def test_a_file_the_tm_cant_answer_for_stays_ticked():
    view, mainview = _tmview()
    view.add_exclude_menu_item(_Model({'/home/user/af.po': None}))

    assert view.mnu_add_on_save.get_active()
    assert mainview.errors == []


def test_a_late_answer_about_a_closed_file_is_dropped():
    store_controller = _StoreController()
    view, _mainview = _tmview(store_controller)
    answers = []
    model = _Model()
    model.store_status = lambda sc, on_done, on_error: answers.append(on_done)
    view.add_exclude_menu_item(model)

    store_controller.filename = None
    view._on_store_closed(store_controller)
    answers[0](True, 3)

    assert view.mnu_add_on_save.get_active()
    assert not view.mnu_add_on_save.get_sensitive()


def test_saving_under_another_name_asks_about_the_new_file():
    store_controller = _StoreController()
    view, _mainview = _tmview(store_controller)
    model = _Model({'/home/user/af.po': (True, 0)})
    view.add_exclude_menu_item(model)

    view._on_store_saved(store_controller)
    store_controller.filename = '/home/user/copy.po'
    view._on_store_saved(store_controller)

    assert model.asked == ['/home/user/af.po', '/home/user/copy.po']
    assert view.mnu_add_on_save.get_active()


# Unticking and ticking.


def _untick(view):
    view.mnu_add_on_save.set_active(False)


def test_unticking_a_file_that_added_nothing_excludes_it_without_asking():
    view, mainview = _tmview()
    model = _Model()
    view.add_exclude_menu_item(model)

    _untick(view)

    assert view.confirmed == []
    assert model.set == [('/home/user/af.po', True)]
    assert not view.mnu_add_on_save.get_active()
    assert mainview.errors == []


@pytest.mark.parametrize('accept', [True, False])
def test_unticking_a_file_that_added_translations_asks_first(accept):
    view, _mainview = _tmview()
    model = _Model({'/home/user/af.po': (False, 3)})
    view.add_exclude_menu_item(model)
    view.confirm_answer = accept

    _untick(view)

    assert view.confirmed == [('af.po', 3)]
    if accept:
        assert model.set == [('/home/user/af.po', True)]
        assert not view.mnu_add_on_save.get_active()
    else:
        assert model.set == []
        assert view.mnu_add_on_save.get_active()


@pytest.mark.parametrize('fail_status', [True, False])
def test_unticking_when_the_tm_fails_ticks_it_again_and_says_so(fail_status):
    view, mainview = _tmview()
    model = _Model(fail_set=not fail_status)
    view.add_exclude_menu_item(model)
    if fail_status:
        model.status['/home/user/af.po'] = None

    _untick(view)

    assert view.mnu_add_on_save.get_active()
    assert mainview.errors == ["Couldn't remove 'af.po' from the translation memory."]


def test_ticking_a_saved_file_again_adds_it_now():
    view, mainview = _tmview()
    model = _Model({'/home/user/af.po': (True, 0)})
    view.add_exclude_menu_item(model)

    view.mnu_add_on_save.set_active(True)

    assert model.set == [('/home/user/af.po', False)]
    assert model.pushed == ['/home/user/af.po']
    assert mainview.notices == []


def test_ticking_a_changed_file_again_adds_it_on_save():
    view, mainview = _tmview(_StoreController(modified=True))
    model = _Model({'/home/user/af.po': (True, 0)})
    view.add_exclude_menu_item(model)

    view.mnu_add_on_save.set_active(True)

    assert model.set == [('/home/user/af.po', False)]
    assert model.pushed == []
    assert mainview.notices == ["'af.po' will be added to the translation memory when you save it."]


def test_ticking_when_the_tm_fails_unticks_it_again_and_says_so():
    view, mainview = _tmview()
    model = _Model({'/home/user/af.po': (True, 0)}, fail_set=True)
    view.add_exclude_menu_item(model)

    view.mnu_add_on_save.set_active(True)

    assert not view.mnu_add_on_save.get_active()
    assert mainview.errors == ["Couldn't add 'af.po' to the translation memory."]


def test_the_confirmation_names_the_file_and_count(monkeypatch):
    view, mainview = _tmview()
    del view.confirm_exclude
    shown = []

    def run(dialog):
        shown.append(
            (
                dialog.props.text,
                dialog.props.secondary_text,
                dialog.get_transient_for(),
                [
                    dialog.get_widget_for_response(response).get_label()
                    for response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.ACCEPT)
                ],
            )
        )
        return Gtk.ResponseType.ACCEPT

    monkeypatch.setattr(Gtk.MessageDialog, 'run', run)

    assert view.confirm_exclude('af.po', 1)
    assert shown[0][0] == (
        "Stop adding 'af.po' to the translation memory and remove the 1 translation it added?"
    )
    assert shown[0][1] == 'Translations another file also added stay in the translation memory.'
    assert shown[0][2] is mainview.main_window
    assert shown[0][3] == ['_Cancel', '_Remove']


# With the real model and server.


class _SavingStoreController(GObject.GObject):
    """A store controller whose file the test 'saves' by emitting
    store-saved, as the real one does."""

    __gtype_name__ = 'TMExcludeTestStoreController'
    __gsignals__ = {'store-saved': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, filename, po):
        super().__init__()
        from translate.storage import pypo

        self.filename = filename
        self.modified = False
        self._store = pypo.pofile.parsestring(po)
        self.store = SimpleNamespace(
            get_units=lambda: self._store.units, get_filename=lambda: filename
        )

    def get_store(self):
        return self.store

    def get_bundle_filename(self):
        return None

    def get_store_filename(self):
        return self.filename

    def is_modified(self):
        return self.modified


def test_unticking_and_ticking_again_with_the_real_local_tm(tmp_path, monkeypatch):
    from virtaal.common import SignalTracker
    from virtaal.plugins.tm.models.localtm import TMModel
    from virtaal.support import test_tmserver, tmclient

    app, port = test_tmserver._start_server(tmp_path / 'tm.db')
    try:
        filename = str(tmp_path / 'af.po')
        store_controller = _SavingStoreController(
            filename, b'msgid "Open"\nmsgstr "Maak oop"\n\nmsgid "Quit"\nmsgstr "Verlaat"\n'
        )
        model = TMModel.__new__(TMModel)
        GObject.GObject.__init__(model)
        model.source_lang, model.target_lang = 'en', 'af'
        model.cache, model._generation, model._excluded = {}, 0, {}
        model.tmclient = tmclient.TMClient('http://127.0.0.1:%d' % port)
        requests = []
        monkeypatch.setattr(model.tmclient, 'add', lambda request: requests.append(request) or True)
        model._signal_tracker = SignalTracker()
        model._signal_tracker.connect(store_controller, 'store-saved', model.push_store)

        def send_all():
            while requests:
                request = requests.pop(0)
                request.curl.perform()
                request.handle_result()

        def units():
            return app.tmdb.store_stats(os.path.realpath(filename), 'en', 'af')['units']

        view, mainview = _tmview(store_controller)
        store_controller.emit('store-saved')
        send_all()
        assert units() == 2

        view.add_exclude_menu_item(model)
        send_all()
        assert view.mnu_add_on_save.get_active()

        _untick(view)
        send_all()
        send_all()
        assert view.confirmed == [('af.po', 2)]
        assert units() == 0
        assert app.tmdb.translate_unit('Open', 'en', 'af') == []

        store_controller.emit('store-saved')
        assert requests == []
        assert units() == 0

        store_controller.modified = True
        view.mnu_add_on_save.set_active(True)
        send_all()
        assert mainview.notices == [
            "'af.po' will be added to the translation memory when you save it."
        ]
        store_controller.emit('store-saved')
        send_all()
        assert units() == 2
        assert app.tmdb.translate_unit('Open', 'en', 'af')[0]['target'] == 'Maak oop'
        assert mainview.errors == []
    finally:
        while test_tmserver._servers:
            test_tmserver._servers.pop().stop()
