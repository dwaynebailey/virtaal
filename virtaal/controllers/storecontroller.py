#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging
import os
from contextlib import contextmanager

from gi.repository import GObject

from virtaal.common import GObjectWrapper

from .basecontroller import BaseController


def suggested_bundle_path(bundle):
    """Where to save a new translation bundle: beside its first
    document, or in Documents if that folder can't be written to."""
    folder = None
    originals = [d.get('original') for d in bundle.documents() if d.get('original')]
    if originals:
        folder = os.path.dirname(originals[0])
    if not folder or not os.access(folder, os.W_OK):
        from gi.repository import GLib

        folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
        folder = folder or os.path.expanduser('~')
    return os.path.join(folder, bundle.suggested_name())


class StoreController(BaseController):
    """The controller for all store-level activities."""

    __gtype_name__ = 'StoreController'
    __gsignals__ = {
        'store-loaded': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'store-saved': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'store-closed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        # The workflow state stats (store.stats) changed.
        'stats-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        # The quality check failures (store.checks) changed.
        'checks-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        # A file joined the open stream (a monolingual file just paired).
        'files-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Files of the open stream waiting for their stats got their
        # units: (whether the file numbers changed).
        'files-filled': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    # With several files open: the paths given, every file found in
    # them, the language picked, and the FileSet of that language.
    opened_paths = None
    all_files = None
    stream_language = None
    fileset = None
    # The target language the user had, when the files opened aren't in it.
    stream_language_replaced = None
    # The CAT tool package whose files are open (virtaal.support.packages),
    # and the one being opened.
    package = None
    _opening_package = None
    # The package whose files were last loaded, for its work folder.
    _loaded_package = None
    # The translation platforms' work copies open, with their side
    # records (virtaal.support.platforms.workcopy): {path: WorkCopy}, the
    # file's, or with several files open, those of every file found.
    # Their folders are kept on closing.
    work_copies = {}
    # {path: StoreModel} read for work copies the stream hasn't read.
    _copy_models = {}
    # The work copies the last save wrote.
    saved_copies = ()
    _stream_handlers = None

    arrival = None
    """How the cursor is being moved, while it is: 'commit' (Enter and the
        like), 'step' (a navigation key), 'click', 'jump' (to a search
        match, the first or last unit, or a new mode) or 'typing' (a search
        being typed). See arriving()."""

    # INITIALIZERS #
    def __init__(self, main_controller):
        GObjectWrapper.__init__(self)

        self.main_controller = main_controller
        self.main_controller.store_controller = self
        self._unit_controller = None  # This is set by UnitController itself when it is created

        self.cursor = None
        self.handler_ids = {}
        self._modified = False
        self._loading_store = False
        self.project = None
        self.store = None
        self._set_work_copies({})
        # The path the open store was read from, or None if it can't be
        # read again (a temporary file).
        self.opened_filename = None
        self._view = None
        self._plural_notice = None

        self._controller_register_id = self.main_controller.connect(
            'controller-registered', self._on_controller_registered
        )

    def destroy(self):
        if self.project:
            del self.project

    @contextmanager
    def arriving(self, kind):
        """Mark the cursor moves made inside as arrivals of kind."""
        previous, self.arrival = self.arrival, kind
        try:
            yield
        finally:
            self.arrival = previous

    # ACCESSORS #

    def _getview(self):
        if not self._view:
            from virtaal.views.storeview import StoreView

            self._view = StoreView(self)
        return self._view

    view = property(_getview)

    def get_nplurals(self, store=None):
        if not store:
            store = self.store
        return store and store.nplurals or 0

    def get_bundle_filename(self):
        """Returns the file name of the bundle archive, if we are working with one."""
        bundle = self.bundle()
        if bundle is not None:
            return bundle.path or suggested_bundle_path(bundle)
        if self.project:
            from virtaal.support.bundleprojstore import BundleProjectStore

            if isinstance(self.project.store, BundleProjectStore):
                return self.project.store.zip.filename
        return None

    def get_store(self):
        return self.store

    def open_package(self, path):
        """Open the bilingual files of the CAT tool package C{path}, to
        be written back into it on saving."""
        from virtaal.support import packages

        package = packages.open_package(path)
        files = package.files()
        self._opening_package = package
        try:
            if len(files) == 1:
                self.open_file(files[0])
            else:
                # The folder, named as the package, for the stream's name.
                self.open_files([package.work_dir])
        finally:
            self._opening_package = None

    def bundle(self):
        """The translation bundle (virtaal.support.documents) open, or None."""
        from virtaal.support.documents import DocumentBundle

        return self.package if isinstance(self.package, DocumentBundle) else None

    def open_bundle(self, bundle, language=None):
        """Open the files of the translation bundle C{bundle}: in
        C{language}, or with several, the user's target language if it
        has it."""
        files = bundle.files()
        self._opening_package = bundle
        try:
            if len(files) == 1:
                self.open_file(files[0])
            else:
                self.open_files([bundle.work_dir])
                if language and language != self.stream_language and language in bundle.languages:
                    self.open_stream_language(language)
        finally:
            self._opening_package = None
        self.main_controller.set_force_saveas(bundle.path is None)
        if bundle.path is None:
            self.main_controller.set_saveable(True)

    def flush_bundle(self):
        """Write the edits not yet saved into the bundle's work folder,
        before the bundle changes its files there."""
        self.unit_controller.prepare_for_save()
        self.store.save_file()

    def reopen_bundle(self, language=None):
        """Open the bundle's files again after a change to the bundle
        (a document or language added; L{flush_bundle()} first), in
        C{language} or the one shown."""
        bundle = self.bundle()
        self.open_bundle(bundle, language or self.stream_language or self._file_language())
        self.set_modified(True)

    def _file_language(self):
        label = self.bundle().label(self.store.filename) if self.store is not None else None
        return label[1] if label else None

    def _lock_bundle_units(self, model):
        """A bundle with no target language shows its documents' units,
        not to be edited."""
        bundle = self.bundle()
        if bundle is None or not bundle.read_only():
            return
        for unit in model.get_units():
            unit.read_only = True

    def _name_bundle_files(self, all_files):
        """Show each of a bundle's files as its document."""
        bundle = self.bundle()
        if bundle is None:
            return
        for entry in all_files:
            found = bundle.label(entry.path)
            if found:
                entry.label = entry.panel_label = found[0]

    def _leave_package(self, package):
        """Note C{package} (or None) as the one whose files are open,
        and remove the work folder of the one before."""
        old, self._loaded_package = self._loaded_package, package
        if old is None or old is package:
            return
        if package is not None and os.path.abspath(package.work_dir) == os.path.abspath(
            old.work_dir
        ):
            return
        old.discard()

    def leave_package(self):
        """Remove the open package's work folder, its edits all saved
        into it: Virtaal is quitting."""
        self._leave_package(None)

    def remove_stale_package_folders(self):
        """Remove the work folders left of packages no longer open."""
        from virtaal.support import packages

        open_now = [p.work_dir for p in (self.package, self._loaded_package) if p is not None]
        open_now += list(self.opened_paths or ()) + [self.opened_filename]
        packages.remove_stale_work_folders(open_now)

    def package_path(self):
        """The package whose files are open, or None."""
        return self.package.path if self.package is not None else None

    def is_stream(self):
        """Whether several files are open as one list."""
        return self.fileset is not None

    def modified_stream_files(self):
        """With several files open, the names of those with unsaved changes."""
        if not self.is_stream():
            return []
        return [entry.display_name for entry in self.store.modified_entries()]

    @property
    def work_copy(self):
        """The work copy of the file open, or with several open, of the
        one the cursor is in; None if it isn't one."""
        if not self.work_copies:
            return None
        if self.is_stream():
            return self.work_copies.get(self.store.filename) if self.store else None
        return next(iter(self.work_copies.values()))

    def _set_work_copies(self, copies):
        from virtaal.support.platforms import workcopy

        self.work_copies = copies
        self._copy_models = {}
        # For a translation server's checks (WeblateCheck).
        workcopy.set_open(copies)

    def attach_work_copy(self, copy):
        """Gives copy, one of work_copies, its file's units: the stream's,
        read now if they weren't; for a file the stream can't read now
        (waiting for its stats, or in another language), a model of its
        own. Whether it has them."""
        if copy.store is not None:
            return True
        if not self.is_stream():
            return False
        number = self.store.segment_number(copy.path)
        if number is not None and not self.store.segments[number].pending:
            try:
                self.store.load_segment(number)
            except Exception:
                logging.exception('Could not read %s', copy.path)
            return copy.store is not None
        entry = next((e for e in self.all_files if e.path == copy.path), None)
        if entry is None:
            return False
        try:
            model = self.store.read_entry(entry)
        except Exception:
            logging.exception('Could not read %s', copy.path)
            return False
        self._copy_models[copy.path] = model
        copy.attach(model)
        return True

    def work_copies_changed(self, copies):
        """The units of copies were rewritten from their server: a file
        the stream shows is marked to save with the others, one it
        doesn't is saved now. Whether any needs saving yet."""
        unsaved = False
        for copy in copies:
            model = self._copy_models.get(copy.path)
            if model is not None and copy.store is model:
                model.save_file()
                copy.saved()
                continue
            if self.is_stream():
                number = self.store.segment_number(copy.path)
                if number is not None:
                    self.store.mark_file_modified(number)
            unsaved = True
        if unsaved:
            self.set_modified(True)
        return unsaved

    def _attach_loaded(self, store):
        for segment in store.segments:
            if segment.loaded:
                self._lock_bundle_units(segment.model)
            copy = self.work_copies.get(segment.path)
            if copy is not None and segment.loaded and copy.store is not segment.model:
                copy.attach(segment.model)

    def get_store_title(self):
        """The open file's name for the window title: a platform's work
        copy as its project and component, and the platform."""
        if self.work_copy is not None and self.store is not None:
            names = {
                'location': self.work_copy.location(),
                'platform': self.work_copy.platform_name(),
            }
            if self.work_copy.submitted() and not self.is_modified():
                # l10n: The window title for a file opened from a translation server once
                # all its changes were sent back. %(location)s is its project and component,
                # such as "Hello/Program"; %(platform)s is the kind of server, such as Weblate
                return _('%(location)s (%(platform)s, submitted)') % names
            # l10n: The window title for a file opened from a translation server.
            # %(location)s is its project and component, such as "Hello/Program";
            # %(platform)s is the kind of server, such as Weblate
            return _('%(location)s (%(platform)s)') % names
        if self.bundle() is not None and self.store is not None:
            return self.bundle().title()
        return os.path.basename(self.get_store_filename())

    def get_store_filename(self):
        """Returns a display-friendly string representing the current open
        store's file name. If a bundle is currently open, this method will
        return a file name in the format "bundle.zip:some_file.xlf"."""
        store = self.get_store()
        # A folder of files to pair has no units but still a name.
        if store is None or (not store and not self.is_stream()):
            return ''
        filename = ''
        if self.project:
            from virtaal.support.bundleprojstore import BundleProjectStore

            if isinstance(self.project.store, BundleProjectStore):
                # This should always be the case
                filename = self.project.store.zip.filename + ':'
                projfname = self.project.get_proj_filename(store.get_filename())
                _dir, projfname = os.path.split(projfname)
                filename += projfname
        else:
            filename += store.get_filename()
        return filename

    def can_binary_export(self):
        """Whether the open file is a Gettext PO file, the only kind
        L{binary_export()} compiles."""
        if self.bundle() is not None:
            return False
        return self.get_store_filename().endswith(('.po', '.po.bz2', '.po.gz'))

    def get_store_checker(self):
        store = self.get_store()
        if store is None:
            raise ValueError('No store to get checker from')
        return store.get_checker()

    def get_store_stats(self):
        store = self.get_store()
        if store is None:
            raise ValueError('No store to get checker from')
        return store.stats

    def get_store_checks(self):
        store = self.get_store()
        if store is None:
            raise ValueError('No store to get checker from')
        return store.checks

    def get_unit_celleditor(self, unit):
        """Load the given unit in via the C{UnitController} and return
        the C{Gtk.CellEditable} it creates."""
        return self.unit_controller.load_unit(unit)

    def is_modified(self):
        return self._modified

    @property
    def loading_store(self):
        """Whether store-loaded is being handled: changes made then fill in
        the file, rather than edit it."""
        return self._loading_store

    def set_modified(self, value):
        """Set the modified flag, also updating set_saveable()."""
        self._modified = value
        self.main_controller.set_saveable(value)

    def _get_unitcontroller(self):
        return self._unit_controller

    def _set_unitcontroller(self, unitcont):
        """@type unitcont: UnitController"""
        if self.unit_controller:
            for key in ('unitview.unit-modified', 'unitview.unit-done'):
                if key in self.handler_ids:
                    self.unit_controller.disconnect(self.handler_ids.pop(key))
        self._unit_controller = unitcont
        self.handler_ids['unitview.unit-modified'] = self.unit_controller.connect(
            'unit-modified', self._unit_modified
        )
        # A unit's workflow state is only written to it once it is done.
        self.handler_ids['unitview.unit-done'] = self.unit_controller.connect(
            'unit-done', self._unit_done
        )

    unit_controller = property(_get_unitcontroller, _set_unitcontroller)

    # METHODS #
    def select_unit(self, unit, force=False):
        """Select the specified unit and scroll to it.
        Note that, because we change units via the cursor, the unit to
        select must be valid according to the cursor."""
        if self.cursor.deref() is unit:
            # Unit is already selected; no need to do more work
            return

        i = self.store.index_of(unit)
        if i is None:
            import logging

            logging.debug('Unit not found: %s', unit)
            i = 0

        if force:
            self.cursor.force_index(i)
        else:
            self.cursor.index = i

    def _open_bundle(self, filename):
        import logging

        from virtaal.models.storemodel import StoreModel
        from virtaal.support import bundleprojstore
        from virtaal.support.project import Project

        self.project = Project(bundleprojstore.BundleProjectStore(filename))

        if not len(self.project.store.transfiles):
            if not len(self.project.store.sourcefiles):
                raise bundleprojstore.InvalidBundleError(
                    'No source or translatable files in bundle'
                )
            transfile, _fname = self.project.convert_forward(self.project.store.sourcefiles[0])
            transfile.close()

        # Opens the first translatable file; with several, the user should pick.
        with self.project.get_file(self.project.store.transfiles[0]) as transfile:
            self.real_filename = transfile.name
            logging.info(
                'Editing translation file %s:%s' % (filename, self.project.store.transfiles[0])
            )
            self.store = StoreModel(transfile, self)

    def _open_plain(self, filename, template=None):
        from virtaal.models.storemodel import StoreModel
        from virtaal.support import converters

        if template is None and converters.is_monolingual(filename):
            template = self.find_template(filename)
            if template is None:
                raise ValueError('No source-language file was found for this file.')
        if template is None:
            self.store = StoreModel(filename, self)
        else:
            from virtaal.models.monolingual import pairing

            self.store = StoreModel(filename, self, **pairing(template))

    def find_template(self, filename):
        """The L{TemplateMatch} for a monolingual file, without asking:
        from the project, its layout, or the user's earlier choice."""
        from virtaal.support import templatefinder

        return templatefinder.known_template(filename)

    def _rename_pot_template(self, filename, force_saveas):
        import re

        pot_re = re.compile(r"\.pot(\.gz|\.bz2)?$")
        if pot_re.search(filename):
            force_saveas = True
            filename = pot_re.sub('.po', filename)
            self.store._trans_store.filename = filename
        return filename, force_saveas

    def _is_readonly(self, filename):
        """Whether the file actually opened (the bundle itself for a
        .zip, not its extracted temp copy) can't be written to."""
        return os.path.isfile(filename) and not os.access(filename, os.W_OK)

    def open_files(self, paths):
        """Open files and directories as one list of units. A single file
        opens as with L{open_file()}."""
        from virtaal.models.fileset import FileSet

        all_files = FileSet.from_paths(paths)
        if len(all_files) == 1 and not all_files.unpaired:
            return self.open_file(all_files[0].path)
        from virtaal.models.langmodel import same_language

        target = self.main_controller.lang_controller
        target = target and target.target_lang and target.target_lang.code
        language = all_files.choose_language(target)
        same = language == target or same_language(language, target)
        replaced = target if target and language and not same else None
        self._open_stream(all_files, language, paths, replaced)

    def pair_in_stream(self, path, match):
        """Pair the open folder's monolingual file C{path} as the
        L{TemplateMatch} C{match} says, in place: its units join the
        stream where it goes; nothing else is read again, so unsaved
        edits stay as they are.
        @returns: Its segment, or C{None} if no units joined (the user
            said it's a source-language file, or it has none)."""
        self.unit_controller.prepare_for_save()
        entry = None
        for fileset in {id(f): f for f in (self.all_files, self.fileset)}.values():
            paired = fileset.pair(path, match)
            if fileset is self.fileset:
                entry = paired
        segment = self.store.insert_file(entry) if entry is not None else None
        if segment is None and entry is not None:
            self.store.skipped.append(entry)
        self.view.reload_rows()
        self.main_controller.mode_controller.refresh_mode()
        self.emit('files-changed')
        self.emit('stats-changed')
        return segment

    def open_stream_language(self, language):
        """Open the files in C{language} of those already open."""
        self._reopen(lambda: self._open_stream(self.all_files, language, self.opened_paths))

    def reopen_stream(self, replaced=None):
        """Open the files again, in the language shown, as they are on
        disk now; replaced maps a path opened to the one it became."""
        from virtaal.models.fileset import FileSet

        replaced = replaced or {}
        paths = [replaced.get(path, path) for path in self.opened_paths]
        language = self.stream_language
        self._reopen(lambda: self._open_stream(FileSet.from_paths(paths), language, paths))

    def _reopen(self, open_again):
        """Open the files again, still the package's if they were."""
        self._opening_package = self.package
        try:
            open_again()
        finally:
            self._opening_package = None

    def stream_name(self):
        """The name of the folder, or first file, opened."""
        paths = self.opened_paths or ('',)
        return os.path.basename(os.path.normpath(paths[0]))

    def opened_folder(self):
        """The folder opened, when one folder was."""
        paths = self.opened_paths or ()
        if len(paths) == 1 and os.path.isdir(paths[0]):
            return paths[0]
        return None

    def _open_stream(self, all_files, language, paths, replaced=None):
        from virtaal.models.fileset import UNKNOWN_LANGUAGE
        from virtaal.models.streammodel import StreamModel
        from virtaal.support.platforms import workcopy

        self.package = self._opening_package
        self._set_work_copies({})
        fileset = all_files.for_language(language) if language else all_files
        # Files waiting to be paired open with no units, to be paired.
        if not len(fileset) and not fileset.unpaired:
            raise ValueError('The file contains nothing to translate.')
        store = StreamModel(fileset, self)
        # Files of unknown language open in the current target language.
        store.language = None if language == UNKNOWN_LANGUAGE else language
        self._forget_stream()
        self.project = None
        self.opened_filename = None
        self.opened_paths = [os.path.abspath(path) for path in paths]
        self.all_files = all_files
        self.stream_language = language
        self.stream_language_replaced = replaced
        self.fileset = fileset
        self.store = store
        self._stream_handlers = (
            store,
            store.connect('file-loaded', self._on_file_loaded),
            store.connect('checks-filled', self._on_checks_filled),
            store.connect('files-filled', self._on_files_filled),
        )
        copies = (workcopy.for_file(entry.path) for entry in all_files)
        self._set_work_copies({copy.path: copy for copy in copies if copy is not None})
        self._name_work_copies(all_files)
        self._name_bundle_files(all_files)
        self._attach_loaded(store)
        self._modified = False
        self.main_controller.set_force_saveas(False)
        self.main_controller.set_saveable(False)
        self.main_controller.view.hide_read_only_notice()
        self._start_cursor()
        self.view.load_store(self.store)
        self.view.show()
        self._emit_store_loaded()

    def _name_work_copies(self, all_files):
        """Shows each work copy of the files by its project and component
        ("Hello/Program"), and in the unit list its language too when the
        files are in more than one, instead of its path in the work
        folder. The Files Panel shows one language's files, and says
        which."""
        several = len([code for code in all_files.languages() if code]) > 1
        for entry in all_files:
            copy = self.work_copies.get(entry.path)
            entry.label = entry.panel_label = None
            if copy is not None:
                entry.panel_label = copy.location()
                entry.label = copy.label() if several else entry.panel_label

    def _start_cursor(self):
        from .cursor import Cursor

        self.cursor = Cursor(self.store, self.store.stats['total'], circular=False)
        if self.is_stream():
            # Before the view's handler: the file must be read to edit it.
            self.cursor.connect('cursor-changed', self._on_stream_cursor_changed)

    def _on_stream_cursor_changed(self, cursor):
        if self.store is None or cursor.index < 0:
            return
        if self.store.approach(cursor.index):
            self.main_controller.view.refresh_title()

    def _forget_stream(self):
        if self._stream_handlers is not None:
            stream, *handlers = self._stream_handlers
            for handler in handlers:
                stream.disconnect(handler)
            stream.stop_filling()
            self._stream_handlers = None
        self.fileset = None
        self.all_files = None
        self.stream_language = None
        self.stream_language_replaced = None
        self.opened_paths = None

    def _on_file_loaded(self, store, number, peek):
        self._attach_loaded(store)
        self.view.file_loaded(store.segments[number], peek)

    def _on_files_filled(self, store, moved, renumbered):
        """Show the units of the files that waited for their stats, the
        cursor staying on its unit."""
        if store is not self.store:
            return
        self.unit_controller.prepare_for_save()
        index = moved(self.cursor.index) if self.cursor.index >= 0 else -1
        if self.cursor.scope is not None:
            # The Files Panel sets it again for the files chosen there.
            self.cursor.set_scope(None)
        self.view.reload_rows()
        self.main_controller.mode_controller.refresh_mode()
        if index >= 0 and self.cursor.index != index:
            self.cursor.force_index(index)
        else:
            # The rows are new: nothing is being edited.
            self.view.edit_cursor_unit()
        if store.checks is not None:
            store.update_checks()
            self.emit('checks-changed')
        self.emit('files-filled', renumbered)
        self.emit('stats-changed')

    def _on_checks_filled(self, store):
        if store is not self.store or store.checks is None:
            return
        store.update_checks()
        self.emit('checks-changed')

    def open_file(self, filename, uri='', forget_dir=False, template=None):
        """@param template: A L{TemplateMatch} for a monolingual file;
        found with L{find_template()} if not given."""
        from virtaal.support.platforms import workcopy

        self.package = self._opening_package
        self._set_work_copies({})
        work_copy = workcopy.for_file(filename)
        extension = filename.split(os.extsep)[-1]
        force_saveas = False
        readonly = self._is_readonly(filename)
        shown_name = os.path.basename(filename)
        self.opened_filename = None if forget_dir else os.path.abspath(filename)
        if extension == 'zip':
            self._open_bundle(filename)
        else:
            self._open_plain(filename, template)
        self._forget_stream()

        if len(self.store.get_units()) < 1:
            # clean up, otherwise self.store still contains the store
            self.close_file()
            raise ValueError('The file contains nothing to translate.')
        if work_copy is not None:
            work_copy.attach(self.store)
            self._set_work_copies({work_copy.path: work_copy})
        self._lock_bundle_units(self.store)

        # A translation carried over from a renamed key isn't in the file yet.
        pair = getattr(self.store, 'pair', None)
        self._modified = bool(pair and pair.carried)

        # if file is a template, force saveas
        filename, force_saveas = self._rename_pot_template(filename, force_saveas)

        # a read-only file also forces saveas (#517), but unlike a
        # template it's unexpected, so it gets a notice too
        pair = getattr(self.store, 'pair', None)
        source_only = pair is not None and pair.source_only
        if readonly or source_only:
            force_saveas = True

        # forgetting the directory only makes sense if we force save as
        if force_saveas and forget_dir:
            filename = os.path.split(filename)[1]
            self.store._trans_store.filename = filename

        self.main_controller.set_force_saveas(force_saveas)
        self.main_controller.set_saveable(self._modified)

        if readonly:
            self.main_controller.view.show_read_only_notice()
        else:
            self.main_controller.view.hide_read_only_notice()
        if source_only:
            from virtaal.support.templatefinder import translations_of

            self.main_controller.view.show_source_notice(translations_of(filename))
        self._hide_plural_notice()
        plural_problem = self.store.get_plural_problem()
        if plural_problem:
            # l10n: Shown on opening a file whose plural forms header gettext would
            # reject; the reason follows on the next line.
            text = _("The plural forms of %(file)s need fixing.") % {'file': shown_name}
            self._plural_notice = self.main_controller.view.show_info_notice(
                text + '\n' + plural_problem
            )

        self._start_cursor()

        self.view.load_store(self.store)
        self.view.show()

        self._emit_store_loaded()

    def _hide_plural_notice(self):
        notice = getattr(self, '_plural_notice', None)
        if notice is not None:
            self._plural_notice = None
            self.main_controller.view.hide_info_notice(notice)

    def _emit_store_loaded(self):
        self._leave_package(self.package)
        # Languages filled in on load (see LanguageController) aren't user edits.
        self._loading_store = True
        try:
            self.emit('store-loaded')
        finally:
            self._loading_store = False

    def save_file(self, filename=None):
        self.unit_controller.prepare_for_save()
        if self.bundle() is not None:
            self._save_bundle(filename)
            return
        if filename:
            # Saved elsewhere, the file is no longer the package's.
            self.package = None
            if self.work_copy is not None and os.path.abspath(filename) != self.work_copy.path:
                self._set_work_copies({})
        saving = self._saving()
        if self.project is None:
            self.store.save_file(
                filename
            )  # store.save_file() will raise an appropriate exception if necessary
        else:
            # self.store is a temporary copy; filename names the bundle.
            self.store.save_file()
            if filename:
                # Updating writes to the open bundle, so move to the new one first.
                self.project.save(filename)

            proj_fname = self.project.get_proj_filename(self.real_filename)
            with open(self.real_filename, 'rb') as infile:
                self.project.update_file(proj_fname, infile)
            outputfile, _fname = self.project.convert_forward(proj_fname, overwrite_output=True)
            outputfile.close()
            self.project.save()
        if self.package is not None:
            self.package.write_back()
        self._saved(saving)

    def _saving(self):
        """The paths of the files a save writes, with several open."""
        return [entry.path for entry in self.store.modified_entries()] if self.is_stream() else []

    def _saved(self, saving):
        # Only the local copies: Submit sends.
        if self.is_stream():
            saved = [self.work_copies[path] for path in saving if path in self.work_copies]
        else:
            saved = list(self.work_copies.values())
        for copy in saved:
            copy.saved()
        self.saved_copies = saved
        self.set_modified(False)
        # reaching here means the write succeeded, so any stale notice goes
        self.main_controller.view.hide_read_only_notice()
        # Unlike open_file()/close_file(), a save doesn't clear the undo
        # stack, so it needs its own explicit clean-point mark.
        self.main_controller.undo_controller.model.mark_clean()
        self.emit('stats-changed')
        self.emit('store-saved')

    def _save_bundle(self, filename):
        """Save the translation bundle open, to C{filename} for Save As:
        its files into the work folder, then the bundle. Translated
        documents too large to make now are made in a child process."""
        bundle = self.bundle()
        if not filename and bundle.changed_on_disk():
            from virtaal.support.documents import BundleChangedError

            self.main_controller.set_force_saveas(True)
            raise BundleChangedError(
                _(
                    # l10n: Shown when saving a translation bundle that another program, or
                    # another Virtaal, changed after it was opened
                    "The bundle was changed by another program since it was opened. Save it under a new name."
                )
            )
        saving = self._saving()
        self.store.save_file()
        self._stop_regeneration()
        left = bundle.save(filename, background=True)
        self._saved(saving)
        self._show_bundle_failures(bundle)
        if left:
            self._start_regeneration(bundle, left)

    def _show_bundle_failures(self, bundle):
        if not bundle.failures:
            return
        (document, _language), error = next(iter(bundle.failures.items()))
        text = _(
            # l10n: Shown after saving a translation bundle. %(document)s is a document's
            # name, such as report.odt; the reason follows
            "The translated document of %(document)s couldn't be made. The translation was saved."
        )
        self.main_controller.view.show_info_notice(text % {'document': document} + '\n' + error)

    _regeneration = None

    def _start_regeneration(self, bundle, entries):
        from gi.repository import GLib

        from virtaal.support import docregen

        self._regeneration = docregen.Regeneration(bundle.regeneration_jobs(entries))

        def poll():
            regeneration = self._regeneration
            if regeneration is None:
                return False
            result = regeneration.poll()
            if result is None:
                return True
            self._regeneration = None
            made, failed = result
            for number, error in failed.items():
                entry = entries[number]
                bundle.failures[(entry['document'], entry['language'])] = error
            if self.bundle() is bundle:
                try:
                    bundle.save_targets(made)
                except Exception:
                    logging.exception('Saving the translated documents')
                self._show_bundle_failures(bundle)
            return False

        GLib.timeout_add(200, poll)

    def _stop_regeneration(self):
        if self._regeneration is not None:
            self._regeneration.close()
            self._regeneration = None

    def binary_export(self, filename):
        from translate.tools.pocompile import POCompile

        compiler = POCompile()
        with open(filename, 'wb') as binary_output:
            binary_output.write(compiler.convertstore(self.store._trans_store))

    def close_file(self):
        self._stop_regeneration()
        self.package = None
        self._set_work_copies({})
        self._leave_package(None)
        self._forget_stream()
        del self.project
        self.project = None
        self.store = None
        self.set_modified(False)
        self.main_controller.view.hide_read_only_notice()
        self._hide_plural_notice()
        self.view.hide()  # This MUST be called BEFORE `self.cursor = None`
        self.emit(
            'store-closed'
        )  # This should be emitted BEFORE `self.cursor = None` to allow any other modules to disconnect from the cursor
        self.cursor = None
        import gc

        gc.collect()

    def revert_file(self):
        bundle = self.bundle()
        if bundle is not None and bundle.path:
            from virtaal.support.documents import DocumentBundle

            self.open_bundle(DocumentBundle.open(bundle.path), self.stream_language)
            return
        if self.is_stream():
            self.open_stream_language(self.stream_language)
            return
        filename = self.store.filename
        self._reopen(lambda: self.open_file(filename))

    def update_file(self, filename, uri=''):
        if not self.store:
            raise ValueError('No file is open to update.')
        if self.is_stream():
            raise ValueError('Several files are open.')
        if getattr(self.store, 'pair', None) is not None:
            raise ValueError('A monolingual file updates from its template when it opens.')

        from translate.storage import factory

        template = factory.getobject(filename)
        if not any(unit.istranslatable() for unit in template.units):
            raise ValueError('The file contains nothing to translate.')

        # Let's entirely clear things in the view to ensure that no signals
        # are still attached to old models before we start changing things. See
        # bug 1854.
        self.view.load_store(None)
        self.store.update_file(filename)

        self._modified = True
        self.main_controller.set_saveable(self._modified)
        # Don't force Save As (#3808) - the file already has a real path.

        self._start_cursor()

        self.view.load_store(self.store)
        self.view.show()

        self.emit('store-loaded')

    def update_store_checks(self, **kwargs):
        """Shortcut to C{StoreModel.update_stats()}"""
        store = self.get_store()
        if store is None:
            raise ValueError('No store to get checker from')
        return store.update_checks(**kwargs)

    def compare_stats(self, oldstats, newstats):
        def counts(stats):
            result = {state: len(stats[state]) for state in ('translated', 'fuzzy', 'untranslated')}
            result['total'] = sum(result.values())
            return result

        self.main_controller.show_template_update_notice(
            # l10n: this refers to updating a file to a new template (POT file)
            _("File Updated"),
            counts(oldstats),
            counts(newstats),
        )

    # EVENT HANDLERS #
    def _on_controller_registered(self, main_controller, controller):
        if controller is main_controller.lang_controller:
            controller.connect('source-lang-changed', self._on_source_lang_changed)
            controller.connect('target-lang-changed', self._on_target_lang_changed)
        elif controller is main_controller.checks_controller:
            controller.connect('unit-checked', self._on_unit_checked)
        else:
            return
        if main_controller.lang_controller and main_controller.checks_controller:
            main_controller.disconnect(self._controller_register_id)

    def _on_source_lang_changed(self, _sender, langcode):
        if self.store.set_source_language(langcode) and not self._loading_store:
            self.set_modified(True)

    def _on_target_lang_changed(self, _sender, langcode):
        if self.store.set_target_language(langcode) and not self._loading_store:
            self.set_modified(True)

    def _unit_done(self, _unit_controller, unit, modified):
        self.update_unit_stats(unit, modified)

    def update_unit_stats(self, unit, modified=True):
        """Move C{unit} to the stats lists for its current state and, if
        C{modified}, recheck it."""
        if self.store is None or not self.store.stats:
            return
        index = self._unit_index(unit)
        if index is None:
            return
        if self.store.update_unit_stats(index):
            self.emit('stats-changed')
        if modified and self.store.update_unit_checks(index):
            self.emit('checks-changed')

    def _unit_index(self, unit):
        if self.cursor and self.cursor.deref() is unit:
            return self.cursor.index
        return self.store.index_of(unit)

    def _on_unit_checked(self, _checks_controller, unit, checker, failures):
        # Keep store.checks in step with the checks shown while typing.
        if self.store is None or not getattr(unit, '_modified', False):
            return
        if checker is not self.store.get_checker():
            return
        index = self._unit_index(unit)
        if index is not None and self.store.update_unit_checks(index, failures):
            self.emit('checks-changed')

    def mark_unit_modified(self, unit):
        """Note a change to unit made other than by editing it."""
        self._unit_modified(None, unit)

    def _unit_modified(self, emitter, unit):
        # Guard against a late "modified" signal from a just-closed or
        # just-replaced file's teardown - both no file open at all, and
        # a unit belonging to a store that's since been replaced.
        if self.store is None:
            return
        index = self.store.index_of(unit)
        if index is None:
            return
        if self.is_stream():
            self.store.mark_modified(index)
        self.set_modified(True)
