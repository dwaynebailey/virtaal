#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Opening a Transifex resource's translation from the fake server:
the asynchronous download and its redirect (without the token), the
strings with their states, plurals and contexts, a JSON resource with
its source file, and the work copy's states after a restart."""

import json
import os

import pytest
from translate.storage.workflow import StateEnum

from devsupport.testing.faketransifex import TOKEN, demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.transifex import backend, client
from virtaal.plugins.transifex.test_submit import Copy
from virtaal.support.platforms import NOT_PLATFORM, workcopy


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(client.Client, 'POLL_FIRST', 0.01)


@pytest.fixture
def fake():
    server = demo().start()
    yield server
    server.stop()
    assert server.schema_problems == []


def test_a_po_resource_downloads_with_its_strings_and_states(fake):
    copy = Copy(fake)
    download = copy.download

    assert download.filename == 'messages/af.po'
    assert download.names == {'project': 'Program', 'component': 'Messages'}
    found = {tuple(u.source): (u.context, u.target, u.state, u.code) for u in download.units}
    assert found[('Open',)] == ('', ['Maak oop'], StateEnum.FINAL, backend.REVIEWED)
    assert found[('Save',)] == ('', ['Stoor'], StateEnum.UNREVIEWED, backend.TRANSLATED)
    assert found[('Close',)] == ('', ['Maak toe'], StateEnum.FINAL, backend.PROOFREAD)
    assert found[('File',)][0] == 'menu'
    assert found[('Help',)] == ('', [''], StateEnum.EMPTY, backend.UNTRANSLATED)
    assert found[('%d file', '%d files')][1] == ['%d lêer', '%d lêers']
    # Every string matches a unit of the file.
    assert len(copy.copy.matched) == len(download.units) == 7
    assert copy.unit('Open').get_state_n() == StateEnum.FINAL


def test_the_file_comes_from_the_redirect_without_the_token(fake):
    Copy(fake)
    [job] = fake.requests_to('/resource_translations_async_downloads', 'POST')
    body = json.loads(job['body'])['data']
    assert body['relationships']['language']['data'] == {'type': 'languages', 'id': 'l:af'}
    assert body['attributes']['mode'] == 'default'
    [file_request] = fake.requests_to('/_files/')
    assert 'Authorization' not in file_request['headers']


def test_a_json_resource_opens_with_its_source_file(fake):
    copy = Copy(fake, 'website', opened=False)
    download = copy.download

    assert download.filename == 'website/af.json'
    assert download.template[0] == 'website/en.json'
    assert json.loads(download.template[1]) == {
        'welcome': 'Welcome',
        'open': 'Open',
        'save': 'Save',
    }
    # Matched by key.
    assert [u.context for u in download.units] == ['welcome', 'open', 'save']
    assert copy.copy.template_path().endswith(os.path.join('website', 'en.json'))


def test_the_states_transifex_has_are_kept_across_a_restart(fake):
    copy = Copy(fake)
    copy.edit('Save', state=StateEnum.FINAL)
    copy.edit('Open', state=StateEnum.NEEDS_REVIEW)
    copy.save()

    copy.open()

    assert copy.unit('Save').get_state_n() == StateEnum.FINAL
    assert copy.unit('Open').get_state_n() == StateEnum.NEEDS_REVIEW
    assert sorted(copy.copy.pending()) == sorted([copy.index('Save'), copy.index('Open')])


def test_a_failed_download_job_says_so(fake, tmp_path):
    fake.fail('/resource_translations_async_downloads/', 200, times=50, body=_failed_job())
    platform = backend.TransifexPlatform(str(tmp_path / 'tx.ini'))
    found = []
    platform.session(fake.url, {'token': TOKEN}).download(
        'hello-org/program', 'messages', 'af', found.append, found.append
    )
    run_until(lambda: found, timeout=20)
    assert found[0].detail == 'Parse error.'


def _failed_job():
    job = {
        'data': {
            'type': 'resource_translations_async_downloads',
            'id': '00000000-0000-0000-0000-000000000000',
            'attributes': {'status': 'failed', 'errors': [{'detail': 'Parse error.'}]},
        }
    }
    return json.dumps(job).encode('utf-8')


def test_a_resource_no_longer_there_is_a_404(fake, tmp_path):
    platform = backend.TransifexPlatform(str(tmp_path / 'tx.ini'))
    found = []
    platform.session(fake.url, {'token': TOKEN}).download(
        'hello-org/program', 'gone', 'af', found.append, found.append
    )
    run_until(lambda: found, timeout=20)
    assert (found[0].kind, found[0].status) == (NOT_PLATFORM, 404)


@pytest.mark.parametrize(
    ('file_format', 'paths'),
    [
        ('PO', ('res/pt_BR.po', None)),
        ('ANDROID', ('res/res/values-pt-rBR/strings.xml', 'res/res/values/strings.xml')),
        ('XLIFF', ('res/pt_BR.xlf', None)),
        ('YAML_GENERIC', ('res/pt_BR.xlf', None)),
        ('STRINGS', ('res/pt_BR.lproj/Localizable.strings', 'res/en.lproj/Localizable.strings')),
        ('ARB', ('res/res_pt_BR.arb', 'res/res_en.arb')),
    ],
)
def test_each_format_has_its_file_path(file_format, paths):
    assert backend.file_paths(file_format, 'res', 'pt_BR', 'en') == paths


def test_a_work_copy_is_found_again_by_its_path(fake):
    copy = Copy(fake)
    again = workcopy.for_file(copy.copy.path)
    assert again.record['platform'] == 'transifex'
    assert again.record['project'] == 'hello-org/program'
    assert again.location() == 'Program/Messages'
