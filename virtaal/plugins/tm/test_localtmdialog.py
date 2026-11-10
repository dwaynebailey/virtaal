#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Local Translation Memory dialog, against a real TM server."""

import datetime
import os
import socket
import time
from types import SimpleNamespace

import pytest
from babel.dates import format_date
from gi.repository import GObject, Gtk
from translate.storage import tmx

from virtaal.common import pan_app
from virtaal.plugins.tm import localtmdialog
from virtaal.plugins.tm.localtmdialog import (
    COL_EXISTS,
    COL_KIND,
    COL_NAME,
    COL_PAIR,
    COL_TOOLTIP,
    COL_UNITS,
    COL_UPDATED,
    RESPONSE_EXPORT,
    RESPONSE_REMOVE,
    LocalTMDialog,
)
from virtaal.plugins.tm.models import localtm
from virtaal.support import tmclient, tmdb


class _MainView:
    def __init__(self):
        self.main_window = Gtk.Window()
        self.notices = []
        self.errors = []

    def show_info_notice(self, text):
        self.notices.append(text)

    def show_error_dialog(self, title='', message='', markup='', parent=None):
        self.errors.append(message)


@pytest.fixture
def tm(tmp_path, monkeypatch):
    """(app, model, pump): a real server, a local TM model talking to
    it, and pump() to complete every request the model queued."""
    from virtaal.support import test_tmserver

    app, port = test_tmserver._start_server(tmp_path / 'tm.db')
    model = localtm.TMModel.__new__(localtm.TMModel)
    GObject.GObject.__init__(model)
    model.source_lang = 'en'
    model.target_lang = 'af'
    model.cache = {}
    model.controller = SimpleNamespace(main_controller=SimpleNamespace(view=_MainView()))
    model.tmclient = tmclient.TMClient('http://127.0.0.1:%d' % port)
    requests = []
    monkeypatch.setattr(model.tmclient, 'add', lambda request: requests.append(request) or True)
    monkeypatch.setattr(localtmdialog.GLib, 'idle_add', lambda func, *args: func(*args))

    def pump():
        while requests:
            request = requests.pop(0)
            try:
                request.curl.perform()
            except Exception as e:
                request.emit('http-failed', str(e))
                continue
            request.handle_result()

    yield app, model, pump
    while test_tmserver._servers:
        test_tmserver._servers.pop().stop()


def _seed(app, tmp_path):
    """The legacy store, a saved file still there, an imported file
    that is gone, and a bundle's file; returns the saved file's path."""
    app.tmdb.add_dict({'source': 'dog', 'target': 'hond', 'context': ''}, 'en', 'af')
    saved = tmp_path / 'af.po'
    saved.write_text('')
    app.tmdb.sync_store(
        str(saved),
        [{'source': 'cat', 'target': 'kat'}, {'source': 'cow', 'target': 'koei'}],
        'en',
        'af',
    )
    app.tmdb.sync_store(
        '/gone/memory.tmx',
        [{'source': 'cat', 'target': 'kat'}, {'source': 'Open', 'target': 'Öffnen'}],
        'en',
        'de',
        kind=tmdb.STORE_IMPORTED,
    )
    app.tmdb.sync_store(
        '/gone/project.zip', [{'source': 'fish', 'target': 'vis'}], 'en', 'af', member='doc.po'
    )
    return str(saved)


def _rows(dialog):
    return [
        (row[COL_NAME], row[COL_EXISTS], row[COL_PAIR], row[COL_UNITS], row[COL_KIND])
        for row in dialog.liststore
    ]


def _open(model, pump):
    dialog = LocalTMDialog(model)
    dialog.show()
    pump()
    return dialog


def _select(dialog, name):
    for row in dialog.liststore:
        if row[COL_NAME] == name:
            dialog.treeview.get_selection().select_iter(row.iter)
            return
    raise AssertionError(name)


def _store(**fields):
    return {'path': '', 'member': '', 'kind': tmdb.STORE_SAVED, **fields}


def test_store_name_labels_the_legacy_store():
    assert (
        localtmdialog.store_name(_store(id=tmdb.LEGACY_STORE_ID, kind=tmdb.STORE_LEGACY))
        == 'Earlier translations (file not recorded)'
    )


@pytest.mark.parametrize('kind', [tmdb.STORE_SAVED, tmdb.STORE_IMPORTED])
def test_store_name_never_calls_another_store_the_legacy_one(kind):
    assert localtmdialog.store_name(_store(id=7, kind=kind)) == ''
    assert localtmdialog.store_name(_store(id=7, kind=kind, path='/x/af.po')) == 'af.po'
    assert (
        localtmdialog.store_name(_store(id=7, kind=kind, path='/x/p.zip', member='doc.po'))
        == 'p.zip:doc.po'
    )


@pytest.mark.parametrize(
    'lang, expected', [('en', 'Nov 10, 2026'), ('de', '10.11.2026'), ('fa', '۱۰ نوامبر ۲۰۲۶')]
)
def test_dates_are_written_as_the_ui_language_writes_them(monkeypatch, lang, expected):
    monkeypatch.setattr(pan_app, 'ui_language', lang)
    noon = time.mktime((2026, 11, 10, 12, 0, 0, 0, 0, -1))

    assert localtmdialog._date(noon) == expected
    assert localtmdialog._date(0) == ''


def test_a_language_pair_shows_by_the_languages_names(monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')

    assert localtmdialog._pair_name({'source_lang': 'en', 'target_lang': 'af'}) == (
        'English → Afrikaans'
    )
    assert localtmdialog._pair_name({'source_lang': 'en', 'target_lang': 'qaa'}) == (
        'English → qaa'
    )


def test_the_list_sorts_by_codes_and_times_not_by_what_it_shows(tm, tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')
    app, model, pump = tm
    app.tmdb.sync_store(str(tmp_path / 'fr.po'), [{'source': 'a', 'target': 'b'}], 'en', 'fr')
    app.tmdb.sync_store(str(tmp_path / 'de.po'), [{'source': 'a', 'target': 'b'}], 'en', 'de')
    dialog = _open(model, pump)
    try:
        columns = {col.get_title(): col for col in dialog.treeview.get_columns()}
        assert columns['Languages'].get_sort_column_id() == localtmdialog.COL_PAIR_CODES
        assert columns['Last updated'].get_sort_column_id() == localtmdialog.COL_TIME
        dialog.liststore.set_sort_column_id(localtmdialog.COL_PAIR_CODES, Gtk.SortType.ASCENDING)
        # German (de) before French (fr), though French sorts first by name.
        assert [row[COL_PAIR] for row in dialog.liststore] == [
            'English → German',
            'English → French',
        ]
    finally:
        dialog.dialog.destroy()


def test_the_window_links_to_microsofts_ui_strings_glossaries(tm):
    _app, model, pump = tm
    dialog = _open(model, pump)
    try:
        link = dialog.glossary_link
        assert link.get_label() == "Get Microsoft's UI strings glossaries"
        assert link.get_uri() == 'https://my.visualstudio.com/downloads?pid=6822'
        assert link.get_parent() is dialog.dialog.get_content_area()
        assert link.get_visible()
    finally:
        dialog.dialog.destroy()


def test_the_list_shows_each_file_per_language_pair(tm, tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')
    app, model, pump = tm
    saved = _seed(app, tmp_path)

    dialog = _open(model, pump)

    assert dialog.dialog.get_title() == 'Local Translation Memory'
    assert _rows(dialog) == [
        ('Earlier translations (file not recorded)', True, 'English → Afrikaans', 1, ''),
        ('af.po', True, 'English → Afrikaans', 2, 'Saved'),
        ('memory.tmx (file not found)', False, 'English → German', 2, 'Imported'),
        ('project.zip:doc.po (file not found)', False, 'English → Afrikaans', 1, 'Saved'),
    ]
    assert [row[COL_TOOLTIP] for row in dialog.liststore] == [
        '',
        saved,
        '/gone/memory.tmx',
        '/gone/project.zip',
    ]
    today = format_date(datetime.date.today(), 'medium', locale='en')
    assert {row[COL_UPDATED] for row in dialog.liststore} == {today}
    assert [col.get_title() for col in dialog.treeview.get_columns()] == [
        'File',
        'Languages',
        'Translations',
        'Last updated',
        'Type',
    ]
    dialog.dialog.destroy()


def test_remove_and_export_need_a_selection_and_a_row(tm, tmp_path):
    app, model, pump = tm

    dialog = _open(model, pump)
    assert not dialog.btn_remove.get_sensitive()
    assert not dialog.btn_export.get_sensitive()

    _seed(app, tmp_path)
    dialog.refresh()
    pump()
    assert dialog.btn_export.get_sensitive()
    _select(dialog, 'af.po')
    assert dialog.btn_remove.get_sensitive()
    dialog.dialog.destroy()


def test_remove_after_confirming_removes_the_file_and_refreshes(tm, tmp_path, monkeypatch):
    app, model, pump = tm
    _seed(app, tmp_path)
    dialog = _open(model, pump)
    asked = []
    monkeypatch.setattr(
        dialog, 'confirm_remove', lambda store, pair: asked.append(pair['units']) or True
    )
    _select(dialog, 'af.po')

    dialog.dialog.response(RESPONSE_REMOVE)
    pump()

    assert asked == [2]
    assert [row[COL_NAME] for row in dialog.liststore] == [
        'Earlier translations (file not recorded)',
        'memory.tmx (file not found)',
        'project.zip:doc.po (file not found)',
    ]
    assert app.tmdb.translate_unit('cow', 'en', 'af') == []
    # Also from the gone memory.tmx, but under en → de.
    assert app.tmdb.translate_unit('cat', 'en', 'de')[0]['target'] == 'kat'
    dialog.dialog.destroy()


def test_remove_the_legacy_store(tm, tmp_path, monkeypatch):
    app, model, pump = tm
    _seed(app, tmp_path)
    dialog = _open(model, pump)
    monkeypatch.setattr(dialog, 'confirm_remove', lambda store, pair: True)
    _select(dialog, 'Earlier translations (file not recorded)')

    dialog.dialog.response(RESPONSE_REMOVE)
    pump()

    assert app.tmdb.translate_unit('dog', 'en', 'af') == []
    assert len(dialog.liststore) == 3
    dialog.dialog.destroy()


def test_remove_declined_changes_nothing(tm, tmp_path, monkeypatch):
    app, model, pump = tm
    _seed(app, tmp_path)
    dialog = _open(model, pump)
    monkeypatch.setattr(dialog, 'confirm_remove', lambda store, pair: False)
    _select(dialog, 'af.po')

    dialog.dialog.response(RESPONSE_REMOVE)
    pump()

    assert len(dialog.liststore) == 4
    assert app.tmdb.translate_unit('cow', 'en', 'af')[0]['target'] == 'koei'
    dialog.dialog.destroy()


@pytest.mark.parametrize('answer, wanted', [(Gtk.ResponseType.ACCEPT, True), (-4, False)])
def test_confirm_remove_asks_with_the_count_and_the_file(tm, monkeypatch, answer, wanted):
    _app, model, _pump = tm
    dialog = LocalTMDialog(model)
    asked = []

    def run(message_dialog):
        asked.append(
            (message_dialog.get_property('text'), message_dialog.get_property('secondary-text'))
        )
        return answer

    monkeypatch.setattr(Gtk.MessageDialog, 'run', run)
    store = {'path': '/a/af.po', 'member': '', 'kind': tmdb.STORE_SAVED}

    assert dialog.confirm_remove(store, {'units': 1}) is wanted
    dialog.confirm_remove(store, {'units': 3})

    assert asked == [
        (
            "Remove 1 translation from 'af.po'?",
            'Translations another file also added stay in the translation memory.',
        ),
        (
            "Remove 3 translations from 'af.po'?",
            'Translations another file also added stay in the translation memory.',
        ),
    ]
    dialog.dialog.destroy()


def test_a_failed_remove_shows_an_error(tm, tmp_path, monkeypatch):
    app, model, pump = tm
    _seed(app, tmp_path)
    dialog = _open(model, pump)
    monkeypatch.setattr(dialog, 'confirm_remove', lambda store, pair: True)
    _select(dialog, 'af.po')
    app.tmdb.forget_store(os.path.join(str(tmp_path), 'af.po'), 'en', 'af')

    dialog.dialog.response(RESPONSE_REMOVE)
    pump()

    assert model.controller.main_controller.view.errors == [
        "Couldn't remove 'af.po' from the translation memory."
    ]
    dialog.dialog.destroy()


def _export(dialog, pump, monkeypatch, filename, limit):
    chosen = []

    def choose(default_name, can_limit):
        chosen.append((default_name, can_limit))
        return filename, limit

    monkeypatch.setattr(dialog, 'choose_export_file', choose)
    dialog.dialog.response(RESPONSE_EXPORT)
    pump()
    return chosen


def test_export_writes_the_selected_files_translations(tm, tmp_path, monkeypatch):
    app, model, pump = tm
    _seed(app, tmp_path)
    dialog = _open(model, pump)
    _select(dialog, 'memory.tmx (file not found)')
    out = tmp_path / 'out.tmx'

    chosen = _export(dialog, pump, monkeypatch, str(out), True)

    assert chosen == [('memory.tmx', True)]
    exported = tmx.tmxfile(out.read_bytes())
    assert sorted((u.source, u.target) for u in exported.units) == [
        ('Open', 'Öffnen'),
        ('cat', 'kat'),
    ]
    assert model.controller.main_controller.view.notices == [
        "Exported the translation memory to '%s'." % out
    ]
    dialog.dialog.destroy()


def test_export_without_the_limit_writes_the_selected_rows_language_pair(tm, tmp_path, monkeypatch):
    app, model, pump = tm
    _seed(app, tmp_path)
    dialog = _open(model, pump)
    _select(dialog, 'af.po')
    out = tmp_path / 'out.tmx'

    _export(dialog, pump, monkeypatch, str(out), False)

    exported = tmx.tmxfile(out.read_bytes())
    assert sorted(u.target for u in exported.units) == ['hond', 'kat', 'koei', 'vis']
    dialog.dialog.destroy()


def test_export_with_nothing_selected_writes_the_current_pair(tm, tmp_path, monkeypatch):
    app, model, pump = tm
    _seed(app, tmp_path)
    model.target_lang = 'de'
    dialog = _open(model, pump)
    out = tmp_path / 'out.tmx'

    chosen = _export(dialog, pump, monkeypatch, str(out), False)

    assert chosen == [('en-de.tmx', False)]
    assert sorted(u.target for u in tmx.tmxfile(out.read_bytes()).units) == ['kat', 'Öffnen']
    dialog.dialog.destroy()


def test_export_cancelled_writes_nothing(tm, tmp_path, monkeypatch):
    app, model, pump = tm
    _seed(app, tmp_path)
    dialog = _open(model, pump)
    monkeypatch.setattr(dialog, 'choose_export_file', lambda *args: None)

    dialog.dialog.response(RESPONSE_EXPORT)
    pump()

    assert list(tmp_path.glob('*.tmx')) == []
    assert model.controller.main_controller.view.notices == []
    dialog.dialog.destroy()


def test_export_to_a_file_that_cant_be_written_shows_an_error(tm, tmp_path, monkeypatch):
    app, model, pump = tm
    _seed(app, tmp_path)
    dialog = _open(model, pump)
    out = tmp_path / 'no-such-folder' / 'out.tmx'

    _export(dialog, pump, monkeypatch, str(out), False)

    view = model.controller.main_controller.view
    assert view.errors == ["Couldn't export the translation memory to '%s'." % out]
    assert view.notices == []
    dialog.dialog.destroy()


def _closed_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_a_server_that_doesnt_answer_shows_an_error(tm, tmp_path, monkeypatch):
    _app, model, pump = tm
    model.tmclient.base_url = 'http://127.0.0.1:%d' % _closed_port()

    dialog = _open(model, pump)

    assert model.controller.main_controller.view.errors == [
        "Couldn't read the local translation memory."
    ]
    assert len(dialog.liststore) == 0
    out = tmp_path / 'out.tmx'
    _export(dialog, pump, monkeypatch, str(out), False)
    assert model.controller.main_controller.view.errors[1:] == [
        "Couldn't export the translation memory to '%s'." % out
    ]
    assert not out.exists()
    dialog.dialog.destroy()


def test_a_reply_after_closing_is_ignored(tm, tmp_path):
    app, model, pump = tm
    _seed(app, tmp_path)
    dialog = LocalTMDialog(model)
    dialog.show()

    dialog.dialog.response(Gtk.ResponseType.CLOSE)
    pump()

    assert len(dialog.liststore) == 0
    assert model.controller.main_controller.view.errors == []


def test_choose_export_file_offers_tmx_and_the_limit(tm, tmp_path, monkeypatch):
    _app, model, _pump = tm
    dialog = LocalTMDialog(model)
    seen = {}

    def run(chooser):
        seen['title'] = chooser.get_title()
        seen['filters'] = [f.get_name() for f in chooser.list_filters()]
        seen['name'] = chooser.get_current_name()
        extra = chooser.get_extra_widget()
        seen['limit'] = extra and (extra.get_label(), extra.get_active())
        chooser.set_filename(str(tmp_path / 'x.tmx'))
        return Gtk.ResponseType.ACCEPT

    monkeypatch.setattr(Gtk.FileChooserDialog, 'run', run)
    monkeypatch.setattr(Gtk.FileChooserDialog, 'get_filename', lambda c: str(tmp_path / 'x.tmx'))
    monkeypatch.setitem(pan_app.settings.general, 'lastdir', str(tmp_path))

    assert dialog.choose_export_file('af.tmx', True) == (str(tmp_path / 'x.tmx'), True)
    assert seen == {
        'title': 'Export Translation Memory',
        'filters': ['TMX Translation Memory'],
        'name': 'af.tmx',
        'limit': ('_Only the selected file', True),
    }
    assert dialog.choose_export_file('af.tmx', False)[1] is False
    assert seen['limit'] is None
    dialog.dialog.destroy()


def test_escape_closes_the_dialog(tm):
    _app, model, _pump = tm
    dialog = LocalTMDialog(model)
    dialog.show()

    # Escape is bound to "close", which sends the window a delete event.
    dialog.dialog.emit('close')
    for _ in range(50):
        if dialog._closed:
            break
        Gtk.main_iteration_do(False)

    assert dialog._closed
