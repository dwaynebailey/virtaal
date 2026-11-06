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
from virtaal.support import statsworker


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


def test_the_language_picked_is_the_target_language(stream):
    stream.language = 'zu'

    assert stream.get_target_language() == 'zu'
