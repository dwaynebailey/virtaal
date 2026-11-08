#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

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

    setattr(unit, change, 'Something else')

    assert dismissedchecks.dismissed(unit) == set()
    assert dismissedchecks.drop_stale(unit)
    assert unit.getnotes('translator') == 'A note'
    assert not dismissedchecks.drop_stale(unit)


def test_every_plural_form_counts_towards_the_fingerprint():
    unit = _po(
        b'msgid "One file"\nmsgid_plural "%d files"\nmsgstr[0] "Een"\nmsgstr[1] "%d"\n'
    ).units[-1]
    dismissedchecks.set_dismissed(unit, 'numbers')

    unit.target = ['Een', '%d lêers']

    assert dismissedchecks.dismissed(unit) == set()


def test_a_format_without_translator_notes_cant_dismiss():
    unit = properties.propunit('Open.')

    assert not dismissedchecks.can_dismiss(unit)
    assert dismissedchecks.dismissed(unit) == set()


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
