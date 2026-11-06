#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


# This is the main loader for Virtaal. We want the GUI to show really quickly,
# so do a few unusual things. The _Deferer helps us to serialise events in idle
# spots in the gobject event loop, so that we can load some expensive stuff
# after the GUI is already showing.


from gi.repository import GLib

from virtaal.common.platform import platform


def _resolve_startup_path(startupfile):
    """Mirrors MainController._open_file()'s own file:// URI handling,
    just enough to check existence before committing to the deferred
    open below - not a replacement for that method's own handling,
    which still runs for real once the open is attempted."""
    if startupfile.startswith('file://'):
        if platform.is_windows:
            return startupfile[len('file:///') :]
        return startupfile[len('file://') :]
    return startupfile


def _is_several(startup):
    """Whether the start-up paths open as one list of units: several
    paths, or a directory."""
    import os

    return isinstance(startup, list) or os.path.isdir(_resolve_startup_path(startup))


def _first_path(startup):
    return startup[0] if isinstance(startup, list) else startup


def _open_filename(main_controller):
    """The file to reopen after a restart: the open one, or its bundle."""
    store = main_controller.get_store()
    if not store:
        return None
    project = getattr(main_controller.store_controller, 'project', None)
    zipfile = getattr(getattr(project, 'store', None), 'zip', None)
    if zipfile is not None:
        return zipfile.filename
    return store.get_filename()


class _Deferer:
    _todo = []

    def __call__(self, func, *args):
        def next_job():
            # We return True if we want gobject to schedule this again while
            # there is still something to do
            try:
                (func, args) = self._todo.pop(0)
            except IndexError:
                # This is possible if a previous func() was last, but added
                # something during execution. Therefore a different next_job()
                # is in the event loop to handle that job, and will be gone
                # when we get round to doing this one again.
                return False
            func(*args)
            return bool(self._todo)

        if not self._todo:
            # No existing jobs, so start one
            GLib.idle_add(next_job, priority=GLib.PRIORITY_LOW)
        self._todo.append((func, args))


class Virtaal:
    """The main Virtaal program entry point."""

    def __init__(self, startupfile):
        from virtaal.support.crash_dialog import install as install_crash_dialog

        install_crash_dialog()

        # PyGObject-wrapped GTK widgets routinely end up in Python
        # reference cycles (e.g. via bound-method signal connections),
        # and GTK's C-level widget dispose/unparent chain is not
        # reentrant: if the cyclic GC frees such a widget at an
        # arbitrary bytecode boundary - including while GTK is already
        # mid-callback tearing down something else - it segfaults deep
        # inside GTK (gtk_menu_shell_forall /
        # gtk_widget_propagate_hierarchy_changed, reached through
        # pygobject_dealloc -> g_object_unref -> gtk_widget_dispose).
        # Confirmed via macOS crash reports and reproduced directly
        # (about half of repeated test-suite runs crashed with the
        # collector left on, none did with it disabled). We rely on
        # CPython's refcounting for the deterministic case and simply
        # never run the cyclic collector for the rest.
        import gc

        gc.disable()

        # Before the window exists, so the desktop matches it to the entry.
        if platform.is_appimage:
            from virtaal.support import appimage

            appimage.integrate()

        # We try to get the welcomescreen loaded as early as possible
        from virtaal.controllers.filepanelcontroller import FilePanelController
        from virtaal.controllers.maincontroller import MainController
        from virtaal.controllers.recoverycontroller import RecoveryController
        from virtaal.controllers.storecontroller import StoreController
        from virtaal.controllers.welcomescreencontroller import WelcomeScreenController

        main_controller = MainController()
        StoreController(main_controller)
        FilePanelController(main_controller)
        recovery = RecoveryController(main_controller)

        self.defer = _Deferer()
        self.main_controller = main_controller
        self._install_signal_handlers()

        # WelcomeScreenController is always constructed (its 'store-closed'
        # handler is what re-shows it later if the user closes their file)
        # but only activated now for the no-file case - a startup file
        # shows a lightweight loading notice instead until the open
        # resolves, falling back to the full welcome screen on failure.
        wc = WelcomeScreenController(main_controller)
        # Resumes where an unclean end left off, instead of the welcome screen.
        if isinstance(startupfile, list):
            wanted = [_resolve_startup_path(path) for path in startupfile]
        else:
            wanted = startupfile and _resolve_startup_path(startupfile)
        startupfile = recovery.choose_startup_file(startupfile, wanted) or startupfile
        if startupfile:
            # Only gates the loading notice - _open_startup_file always
            # runs regardless, since it does controller construction
            # later deferred code depends on either way.
            import os

            if os.path.exists(_resolve_startup_path(_first_path(startupfile))):
                main_controller.view.show_loading_notice(_first_path(startupfile))
            self.defer(self._open_startup_file, startupfile)
        else:
            wc.activate()
            self.defer(self._open_with_welcome)
        recovery.show_startup_notices()

    def _install_signal_handlers(self):
        """SIGTERM (logout, shutdown, systemd or a test harness stopping
        the process), SIGHUP (the terminal closing) and SIGINT (Ctrl+C)
        quit through quit(), so plugins shut down their subprocesses,
        without the save-changes dialog nobody is there to answer.
        Unsaved work stays in the recovery slot and is restored on the
        next start-up. GLib.unix_signal_add integrates with the main
        loop, unlike signal.signal(). POSIX-only; Windows has no
        equivalent signal to catch here."""
        if platform.is_windows:
            return
        import signal

        def handle_signal(signum):
            recovery = getattr(self.main_controller, 'recovery_controller', None)
            if recovery is not None:
                recovery.end_session('signal')
            self.main_controller.quit(force=True)
            return GLib.SOURCE_REMOVE

        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, handle_signal, sig)

    def _open_with_file(self, startupfile):
        # Things needed for opening a file, including inter-dependencies
        from virtaal.controllers.langcontroller import LanguageController
        from virtaal.controllers.modecontroller import ModeController
        from virtaal.controllers.placeablescontroller import PlaceablesController
        from virtaal.controllers.unitcontroller import UnitController

        main_controller = self.main_controller

        UnitController(main_controller.store_controller)
        ModeController(main_controller)
        LanguageController(main_controller)
        PlaceablesController(main_controller)

        if _is_several(startupfile):
            paths = startupfile if isinstance(startupfile, list) else [startupfile]
            return main_controller.open_files(paths)
        return main_controller.open_file(startupfile)

    def _open_startup_file(self, startupfile):
        main_controller = self.main_controller
        opened = self._open_with_file(startupfile)
        main_controller.view.hide_loading_notice()
        main_controller.recovery_controller.startup_file_opened(opened)
        if opened:
            # Plugins load next, and process the open file.
            import os

            name = os.path.basename(os.path.normpath(_first_path(startupfile)))
            main_controller.view.show_progress(_("Loading '%s'…") % name)
        else:
            main_controller.welcomescreen_controller.activate()
            main_controller.view.show_startup_open_failure_notice(
                _first_path(startupfile), main_controller.last_open_error
            )
            # Otherwise it just sits there as a stale reminder about a
            # file the user has since moved past.
            main_controller.store_controller.connect(
                'store-loaded', lambda *a: main_controller.view.hide_startup_open_failure_notice()
            )
        self.defer(self._load_extras)

    def _open_with_welcome(self):
        from virtaal.controllers.langcontroller import LanguageController
        from virtaal.controllers.modecontroller import ModeController
        from virtaal.controllers.placeablescontroller import PlaceablesController
        from virtaal.controllers.unitcontroller import UnitController

        defer = self.defer
        main_controller = self.main_controller

        defer(UnitController, main_controller.store_controller)
        defer(ModeController, main_controller)
        defer(LanguageController, main_controller)
        defer(PlaceablesController, main_controller)
        self.defer(self._load_extras)

    def _load_extras(self):
        from virtaal.controllers.checkscontroller import ChecksController
        from virtaal.controllers.plugincontroller import PluginController
        from virtaal.controllers.prefscontroller import PreferencesController
        from virtaal.controllers.propertiescontroller import PropertiesController
        from virtaal.controllers.undocontroller import UndoController
        from virtaal.support.dictionary_download_watcher import (
            DictionaryDownloadWatcher,
        )

        defer = self.defer
        main_controller = self.main_controller

        defer(ChecksController, main_controller)
        defer(UndoController, main_controller)
        defer(PluginController, main_controller)
        defer(PreferencesController, main_controller)
        defer(PropertiesController, main_controller)
        defer(DictionaryDownloadWatcher, main_controller)
        defer(main_controller.load_plugins)
        defer(self._check_for_config_recovery)

        # Only bundled builds (macOS .app, Windows installer, AppImage) can't
        # already control their own updates the way a checkout/pip
        # install can.
        if platform.is_frozen:
            defer(self._check_for_update)
        # Deferred jobs run at low priority, after the plugins' own idle jobs.
        defer(main_controller.view.hide_progress)

    def _check_for_config_recovery(self):
        from virtaal.common import pan_app

        backup_path = pan_app.settings.config_recovery_backup
        if backup_path:
            self.main_controller.view.show_config_recovery_notice(backup_path)
        elif pan_app.reset_settings:
            self.main_controller.view.show_info_notice(
                # l10n: %s lists the settings by the names they have in the settings files.
                _('Some settings were invalid and were reset to their defaults: %s')
                % ', '.join(pan_app.reset_settings)
            )

    def _check_for_update(self):
        from virtaal.__version__ import ver
        from virtaal.support.update_check import UpdateChecker

        UpdateChecker(ver, self.main_controller.view.show_update_notice).check()

    # METHODS #
    def run(self):
        self.main_controller.run()
        # The main loop has ended and the plugins, the TM server with
        # them, are shut down: only now start the new instance.
        relaunch = getattr(self.main_controller, 'relaunch_on_exit', False)
        filename = relaunch and _open_filename(self.main_controller)
        self.main_controller.destroy()
        del self.main_controller
        if relaunch:
            from virtaal.support import relaunch as relauncher

            relauncher.relaunch(filename)


if __name__ == '__main__':
    virtaal = Virtaal('')
    virtaal.run()
