#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""CAT tool packages opened through the main controller, edited and saved
back into the package."""

import os
import shutil
import zipfile

import pytest
from test_scaffolding import TestScaffolding

from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.support import packages

OKAPI = os.path.join('devsupport', 'testfiles', 'packages', 'okapi')


class TestPackagesOpen(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _work(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            packages, 'work_folder', lambda path: str(tmp_path / 'work' / os.path.basename(path))
        )
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'French'),
        )
        self.errors = []
        monkeypatch.setattr(self.main_controller, 'show_error', self.errors.append)
        self.dir = tmp_path
        self.store_controller.close_file()
        yield
        self.store_controller.close_file()

    def _copy(self, name):
        path = self.dir / name
        shutil.copy(os.path.join(OKAPI, name), path)
        return str(path)

    def _edit(self, index, text):
        sc = self.store_controller
        sc.cursor.force_index(index)
        unit = sc.store[index]
        self.unit_controller.load_unit(unit)
        self.unit_controller.set_unit_target(0, [text])
        self.unit_controller.finish_current_unit()

    def test_a_package_opens_as_its_files_and_saves_into_itself(self):
        path = self._copy('test-packages.sdlppx')

        assert self.main_controller.open_file(path)
        sc = self.store_controller
        assert sc.is_stream() and sc.package_path() == path
        assert sc.stream_name() == 'test-packages.sdlppx'
        self._edit(0, 'Neu')
        assert self.main_controller.save_file()

        with zipfile.ZipFile(path) as archive:
            saved = archive.read('zh-x-hmn-SDL/dir1/subdir/test-in-subdir.txt.sdlxliff')
        assert b'Neu' in saved
        assert self.errors == []

    def test_a_package_with_one_file_opens_that_file(self):
        path = self._copy('test1.wsxz')

        assert self.main_controller.open_file(path)
        sc = self.store_controller
        assert not sc.is_stream() and sc.package_path() == path
        self._edit(0, 'Neu')
        assert self.main_controller.save_file()
        with zipfile.ZipFile(path) as archive:
            assert b'Neu' in archive.read('okapi_intro_test15977.sdlxliff')

    def test_closing_or_opening_another_file_forgets_the_package(self):
        self.main_controller.open_file(self._copy('test1.wsxz'))
        self.store_controller.close_file()
        assert self.store_controller.package is None

    def test_a_damaged_package_shows_an_error(self):
        path = self.dir / 'bad.sdlppx'
        path.write_bytes(b'not a zip')

        assert not self.main_controller.open_file(str(path))
        assert len(self.errors) == 1
