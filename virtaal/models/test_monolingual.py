#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest

from virtaal.models.monolingual import MonolingualPair
from virtaal.support.converters import FluentConverter

EN = """\
# The window title
app.title=File Manager
greeting=Hello, {0}!
quit=Quit
"""
AF = 'app.title=Lêerbestuurder\ngreeting=Hallo, {0}!\n'


@pytest.fixture
def project(tmp_path):
    (tmp_path / 'en.properties').write_text(EN, encoding='utf-8')
    (tmp_path / 'af.properties').write_text(AF, encoding='utf-8')
    return tmp_path


def _open(project):
    return MonolingualPair(str(project / 'af.properties'), str(project / 'en.properties'))


def _units(pair):
    return dict(pair.units())


def _state(unit):
    if unit.isfuzzy():
        return 'fuzzy'
    return 'translated' if unit.target else 'untranslated'


def test_the_pair_translates_from_the_template(project):
    pair = _open(project)

    assert {key: (u.source, u.target) for key, u in _units(pair).items()} == {
        'app.title': ('File Manager', 'Lêerbestuurder'),
        'greeting': ('Hello, {0}!', 'Hallo, {0}!'),
        'quit': ('Quit', ''),
    }
    assert pair.store.gettargetlanguage() == 'af'
    assert pair.store.getsourcelanguage() == 'en'
    assert pair.store.filename == str(project / 'af.properties')


def test_opening_writes_nothing_into_the_project(project):
    before = {p.name: p.read_bytes() for p in project.iterdir()}

    _open(project)

    assert {p.name: p.read_bytes() for p in project.iterdir()} == before


def test_save_writes_only_the_target_in_its_own_format(project):
    pair = _open(project)
    _units(pair)['quit'].target = 'Verlaat'

    pair.save()

    assert sorted(p.name for p in project.iterdir()) == ['af.properties', 'en.properties']
    assert (project / 'en.properties').read_text(encoding='utf-8') == EN
    assert (project / 'af.properties').read_text(encoding='utf-8') == (
        '# The window title\napp.title=Lêerbestuurder\ngreeting=Hallo, {0}!\nquit=Verlaat\n'
    )


def test_round_trip_open_edit_save_reopen(project):
    pair = _open(project)
    _units(pair)['greeting'].target = 'Goeie dag, {0}!'
    pair.save()

    again = _open(project)

    assert {key: (u.target, _state(u)) for key, u in _units(again).items()} == {
        'app.title': ('Lêerbestuurder', 'translated'),
        'greeting': ('Goeie dag, {0}!', 'translated'),
        'quit': ('', 'untranslated'),
    }


def test_save_as_writes_the_new_file_in_the_same_format(project):
    pair = _open(project)

    pair.save(str(project / 'copy.properties'))

    assert (project / 'copy.properties').read_text(encoding='utf-8') == (
        '# The window title\napp.title=Lêerbestuurder\ngreeting=Hallo, {0}!\n'
    )
    assert pair.store.filename == str(project / 'copy.properties')
    assert (project / 'af.properties').read_text(encoding='utf-8') == AF


@pytest.mark.skipif(
    not FluentConverter().available(), reason='translate-toolkit before 3.18 has no fluent2po'
)
def test_a_fluent_pair_round_trips(tmp_path):
    (tmp_path / 'en-US').mkdir()
    (tmp_path / 'af').mkdir()
    (tmp_path / 'en-US' / 'main.ftl').write_text(
        '# Title\napp-title = File Manager\nquit = Quit\n', encoding='utf-8'
    )
    (tmp_path / 'af' / 'main.ftl').write_text('app-title = Lêerbestuurder\n', encoding='utf-8')
    target = str(tmp_path / 'af' / 'main.ftl')
    pair = MonolingualPair(target, str(tmp_path / 'en-US' / 'main.ftl'))
    assert pair.store.getsourcelanguage() == 'en_US'
    _units(pair)['quit'].target = 'Verlaat'

    pair.save()

    assert (tmp_path / 'af' / 'main.ftl').read_text(encoding='utf-8') == (
        '# Title\napp-title = Lêerbestuurder\nquit = Verlaat\n'
    )
    again = MonolingualPair(target, str(tmp_path / 'en-US' / 'main.ftl'))
    assert {key: u.target for key, u in _units(again).items()} == {
        'app-title': 'Lêerbestuurder',
        'quit': 'Verlaat',
    }


def test_not_a_monolingual_file(tmp_path):
    with pytest.raises(ValueError):
        MonolingualPair(str(tmp_path / 'af.po'), str(tmp_path / 'en.po'))
