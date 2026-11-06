#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

import pytest

from virtaal.models.monolingual import MonolingualPair
from virtaal.support import baseline
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


def _reviewed(project):
    """Open the pair, mark every translation reviewed and save: the
    baseline then holds the files as they are."""
    pair = _open(project)
    for _key, unit in pair.units():
        unit.markfuzzy(False)
    pair.save()
    return pair


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
    for _key, unit in pair.units():
        unit.markfuzzy(False)
    _units(pair)['greeting'].target = 'Goeie dag, {0}!'
    pair.save()

    again = _open(project)

    assert {key: (u.target, _state(u)) for key, u in _units(again).items()} == {
        'app.title': ('Lêerbestuurder', 'translated'),
        'greeting': ('Goeie dag, {0}!', 'translated'),
        'quit': ('', 'untranslated'),
    }
    assert again.reviews == {}


def test_save_as_writes_the_new_file_in_the_same_format(project):
    pair = _open(project)

    pair.save(str(project / 'copy.properties'))

    assert (project / 'copy.properties').read_text(encoding='utf-8') == (
        '# The window title\napp.title=Lêerbestuurder\ngreeting=Hallo, {0}!\nquit=Quit\n'
    )
    assert pair.store.filename == str(project / 'copy.properties')
    assert (project / 'af.properties').read_text(encoding='utf-8') == AF


def test_the_baseline_is_kept_outside_the_project(project):
    _open(project)

    path = baseline.sidecar_path(str(project / 'af.properties'))
    assert os.path.isfile(path)
    assert not path.startswith(str(project))
    assert set(baseline.load(str(project / 'af.properties'))['units']) == {
        'app.title',
        'greeting',
        'quit',
    }


def test_a_changed_source_needs_review_and_shows_the_old_source(project):
    _reviewed(project)
    (project / 'en.properties').write_text(
        EN.replace('Hello, {0}!', 'Hello there, {0}!'), encoding='utf-8'
    )

    pair = _open(project)

    unit = _units(pair)['greeting']
    assert unit.isfuzzy()
    assert unit.target == 'Hallo, {0}!'
    assert 'Previous source: Hello, {0}!' in unit.getnotes('translator')
    assert not _units(pair)['app.title'].isfuzzy()


def test_a_review_still_shows_on_the_next_open(project):
    _reviewed(project)
    (project / 'en.properties').write_text(
        EN.replace('Hello, {0}!', 'Hello there, {0}!'), encoding='utf-8'
    )
    _open(project)

    unit = _units(_open(project))['greeting']

    assert unit.isfuzzy()
    assert 'Previous source: Hello, {0}!' in unit.getnotes('translator')


def test_a_reviewed_unit_is_settled_by_saving(project):
    _reviewed(project)
    (project / 'en.properties').write_text(
        EN.replace('Hello, {0}!', 'Hello there, {0}!'), encoding='utf-8'
    )
    pair = _open(project)
    unit = _units(pair)['greeting']
    unit.target = 'Hallo daar, {0}!'
    unit.markfuzzy(False)
    pair.save()

    unit = _units(_open(project))['greeting']

    assert not unit.isfuzzy()
    assert not unit.getnotes('translator')


def test_a_unit_left_for_review_stays_fuzzy_after_saving(project):
    _reviewed(project)
    (project / 'en.properties').write_text(
        EN.replace('Hello, {0}!', 'Hello there, {0}!'), encoding='utf-8'
    )
    _open(project).save()

    unit = _units(_open(project))['greeting']

    assert unit.isfuzzy()
    assert 'Previous source: Hello, {0}!' in unit.getnotes('translator')
    assert 'greeting=Hallo, {0}!' in (project / 'af.properties').read_text(encoding='utf-8')


def test_a_new_key_is_untranslated_and_a_removed_one_dropped(project):
    _reviewed(project)
    (project / 'en.properties').write_text(
        EN.replace('quit=Quit\n', 'help=Help\n'), encoding='utf-8'
    )

    pair = _open(project)
    units = _units(pair)
    pair.save()

    assert 'quit' not in units
    assert units['help'].target == ''
    assert not units['help'].isfuzzy()
    assert 'quit' not in baseline.load(str(project / 'af.properties'))['units']


def test_a_target_changed_outside_is_taken_and_flagged(project):
    _reviewed(project)
    (project / 'af.properties').write_text(
        AF.replace('Hallo, {0}!', 'Goeie môre, {0}!'), encoding='utf-8'
    )

    unit = _units(_open(project))['greeting']

    assert unit.target == 'Goeie môre, {0}!'
    assert unit.isfuzzy()
    assert 'Translation changed outside Virtaal' in unit.getnotes('translator')


def test_a_translation_added_outside_with_a_new_key_is_flagged(project):
    _reviewed(project)
    (project / 'en.properties').write_text(EN + 'help=Help\n', encoding='utf-8')
    (project / 'af.properties').write_text(AF + 'help=Hulp\n', encoding='utf-8')

    unit = _units(_open(project))['help']

    assert unit.target == 'Hulp'
    assert unit.isfuzzy()


def test_a_renamed_key_with_the_same_source_keeps_its_translation(project):
    _reviewed(project)
    (project / 'en.properties').write_text(
        EN.replace('app.title=', 'window.title='), encoding='utf-8'
    )

    pair = _open(project)

    assert _units(pair)['window.title'].target == 'Lêerbestuurder'
    assert pair.carried == 1
    # Until saved, the file still has the old key, so it carries over again.
    assert _units(_open(project))['window.title'].target == 'Lêerbestuurder'
    pair.save()
    text = (project / 'af.properties').read_text(encoding='utf-8')
    assert 'window.title=Lêerbestuurder' in text
    assert 'app.title' not in text


def test_first_open_marks_every_translation_for_review(project):
    (project / 'af.properties').write_text(AF + 'quit=Quit\n', encoding='utf-8')

    pair = _open(project)

    assert {key: _state(u) for key, u in _units(pair).items()} == {
        'app.title': 'fuzzy',
        'greeting': 'fuzzy',
        # The same as the English, but the target has it.
        'quit': 'fuzzy',
    }
    states = {
        key: e['state'] for key, e in baseline.load(str(project / 'af.properties'))['units'].items()
    }
    assert states == {'app.title': 'fuzzy', 'greeting': 'fuzzy', 'quit': 'fuzzy'}
    assert not any(unit.getnotes('translator') for unit in _units(pair).values())


def test_first_open_leaves_keys_the_target_lacks_untranslated(project):
    unit = _units(_open(project))['quit']

    assert (unit.target, unit.isfuzzy()) == ('', False)


def test_saving_a_first_open_unchanged_writes_the_targets_values(project):
    from virtaal.support.converters import PropertiesConverter

    af = str(project / 'af.properties')
    before = PropertiesConverter().values(af)

    _open(project).save()

    after = PropertiesConverter().values(af)
    assert {key: after[key] for key in before} == before


def test_an_untranslated_key_is_written_in_english_and_stays_untranslated(project):
    _open(project).save()

    assert 'quit=Quit\n' in (project / 'af.properties').read_text(encoding='utf-8')
    unit = _units(_open(project))['quit']
    assert (unit.target, unit.isfuzzy()) == ('', False)


def test_english_written_in_and_then_changed_outside_is_taken(project):
    _open(project).save()
    text = (project / 'af.properties').read_text(encoding='utf-8')
    (project / 'af.properties').write_text(
        text.replace('quit=Quit', 'quit=Verlaat'), encoding='utf-8'
    )

    unit = _units(_open(project))['quit']

    assert (unit.target, unit.isfuzzy()) == ('Verlaat', True)


def test_a_project_that_skips_untranslated_keys_is_remembered(project):
    target, template = str(project / 'af.properties'), str(project / 'en.properties')
    MonolingualPair(target, template, skip_untranslated=True).save()

    again = MonolingualPair(target, template)
    again.save()

    assert again.skip_untranslated is True
    assert 'quit' not in (project / 'af.properties').read_text(encoding='utf-8')
    assert baseline.load(target)['skip_untranslated'] is True


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
    for _key, unit in pair.units():
        unit.markfuzzy(False)
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
    assert again.reviews == {}


def test_not_a_monolingual_file(tmp_path):
    with pytest.raises(ValueError):
        MonolingualPair(str(tmp_path / 'af.po'), str(tmp_path / 'en.po'))
