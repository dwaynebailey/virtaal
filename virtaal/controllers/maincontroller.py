#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, GObject, Gtk

from virtaal.common import GObjectWrapper, pan_app
from virtaal.common.platform import platform
from virtaal.views.mainview import MainView

from .basecontroller import BaseController


def _one_line(*values):
    """C{values} with each run of whitespace, newlines included, as one space."""
    return tuple(' '.join((value or '').split()) for value in values)


def _strip_file_uri(filename):
    if filename.startswith('file://'):
        if platform.is_windows:
            return filename[len('file:///') :]
        return filename[len('file://') :]
    return filename


class MainController(BaseController):
    """The main controller that initializes the others and contains the main
    program loop."""

    __gtype_name__ = 'MainController'
    __gsignals__ = {
        'controller-registered': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'quit': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    # restart() asked for a quit that may still be cancelled.
    _restart_pending = False
    # Read by main.py once the main loop has ended.
    relaunch_on_exit = False

    # INITIALIZERS #
    def __init__(self):
        GObjectWrapper.__init__(self)
        self._force_saveas = False

        self._checks_controller = None
        self._lang_controller = None
        self._mode_controller = None
        self._placeables_controller = None
        self._plugin_controller = None
        self._store_controller = None
        self._undo_controller = None
        self._unit_controller = None
        self._welcomescreen_controller = None
        self._opening_file = False
        # Set alongside show_error() below on a failed open_file() -
        # lets a caller that needs the reason (main.py's startup path)
        # pick it up without open_file()'s own True/False contract
        # having to change for its many other callers.
        self.last_open_error = None
        from .bundleactions import BundleActions

        self.bundle_actions = BundleActions(self)
        self.view = MainView(self)

    def load_plugins(self):
        # Helper method to be called from virtaal.main
        if self.plugin_controller:
            self.plugin_controller.load_plugins()

    def destroy(self):
        self.store_controller.destroy()

    # ACCESSORS #
    def get_store(self):
        """Returns the store model of the current open translation store or C{None}."""
        return self.store_controller.get_store()

    def get_store_filename(self):
        """C{self.store_controller.get_store_filename()}"""
        return self.store_controller.get_store_filename()

    def get_store_title(self):
        """C{self.store_controller.get_store_title()}"""
        return self.store_controller.get_store_title()

    def get_translator_details(self):
        """The translator's name, e-mail address and team, asking for all
        three in one dialog if the name or e-mail is missing; the team
        is optional. Each is one line: they go into the PO header,
        where a newline starts a new header field (#4339)."""
        translator = pan_app.settings.translator
        details = _one_line(translator["name"], translator["email"], translator["team"])
        if all(details[:2]):
            return details
        answer = self.view.show_translator_dialog(*details)
        return _one_line(*answer) if answer else details

    def set_saveable(self, value):
        self.view.set_saveable(value)

    def set_force_saveas(self, value):
        self._force_saveas = value

    def get_force_saveas(self):
        return self._force_saveas

    def _get_checks_controller(self):
        return self._checks_controller

    def _set_checks_controller(self, value):
        self._checks_controller = value
        self.emit('controller-registered', self._checks_controller)

    checks_controller = property(_get_checks_controller, _set_checks_controller)

    def _get_lang_controller(self):
        return self._lang_controller

    def _set_lang_controller(self, value):
        self._lang_controller = value
        self.emit('controller-registered', self._lang_controller)

    lang_controller = property(_get_lang_controller, _set_lang_controller)

    def _get_mode_controller(self):
        return self._mode_controller

    def _set_mode_controller(self, value):
        self._mode_controller = value
        self.emit('controller-registered', self._mode_controller)

    mode_controller = property(_get_mode_controller, _set_mode_controller)

    def _get_placeables_controller(self):
        return self._placeables_controller

    def _set_placeables_controller(self, value):
        self._placeables_controller = value
        self.emit('controller-registered', self._placeables_controller)

    placeables_controller = property(_get_placeables_controller, _set_placeables_controller)

    def _get_plugin_controller(self):
        return self._plugin_controller

    def _set_plugin_controller(self, value):
        self._plugin_controller = value
        self.emit('controller-registered', self._plugin_controller)

    plugin_controller = property(_get_plugin_controller, _set_plugin_controller)

    def _get_store_controller(self):
        return self._store_controller

    def _set_store_controller(self, value):
        self._store_controller = value
        self.emit('controller-registered', self._store_controller)

    store_controller = property(_get_store_controller, _set_store_controller)

    def _get_undo_controller(self):
        return self._undo_controller

    def _set_undo_controller(self, value):
        self._undo_controller = value
        self.emit('controller-registered', self._undo_controller)

    undo_controller = property(_get_undo_controller, _set_undo_controller)

    def _get_unit_controller(self):
        return self._unit_controller

    def _set_unit_controller(self, value):
        self._unit_controller = value
        self.emit('controller-registered', self._unit_controller)

    unit_controller = property(_get_unit_controller, _set_unit_controller)

    def _get_welcomescreen_controller(self):
        return self._welcomescreen_controller

    def _set_welcomescreen_controller(self, value):
        self._welcomescreen_controller = value
        self.emit('controller-registered', self._welcomescreen_controller)

    welcomescreen_controller = property(
        _get_welcomescreen_controller, _set_welcomescreen_controller
    )

    # METHODS #
    def open_file(self, filename=None, uri='', forget_dir=False):
        """Open the file given by C{filename}.
        @returns: The filename opened, or C{None} if an error has occurred."""
        # The wait below pumps the main loop, which can re-enter this
        # same method (e.g. a macOS "open file" event arriving while
        # placeables_controller never gets constructed at all, as
        # happens outside the real app's own startup sequence) -
        # unbounded recursive stack growth, a multi-minute CI/local
        # hang with no error output. Bailing out on a re-entrant call,
        # rather than just bounding each level's own wait, is what
        # actually stops the recursion.
        if self._opening_file:
            return None
        if filename and os.path.isdir(_strip_file_uri(filename)):
            return self.open_files([filename])
        from virtaal.support import converters, documents, packages

        path = _strip_file_uri(filename) if filename else None
        if path and packages.kind_of(path):
            return self.open_package(path)
        if path and documents.is_bundle(path):
            return self._open_guarded(self.bundle_actions.open_bundle, path)
        if path and converters.is_document(path):
            return self._open_guarded(self.bundle_actions.open_documents, [path])
        self._opening_file = True
        try:
            return self._open_file(filename, uri, forget_dir)
        finally:
            self._opening_file = False

    def _open_guarded(self, open_, *args):
        """Run C{open_(*args)} unless something is being opened already."""
        if self._opening_file:
            return None
        self._opening_file = True
        try:
            self._wait_for_controllers()
            return open_(*args)
        finally:
            self._opening_file = False

    def confirm_leaving(self):
        """Whether to go on and leave the files open, asking first to
        save unsaved changes."""
        if not self.store_controller.is_modified():
            return True
        response = self._show_save_confirm_dialog()
        if response == 'save':
            return bool(self.save_file())
        return response != 'cancel'

    def _wait_for_controllers(self):
        # We might be a bit early for some of the other controllers, so
        # let's make it our problem and ensure the last ones are in the
        # main controller. Bounded, not an unconditional while - belt
        # and suspenders alongside the re-entrancy guard above.
        for _attempt in range(1000):
            if self.placeables_controller:
                break
            Gtk.main_iteration()
        else:
            import logging

            logging.warning('open_file(): gave up waiting for placeables_controller')

    def _reload_confirmed(self, filename):
        """Whether to go on opening C{filename}: asks first if it's the
        file already open."""
        if self.store_controller.store and self.store_controller.store.get_filename() == filename:
            promptmsg = _(
                'You selected the currently open file for opening. Do you want to reload the file?'
            )
            return self.show_prompt(msg=promptmsg, yes_label=_("_Reload"))
        return True

    def _open_file(self, filename, uri, forget_dir):
        self._wait_for_controllers()
        if filename is None:
            self._opening_file = False
            try:
                return self.view.open_file()
            finally:
                self._opening_file = True
        if self.store_controller.is_modified():
            response = self._show_save_confirm_dialog()
            if response == 'save':
                if not self.save_file():
                    return False
            elif response == 'cancel':
                return False
            # Unnecessary to test for 'discard'

        filename = _strip_file_uri(filename)

        template = False
        if self._reload_confirmed(filename):
            template = self._monolingual_template(filename)
        if template is False:
            return False

        try:
            with self.view.busy(_("Loading '%s'…") % os.path.basename(filename)):
                self.store_controller.open_file(
                    filename, uri, forget_dir=forget_dir, template=template
                )
                self.mode_controller.refresh_mode()
            return True
        except Exception as exc:
            import logging

            logging.exception('MainController.open_file(filename="%s", uri="%s")' % (filename, uri))
            self.last_open_error = str(exc)
            self.show_error(
                filename
                + ":\n"
                + _("Could not open file.\n\n%(error_message)s\n\nTry opening a different file.")
                % {'error_message': str(exc)}
            )
            return False

    def _monolingual_template(self, filename):
        """The L{TemplateMatch} to open a monolingual file with, asking
        the user when the project doesn't say; C{None} for any other
        file, C{False} if the user cancelled."""
        from virtaal.models.fileset import is_monolingual_source
        from virtaal.support import converters, templatefinder

        if not converters.is_monolingual(filename) or not os.path.isfile(filename):
            return None
        match = self.store_controller.find_template(filename)
        if match is not None:
            return match
        if is_monolingual_source(filename):
            # The source-language file itself.
            return templatefinder.TemplateMatch(filename, None, 'source')
        chosen = self.choose_template(filename)
        if not chosen:
            return False
        return templatefinder.TemplateMatch(chosen, None, 'chosen')

    def choose_template(self, filename):
        """Ask for the source-language file of the monolingual file
        C{filename}, and remember the answer for it.
        @returns: The file chosen (C{filename} itself when the user says
            it is the source-language file), or C{None}."""
        from virtaal.support import baseline, templatefinder

        chosen = self.view.ask_template(filename, templatefinder.guess_template(filename))
        if not chosen:
            return None
        baseline.remember_template(filename, chosen)
        return chosen

    def open_files(self, paths):
        """Open several files, or directories of them, as one list of
        units. One file opens as with L{open_file()}.
        @returns: Whether they opened."""
        paths = [_strip_file_uri(path) for path in paths]
        if len(paths) == 1 and not os.path.isdir(paths[0]):
            return self.open_file(paths[0])
        from virtaal.support import converters

        if all(converters.is_document(path) for path in paths):
            return self._open_guarded(self.bundle_actions.open_documents, paths)
        if self._opening_file:
            return None
        self._opening_file = True
        try:
            return self._open_files(paths)
        finally:
            self._opening_file = False

    def _open_files(self, paths):
        self._wait_for_controllers()
        if self.store_controller.is_modified():
            response = self._show_save_confirm_dialog()
            if response == 'save':
                if not self.save_file():
                    return False
            elif response == 'cancel':
                return False
        name = os.path.basename(os.path.normpath(paths[0]))
        try:
            with self.view.busy(_("Loading '%s'…") % name):
                self.store_controller.open_files(paths)
                self.mode_controller.refresh_mode()
            return True
        except Exception as exc:
            import logging

            logging.exception('MainController.open_files(%r)', paths)
            self.last_open_error = str(exc)
            self.show_error(
                paths[0]
                + ":\n"
                + _("Could not open file.\n\n%(error_message)s\n\nTry opening a different file.")
                % {'error_message': str(exc)}
            )
            return False

    def open_stream_language(self, language):
        """With several files open, open those in C{language} instead.
        @returns: Whether they opened."""
        if self._opening_file:
            return None
        self._opening_file = True
        try:
            if self.store_controller.is_modified():
                response = self._show_save_confirm_dialog()
                if response == 'save':
                    if not self.save_file():
                        return False
                elif response == 'cancel':
                    return False
            with self.view.busy(_("Loading '%s'…") % self.store_controller.stream_name()):
                self.store_controller.open_stream_language(language)
                self.mode_controller.refresh_mode()
            return True
        except Exception as exc:
            import logging

            logging.exception('MainController.open_stream_language(%r)', language)
            self.show_error(
                _("Could not open file.\n\n%(error_message)s\n\nTry opening a different file.")
                % {'error_message': str(exc)}
            )
            return False
        finally:
            self._opening_file = False

    def open_package(self, path):
        """Open a CAT tool package's bilingual files.
        @returns: Whether they opened."""
        if self._opening_file:
            return None
        self._opening_file = True
        try:
            self._wait_for_controllers()
            if self.store_controller.is_modified():
                response = self._show_save_confirm_dialog()
                if response == 'save':
                    if not self.save_file():
                        return False
                elif response == 'cancel':
                    return False
            with self.view.busy(_("Loading '%s'…") % os.path.basename(path)):
                self.store_controller.open_package(path)
                self.mode_controller.refresh_mode()
            return True
        except Exception as exc:
            import logging

            logging.exception('MainController.open_package(%r)', path)
            self.last_open_error = str(exc)
            self.show_error(
                path
                + ":\n"
                + _("Could not open file.\n\n%(error_message)s\n\nTry opening a different file.")
                % {'error_message': str(exc)}
            )
            return False
        finally:
            self._opening_file = False

    def create_return_package(self):
        """Write the open package's return package, saving first.
        @returns: Whether it was written."""
        package = self.store_controller.package
        if package is None or not package.can_return():
            return False
        if self.store_controller.is_modified() and not self.save_file():
            return False
        filename = self.view.show_save_dialog(
            current_filename=package.return_name(), title=_("Save")
        )
        if not filename:
            return False
        try:
            package.write_return(filename)
            return True
        except Exception as exc:
            import logging

            logging.exception('MainController.create_return_package(%r)', filename)
            self.show_error(
                _(
                    "Could not export file.\n\n%(error_message)s\n\nTry saving to a different location."
                )
                % {'error_message': str(exc)}
            )
            return False

    def _show_save_confirm_dialog(self):
        if getattr(self.store_controller, 'fileset', None) is None:
            return self.view.show_save_confirm_dialog()
        return self.view.show_save_confirm_dialog(self.store_controller.modified_stream_files())

    def open_tutorial(self):
        # Save on the disk a localized version of the tutorial using the
        # current locale.
        from virtaal.support.tutorial import create_localized_tutorial

        filename = create_localized_tutorial()

        # Open the file just created on the fly.
        self.open_file(filename, forget_dir=True)
        import os.path
        import shutil

        shutil.rmtree(os.path.dirname(filename))

    def save_file(self, filename=None, force_saveas=False):
        # we return True on success
        if not filename and (self.get_force_saveas() or force_saveas):
            filename = self.store_controller.get_bundle_filename()
            if filename is None:
                filename = self.get_store_filename() or ''
            filename = self.view.show_save_dialog(current_filename=filename, title=_("Save As"))
            if not filename:
                return False

        # A Save As target can be read-only too (e.g. the original file,
        # picked again and confirmed to replace) - fail before header-info
        # prompts or the write itself, not after (#517).
        if filename and os.path.exists(filename) and not os.access(filename, os.W_OK):
            self.show_error(
                _("%(filename)s is read-only.\n\nTry saving to a different location.")
                % {'filename': filename}
            )
            return False

        if self._do_save_file(filename):
            if self.get_force_saveas():
                self.set_force_saveas(False)
            return True
        else:
            return False

    def _do_save_file(self, filename=None):
        """Delegate saving to the store_controller, but do error handling.

        Return True on success, False otherwise."""
        try:
            self.store_controller.save_file(filename)
            return True
        except OSError as exc:
            self.show_error(
                _(
                    "Could not save file.\n\n%(error_message)s\n\nTry saving to a different location."
                )
                % {'error_message': str(exc)}
            )
        except Exception as exc:
            import logging

            logging.exception('MainController.save_file(filename="%s")' % (filename))
            self.show_error(
                _("Could not save file.\n\n%(error_message)s") % {'error_message': str(exc)}
            )
        return False

    def binary_export(self):
        # let's try to suggest a filename:
        filename = self.store_controller.get_bundle_filename()
        if filename is None:
            filename = self.get_store_filename() or ''
        if not (
            filename.endswith('.po') or filename.endswith('.po.bz2') or filename.endswith('.po.gz')
        ):
            self.show_error(_("Can only export Gettext PO files"))
            return False

        if filename.endswith('.po'):
            # TODO: suggest <domain>.mo - this gives fr.mo for fr.po and
            # gnome-shell.po.master.fr.mo for gnome-shell.po.master.fr.po.
            filename = filename[:-3] + '.mo'
        else:
            filename = 'messages.mo'
        filename = self.view.show_save_dialog(current_filename=filename, title=_("Export"))
        if not filename:
            return False
        try:
            self.store_controller.binary_export(filename)
            return True
        except OSError as exc:
            self.show_error(
                _(
                    "Could not export file.\n\n%(error_message)s\n\nTry saving to a different location."
                )
                % {'error_message': str(exc)}
            )
        except Exception as exc:
            import logging

            logging.exception('MainController.binary_export(filename="%s")' % (filename))
            self.show_error(
                _("Could not export file.\n\n%(error_message)s") % {'error_message': str(exc)}
            )
        return False

    def close_file(self):
        if self.store_controller.is_modified():
            response = self._show_save_confirm_dialog()
            if response == 'save':
                if not self.save_file():
                    return False
            elif response == 'cancel':
                return False
            # Unnecessary to test for 'discard'
        self.store_controller.close_file()

    def revert_file(self, filename=None):
        confirm = self.show_prompt(
            _("Revert to Saved"),
            _("Revert to the last saved copy and lose all changes?"),
            yes_label=_("_Revert"),
        )
        if not confirm:
            return

        try:
            self.store_controller.revert_file()
            self.mode_controller.refresh_mode()
        except Exception as exc:
            import logging

            logging.exception('MainController.revert_file(filename="%s")' % (filename))
            self.show_error(
                _("Could not open file.\n\n%(error_message)s\n\nTry opening a different file.")
                % {'error_message': str(exc)}
            )

    def update_file(self, filename, uri=''):
        """Update the current file using the file given by C{filename} as template.
        @returns: The filename opened, or C{None} if an error has occurred."""
        if self.store_controller.is_modified():
            response = self._show_save_confirm_dialog()
            if response == 'save':
                if not self.save_file():
                    return False
            elif response == 'cancel':
                return False
            # Unnecessary to test for 'discard'

        if self.store_controller.store and self.store_controller.store.get_filename() == filename:
            promptmsg = _(
                'You selected the currently open file for opening. Do you want to reload the file?'
            )
            if not self.show_prompt(msg=promptmsg, yes_label=_("_Reload")):
                return False

        try:
            self.store_controller.update_file(filename, uri)
            self.mode_controller.refresh_mode()
            return True
        except Exception as exc:
            import logging

            logging.exception(
                'MainController.update_file(filename="%s", uri="%s")' % (filename, uri)
            )
            self.show_error(
                _("Could not open file.\n\n%(error_message)s\n\nTry opening a different file.")
                % {'error_message': str(exc)}
            )
            return False

    def select_unit(self, unit, force=False):
        """Select the specified unit in the store view."""
        self.store_controller.select_unit(unit, force)

    def show_error(self, msg, parent=None):
        """Shortcut for C{self.view.show_error_dialog()}"""
        return self.view.show_error_dialog(message=msg, parent=parent)

    def show_prompt(self, title='', msg='', parent=None, yes_label='', no_label=''):
        """Shortcut for C{self.view.show_prompt_dialog()}"""
        return self.view.show_prompt_dialog(
            title=title, message=msg, parent=parent, yes_label=yes_label, no_label=no_label
        )

    def show_info(self, title='', msg='', parent=None):
        """Shortcut for C{self.view.show_info_dialog()}"""
        return self.view.show_info_dialog(title=title, message=msg, parent=parent)

    def show_template_update_notice(self, title, before, after):
        """Shortcut for C{self.view.show_template_update_notice()}"""
        return self.view.show_template_update_notice(title, before, after)

    def quit(self, force=False):
        # Gtk.Dialog.run() has its own main loop, unaffected by
        # Gtk.main_quit() below - force any open dialog closed first.
        dialogs_open = False
        for window in Gtk.Window.list_toplevels():
            if (
                isinstance(window, Gtk.Dialog)
                and window is not self.view.main_window
                and window.get_visible()
            ):
                window.response(Gtk.ResponseType.CANCEL)
                dialogs_open = True
        if dialogs_open:
            GLib.idle_add(self.quit, force)
            return False

        if self.store_controller.is_modified() and not force:
            response = self._show_save_confirm_dialog()
            if response == 'save':
                if not self.save_file():
                    self._restart_pending = False
                    return False
            elif response != 'discard':
                self._restart_pending = False
                return True

        if not (force and self.store_controller.is_modified()):
            # Unsaved edits stay for recovery to find.
            self.store_controller.leave_package()
        # A forced quit (SIGTERM) never relaunches.
        self.relaunch_on_exit = self._restart_pending and not force
        self._restart_pending = False
        self.view.hide()
        if self.plugin_controller:
            self.plugin_controller.shutdown()
        self.emit('quit')
        self.view.quit()
        return False

    def restart(self):
        """Quit as usual, and start Virtaal again only if it really
        quits (#3605)."""
        self._restart_pending = True
        self.quit()

    def run(self):
        self.view.show()
