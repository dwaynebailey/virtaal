#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The glossaries kept of a project in a language (glossary.py): where
they go, what a refresh keeps, writes and drops, and when to ask again.
No server: a session stands in."""

import os
from types import SimpleNamespace

import pytest

from virtaal.common import pan_app
from virtaal.support.platforms import Glossary, glossary, workcopy

SERVER = 'https://weblate.example.org/api/'


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(glossary.Glossaries, 'clock', staticmethod(lambda: now[0]))
    return now


class Session:
    """Gives found, and keeps what it was asked with."""

    def __init__(self, found=None, error=None):
        self.found = found or []
        self.error = error
        self.asked = []

    def glossaries(self, project, language, known, on_done, on_error):
        self.asked.append((project, language, known))
        if self.error is not None:
            on_error(self.error)
        else:
            on_done(self.found)


def _kept(project='hello', language='af'):
    return glossary.Glossaries('weblate', SERVER, project, language)


def _refresh(kept, session):
    result = []
    kept.refresh(session, result.append, lambda e: result.append(('error', e)))
    return result[0]


def test_a_projects_glossaries_in_a_language_have_their_own_folder():
    folders = {
        _kept().folder,
        _kept(language='de').folder,
        _kept(project='docs').folder,
    }

    assert len(folders) == 3
    assert all(f.startswith(workcopy.platforms_dir() + os.sep) for f in folders)
    assert glossary.FOLDER in _kept().folder.split(os.sep)


def test_a_work_copy_uses_its_projects_glossaries_in_its_language():
    copy = SimpleNamespace(
        record={'platform': 'weblate', 'server': SERVER, 'project': 'hello', 'language': 'af'}
    )

    assert glossary.for_copy(copy).folder == _kept().folder


def test_nothing_kept_is_due_and_has_no_files(clock):
    kept = _kept()

    assert kept.files() == [] and kept.known() == {} and kept.due()


def test_a_refresh_keeps_the_files_and_their_validators(clock):
    session = Session([Glossary('glossary', 'Glossary', b'<martif/>', 'tbx', 'Mon, 02 Nov')])

    assert _refresh(_kept(), session) is True

    kept = _kept()
    assert [os.path.basename(p) for p in kept.files()] == ['glossary.tbx']
    assert kept.known() == {'glossary': 'Mon, 02 Nov'}
    assert not kept.due()
    clock[0] += glossary.REFRESH_AGE + 1
    assert kept.due()


def test_the_server_is_asked_with_the_validators_kept(clock):
    _refresh(_kept(), Session([Glossary('glossary', 'Glossary', b'x', 'tbx', 'v1')]))
    session = Session([Glossary('glossary', 'Glossary', None, 'tbx', 'v1')])

    assert _refresh(_kept(), session) is False

    assert session.asked == [('hello', 'af', {'glossary': 'v1'})]
    assert len(_kept().files()) == 1


def test_a_glossary_gone_from_the_server_is_dropped(clock):
    _refresh(_kept(), Session([Glossary('glossary', 'Glossary', b'x', 'tbx', 'v1')]))
    path = _kept().files()[0]

    assert _refresh(_kept(), Session([])) is True

    assert _kept().files() == [] and not os.path.exists(path)


def test_a_glossary_in_another_format_replaces_its_old_file(clock):
    _refresh(_kept(), Session([Glossary('glossary', 'Glossary', b'x', 'tbx', 'v1')]))

    _refresh(_kept(), Session([Glossary('glossary', 'Glossary', b'y', 'po', 'v2')]))

    assert [os.path.basename(p) for p in _kept().files()] == ['glossary.po']
    assert sorted(os.listdir(_kept().folder)) == ['.glossaries.json', 'glossary.po']


def test_a_category_slug_makes_one_file(clock):
    _refresh(_kept(), Session([Glossary('terms/ui', 'UI terms', b'x', 'tbx', None)]))

    assert [os.path.basename(p) for p in _kept().files()] == ['terms-ui.tbx']


def test_no_reply_keeps_what_was_kept(clock):
    _refresh(_kept(), Session([Glossary('glossary', 'Glossary', b'x', 'tbx', 'v1')]))
    clock[0] += glossary.REFRESH_AGE + 1

    result = _refresh(_kept(), Session(error='no network'))

    assert result == ('error', 'no network')
    assert len(_kept().files()) == 1 and _kept().due()


def test_a_broken_record_is_as_none_kept(clock):
    os.makedirs(_kept().folder)
    with open(os.path.join(_kept().folder, glossary.RECORD), 'w', encoding='utf-8') as f:
        f.write('{not json')

    assert _kept().files() == [] and _kept().due()
