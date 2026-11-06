#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from types import SimpleNamespace

import pytest
from gi.repository import GLib
from translate.filters import checks
from translate.storage import factory

from virtaal.models.fileset import FileSet
from virtaal.models.langmodel import LanguageModel
from virtaal.models.streammodel import PeekUnit, StreamModel
from virtaal.support import statsdb, statsworker


class _FakeController:
    """What StoreModel needs from a StoreController to save a PO file."""

    main_controller = SimpleNamespace(
        get_translator_details=lambda: ('Tester', 'tester@example.org', ''),
        lang_controller=SimpleNamespace(target_lang=LanguageModel('af')),
        checks_controller=SimpleNamespace(code=None),
    )


def _po(path, units):
    body = ''.join('msgid "%s"\nmsgstr "%s"\n\n' % unit for unit in units)
    path.write_text('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n' + body)
    return str(path)


@pytest.fixture
def stream(tmp_path):
    _po(tmp_path / 'a.po', [('One', ''), ('Two', 'Twee')])
    _po(tmp_path / 'b.po', [('Three', ''), ('Four', 'Vier'), ('Five', '')])
    (tmp_path / 'empty.po').write_text('msgid ""\nmsgstr ""\n')
    _po(tmp_path / 'c.po', [('Six', 'Ses')])
    model = StreamModel(FileSet.from_paths([str(tmp_path)]), _FakeController())
    yield model
    statsworker.wait()


def _drain():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def test_the_units_of_every_file_follow_each_other(stream):
    assert len(stream) == 6
    assert [u.source for u in stream] == ['One', 'Two', 'Three', 'Four', 'Five', 'Six']
    assert [entry.display_name for _offset, entry in stream.file_offsets()] == [
        'a.po',
        'b.po',
        'c.po',
    ]
    assert [offset for offset, _entry in stream.file_offsets()] == [0, 2, 5]
    assert [e.display_name for e in stream.skipped] == ['empty.po']


def test_only_the_first_file_is_read_at_first(stream):
    assert [s.loaded for s in stream.segments] == [True, False, False]
    assert isinstance(stream.peek_unit(3), PeekUnit)
    assert stream.peek_unit(3).target == 'Vier'
    assert stream.peek_unit(3).istranslated()
    assert not stream.peek_unit(2).istranslated()


def test_getting_a_unit_reads_its_file_and_says_so(stream):
    loaded = []
    stream.connect('file-loaded', lambda _m, number, peek: loaded.append((number, peek)))

    unit = stream[3]

    assert unit.source == 'Four' and not isinstance(unit, PeekUnit)
    assert stream.segments[1].loaded
    ((number, peek),) = loaded
    assert number == 1
    assert [u.source for u in peek] == ['Three', 'Four', 'Five']
    assert stream.index_of(unit) == 3
    assert stream.index_of(peek[1]) is None


def test_the_cursor_nearing_a_file_end_reads_the_next_file_later(stream):
    assert stream.approach(1) is False
    assert not stream.segments[1].loaded
    _drain()
    assert stream.segments[1].loaded
    assert stream.approach(2) is True
    assert stream.filename == stream.segments[1].path


def test_stats_are_in_stream_indices(stream):
    assert stream.stats['total'] == [0, 1, 2, 3, 4, 5]
    assert stream.stats['translated'] == [1, 3, 5]
    assert stream.stats['untranslated'] == [0, 2, 4]

    stream[2].target = 'Drie'
    assert stream.update_unit_stats(2)

    assert stream.stats['translated'] == [1, 2, 3, 5]
    assert stream.update_stats()['translated'] == [1, 2, 3, 5]


def test_checks_are_in_stream_indices(stream):
    stream[4].target = 'vyf.'
    stream.update_unit_checks(4)

    found = stream.update_checks(checks.StandardChecker())

    assert found['check-endpunc'] == [4]
    assert found['check-startcaps'] == [4]


def _checker():
    checker = checks.StandardChecker()
    checker.config.updatetargetlanguage('af')
    return checker


def _check_later(monkeypatch):
    """check_in_background() that records what it was asked to check."""
    asked = []

    def check_in_background(filenames, checker, done):
        asked.append((filenames, done))
        return True

    monkeypatch.setattr(statsworker, 'check_in_background', check_in_background)
    return asked


def test_checks_of_files_not_read_yet_are_left_to_a_child(stream, tmp_path, monkeypatch):
    asked = _check_later(monkeypatch)
    _po(tmp_path / 'c.po', [('Six.', 'ses')])
    stream.load_segment(1)

    found = stream.update_checks(_checker())

    assert [paths for paths, _done in asked] == [[str(tmp_path / 'c.po')]]
    assert stream.checks_pending == 1
    assert 5 not in found.get('check-endpunc', [])


def test_the_files_a_child_checked_join_the_stream_checks(stream, tmp_path, monkeypatch):
    asked = _check_later(monkeypatch)
    path = _po(tmp_path / 'c.po', [('Six.', 'ses')])
    stream.load_segment(1)
    checker = _checker()
    stream.update_checks(checker)
    filled = []
    stream.connect('checks-filled', lambda model: filled.append(model.checks_pending))

    # What the child does.
    statsdb.StatsCache().filechecks(path, checker)
    asked[0][1](True)
    found = stream.update_checks()

    assert filled == [0]
    assert len(asked) == 1
    assert found['check-endpunc'] == [5]
    assert stream.checks_pending == 0


def test_files_a_child_couldnt_check_are_checked_here(stream, tmp_path, monkeypatch):
    asked = _check_later(monkeypatch)
    _po(tmp_path / 'c.po', [('Six.', 'ses')])
    stream.load_segment(1)
    stream.update_checks(_checker())

    asked[0][1](False)
    found = stream.update_checks()

    assert len(asked) == 1
    assert found['check-endpunc'] == [5]


def test_save_writes_only_the_modified_files(stream, tmp_path):
    before = (tmp_path / 'c.po').stat().st_mtime_ns
    stream[2].target = 'Drie'
    stream.mark_modified(2)

    assert [e.display_name for e in stream.modified_entries()] == ['b.po']
    stream.save_file()

    assert factory.getobject(str(tmp_path / 'b.po')).units[1].target == 'Drie'
    assert (tmp_path / 'c.po').stat().st_mtime_ns == before
    assert stream.modified_entries() == []
    assert stream.file_parts()[1][0] == str(tmp_path / 'b.po')


def test_save_as_is_refused(stream):
    with pytest.raises(ValueError):
        stream.save_file('/tmp/elsewhere.po')


def test_segment_counts_follow_the_stream_stats(stream):
    assert stream.segment_counts() == [(1, 0, 2), (1, 0, 3), (1, 0, 1)]

    stream.get_unit(0).target = 'Een'
    stream.update_unit_stats(0)

    assert stream.segment_counts()[0] == (2, 0, 2)


def test_the_language_picked_is_the_target_language(stream):
    stream.language = 'zu'

    assert stream.get_target_language() == 'zu'
