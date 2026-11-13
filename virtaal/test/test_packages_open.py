#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""CAT tool packages opened through the main controller, edited, saved
back into the package and answered with a return package."""

import os
import shutil
import time
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
        self.state = tmp_path / 'state' / 'packages'
        monkeypatch.setattr(packages, 'packages_dir', lambda: str(self.state))
        monkeypatch.setattr(
            self.main_controller,
            'get_translator_details',
            lambda *args: ('Tester', 'tester@example.org', 'French'),
        )
        self.errors = []
        monkeypatch.setattr(
            self.main_controller,
            'show_error',
            lambda msg, parent=None, detail='': self.errors.append((msg + '\n\n' + detail).strip()),
        )
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
        assert self.main_controller.view.gui.get_object('mnu_return_package').get_sensitive()
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
        assert not self.main_controller.view.gui.get_object('mnu_return_package').get_sensitive()
        self._edit(0, 'Neu')
        assert self.main_controller.save_file()
        with zipfile.ZipFile(path) as archive:
            assert b'Neu' in archive.read('okapi_intro_test15977.sdlxliff')

    def test_create_return_package_saves_first_and_writes_it(self, monkeypatch):
        path = self._copy('test-packages.sdlppx')
        self.main_controller.open_file(path)
        self._edit(0, 'Neu')
        target = str(self.dir / 'back.sdlrpx')
        asked = []

        def save_dialog(current_filename, title):
            asked.append(current_filename)
            return target

        monkeypatch.setattr(self.main_controller.view, 'show_save_dialog', save_dialog)
        assert self.main_controller.create_return_package()

        assert asked == [str(self.dir / 'test-packages.sdlrpx')]
        assert not self.store_controller.is_modified()
        with zipfile.ZipFile(target) as archive:
            assert b'Neu' in archive.read('zh-x-hmn-SDL/dir1/subdir/test-in-subdir.txt.sdlxliff')
            assert not any(name.startswith('Reports/') for name in archive.namelist())

    def test_closing_or_opening_another_file_forgets_the_package(self):
        self.main_controller.open_file(self._copy('test1.wsxz'))
        self.store_controller.close_file()
        assert self.store_controller.package is None

    def test_a_damaged_package_shows_an_error(self):
        path = self.dir / 'bad.sdlppx'
        path.write_bytes(b'not a zip')

        assert not self.main_controller.open_file(str(path))
        assert len(self.errors) == 1

    def _folders(self):
        return sorted(os.listdir(self.state)) if self.state.exists() else []

    def test_closing_a_package_removes_its_work_folder(self):
        path = self._copy('test-packages.sdlppx')
        self.main_controller.open_file(path)
        self._edit(0, 'Neu')
        self.main_controller.save_file()
        assert len(self._folders()) == 1

        self.store_controller.close_file()

        assert self._folders() == []
        with zipfile.ZipFile(path) as archive:
            assert b'Neu' in archive.read('zh-x-hmn-SDL/dir1/subdir/test-in-subdir.txt.sdlxliff')

    def test_opening_another_file_removes_the_package_s_work_folder(self):
        self.main_controller.open_file(self._copy('test-packages.sdlppx'))
        other = self._copy('test1.wsxz')

        self.main_controller.open_file(other)

        assert self._folders() == [os.path.basename(os.path.dirname(packages.work_folder(other)))]

    def test_reopening_or_switching_language_keeps_the_work_folder(self):
        path = self._copy('ts2017-test01.sdlppx')
        self.main_controller.open_file(path)
        work = self.store_controller.package.work_dir

        self.store_controller.open_stream_language('pl_PL')
        assert os.path.isdir(work)
        self.main_controller.open_file(path)
        assert os.path.isdir(work) and len(self._folders()) == 1

    def test_edits_not_written_into_the_package_keep_its_work_folder(self):
        self.main_controller.open_file(self._copy('test1.wsxz'))
        with open(self.store_controller.package.files()[0], 'ab') as f:
            f.write(b'<!-- not in the package -->')

        self.store_controller.close_file()

        assert len(self._folders()) == 1

    def test_quitting_removes_the_work_folder_and_stale_ones_go_at_start_up(self):
        self.main_controller.open_file(self._copy('test1.wsxz'))
        (self.state / 'left-by-a-crash' / 'old.sdlppx').mkdir(parents=True)
        then = time.time() - 2 * packages.STALE_SECONDS
        for folder in (
            self.state / 'left-by-a-crash' / 'old.sdlppx',
            self.state / 'left-by-a-crash',
        ):
            os.utime(folder, (then, then))
        open_now = os.path.dirname(self.store_controller.package.work_dir)
        os.utime(open_now, (then, then))

        self.store_controller.remove_stale_package_folders()
        assert self._folders() == [os.path.basename(open_now)]
        self.store_controller.leave_package()
        assert self._folders() == []
