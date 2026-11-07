#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

import pytest

from virtaal.models.monolingual import MonolingualPair, state_name
from virtaal.support import sourcehistory, tmdb

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

    assert sourcehistory.from_local_tm(db, str(project / 'af.properties'), {'quit': 'Verlaat'}) == {}


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

    pair = MonolingualPair(
        target, str(project / 'en.properties'), recall=sourcehistory.recall
    )

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
