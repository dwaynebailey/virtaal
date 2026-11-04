#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os
from pathlib import Path

import pytest
from translate.storage import po

from virtaal.support import priorityfile

REPO_PRIORITIES = Path(__file__).resolve().parents[2] / 'po' / 'virtaal.priorities.yaml'

SMALL = """\
format: 1
priorities:
- id: b
  name: Second
  order: 2
- id: a
  name: First
  order: 1
  description: The most important.
domains:
  app:
    a:
    - Open
    - "menu\\x04Quit"
    b:
    - '%d file'
    - Open
  other:
    a:
    - Unrelated
"""


def _write(path, text=SMALL):
    path.write_text(text, encoding='utf-8')
    return str(path)


# load() #


def test_load_reads_virtaals_own_priority_file():
    priority_file = priorityfile.load(REPO_PRIORITIES)

    assert [level.id for level in priority_file.levels] == ['1', '1~', '2', '3', 'x']
    assert priority_file.levels[0].name == 'Level 1 core'
    assert 'virtaal' in priority_file.domains
    assert any('\x04' in key for keys in priority_file.domains['gtk30'].values() for key in keys)


def test_load_orders_the_levels_by_order(tmp_path):
    priority_file = priorityfile.load(_write(tmp_path / 'app.priorities.yaml'))

    assert [(level.id, level.name) for level in priority_file.levels] == [
        ('a', 'First'),
        ('b', 'Second'),
    ]
    assert priority_file.levels[0].description == 'The most important.'
    assert priority_file.levels[1].description == ''


def test_load_accepts_numeric_ids(tmp_path):
    text = (
        'format: 1\npriorities:\n- {id: 1, name: One, order: 1}\ndomains:\n  app:\n    1: [Open]\n'
    )

    priority_file = priorityfile.load(_write(tmp_path / 'p.priorities.yaml', text))

    assert priority_file.domains == {'app': {'1': frozenset({'Open'})}}


@pytest.mark.parametrize(
    'text',
    [
        'format: 2\npriorities: [{id: a, name: A, order: 1}]\n',
        'priorities: [{id: a, name: A, order: 1}]\n',
        'format: 1\npriorities: []\n',
        'format: 1\npriorities: [{id: a, order: 1}]\n',
        'format: 1\npriorities: [{id: a, name: A}]\n',
        'format: 1\npriorities: [{id: a, name: A, order: 1}, {id: a, name: B, order: 2}]\n',
        'format: 1\npriorities: [{id: a, name: A, order: 1}]\ndomains: {app: {b: [Open]}}\n',
        'format: 1\npriorities: [{id: a, name: A, order: 1}]\ndomains: {app: {a: [yes]}}\n',
        'format: 1\npriorities: [{id: a, name: A, order: 1}]\ndomains: {app: [Open]}\n',
        'format: 1\npriorities: [unclosed\n',
        '- just\n- a list\n',
    ],
)
def test_load_rejects_a_malformed_file(tmp_path, caplog, text):
    path = _write(tmp_path / 'bad.priorities.yaml', text)

    assert priorityfile.load(path) is None
    assert 'Not using priority file' in caplog.text


def test_load_rejects_a_missing_file(tmp_path):
    assert priorityfile.load(str(tmp_path / 'none.priorities.yaml')) is None


# level_keys() #


def test_level_keys_picks_the_domain_sharing_most_keys(tmp_path):
    priority_file = priorityfile.load(_write(tmp_path / 'x.priorities.yaml'))

    assert priority_file.domain_for({'Open', 'Unrelated', '%d file'}) == 'app'
    assert priority_file.domain_for({'Unrelated'}) == 'other'
    assert priority_file.domain_for({'Nothing'}) is None


def test_level_keys_breaks_a_tie_for_the_files_own_domain(tmp_path):
    priority_file = priorityfile.load(_write(tmp_path / 'other.priorities.yaml'))

    assert priority_file.domain_for({'Open', 'Unrelated'}) == 'other'


def test_level_keys_puts_a_key_at_its_most_important_level(tmp_path):
    priority_file = priorityfile.load(_write(tmp_path / 'app.priorities.yaml'))

    assert priority_file.level_keys({'Open'}) == {
        'a': {'Open', 'menu\x04Quit'},
        'b': {'%d file'},
    }


def test_level_keys_is_empty_for_a_file_sharing_nothing(tmp_path):
    priority_file = priorityfile.load(_write(tmp_path / 'app.priorities.yaml'))

    assert priority_file.level_keys({'Nothing'}) == {'a': set(), 'b': set()}


# unit_key() #


def test_unit_key_is_msgctxt_and_the_singular_msgid():
    store = po.pofile(
        b'msgctxt "menu"\nmsgid "Quit"\nmsgstr ""\n\n'
        b'msgid "%d file"\nmsgid_plural "%d files"\nmsgstr[0] ""\nmsgstr[1] ""\n\n'
        b'msgctxt "c"\nmsgid "%d dog"\nmsgid_plural "%d dogs"\nmsgstr[0] ""\nmsgstr[1] ""\n\n'
        b'msgid "Open"\nmsgstr ""\n'
    )

    assert [priorityfile.unit_key(unit) for unit in store.units] == [
        'menu\x04Quit',
        '%d file',
        'c\x04%d dog',
        'Open',
    ]


def test_unit_key_matches_virtaals_own_priority_file():
    store = po.pofile.parsefile(str(REPO_PRIORITIES.parent / 'virtaal.pot'))
    listed = set().union(*priorityfile.load(REPO_PRIORITIES).domains['virtaal'].values())

    keys = {priorityfile.unit_key(unit) for unit in store.units if not unit.isheader()}

    assert listed <= keys


# candidates() and find() #


def test_candidates_order(tmp_path):
    folder = tmp_path / 'po'
    folder.mkdir()
    translation = folder / 'af.po'
    translation.write_text('')
    for name in ('named.yaml', 'af.priorities.yaml', 'b.priorities.yaml', 'a.priorities.yaml'):
        (folder / name).write_text('')
    (tmp_path / 'up.priorities.yaml').write_text('')

    assert [
        os.path.relpath(path, tmp_path)
        for path in priorityfile.candidates(str(translation), 'named.yaml')
    ] == [
        os.path.join('po', 'named.yaml'),
        os.path.join('po', 'af.priorities.yaml'),
        os.path.join('po', 'a.priorities.yaml'),
        os.path.join('po', 'b.priorities.yaml'),
        'up.priorities.yaml',
    ]


def test_candidates_skips_a_header_naming_no_file(tmp_path):
    translation = tmp_path / 'af.po'
    translation.write_text('')

    assert priorityfile.candidates(str(translation), 'missing.yaml') == []


def test_find_finds_virtaals_own_file_beside_a_translation():
    priority_file = priorityfile.find(str(REPO_PRIORITIES.parent / 'af.po'))

    assert priority_file.path == str(REPO_PRIORITIES)


def test_find_skips_a_malformed_file(tmp_path):
    translation = tmp_path / 'af.po'
    translation.write_text('')
    _write(tmp_path / 'af.priorities.yaml', 'format: 99\n')
    good = _write(tmp_path / 'good.priorities.yaml')

    assert priorityfile.find(str(translation)).path == good


def test_find_is_none_without_a_priority_file(tmp_path):
    translation = tmp_path / 'af.po'
    translation.write_text('')

    assert priorityfile.find(str(translation)) is None
