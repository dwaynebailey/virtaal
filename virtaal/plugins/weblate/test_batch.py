#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Several Weblate work copies at once, against the fake server through
the real curl client: Submit and Update of several files as one (one
list of conflicts, sent file by file), and downloading several
translations, paced, with failures listed and Cancel."""

import os

import pytest

from devsupport.testing.fakeweblate import TOKEN, demo_many, run_until
from virtaal.common import pan_app
from virtaal.plugins.weblate import backend
from virtaal.plugins.weblate.test_submit import PLATFORM, Copy
from virtaal.support.platforms import openmany, submit, update, workcopy


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(pan_app, 'ui_language', 'en')


@pytest.fixture
def fake():
    server = demo_many().start()
    yield server
    server.stop()
    assert server.schema_problems == []


def _server_unit(fake, component, source):
    units = fake.projects['hello']['components'][component]['units']['af']
    return next(u for u in units if u['source'] == [source])


def _files(fake):
    """The Program and Installer work copies, each with a change saved."""
    program, installer = Copy(fake), Copy(fake, 'installer')
    program.edit('Help', 'Hulp')
    program.save()
    installer.edit('Cancel', 'Kanselleer')
    installer.edit('Next', 'Volgende een')
    installer.save()
    return program, installer


def _items(*copies):
    session = copies[0].session()
    return [(copy.copy, copy.platform, session) for copy in copies]


def _submit(copies, decisions=None):
    done, asked, progress = [], [], []

    def on_conflicts(found, resolve):
        asked.append(found)
        resolve(decisions(found) if decisions else {})

    batch = submit.Batch(
        _items(*copies), lambda n, total: progress.append((n, total)), on_conflicts, done.append
    )
    batch.start()
    run_until(lambda: done, timeout=30)
    return done[0], asked, progress


def test_submit_sends_every_file_counting_across_them(fake):
    program, installer = _files(fake)

    result, asked, progress = _submit([program, installer])

    assert asked == []
    assert (result.total, result.sent, result.stop) == (3, 3, None)
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert _server_unit(fake, 'program', 'Help')['target'] == ['Hulp']
    assert _server_unit(fake, 'installer', 'Next')['target'] == ['Volgende een']
    assert program.copy.submitted() and installer.copy.submitted()


def test_submit_asks_about_every_files_conflicts_at_once(fake):
    program, installer = _files(fake)
    fake.edit(_server_unit(fake, 'program', 'Help')['id'], 'Help my')
    fake.edit(_server_unit(fake, 'installer', 'Cancel')['id'], 'Kanselleer dit')

    def theirs_for_the_installer(found):
        return {c.index: submit.TAKE_THEIRS for c in found if c.index[0] == 1}

    result, asked, _progress = _submit([program, installer], theirs_for_the_installer)

    [found] = asked
    assert [(c.where, c.source, c.theirs) for c in found] == [
        ('Hello/Program (Afrikaans)', 'Help', ['Help my']),
        ('Hello/Installer (Afrikaans)', 'Cancel', ['Kanselleer dit']),
    ]
    assert (result.sent, result.taken, result.skipped) == (1, 1, 1)
    assert [r.file_changed for r in result.results] == [False, True]
    assert str(installer.unit('Cancel').target) == 'Kanselleer dit'
    assert _server_unit(fake, 'program', 'Help')['target'] == ['Help my']


def test_cancelling_the_conflicts_sends_nothing(fake):
    program, installer = _files(fake)
    fake.edit(_server_unit(fake, 'installer', 'Cancel')['id'], 'Kanselleer dit')
    done = []
    batch = submit.Batch(
        _items(program, installer), lambda *a: None, lambda f, resolve: resolve(None), done.append
    )

    batch.start()
    run_until(lambda: done, timeout=30)

    assert done[0].stop == submit.CANCELLED and done[0].sent == 0
    assert fake.patched == []


def test_a_locked_file_is_left_out_and_the_others_go(fake):
    program, installer = _files(fake)
    fake.lock('hello', 'program')

    result, _asked, _progress = _submit([program, installer])

    assert result.stop == submit.LOCKED and result.sent == 2
    assert [r.stop for r in result.results] == [submit.LOCKED, None]


def test_out_of_budget_stops_the_files_after_it(fake):
    program, installer = _files(fake)
    session = program.session()
    session.budget = lambda: None
    done = []
    batch = submit.Batch(
        [(program.copy, program.platform, session), (installer.copy, installer.platform, session)],
        lambda *a: None,
        None,
        done.append,
    )
    # Two requests a file to check, then the budget is gone.
    for s in batch.submissions:
        s.pacer.limit = (4 + submit.Submission.RESERVE, 3600)

    batch.start()
    run_until(lambda: done, timeout=30)

    result = done[0]
    assert result.stop == submit.BUDGET and result.sent == 0
    assert [r.stop for r in result.results] == [submit.BUDGET, submit.CANCELLED]
    assert result.reset_in is not None


# Update #


def _update(copies, decisions=None, download=True):
    done, asked, plans = [], [], []

    def on_conflicts(found, resolve):
        asked.append(found)
        resolve(decisions if decisions is not None else {})

    def on_download(found, proceed):
        plans.append(found)
        proceed(download)

    batch = update.Batch(_items(*copies), on_conflicts, on_download, done.append)
    batch.start()
    run_until(lambda: done, timeout=30)
    return done[0], asked, plans


def test_update_brings_in_every_files_changes(fake):
    program, installer = Copy(fake), Copy(fake, 'installer')
    fake.edit(_server_unit(fake, 'program', 'Help')['id'], 'Hulp')
    fake.edit(_server_unit(fake, 'installer', 'Cancel')['id'], 'Kanselleer')

    result, asked, plans = _update([program, installer])

    assert (asked, plans) == ([], [])
    assert result.taken == 2 and result.stop is None and result.changed
    assert str(program.unit('Help').target) == 'Hulp'
    assert str(installer.unit('Cancel').target) == 'Kanselleer'


def test_update_asks_about_every_files_conflicts_at_once(fake):
    program, installer = _files(fake)
    fake.edit(_server_unit(fake, 'program', 'Help')['id'], 'Help my')
    fake.edit(_server_unit(fake, 'installer', 'Cancel')['id'], 'Kanselleer dit')

    result, asked, _plans = _update(
        [program, installer], {(0, program.index('Help')): submit.TAKE_THEIRS}
    )

    [found] = asked
    assert [c.where for c in found] == ['Hello/Program (Afrikaans)', 'Hello/Installer (Afrikaans)']
    assert (result.taken, result.skipped) == (1, 1)
    assert str(program.unit('Help').target) == 'Help my'
    assert str(installer.unit('Cancel').target) == 'Kanselleer'


def test_saying_no_to_a_new_download_leaves_that_file_alone(fake):
    program, installer = _files(fake)
    fake.add_unit('hello', 'installer', 'af', 'Finish')
    fake.edit(_server_unit(fake, 'program', 'Open')['id'], 'Open dit')

    result, _asked, plans = _update([program, installer], download=False)

    [[(copy, plan)]] = plans
    assert copy is installer.copy and len(plan.kept) == 2
    assert [r.stop for r in result.results] == [None, update.CANCELLED]
    assert result.stop is None and result.taken == 1


def test_a_new_download_in_one_file_of_several(fake):
    program, installer = _files(fake)
    fake.add_unit('hello', 'installer', 'af', 'Finish')

    result, _asked, plans = _update([program, installer])

    assert len(plans) == 1 and result.stop is None
    assert result.results[0].downloaded is None
    assert result.results[1].downloaded is not None


def test_cancel_stops_every_file(fake):
    program, installer = Copy(fake), Copy(fake, 'installer')
    done = []
    batch = update.Batch(_items(program, installer), None, None, done.append)

    batch.start()
    batch.cancel()
    run_until(lambda: done, timeout=30)

    assert done[0].stop == update.CANCELLED
    assert [r.stop for r in done[0].results] == [update.CANCELLED, update.CANCELLED]


# Downloads #


def _server(fake):
    from types import SimpleNamespace

    platform = PLATFORM.__new__(PLATFORM)
    return SimpleNamespace(platform=platform, url=fake.url), platform


def _session(fake):
    return backend.WeblateSession(PLATFORM.client_factory(fake.url, TOKEN))


def _download(fake, wanted, **attrs):
    server, _platform = _server(fake)
    done, progress = [], []
    runner = openmany.Downloads(
        server, _session(fake), wanted, lambda n, t: progress.append((n, t)), done.append
    )
    for name, value in attrs.items():
        setattr(runner, name, value)
    runner.start()
    run_until(lambda: done, timeout=30)
    return done[0], progress


WANTED = [
    openmany.Wanted('hello', 'program', 'af', ('Hello', 'Program', 'Afrikaans')),
    openmany.Wanted('hello', 'installer', 'af', ('Hello', 'Installer', 'Afrikaans')),
    openmany.Wanted('hello', 'program', 'de', ('Hello', 'Program', 'German')),
]


def test_downloads_each_into_its_work_copy_in_turn(fake):
    result, progress = _download(fake, WANTED)

    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert result.stop is None and result.failures == []
    assert [workcopy.for_file(p).label() for p in result.paths] == [
        'Hello/Program (Afrikaans)',
        'Hello/Installer (Afrikaans)',
        'Hello/Program (German)',
    ]
    assert [os.path.basename(p) for p in result.paths] == ['af.po', 'af.po', 'de.po']
    assert openmany.title_of(WANTED) == 'Hello'
    assert openmany.title_of(WANTED[::2]) == 'Hello/Program'


def test_a_failed_download_is_listed_and_the_rest_go(fake):
    fake.fail('/api/translations/hello/installer/af/file/', 500)

    result, _progress = _download(fake, WANTED)

    assert len(result.paths) == 2
    [(wanted, error)] = result.failures
    assert wanted.label == 'Hello/Installer (Afrikaans)' and error.status == 500


def test_work_not_yet_submitted_opens_as_it_is(fake):
    program = Copy(fake)
    program.edit('Help', 'Hulp')
    program.save()
    asked = len(fake.requests_to('/api/translations/hello/program/af/file/'))

    result, progress = _download(fake, WANTED[:1])

    assert result.paths == [program.copy.path] and progress == []
    assert len(fake.requests_to('/api/translations/hello/program/af/file/')) == asked


def test_cancel_opens_nothing(fake):
    server, _platform = _server(fake)
    done = []
    runner = openmany.Downloads(server, _session(fake), WANTED, lambda *a: None, done.append)

    runner.start()
    runner.cancel()

    assert done[0].stop == submit.CANCELLED and done[0].paths == []


def test_the_components_with_a_language(fake):
    found = []

    openmany.components_with(_session(fake), 'hello', 'de', found.append, found.append)
    run_until(lambda: found, timeout=30)

    # Not the glossary, and only those with German.
    assert [(c.slug, item.slug) for c, item in found[0]] == [
        ('program', 'de'),
        ('installer', 'de'),
    ]


# What the dialogs say #


def test_a_submit_of_several_files_says_what_each_left(fake):
    from virtaal.support.platforms.submitdialog import batch_message, files_message

    program, installer = _files(fake)
    fake.lock('hello', 'installer')
    result, _asked, _progress = _submit([program, installer])
    platforms = [program.platform, installer.platform]

    assert files_message([program.copy, installer.copy]) == (
        'In 2 files:\n  Hello/Program (Afrikaans)\n  Hello/Installer (Afrikaans)'
    )
    assert batch_message(result, [program.copy, installer.copy], platforms) == (
        'Submitted 1 of 3 changed units.\n'
        'Hello/Installer (Afrikaans)\n'
        '  Submitted 0 of 2 changed units.\n'
        '  The translation is locked on the server.\n'
        'Submit again to send the rest.'
    )
    fake.lock('hello', 'installer', False)
    result, _asked, _progress = _submit([program, installer])
    assert batch_message(result, [program.copy, installer.copy], platforms) is None


def test_the_download_question_names_each_file(fake, monkeypatch):
    from types import SimpleNamespace

    from virtaal.support.platforms import updatedialog

    program, installer = _files(fake)
    fake.remove_unit(_server_unit(fake, 'installer', 'Next')['id'])
    fake.add_unit('hello', 'program', 'af', 'Undo')
    _result, _asked, plans = _update([program, installer], download=False)
    shown = []

    def answer(dialog):
        shown.append(dialog.props.secondary_text)
        return 0

    monkeypatch.setattr(updatedialog, 'run_dialog', answer)
    dialogs = updatedialog.UpdateDialogs.__new__(updatedialog.UpdateDialogs)
    dialogs.parent = None

    assert not dialogs.ask_downloads(plans[0])
    assert shown == [
        'Hello/Program (Afrikaans)\n'
        'Hello/Installer (Afrikaans)\n'
        '  1 change not yet submitted is to a string removed or reworded on the server, '
        'and will be lost:\n'
        '    Next'
    ]
    assert updatedialog.status_message(SimpleNamespace(changed=True, taken=0)) == (
        'Your copy is already up to date.'
    )
