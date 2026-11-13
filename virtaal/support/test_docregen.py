#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os
import sys
import time

import pytest
from translate.storage import factory

from virtaal.support import _docsamples, docregen, documents


@pytest.fixture(autouse=True)
def _state_dir(tmp_path_factory, monkeypatch):
    from virtaal.common import pan_app

    state = str(tmp_path_factory.mktemp('state'))
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: state)


def _wait(regeneration, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = regeneration.poll()
        if result is not None:
            return result
        time.sleep(0.05)
    regeneration.close()
    raise AssertionError('the helper process never finished')


def _bundle(tmp_path):
    path = tmp_path / 'report.odt'
    path.write_bytes(_docsamples.odt(['Hello.', 'Bye.']))
    bundle = documents.from_documents([str(path)], 'en', 'af')
    trans = bundle.files()[0]
    store = factory.getobject(trans)
    store.units[0].target = 'Hallo.'
    store.save()
    return bundle


def test_a_child_process_makes_the_translated_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, 'INLINE_BYTES', 1)
    bundle = _bundle(tmp_path)
    left = bundle.save(str(tmp_path / 'report.af.virtaal'), background=True)
    regeneration = docregen.Regeneration(bundle.regeneration_jobs(left))
    folder = regeneration.folder

    made, failed = _wait(regeneration)

    assert failed == {}
    [(entry, target, _made_from)] = made
    assert _docsamples.odt_text(target) == ['Hallo.', 'Bye.']
    assert bundle.save_targets(made)
    assert bundle.stale() == []
    assert not os.path.exists(folder)


def test_a_document_that_cant_be_made_is_reported(tmp_path):
    bundle = _bundle(tmp_path)
    [(entry, converter_id, trans, _source)] = bundle.regeneration_jobs(bundle.translations())
    regeneration = docregen.Regeneration([(entry, converter_id, trans, b'not a document')])

    made, failed = _wait(regeneration)

    assert made == []
    assert list(failed) == [0]


def test_a_child_that_stops_reports_every_job_failed(tmp_path):
    bundle = _bundle(tmp_path)
    jobs = bundle.regeneration_jobs(bundle.translations())
    regeneration = docregen.Regeneration(jobs, command=[sys.executable, '-c', 'pass'])

    made, failed = _wait(regeneration)

    assert made == []
    assert failed == {0: 'The helper process stopped.'}


def test_close_stops_the_child(tmp_path):
    bundle = _bundle(tmp_path)
    jobs = bundle.regeneration_jobs(bundle.translations())
    sleeper = [sys.executable, '-c', 'import time; time.sleep(60)']
    regeneration = docregen.Regeneration(jobs, command=sleeper)

    assert regeneration.poll() is None
    regeneration.close()

    assert regeneration.process.returncode is not None
    assert not os.path.exists(regeneration.folder)
