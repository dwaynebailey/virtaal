#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

import pytest

from virtaal.models.monolingual import MonolingualPair, state_name
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
    return state_name(unit)


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
    assert _state(unit) == 'needs-work'
    assert unit.target == 'Hallo, {0}!'
    assert 'Previous source: Hello, {0}!' in unit.getnotes('translator')
    assert _state(_units(pair)['app.title']) == 'translated'


def test_a_review_still_shows_on_the_next_open(project):
    _reviewed(project)
    (project / 'en.properties').write_text(
        EN.replace('Hello, {0}!', 'Hello there, {0}!'), encoding='utf-8'
    )
    _open(project)

    unit = _units(_open(project))['greeting']

    assert _state(unit) == 'needs-work'
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

    assert _state(unit) == 'needs-work'
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
    assert _state(unit) == 'needs-review'
    assert 'Translation changed outside Virtaal' in unit.getnotes('translator')


def test_a_translation_added_outside_with_a_new_key_is_flagged(project):
    _reviewed(project)
    (project / 'en.properties').write_text(EN + 'help=Help\n', encoding='utf-8')
    (project / 'af.properties').write_text(AF + 'help=Hulp\n', encoding='utf-8')

    unit = _units(_open(project))['help']

    assert unit.target == 'Hulp'
    assert _state(unit) == 'needs-review'


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
        'app.title': 'needs-review',
        'greeting': 'needs-review',
        # The same as the English, but the target has it.
        'quit': 'needs-review',
    }
    states = {
        key: e['state'] for key, e in baseline.load(str(project / 'af.properties'))['units'].items()
    }
    assert states == {
        'app.title': 'needs-review',
        'greeting': 'needs-review',
        'quit': 'needs-review',
    }
    assert not any(unit.getnotes('translator') for unit in _units(pair).values())


def test_first_open_after_choosing_the_template_is_still_a_first_open(project):
    baseline.remember_template(str(project / 'af.properties'), str(project / 'en.properties'))

    pair = _open(project)

    assert {key: _state(u) for key, u in _units(pair).items()} == {
        'app.title': 'needs-review',
        'greeting': 'needs-review',
        'quit': 'untranslated',
    }
    assert not any(unit.getnotes('translator') for unit in _units(pair).values())
    assert baseline.load(str(project / 'af.properties'))['chosen']


def test_first_open_leaves_keys_the_target_lacks_untranslated(project):
    unit = _units(_open(project))['quit']

    assert (unit.target, unit.isfuzzy()) == ('', False)


def test_saving_a_first_open_unchanged_writes_the_targets_values(project):
    from virtaal.support.converters import PropertiesConverter

    af = str(project / 'af.properties')
    (project / 'af.properties').write_text(
        AF + 'quit=Hou  op <b>&amp; {0}\\nnou\n', encoding='utf-8'
    )
    before = PropertiesConverter().values(af)
    assert before['quit'] == 'Hou  op <b>&amp; {0}\nnou'

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

    assert (unit.target, _state(unit)) == ('Verlaat', 'needs-review')


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


def test_the_source_file_paired_with_itself_shows_its_text_untranslated(project):
    en = str(project / 'en.properties')

    pair = MonolingualPair(en, en)

    assert pair.source_only
    assert [(u.source, u.target) for _key, u in pair.units()] == [
        ('File Manager', ''),
        ('Hello, {0}!', ''),
        ('Quit', ''),
    ]
    assert pair.store.gettargetlanguage() in ('', None)
    assert baseline.load(en) is None


def test_the_source_file_is_never_saved_over(project):
    en = str(project / 'en.properties')
    pair = MonolingualPair(en, en)

    with pytest.raises(OSError):
        pair.save()

    assert (project / 'en.properties').read_text(encoding='utf-8') == EN


def test_the_source_file_saved_as_another_file_starts_a_translation(project):
    en = str(project / 'en.properties')
    pair = MonolingualPair(en, en)
    _units(pair)['quit'].target = 'Verlaat'

    pair.save(str(project / 'af_new.properties'))

    assert not pair.source_only
    assert 'quit=Verlaat' in (project / 'af_new.properties').read_text(encoding='utf-8')


def test_the_working_store_can_hold_needs_review(project):
    from translate.storage import xliff

    pair = _open(project)

    assert isinstance(pair.store, xliff.xlifffile)
    assert [key for key, _unit in pair.units()] == ['app.title', 'greeting', 'quit']
    assert 'The window title' in _units(pair)['app.title'].getnotes('developer')


def test_a_changed_source_wins_over_a_change_outside(project):
    _reviewed(project)
    (project / 'en.properties').write_text(
        EN.replace('Hello, {0}!', 'Hello there, {0}!'), encoding='utf-8'
    )
    text = (project / 'af.properties').read_text(encoding='utf-8')
    (project / 'af.properties').write_text(text.replace('Hallo,', 'Goeie dag,'), encoding='utf-8')

    unit = _units(_open(project))['greeting']

    assert _state(unit) == 'needs-work'
    notes = unit.getnotes('translator')
    assert 'Previous source: Hello, {0}!' in notes
    assert 'Translation changed outside Virtaal' in notes


def test_needs_review_is_restored_on_reopen(project):
    _open(project).save()

    states = {key: _state(u) for key, u in _units(_open(project)).items()}

    assert states == {
        'app.title': 'needs-review',
        'greeting': 'needs-review',
        'quit': 'untranslated',
    }


def test_the_working_units_ids_are_the_bare_keys(project):
    pair = _open(project)

    assert [unit.getid() for _key, unit in pair.units()] == ['app.title', 'greeting', 'quit']


def test_an_android_pair_leaves_untranslated_strings_out_unless_told(tmp_path):
    (tmp_path / 'values').mkdir()
    (tmp_path / 'values-af').mkdir()
    (tmp_path / 'values' / 'strings.xml').write_text(
        '<resources>\n    <string name="open">Open</string>\n'
        '    <string name="save">Save</string>\n</resources>\n',
        encoding='utf-8',
    )
    target = tmp_path / 'values-af' / 'strings.xml'
    target.write_text('<resources>\n    <string name="open">Maak oop</string>\n</resources>\n')

    pair = MonolingualPair(str(target), str(tmp_path / 'values' / 'strings.xml'))
    pair.save()

    assert 'save' not in target.read_text(encoding='utf-8')
    assert (
        MonolingualPair(
            str(target), str(tmp_path / 'values' / 'strings.xml'), skip_untranslated=False
        ).skip_untranslated
        is False
    )


# Plural messages

EN_I18NEXT = '{\n  "title": "Notes",\n  "item_one": "{{count}} note",\n  "item_other": "{{count}} notes"\n}\n'


def _i18next(tmp_path, target):
    for code, text in (('en', EN_I18NEXT), ('ru', target)):
        (tmp_path / code).mkdir(exist_ok=True)
        (tmp_path / code / 'common.json').write_text(text, encoding='utf-8')
    return str(tmp_path / 'ru' / 'common.json'), str(tmp_path / 'en' / 'common.json')


def _strings(value):
    return [str(form) for form in value.strings]


def _plural(pair, key='item'):
    return dict(pair.units())[key]


def test_an_i18next_plural_is_one_unit_in_the_target_s_cldr_forms(tmp_path):
    target, template = _i18next(tmp_path, '{"item_one": "{{count}} заметка"}')

    pair = MonolingualPair(target, template)
    unit = _plural(pair)

    assert unit.hasplural()
    assert _strings(unit.source) == ['{{count}} note', '{{count}} notes']
    assert _strings(unit.target) == ['{{count}} заметка', '', '', '']
    assert unit.plural_tags == ['one', 'few', 'many', 'other']
    # Not gettext's three Russian forms.
    assert pair.nplurals == 4
    assert [str(e) for e in unit.rich_target] == ['{{count}} заметка', '', '', '']


def test_a_plural_unit_saves_its_forms_under_their_keys_and_reopens_translated(tmp_path):
    import json

    target, template = _i18next(tmp_path, '{}')
    pair = MonolingualPair(target, template)
    unit = _plural(pair)
    unit.target = ['%d заметка', '%d заметки', '%d заметок', '%d заметки']
    unit.markfuzzy(False)
    pair.save()

    with open(target, encoding='utf-8') as f:
        saved = json.load(f)
    assert saved == {
        'title': 'Notes',
        'item_one': '%d заметка',
        'item_few': '%d заметки',
        'item_many': '%d заметок',
        'item_other': '%d заметки',
    }
    again = MonolingualPair(target, template)
    assert _strings(_plural(again).target) == _strings(unit.target)
    assert state_name(_plural(again)) == 'translated'
    assert again.reviews == {}


def test_a_plural_whose_english_changed_needs_work_and_shows_both_old_forms(tmp_path):
    target, template = _i18next(tmp_path, '{}')
    pair = MonolingualPair(target, template)
    _plural(pair).target = ['a', 'b', 'c', 'd']
    _plural(pair).markfuzzy(False)
    pair.save()
    with open(template, 'w', encoding='utf-8') as f:
        f.write(EN_I18NEXT.replace('notes"', 'notes in all"'))

    again = MonolingualPair(target, template)

    unit = _plural(again)
    assert state_name(unit) == 'needs-work'
    assert 'Previous source: {{count}} note\n{{count}} notes' in unit.getnotes('translator')


def test_a_baseline_with_one_entry_per_form_is_read_as_one_plural(tmp_path):
    from virtaal.support.baseline import TRANSLATED, entry

    forms = ['a', 'b', 'c', 'd']
    target, template = _i18next(
        tmp_path,
        '{"title": "Заметки", "item_one": "a", "item_few": "b", "item_many": "c", "item_other": "d"}',
    )
    sources = ['{{count}} note'] + ['{{count}} notes'] * 3
    units = {'title': entry('Notes', 'Заметки', TRANSLATED)}
    for category, form, source in zip(('one', 'few', 'many', 'other'), forms, sources, strict=True):
        units['item_' + category] = entry(source, form, TRANSLATED)
    baseline.save(target, {'template': template, 'units': units, 'skip_untranslated': False})

    pair = MonolingualPair(target, template)

    assert state_name(_plural(pair)) == 'translated'
    assert pair.reviews == {}


def test_an_android_plural_round_trips_in_russian(tmp_path):
    for folder in ('values', 'values-ru'):
        (tmp_path / folder).mkdir()
    template = tmp_path / 'values' / 'strings.xml'
    template.write_text(
        '<resources>\n    <plurals name="notes">\n'
        '        <item quantity="one">%d note</item>\n'
        '        <item quantity="other">%d notes</item>\n    </plurals>\n</resources>\n',
        encoding='utf-8',
    )
    target = tmp_path / 'values-ru' / 'strings.xml'
    target.write_text('<resources/>\n', encoding='utf-8')
    pair = MonolingualPair(str(target), str(template))
    unit = _plural(pair, 'notes')
    assert unit.plural_tags == ['one', 'few', 'many', 'other']
    unit.target = ['%d заметка', '%d заметки', '%d заметок', '%d заметки']

    pair.save()

    text = target.read_text(encoding='utf-8')
    assert '<item quantity="many">%d заметок</item>' in text
    assert text.count('<item ') == 4


def test_a_plural_unit_is_checked_form_by_form(tmp_path):
    from translate.filters import checks

    target, template = _i18next(tmp_path, '{}')
    unit = _plural(MonolingualPair(target, template))
    unit.target = ['{{count}} a', '{{count}} b', 'c', '{{count}} d']

    failures = checks.StandardChecker().run_filters(unit)

    assert failures


def test_an_ordinal_group_is_its_own_plural_with_ordinal_forms(tmp_path):
    (tmp_path / 'en').mkdir()
    (tmp_path / 'af').mkdir()
    (tmp_path / 'en' / 'a.json').write_text(
        '{"place_ordinal_one": "{{count}}st", "place_ordinal_two": "{{count}} (two)",'
        ' "place_ordinal_few": "{{count}}rd", "place_ordinal_other": "{{count}}th"}',
        encoding='utf-8',
    )
    (tmp_path / 'af' / 'a.json').write_text('{}', encoding='utf-8')

    pair = MonolingualPair(str(tmp_path / 'af' / 'a.json'), str(tmp_path / 'en' / 'a.json'))

    unit = _plural(pair, 'place_ordinal')
    # Afrikaans ordinals have one form.
    assert unit.plural_tags == ['other'] and unit.plural_ordinal
    assert _strings(unit.source) == ['{{count}}st', '{{count}}th']


# Dismissed checks kept in the target's comments (#3276) #

_COMMENTED = {
    'properties': (
        'en.properties',
        'af.properties',
        b'# Greeting\nhello=Hello.\nbye=Goodbye\n',
        b'hello=Hallo\nbye=Totsiens\n',
        b'# (dismissed-check) endpunc ',
    ),
    'fluent': (
        'en.ftl',
        'af.ftl',
        b'hello = Hello.\nbye = Goodbye\n',
        b'hello = Hallo\nbye = Totsiens\n',
        b'# (dismissed-check) endpunc ',
    ),
    'strings': (
        'en.strings',
        'af.strings',
        b'"hello" = "Hello.";\n"bye" = "Goodbye";\n',
        b'"hello" = "Hallo";\n"bye" = "Totsiens";\n',
        b'/* (dismissed-check) endpunc ',
    ),
    'android': (
        'values/strings.xml',
        'values-af/strings.xml',
        b'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        b'    <string name="hello">Hello.</string>\n'
        b'    <string name="bye">Goodbye</string>\n</resources>\n',
        b'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        b'    <string name="hello">Hallo</string>\n'
        b'    <string name="bye">Totsiens</string>\n</resources>\n',
        b'    <!-- (dismissed-check) endpunc ',
    ),
}


@pytest.mark.parametrize('kind', list(_COMMENTED))
def test_a_dismissed_check_is_kept_in_a_comment_before_the_key(tmp_path, kind):
    from virtaal.support import dismissedchecks

    template, target, template_data, target_data, comment = _COMMENTED[kind]
    for name, data in ((template, template_data), (target, target_data)):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_bytes(data)
    pair = MonolingualPair(str(tmp_path / target), str(tmp_path / template), language='af')

    dismissedchecks.set_dismissed(_units(pair)['hello'], 'endpunc')
    pair.save()

    saved = (tmp_path / target).read_bytes()
    before, after = saved.split(comment)
    assert after.split(b'\n', 1)[1].lstrip().startswith((b'hello', b'"hello"', b'<string'))
    again = MonolingualPair(str(tmp_path / target), str(tmp_path / template), language='af')
    assert dismissedchecks.dismissed(_units(again)['hello']) == {'endpunc'}
    assert dismissedchecks.dismissed(_units(again)['bye']) == set()
    again.save()
    assert (tmp_path / target).read_bytes() == saved
