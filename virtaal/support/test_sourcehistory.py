#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os
import shutil
import subprocess

import pytest

from virtaal.models.monolingual import MonolingualPair, state_name
from virtaal.support import sourcehistory, tmdb
from virtaal.support.converters import PropertiesConverter

EN = 'app.title=File Manager\ngreeting=Hello, {0}!\nquit=Quit\n'
AF = 'app.title=Lêerbestuurder\ngreeting=Hallo, {0}!\nquit=Verlaat\n'


@pytest.fixture
def config_dir(monkeypatch, tmp_path):
    config = tmp_path / 'config'
    config.mkdir()
    monkeypatch.setattr('virtaal.common.pan_app.get_config_dir', lambda: str(config))
    return config


@pytest.fixture
def project(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'en.properties').write_text(EN, encoding='utf-8')
    (project / 'af.properties').write_text(AF, encoding='utf-8')
    return project


def _tm(path, store, units):
    """A local TM at C{path} holding C{units} ((key, source, target)) as
    saved from C{store}."""
    db = tmdb.TMDB(str(path))
    db.sync_store(
        store,
        [{'source': s, 'target': t, 'key': k} for k, s, t in units],
        'en',
        'af',
    )
    for connection, _cursor in tmdb.TMDB._tm_dbs.pop(str(path), {}).values():
        connection.close()
    return str(path)


SAVED = [
    ('app.title', 'File Manager', 'Lêerbestuurder'),
    ('greeting', 'Hello!', 'Hallo, {0}!'),
    ('quit', 'Quit', 'Verlaat'),
]


def test_the_local_tm_gives_the_source_each_translation_had(tmp_path, project):
    target = str(project / 'af.properties')
    db = _tm(tmp_path / 'tm.db', os.path.realpath(target), SAVED)

    found = sourcehistory.from_local_tm(
        db, target, {'app.title': 'Lêerbestuurder', 'greeting': 'Hallo, {0}!'}
    )

    assert found == {'app.title': 'File Manager', 'greeting': 'Hello!'}


def test_a_moved_file_is_found_by_its_name(tmp_path, project):
    db = _tm(tmp_path / 'tm.db', os.path.join(str(tmp_path), 'old', 'af.properties'), SAVED)

    found = sourcehistory.from_local_tm(db, str(project / 'af.properties'), {'quit': 'Verlaat'})

    assert found == {'quit': 'Quit'}


def test_a_translation_the_tm_never_saw_is_not_recalled(tmp_path, project):
    target = str(project / 'af.properties')
    db = _tm(tmp_path / 'tm.db', target, SAVED)

    found = sourcehistory.from_local_tm(
        db, target, {'quit': 'Maak toe', 'other': 'Ander', 'app.title': 'Lêerbestuurder'}
    )

    assert found == {'app.title': 'File Manager'}


def test_another_files_entries_are_left_alone(tmp_path, project):
    db = _tm(tmp_path / 'tm.db', os.path.join(str(tmp_path), 'other.properties'), SAVED)

    assert (
        sourcehistory.from_local_tm(db, str(project / 'af.properties'), {'quit': 'Verlaat'}) == {}
    )


def test_no_or_an_unreadable_local_tm_recalls_nothing(tmp_path, project):
    target = str(project / 'af.properties')
    (tmp_path / 'broken.db').write_bytes(b'not a database')

    assert sourcehistory.from_local_tm(None, target, {'quit': 'Verlaat'}) == {}
    assert sourcehistory.from_local_tm(str(tmp_path / 'broken.db'), target, {'quit': 'x'}) == {}
    assert sourcehistory.from_local_tm(str(tmp_path / 'none.db'), target, {'quit': 'x'}) == {}


def test_reading_the_local_tm_leaves_it_unchanged(tmp_path, project):
    target = str(project / 'af.properties')
    db = _tm(tmp_path / 'tm.db', target, SAVED)
    before = sorted(os.listdir(tmp_path)), (tmp_path / 'tm.db').read_bytes()

    sourcehistory.from_local_tm(db, target, {'quit': 'Verlaat'})

    assert (sorted(os.listdir(tmp_path)), (tmp_path / 'tm.db').read_bytes()) == before


def test_the_local_tm_is_where_its_settings_say(config_dir):
    assert sourcehistory.local_tm_path() is None
    (config_dir / 'tm.db').write_bytes(b'')
    assert sourcehistory.local_tm_path() == str(config_dir / 'tm.db')
    (config_dir / 'tm.ini').write_text('[localtm]\ntmdb = other.db\n', encoding='utf-8')
    (config_dir / 'other.db').write_bytes(b'')
    assert sourcehistory.local_tm_path() == str(config_dir / 'other.db')


def test_a_first_open_flags_a_changed_source_from_the_local_tm(config_dir, project):
    target = str(project / 'af.properties')
    _tm(config_dir / 'tm.db', os.path.join(str(config_dir), 'moved', 'af.properties'), SAVED)

    pair = MonolingualPair(target, str(project / 'en.properties'), recall=sourcehistory.recall)

    units = dict(pair.units())
    assert {key: state_name(unit) for key, unit in units.items()} == {
        # The same source as when translated: a first open's review.
        'app.title': 'needs-review',
        'greeting': 'needs-work',
        'quit': 'needs-review',
    }
    assert units['greeting'].getnotes('translator') == 'Previous source: Hello!'
    assert pair.reviews['greeting'].previous_source == 'Hello!'


def test_a_failing_recall_leaves_a_plain_first_open(project, caplog):
    def recall(_pair, _targets):
        raise RuntimeError('broken')

    pair = MonolingualPair(
        str(project / 'af.properties'), str(project / 'en.properties'), recall=recall
    )

    assert {state_name(unit) for _key, unit in pair.units()} == {'needs-review'}
    assert "Couldn't recall" in caplog.text


def test_a_baseline_wins_over_recalling(project):
    target, template = str(project / 'af.properties'), str(project / 'en.properties')
    MonolingualPair(target, template).save()
    calls = []

    MonolingualPair(target, template, recall=lambda *args: calls.append(args) or {})

    assert calls == []


# Git #

needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='no git')


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repository of its own, untouched by the user's git settings."""
    empty = tmp_path / 'gitconfig'
    empty.write_text('', encoding='utf-8')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(empty))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.name', 'Test')
    _git(repo, 'config', 'user.email', 'test@example.com')
    _git(repo, 'config', 'commit.gpgsign', 'false')
    return repo


def _git(repo, *args):
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)


def _commit(repo, files, message):
    for name, text in files.items():
        (repo / name).write_text(text, encoding='utf-8')
    _git(repo, 'add', *files)
    _git(repo, 'commit', '-q', '-m', message)


def _history(repo):
    """greeting's English changes after it was translated, quit is
    translated again after, and app.title's translation isn't committed."""
    _commit(
        repo,
        {
            'en.properties': 'app.title=File Manager\ngreeting=Hello!\nquit=Quit\n',
            'af.properties': 'app.title=Lêers\ngreeting=Hallo!\nquit=Verlaat\n',
        },
        'Translate',
    )
    _commit(
        repo,
        {'en.properties': 'app.title=File Manager\ngreeting=Hello, {0}!\nquit=Close\n'},
        'Change the English',
    )
    _commit(
        repo,
        {'af.properties': 'app.title=Lêers\ngreeting=Hallo!\nquit=Maak toe\n'},
        'Translate quit again',
    )
    (repo / 'af.properties').write_text(
        'app.title=Lêerbestuurder\ngreeting=Hallo!\nquit=Maak toe\n', encoding='utf-8'
    )


TARGETS = {'app.title': 'Lêerbestuurder', 'greeting': 'Hallo!', 'quit': 'Maak toe'}


def _from_git(repo, targets=TARGETS, **kwargs):
    return sourcehistory.from_git(
        str(repo / 'af.properties'),
        str(repo / 'en.properties'),
        PropertiesConverter(),
        targets,
        **kwargs,
    )


@needs_git
def test_git_gives_the_template_as_of_each_keys_last_change(repo):
    _history(repo)

    assert _from_git(repo) == {'greeting': 'Hello!', 'quit': 'Close'}


@needs_git
def test_git_reads_but_never_changes_the_repository(repo):
    _history(repo)
    status = subprocess.run(
        ['git', '-C', str(repo), 'status', '--porcelain'], capture_output=True, check=True
    ).stdout
    files = sorted(p.relative_to(repo) for p in repo.rglob('*'))

    _from_git(repo)

    assert sorted(p.relative_to(repo) for p in repo.rglob('*')) == files
    assert (
        subprocess.run(
            ['git', '-C', str(repo), 'status', '--porcelain'], capture_output=True, check=True
        ).stdout
        == status
    )


@needs_git
def test_a_key_the_target_had_in_its_first_commit(repo):
    _commit(
        repo,
        {'en.properties': 'quit=Quit\n', 'af.properties': 'quit=Verlaat\n'},
        'Translate',
    )

    assert _from_git(repo, {'quit': 'Verlaat'}) == {'quit': 'Quit'}


@needs_git
def test_a_change_to_any_line_of_a_message_counts(repo):
    _commit(
        repo,
        {'en.properties': 'about=One\n', 'af.properties': 'about=Een \\\n  twee\n'},
        'Translate',
    )
    _commit(repo, {'en.properties': 'about=One two\n'}, 'Change the English')
    _commit(repo, {'af.properties': 'about=Een \\\n  twee drie\n'}, 'Translate again')

    assert _from_git(repo, {'about': 'Een twee drie'}) == {'about': 'One two'}


@needs_git
def test_a_template_not_yet_committed_with_the_target_gives_nothing(repo):
    _commit(repo, {'af.properties': 'quit=Verlaat\n'}, 'Translate')
    (repo / 'en.properties').write_text('quit=Quit\n', encoding='utf-8')

    assert _from_git(repo, {'quit': 'Verlaat'}) == {}


@needs_git
def test_a_folder_outside_git_gives_nothing(tmp_path, project):
    found = sourcehistory.from_git(
        str(project / 'af.properties'),
        str(project / 'en.properties'),
        PropertiesConverter(),
        {'quit': 'Verlaat'},
    )

    assert found == {}


@needs_git
def test_a_template_outside_the_repository_gives_nothing(repo, project):
    _history(repo)

    found = sourcehistory.from_git(
        str(repo / 'af.properties'),
        str(project / 'en.properties'),
        PropertiesConverter(),
        TARGETS,
    )

    assert found == {}


@needs_git
def test_git_gives_up_when_out_of_time(repo):
    _history(repo)

    assert _from_git(repo, seconds=0) == {}


def test_no_git_gives_nothing(project, monkeypatch):
    monkeypatch.setattr(sourcehistory.shutil, 'which', lambda _name: None)

    assert _from_git(project) == {}


def test_apples_git_stub_is_never_run_without_the_command_line_tools(monkeypatch):
    from virtaal.common.platform import platform

    ran = []

    def run(command, **_kwargs):
        ran.append(command[0])
        raise subprocess.CalledProcessError(2, command)

    monkeypatch.setattr(platform, 'is_mac', True)
    monkeypatch.setattr(sourcehistory.shutil, 'which', lambda _name: '/usr/bin/git')
    monkeypatch.setattr(sourcehistory.os.path, 'realpath', lambda path: path)
    monkeypatch.setattr(sourcehistory.subprocess, 'run', run)

    assert sourcehistory._git() is None
    assert ran == ['xcode-select']


def test_apples_git_stub_needs_the_tools_folder_it_names(tmp_path, monkeypatch):
    from virtaal.common.platform import platform

    def run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, os.fsencode(missing) + b'\n', b'')

    missing = str(tmp_path / 'CommandLineTools')
    monkeypatch.setattr(platform, 'is_mac', True)
    monkeypatch.setattr(sourcehistory.shutil, 'which', lambda _name: '/usr/bin/git')
    monkeypatch.setattr(sourcehistory.os.path, 'realpath', lambda path: path)
    monkeypatch.setattr(sourcehistory.subprocess, 'run', run)

    assert sourcehistory._git() is None
    os.mkdir(missing)
    assert sourcehistory._git() == '/usr/bin/git'


@needs_git
def test_a_first_open_flags_a_source_changed_since_its_commit(config_dir, repo):
    _history(repo)

    pair = MonolingualPair(
        str(repo / 'af.properties'), str(repo / 'en.properties'), recall=sourcehistory.recall
    )

    units = dict(pair.units())
    assert {key: state_name(unit) for key, unit in units.items()} == {
        'app.title': 'needs-review',
        'greeting': 'needs-work',
        # Translated again for the English it has now.
        'quit': 'needs-review',
    }
    assert units['greeting'].getnotes('translator') == 'Previous source: Hello!'
    assert units['quit'].getnotes('translator') == ''


@needs_git
def test_the_local_tm_comes_before_git(config_dir, repo):
    _history(repo)
    _tm(
        config_dir / 'tm.db',
        os.path.realpath(str(repo / 'af.properties')),
        [('greeting', 'Hi!', 'Hallo!')],
    )

    pair = MonolingualPair(
        str(repo / 'af.properties'), str(repo / 'en.properties'), recall=sourcehistory.recall
    )

    notes = {key: unit.getnotes('translator') for key, unit in pair.units()}
    assert notes == {'app.title': '', 'greeting': 'Previous source: Hi!', 'quit': ''}


def test_a_shared_budget_stops_asking_git_once_spent(project, monkeypatch):
    asked = []
    monkeypatch.setattr(sourcehistory, 'from_git', lambda *args: asked.append(args[-1]) or {})
    pair = MonolingualPair(str(project / 'af.properties'), str(project / 'en.properties'))

    sourcehistory.budgeted(10)(pair, {'quit': 'Verlaat'})
    sourcehistory.budgeted(0)(pair, {'quit': 'Verlaat'})

    assert len(asked) == 1 and 0 < asked[0] <= sourcehistory.GIT_SECONDS
