#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import json
import os

from virtaal.common import pan_app
from virtaal.support import baseline


def test_the_sidecar_is_in_the_state_dir_not_the_project(tmp_path):
    target = tmp_path / 'project' / 'af.properties'

    path = baseline.sidecar_path(str(target))

    assert os.path.dirname(path) == os.path.join(pan_app.get_state_dir(), 'monolingual')
    assert not path.startswith(str(tmp_path / 'project'))


def test_each_target_has_its_own_sidecar(tmp_path):
    assert baseline.sidecar_path(str(tmp_path / 'af.ftl')) != baseline.sidecar_path(
        str(tmp_path / 'de.ftl')
    )


def test_a_chosen_template_is_remembered(tmp_path):
    target = tmp_path / 'af.properties'
    template = tmp_path / 'source.properties'
    template.write_text('a=b\n', encoding='utf-8')

    baseline.remember_template(str(target), str(template))

    assert baseline.remembered_template(str(target)) == str(template)
    assert baseline.remembered_template(str(tmp_path / 'de.properties')) is None


def test_a_remembered_template_that_is_gone_is_forgotten(tmp_path):
    target = tmp_path / 'af.properties'
    baseline.remember_template(str(target), str(tmp_path / 'gone.properties'))

    assert baseline.remembered_template(str(target)) is None


def test_an_unreadable_sidecar_is_ignored(tmp_path):
    target = str(tmp_path / 'af.properties')
    path = baseline.sidecar_path(target)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('{not json')

    assert baseline.load(target) is None


def test_a_sidecar_for_another_path_is_ignored(tmp_path):
    target = str(tmp_path / 'af.properties')
    path = baseline.sidecar_path(target)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'format': baseline.FORMAT, 'target': 'elsewhere', 'units': {}}, f)

    assert baseline.load(target) is None


def test_entry_keeps_review_details_only_while_fuzzy():
    fuzzy = baseline.entry('New', 'Nuut', baseline.NEEDS_WORK, 'Old', True)
    done = baseline.entry('New', 'Nuut', baseline.TRANSLATED, 'Old', True)

    assert fuzzy['previous_source'] == 'Old'
    assert fuzzy['outside'] is True
    assert 'previous_source' not in done
    assert 'outside' not in done
    assert done['source_hash'] == baseline.text_hash('New')
    assert baseline.entry('New', '', baseline.UNTRANSLATED)['target_hash'] is None
