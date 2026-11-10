#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import io
import json
import time

from gi.repository import GLib

from virtaal.support import setscan


def _po(path, units, language='af'):
    body = ''.join('msgid "%s"\nmsgstr "%s"\n\n' % unit for unit in units)
    path.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Language: %s\\n"\n\n%s' % (language, body),
        encoding='utf-8',
    )
    return str(path)


def _units(path):
    from translate.storage import factory

    return factory.getobject(path).units


def _scan(job):
    out = io.StringIO()
    setscan.scan(job, out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_check_units_reports_each_failure_with_its_index_and_position(tmp_path):
    path = _po(
        tmp_path / 'af.po',
        [('Open %s', 'Maak %d oop'), ('Close', ''), ('Save {name}', 'Stoor {naam}')],
    )
    checker = setscan.checker_for(None, 'af', setscan.DEFAULT_CHECKS)

    rows = list(setscan.check_units(_units(path), checker, setscan.DEFAULT_CHECKS))

    # The header is unit 0; the untranslated unit isn't checked.
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (1, 0, 'printf'),
        (3, 2, 'pythonbraceformat'),
    ]
    assert rows[0][4:] == ('Open %s', 'Maak %d oop')


def test_check_units_runs_only_the_checks_chosen(tmp_path):
    path = _po(tmp_path / 'af.po', [('Open %s <b>now</b>', 'Maak %d nou oop')])
    checker = setscan.checker_for(None, 'af', ['xmltags'])

    rows = list(setscan.check_units(_units(path), checker, ['xmltags']))

    assert [r[2] for r in rows] == ['xmltags']


def test_the_project_style_s_accelerators_are_checked(tmp_path):
    path = _po(tmp_path / 'af.po', [('_Open', 'Maak oop')])

    gnome = setscan.checker_for('gnome', 'af', ['accelerators'])
    default = setscan.checker_for('kde', 'af', ['accelerators'])

    assert [r[2] for r in setscan.check_units(_units(path), gnome, ['accelerators'])] == [
        'accelerators'
    ]
    assert list(setscan.check_units(_units(path), default, ['accelerators'])) == []


def test_every_default_check_is_one_a_checker_has():
    from translate.filters import checks

    assert set(setscan.DEFAULT_CHECKS) <= set(setscan.CHECKS)
    assert set(setscan.CHECKS) <= set(checks.StandardChecker().getfilters())


def test_search_units_lists_each_matching_unit_once(tmp_path):
    path = _po(
        tmp_path / 'af.po', [('File', 'Lêer'), ('Open file', 'Maak lêer oop'), ('Edit', 'Wysig')]
    )
    grep = setscan.make_grep({'text': 'lêer'})

    rows = list(setscan.search_units(_units(path), grep))

    assert [(r[0], r[1]) for r in rows] == [(1, 0), (2, 1)]


def test_search_follows_case_and_regex_choices(tmp_path):
    path = _po(tmp_path / 'af.po', [('File', 'Lêer'), ('file', 'lêer')])
    units = _units(path)

    case = setscan.make_grep({'text': 'File', 'case': True})
    regex = setscan.make_grep({'text': '^f', 'regex': True, 'case': True})

    assert [r[1] for r in setscan.search_units(units, case)] == [0]
    assert [r[1] for r in setscan.search_units(units, regex)] == [1]


def test_search_units_searches_only_the_parts_chosen(tmp_path):
    path = _po(tmp_path / 'af.po', [('Open', 'Maak oop'), ('Close', 'Open dit')])
    units = _units(path)

    def found(parts):
        grep = setscan.make_grep({'text': 'open', 'parts': parts})
        return [r[1] for r in setscan.search_units(units, grep)]

    assert found(['source']) == [0]
    assert found(['target']) == [1]
    assert found(['source', 'target']) == [0, 1]


def test_scan_writes_a_line_per_file_with_each_file_s_language(tmp_path):
    af = _po(tmp_path / 'af.po', [('Open %s', 'Maak %s oop')], 'af')
    de = _po(tmp_path / 'de.po', [('Open %s', 'Öffnen %d')], 'de')
    broken = tmp_path / 'broken.po'
    broken.write_text('msgid "x\n', encoding='utf-8')

    lines = _scan(
        {
            'kind': 'check',
            'files': [[af, 'af'], [de, 'de'], [str(broken), None]],
            'checker': None,
            'checks': ['printf'],
        }
    )

    assert [line['file'] for line in lines] == [0, 1, 2]
    assert lines[0]['results'] == []
    assert [r[2] for r in lines[1]['results']] == ['printf']
    assert 'error' in lines[2]


def test_a_search_scan_reads_every_file(tmp_path):
    af = _po(tmp_path / 'af.po', [('Open', 'Maak oop')], 'af')
    de = _po(tmp_path / 'de.po', [('Open', 'Öffnen')], 'de')

    lines = _scan({'kind': 'search', 'files': [[af, 'af'], [de, 'de']], 'search': {'text': 'öff'}})

    assert [len(line['results']) for line in lines] == [0, 1]


def _wait(condition, seconds=30):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    assert condition()


def test_a_scan_in_a_child_reports_each_file_then_finishes(tmp_path):
    files = [[_po(tmp_path / ('f%d.po' % n), [('Open %s', 'Maak %d oop')]), 'af'] for n in range(3)]
    seen = []
    done = []

    scan = setscan.Scan(
        {'kind': 'check', 'files': files, 'checker': None, 'checks': ['printf']},
        lambda number, results, error: seen.append((number, len(results), error)),
        done.append,
    )
    _wait(lambda: done)

    assert sorted(seen) == [(0, 1, None), (1, 1, None), (2, 1, None)]
    assert done == [True]
    assert not scan.running


def test_stopping_a_scan_reports_it_unfinished(tmp_path):
    files = [[_po(tmp_path / 'f.po', [('Open', 'Maak oop')]), 'af']] * 200
    done = []

    scan = setscan.Scan(
        {'kind': 'search', 'files': files, 'search': {'text': 'x'}}, lambda *a: None, done.append
    )
    scan.stop()

    assert done == [False]
    assert not scan.running
    scan.stop()
    assert done == [False]
