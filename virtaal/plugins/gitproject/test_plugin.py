#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Git projects in the server list and the browser: off until enabled,
added with a branch and the language files' path, and a language opened
from a local bare repository with no change to the platform layer."""

import os

import pytest

from devsupport.testing.fakeplatform import run_until
from devsupport.testing.gitremote import Remote, needs_git
from virtaal.common import pan_app
from virtaal.plugins.gitproject import Plugin, backend
from virtaal.support.platforms import workcopy
from virtaal.support.platforms.browsedialog import COL_NAME, BrowseDialog
from virtaal.support.platforms.servers import ServerList
from virtaal.support.platforms.serversdialog import ServerDialog, ServersDialog

PO = (
    'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\nmsgid "Open"\nmsgstr ""\n'
)


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'ui_language', 'en')
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))


@pytest.fixture
def platform(tmp_path):
    return backend.GitPlatform(str(tmp_path / 'gitprojects.ini'), str(tmp_path / 'clones'))


def test_the_plug_in_is_experimental_and_off_until_enabled(tmp_path):
    assert Plugin.display_name == 'Git (experimental)'
    assert Plugin.description == 'Translate files a team keeps in a Git repository'
    path = tmp_path / 'virtaal.ini'
    path.write_text('', encoding='utf-8')
    assert pan_app.Settings(str(path)).plugin_state['gitproject'] == 'disabled'


def test_add_asks_for_the_branch_and_files_already_filled_in(tmp_path, platform, monkeypatch):
    asked = []

    def answer(self):
        asked.append((self.dialog.get_title(), self.get_values()))
        labels = [f.label for f in self.fields]
        keys = [label[label.index('_') + 1].lower() for label in labels]
        assert len(set(keys)) == len(keys), labels
        return None

    monkeypatch.setattr(ServerDialog, 'run', answer)
    ServersDialog(ServerList([platform], str(tmp_path / 'platforms.ini'))).btn_add.clicked()

    assert asked == [
        (
            'Add Git Server',
            {'url': '', 'branch': 'main', 'files': 'po/*.po', 'send_finished': False},
        )
    ]


def test_a_path_without_its_star_says_so_and_can_t_be_added(platform):
    dialog = ServerDialog(None, platform, '', platform.default_credentials)
    dialog.entries['url'].set_text('git@example.org:team/program.git')
    assert dialog.btn_ok.get_sensitive()
    dialog.entries['files'].set_text('po/af.po')
    assert not dialog.btn_ok.get_sensitive()
    assert dialog.problem_label.get_text() == 'Use one * for the language’s code, such as po/*.po.'
    dialog.dialog.destroy()


def _rows(model, parent=None):
    it = model.iter_children(parent)
    while it is not None:
        yield it
        it = model.iter_next(it)


@needs_git
def test_the_browser_opens_a_language_of_the_repository(tmp_path, platform):
    remote = Remote(tmp_path / 'team', {'po/af.po': PO, 'po/de.po': PO, 'README': 'x\n'})
    servers = ServerList([platform], str(tmp_path / 'platforms.ini'))
    servers.put(platform, remote.url, {'branch': 'main', 'files': 'po/*.po'})
    dialog = BrowseDialog(servers)
    run_until(lambda: not dialog.spinner.get_visible(), timeout=20)
    model = dialog.filtered

    assert [model[it][COL_NAME] for it in _rows(model)] == ['remote']
    path = model.get_path(next(_rows(model)))
    dialog.treeview.expand_row(path, False)
    run_until(lambda: not dialog.spinner.get_visible(), timeout=20)
    languages = list(_rows(model, model.get_iter(path)))
    assert [model[it][COL_NAME] for it in languages] == ['Afrikaans', 'German']
    dialog.treeview.get_selection().select_iter(languages[0])

    dialog.open_selected()
    run_until(lambda: dialog.opened is not None, timeout=20)

    copy = workcopy.for_file(dialog.opened)
    assert copy.platform_name() == 'Git'
    assert copy.location() == 'remote'
    assert dialog.opened.endswith(os.path.join('po', 'af.po'))
    dialog.dialog.destroy()
