#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""File > Import into Translation Memory…"""

import os
from types import SimpleNamespace

import pytest
from gi.repository import Gtk

from virtaal.common import pan_app
from virtaal.plugins.tm.tmview import TMView
from virtaal.views.mainview import MainView


class _MainView:
    """A File menu holding the items Import goes between, and a record of
    what was shown."""

    def __init__(self):
        self.main_window = Gtk.Window()
        self.menuitem_file = Gtk.MenuItem.new_with_mnemonic('_File')
        self.menu_file = Gtk.Menu()
        self.menuitem_file.set_submenu(self.menu_file)
        self.items = {}
        for name, label in (
            ('mnu_update', '_Update from Template'),
            ('mnu_binary_export', '_Export'),
            ('mnu_recent_files', '_Recent Files'),
        ):
            self.items[name] = Gtk.MenuItem.new_with_mnemonic(label)
            self.menu_file.append(self.items[name])
        self.gui = SimpleNamespace(get_object=self._get_object)
        self.notices = []
        self.errors = []
        self.synced = 0

    def _get_object(self, name):
        if name == 'menuitem_file':
            return self.menuitem_file
        return self.items[name]

    def append_menu_item(self, name, menu, after=None):
        return MainView.append_menu_item(self, name, menu, after)

    def sync_menubar(self):
        self.synced += 1

    def show_info_notice(self, text):
        self.notices.append(text)

    def show_error_dialog(self, title='', message='', markup='', parent=None):
        self.errors.append(message)


def _labels(mainview):
    return [item.get_label() for item in mainview.menu_file.get_children()]


def _tmview():
    view = TMView.__new__(TMView)
    view.controller = SimpleNamespace(main_controller=SimpleNamespace(view=_MainView()))
    return view, view.controller.main_controller.view


class _Model:
    """import_file() answering at once with a count per file, or an error."""

    def __init__(self, counts):
        self.counts = counts
        self.imported = []

    def import_file(self, path, on_done, on_error):
        self.imported.append(path)
        count = self.counts[os.path.basename(path)]
        if count is None:
            on_error(path)
        else:
            on_done(path, count)


@pytest.fixture
def run_idle(monkeypatch):
    """Run what the view queues with GLib.idle_add() at once."""
    from virtaal.plugins.tm import tmview

    monkeypatch.setattr(tmview.GLib, 'idle_add', lambda func, *args: func(*args))


# The menu item follows the local TM.


def test_the_import_item_goes_after_export_with_its_own_access_key():
    view, mainview = _tmview()

    view.add_import_menu_item(_Model({}))

    assert _labels(mainview) == [
        '_Update from Template',
        '_Export',
        '_Import into Translation Memory…',
        '_Recent Files',
    ]
    assert view.mnu_import.get_use_underline()
    assert view.mnu_import.get_visible()
    assert mainview.synced == 1


def test_adding_the_import_item_twice_keeps_one():
    view, mainview = _tmview()
    view.add_import_menu_item(_Model({}))

    view.add_import_menu_item(_Model({}))

    assert _labels(mainview).count('_Import into Translation Memory…') == 1


def test_removing_the_import_item_takes_it_off_the_file_menu():
    view, mainview = _tmview()
    view.add_import_menu_item(_Model({}))

    view.remove_import_menu_item()

    assert '_Import into Translation Memory…' not in _labels(mainview)
    assert view.mnu_import is None
    view.remove_import_menu_item()  # once more is harmless


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


class _Plugin:
    def connect(self, signal, handler):
        return 1

    def disconnect(self, signal_id):
        pass


class _ImportingPlugin(_Plugin):
    def import_file(self, path, on_done, on_error):
        pass


def test_the_local_tm_brings_the_import_item_and_takes_it_away(monkeypatch):
    controller, mainview, handlers = _controller_handlers(monkeypatch)
    local_tm = _ImportingPlugin()

    handlers['plugin-enabled'](None, local_tm)
    assert '_Import into Translation Memory…' in _labels(mainview)
    assert controller.view._import_model is local_tm

    handlers['plugin-disabled'](None, local_tm)
    assert '_Import into Translation Memory…' not in _labels(mainview)


def test_no_import_item_without_the_local_tm(monkeypatch):
    controller, mainview, handlers = _controller_handlers(monkeypatch)

    handlers['plugin-enabled'](None, _Plugin())

    assert '_Import into Translation Memory…' not in _labels(mainview)


def test_the_real_local_tm_model_can_import():
    from virtaal.plugins.tm.models import localtm, remotetm

    assert hasattr(localtm.TMModel, 'import_file')
    assert not hasattr(remotetm.TMModel, 'import_file')


# Choosing files.


def test_the_chooser_opens_several_translation_files_from_the_last_folder(tmp_path, monkeypatch):
    monkeypatch.setitem(pan_app.settings.general, 'lastdir', str(tmp_path))
    # The folder only reads back once the dialog is shown.
    folders = []
    monkeypatch.setattr(
        Gtk.FileChooserNative, 'set_current_folder', lambda self, folder: folders.append(folder)
    )
    view, mainview = _tmview()

    chooser = view._build_import_chooser()
    try:
        # Native, like File > Open: the real Windows and macOS picker.
        assert isinstance(chooser, Gtk.FileChooserNative)
        assert chooser.get_title() == 'Import into Translation Memory'
        assert chooser.get_action() == Gtk.FileChooserAction.OPEN
        assert chooser.get_select_multiple()
        assert chooser.get_transient_for() is mainview.main_window
        names = [f.get_name() for f in chooser.list_filters()]
        assert names[0] == 'All Supported Files'
        assert 'TMX Translation Memory' in names
        assert 'Trados Studio Translation Memory' in names
        assert 'MultiTerm XML Termbase' in names
        assert 'Microsoft UI Strings Glossary' in names
        assert names[-1] == 'All Files'
        patterns = chooser.list_filters()[0].to_gvariant().unpack()[1]
        assert (0, '*.sdltm') in patterns
        assert (0, '*.zip') in patterns
        assert folders == [str(tmp_path)]
    finally:
        chooser.destroy()


class _Chooser:
    def __init__(self, response, filenames):
        self.response = response
        self.filenames = filenames
        self.destroyed = False

    def run(self):
        return self.response

    def get_filenames(self):
        return self.filenames

    def destroy(self):
        self.destroyed = True


def test_choosing_files_remembers_their_folder(tmp_path, monkeypatch):
    monkeypatch.setitem(pan_app.settings.general, 'lastdir', '')
    view, _mainview = _tmview()
    chooser = _Chooser(Gtk.ResponseType.ACCEPT, [str(tmp_path / 'a.tmx'), str(tmp_path / 'b.po')])
    view._build_import_chooser = lambda: chooser

    assert view.choose_import_files() == chooser.filenames
    assert pan_app.settings.general['lastdir'] == str(tmp_path)
    assert chooser.destroyed


def test_cancelling_the_chooser_imports_nothing(monkeypatch):
    view, mainview = _tmview()
    chooser = _Chooser(Gtk.ResponseType.CANCEL, ['/home/user/a.tmx'])
    view._build_import_chooser = lambda: chooser
    model = _Model({})
    view._import_model = model

    view._on_import()

    assert model.imported == []
    assert chooser.destroyed
    assert mainview.notices == mainview.errors == []


# The result.


def _import(view, *paths):
    view.choose_import_files = lambda: list(paths)
    view._on_import()


@pytest.mark.parametrize(
    'counts, notice',
    [
        ({'a.tmx': 1}, 'Added 1 translation to the translation memory.'),
        ({'a.tmx': 0}, 'Added 0 translations to the translation memory.'),
        ({'a.tmx': 2, 'b.po': 3}, 'Added 5 translations to the translation memory.'),
    ],
)
def test_the_notice_says_how_many_translations_were_added(run_idle, counts, notice):
    view, mainview = _tmview()
    view._import_model = _Model(counts)

    _import(view, *('/home/user/' + name for name in counts))

    assert mainview.notices == [notice]
    assert mainview.errors == []


def test_the_notice_says_how_many_glossary_rows_were_skipped(run_idle):
    view, mainview = _tmview()

    class Glossaries:
        def import_file(self, path, on_done, on_error):
            on_done(path, 3, {'a.csv': 1, 'b.zip': 2}[os.path.basename(path)])

    view._import_model = Glossaries()
    _import(view, '/home/user/a.csv')
    _import(view, '/home/user/a.csv', '/home/user/b.zip')

    assert mainview.notices == [
        "Added 3 translations to the translation memory. Skipped 1 row that couldn't be read.",
        "Added 6 translations to the translation memory. Skipped 3 rows that couldn't be read.",
    ]


def test_a_file_that_cant_be_imported_is_named_in_an_error(run_idle):
    view, mainview = _tmview()
    view._import_model = _Model({'a.tmx': None})

    _import(view, '/home/user/a.tmx')

    assert mainview.notices == []
    assert mainview.errors == ["Couldn't import 'a.tmx' into the translation memory."]


def test_some_files_imported_and_some_not_shows_both(run_idle):
    view, mainview = _tmview()
    view._import_model = _Model({'a.tmx': 4, 'b.po': None, 'c.xlf': None})

    _import(view, '/home/user/a.tmx', '/home/user/b.po', '/home/user/c.xlf')

    assert mainview.notices == ['Added 4 translations to the translation memory.']
    assert mainview.errors == [
        "Couldn't import 'b.po' into the translation memory.\n"
        "Couldn't import 'c.xlf' into the translation memory."
    ]


def test_the_result_waits_for_every_file(run_idle):
    view, mainview = _tmview()
    answers = []

    class Later:
        def import_file(self, path, on_done, on_error):
            answers.append(lambda: on_done(path, 1))

    view._import_model = Later()
    _import(view, '/home/user/a.tmx', '/home/user/b.po')

    answers[0]()
    assert mainview.notices == []
    answers[1]()
    assert mainview.notices == ['Added 2 translations to the translation memory.']
