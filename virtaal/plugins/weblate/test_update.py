#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Update from Server on a Weblate work copy, against the fake server
through the real curl client: clean merges, conflicts, and a new
download when the set of units changed."""

import pytest

from devsupport.testing.fakeweblate import demo, run_until
from virtaal.common import pan_app
from virtaal.plugins.weblate.test_submit import Copy
from virtaal.support.platforms import submit, update, workcopy


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pan_app, 'get_state_dir', lambda: str(tmp_path / 'state'))


@pytest.fixture
def fake():
    server = demo().start()
    yield server
    server.stop()
    assert server.schema_problems == []


def _bytes(path):
    with open(path, 'rb') as f:
        return f.read()


class Updating(Copy):
    def update(self, decisions=None, download=True):
        """One Update: decisions answer its conflicts (and a new
        download's), download whether to take a new download."""
        done = []
        self.conflicts = []
        self.plans = []

        def on_conflicts(found, resolve):
            self.conflicts.extend(found)
            resolve(decisions or {})

        def on_download(plan, proceed):
            self.plans.append(plan)
            self.conflicts.extend(plan.conflicts)
            proceed((decisions or {}) if download else None)

        run = update.Update(
            self.copy, self.platform, self.session(), on_conflicts, on_download, done.append
        )
        run.start()
        run_until(lambda: done, timeout=30)
        result = done[0]
        if result.downloaded is not None:
            # As the dialog does: the new file opened, the changes kept
            # written back into it, saved.
            self.copy = result.downloaded
            self.open()
            update.carry(self.copy, result.carried)
            self.save()
        elif result.file_changed:
            self.save()
        return result


def test_a_unit_changed_only_on_the_server_comes_in(fake):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    theirs = fake.edit(copy.server_unit('Open')['id'], 'Open maak')

    result = copy.update()

    assert (result.taken, result.stop, result.downloaded) == (1, None, None)
    assert str(copy.unit('Open').target) == 'Open maak'
    entry = copy.entry('Open')
    assert entry['target_hash'] == workcopy.text_hash(['Open maak'])
    assert entry['last_updated'] == theirs['last_updated'] and not entry['changed']
    # The change here is still to send, and nothing was.
    assert [copy.index('Help')] == copy.copy.pending()
    assert fake.patched == []
    assert copy.copy.record['updated_at']


def test_nothing_new_on_the_server_changes_nothing(fake):
    copy = Updating(fake)

    result = copy.update()

    assert not result.changed and result.stop is None


def test_the_same_change_on_both_sides_is_no_conflict(fake):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.edit(copy.server_unit('Help')['id'], 'Hulp')

    result = copy.update()

    assert copy.conflicts == [] and result.adopted == 1
    assert copy.copy.pending() == []
    assert result.taken == 0


@pytest.mark.parametrize(
    'decision, text, pending, taken',
    [
        (submit.KEEP_MINE, 'Hulp', True, 0),
        (submit.TAKE_THEIRS, 'Help my', False, 1),
        (submit.SKIP, 'Hulp', True, 0),
    ],
)
def test_a_unit_changed_on_both_sides_is_a_conflict(fake, decision, text, pending, taken):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.edit(copy.server_unit('Help')['id'], 'Help my')
    r = copy.index('Help')

    assert copy.update({r: decision}).taken == taken

    [conflict] = copy.conflicts
    assert (conflict.index, conflict.mine, conflict.theirs) == (r, ['Hulp'], ['Help my'])
    assert str(copy.unit('Help').target) == text
    assert bool(copy.copy.pending()) == pending
    # Kept: Submit sends it over theirs, with no conflict. Skipped: it
    # comes up again.
    result = copy.submit()
    expected = {submit.KEEP_MINE: [], submit.TAKE_THEIRS: [], submit.SKIP: [r]}[decision]
    assert [c.index for c in copy.conflicts] == expected
    if decision == submit.KEEP_MINE:
        assert result.sent == 1 and copy.server_unit('Help')['target'] == ['Hulp']


def test_a_new_unit_on_the_server_downloads_again_keeping_the_changes(fake):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.edit('Save', 'Bewaar')
    copy.save()
    fake.add_unit('hello', 'program', 'af', 'Undo')
    fake.edit(copy.server_unit('Open')['id'], 'Open maak')

    result = copy.update()

    [plan] = copy.plans
    assert (len(plan.kept), plan.lost, plan.conflicts) == (2, [], [])
    assert result.downloaded is not None and result.stop is None
    assert [str(u.source) for u in copy.units][-1] == 'Undo'
    assert str(copy.unit('Help').target) == 'Hulp'
    assert str(copy.unit('Save').target) == 'Bewaar'
    assert str(copy.unit('Open').target) == 'Open maak'
    assert sorted(copy.copy.pending()) == sorted([copy.index('Help'), copy.index('Save')])
    # Only Open changed here: the changes kept are as they were, Undo
    # came untranslated.
    assert result.taken == 1
    # And they go with the next Submit, with no conflict.
    assert copy.submit().sent == 2 and copy.conflicts == []


def test_a_change_to_a_removed_unit_is_said_to_be_lost(fake):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.edit('Save', 'Bewaar')
    copy.save()
    fake.remove_unit(copy.server_unit('Help')['id'])

    result = copy.update()

    [plan] = copy.plans
    assert len(plan.lost) == 1 and len(plan.kept) == 1
    assert result.lost == ['Help']
    assert 'Help' not in [str(u.source) for u in copy.units]
    assert copy.copy.pending() == [copy.index('Save')]


def test_a_change_to_a_reworded_unit_is_kept_as_needing_work(fake):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.change_source(copy.server_unit('Help')['id'], 'Help me')

    assert copy.update().taken == 1

    unit = copy.unit('Help me')
    assert str(unit.target) == 'Hulp' and unit.isfuzzy()
    assert copy.copy.pending() == [copy.index('Help me')]


def test_a_conflict_skipped_in_a_new_download_comes_up_at_submit(fake):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.edit(copy.server_unit('Help')['id'], 'Help my')
    fake.add_unit('hello', 'program', 'af', 'Undo')

    assert copy.update({}).taken == 0

    [conflict] = copy.conflicts
    assert conflict.theirs == ['Help my']
    assert str(copy.unit('Help').target) == 'Hulp'
    copy.submit()
    assert [c.theirs for c in copy.conflicts] == [['Help my']]


def test_a_new_download_counts_no_unit_the_file_already_has(fake):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.edit(copy.server_unit('Help')['id'], 'Hulp')
    fake.add_unit('hello', 'program', 'af', 'Undo')

    result = copy.update()

    assert result.downloaded is not None and result.taken == 0
    assert copy.plans[0].same == 1


def test_saying_no_to_a_new_download_leaves_everything(fake):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    before = _bytes(copy.copy.path), _bytes(copy.copy.record_path)
    fake.add_unit('hello', 'program', 'af', 'Undo')

    result = copy.update(download=False)

    assert result.stop == submit.CANCELLED and result.downloaded is None
    after = _bytes(copy.copy.path), _bytes(copy.copy.record_path)
    assert after == before


def test_an_update_offline_fails_cleanly(fake):
    copy = Updating(fake)
    copy.edit('Help', 'Hulp')
    copy.save()
    fake.offline = True

    result = copy.update()

    assert result.stop == submit.FAILED
    assert copy.copy.pending() == [copy.index('Help')]
    assert 'updated_at' not in copy.copy.record
