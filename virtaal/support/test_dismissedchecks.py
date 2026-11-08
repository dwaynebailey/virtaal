#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

import pytest
from translate.storage import properties, pypo, ts2, xliff

from virtaal.support import dismissedchecks

HEADER = b'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'


def _po(body=b'# A note\n#. For translators\nmsgid "Open."\nmsgstr "Oop"\n'):
    return pypo.pofile.parsestring(HEADER + body)


def _reloaded(store):
    return type(store).parsestring(bytes(store)).units[-1]


def test_a_dismissal_is_kept_in_the_po_file_beside_the_translators_note():
    store = _po()
    unit = store.units[-1]

    assert dismissedchecks.set_dismissed(unit, 'endpunc')

    text = bytes(store).decode()
    assert '# A note\n# (dismissed-check) endpunc ' in text
    assert '#. For translators\n' in text
    reloaded = _reloaded(store)
    assert dismissedchecks.dismissed(reloaded) == {'endpunc'}
    assert reloaded.getnotes('translator').splitlines()[0] == 'A note'


@pytest.mark.parametrize(
    'store',
    [
        xliff.xlifffile.parsestring(
            b'<?xml version="1.0"?><xliff version="1.2" '
            b'xmlns="urn:oasis:names:tc:xliff:document:1.2"><file original="a" '
            b'source-language="en" target-language="af" datatype="plaintext"><body>'
            b'<trans-unit id="1"><source>Open.</source><target>Oop</target></trans-unit>'
            b'</body></file></xliff>'
        ),
        ts2.tsfile.parsestring(
            b'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE TS><TS version="2.1" '
            b'language="af"><context><name>c</name><message><source>Open.</source>'
            b'<translation>Oop</translation></message></context></TS>'
        ),
    ],
    ids=['xliff', 'ts'],
)
def test_xliff_and_ts_keep_a_dismissal_as_a_translator_note(store):
    dismissedchecks.set_dismissed(store.units[-1], 'endpunc')

    assert dismissedchecks.dismissed(_reloaded(store)) == {'endpunc'}


def test_a_crlf_po_keeps_one_comment_per_line():
    crlf = (HEADER + b'# A note\nmsgid "Open."\nmsgstr "Oop"\n').replace(b'\n', b'\r\n')
    store = pypo.pofile.parsestring(crlf)
    unit = store.units[-1]
    dismissedchecks.set_dismissed(unit, 'endpunc')
    dismissedchecks.set_dismissed(unit, 'startcaps')

    assert dismissedchecks.dismissed(_reloaded(store)) == {'endpunc', 'startcaps'}
    assert b'# A note\r\n# (dismissed-check) endpunc ' in bytes(store)


def test_restoring_a_check_removes_only_its_line():
    store = _po()
    unit = store.units[-1]
    dismissedchecks.set_dismissed(unit, 'endpunc')
    dismissedchecks.set_dismissed(unit, 'startcaps')

    assert dismissedchecks.set_dismissed(unit, 'endpunc', False)

    assert dismissedchecks.dismissed(unit) == {'startcaps'}
    assert unit.getnotes('translator').splitlines()[0] == 'A note'
    assert not dismissedchecks.set_dismissed(unit, 'endpunc', False)


@pytest.mark.parametrize('change', ['target', 'source'])
def test_a_dismissal_stops_counting_once_the_text_changes(change):
    unit = _po().units[-1]
    dismissedchecks.set_dismissed(unit, 'endpunc')
    original = getattr(unit, change)

    setattr(unit, change, 'Something else')

    assert dismissedchecks.dismissed(unit) == set()
    # Kept with the unit, so undoing the change brings it back.
    setattr(unit, change, original)
    assert dismissedchecks.dismissed(unit) == {'endpunc'}


def test_a_save_leaves_out_a_dismissal_that_stopped_counting_and_puts_it_back():
    store = _po()
    unit = store.units[-1]
    dismissedchecks.set_dismissed(unit, 'endpunc')
    unit.target = 'Oop!'

    with dismissedchecks.stale_left_out(store.units):
        saved = bytes(store)

    assert b'dismissed-check' not in saved
    assert b'# A note\n' in saved
    unit.target = 'Oop'
    assert dismissedchecks.dismissed(unit) == {'endpunc'}


def test_every_plural_form_counts_towards_the_fingerprint():
    unit = _po(
        b'msgid "One file"\nmsgid_plural "%d files"\nmsgstr[0] "Een"\nmsgstr[1] "%d"\n'
    ).units[-1]
    dismissedchecks.set_dismissed(unit, 'numbers')

    unit.target = ['Een', '%d lêers']

    assert dismissedchecks.dismissed(unit) == set()


def test_a_format_without_translator_notes_keeps_a_dismissal_on_the_unit():
    unit = properties.propunit('Open.')
    unit.name = 'open'
    unit.target = 'Oop'

    assert dismissedchecks.can_dismiss(unit)
    assert dismissedchecks.set_dismissed(unit, 'endpunc')
    assert dismissedchecks.dismissed(unit) == {'endpunc'}
    assert dismissedchecks.lines(unit) == [
        '(dismissed-check) endpunc %s' % dismissedchecks.fingerprint(unit)
    ]
    assert dismissedchecks.set_dismissed(unit, 'endpunc', False)
    assert dismissedchecks.dismissed(unit) == set()


# The record #


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    from virtaal.common import pan_app

    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    return tmp_path / 'state'


def test_the_record_brings_back_a_dismissal_another_tool_dropped(tmp_path, state_dir):
    path = str(tmp_path / 'af.po')
    store = _po()
    dismissedchecks.set_dismissed(store.units[-1], 'endpunc')
    dismissedchecks.save_record(path, store.units)
    stripped = _po()

    dismissedchecks.apply_record(path, stripped.units)

    assert dismissedchecks.dismissed(stripped.units[-1]) == {'endpunc'}


def test_the_record_doesnt_bring_back_a_dismissal_for_another_text(tmp_path, state_dir):
    path = str(tmp_path / 'af.po')
    store = _po()
    dismissedchecks.set_dismissed(store.units[-1], 'endpunc')
    dismissedchecks.save_record(path, store.units)
    changed = _po(b'msgid "Open."\nmsgstr "Oop dit"\n')

    dismissedchecks.apply_record(path, changed.units)

    assert dismissedchecks.dismissed(changed.units[-1]) == set()


def test_a_save_without_dismissals_records_nothing(tmp_path, state_dir):
    dismissedchecks.save_record(str(tmp_path / 'af.po'), _po().units)

    assert not state_dir.exists()


def test_an_unreadable_record_counts_as_none(tmp_path, state_dir):
    path = str(tmp_path / 'af.po')
    record = dismissedchecks.record_path(path)
    os.makedirs(os.path.dirname(record))
    with open(record, 'w', encoding='utf-8') as f:
        f.write('not json')

    assert dismissedchecks.load_record(path) == {}


def test_filter_failures_leaves_out_only_dismissed_checks():
    unit = _po().units[-1]
    dismissedchecks.set_dismissed(unit, 'endpunc')
    failures = {'endpunc': 'Different punctuation', 'startcaps': 'Different capitals'}

    assert dismissedchecks.filter_failures(unit, failures) == {'startcaps': 'Different capitals'}


def test_without_dismissals_hides_the_lines_from_the_notes_shown():
    unit = _po().units[-1]
    dismissedchecks.set_dismissed(unit, 'endpunc')

    assert dismissedchecks.without_dismissals(unit.getnotes()) == 'A note\nFor translators'
    assert dismissedchecks.without_dismissals('') == ''

