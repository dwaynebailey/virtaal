#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import logging

from gi.repository import GLib, GObject

from virtaal.common import GObjectWrapper
from virtaal.support import dismissedchecks

from .basecontroller import BaseController

check_names = {
    'fuzzy': _("Fuzzy"),
    'untranslated': _("Untranslated"),
    'accelerators': _("Accelerators"),
    'acronyms': _("Acronyms"),
    'blank': _("Blank"),
    'brackets': _("Brackets"),
    'compendiumconflicts': _("Compendium conflict"),
    # l10n: Name of a quality check. Your names go in the "translator-credits" message, not here
    'credits': _("Translator credits"),
    'doublequoting': _("Double quotes"),
    'doublespacing': _("Double spaces"),
    'doublewords': _("Repeated word"),
    'emails': _("E-mail"),
    'endpunc': _("Ending punctuation"),
    'endwhitespace': _("Ending whitespace"),
    'escapes': _("Escapes"),
    'filepaths': _("File paths"),
    'functions': _("Functions"),
    'gconf': _("GConf values"),
    'kdecomments': _("Old KDE comment"),
    'long': _("Long"),
    # l10n: Name of a quality check: a translation server's limit on the length of a translation
    'maxlength': _("Maximum length"),
    'musttranslatewords': _("Must translate words"),
    'newlines': _("Newlines"),
    'nplurals': _("Number of plurals"),
    'notranslatewords': _("Don’t translate words"),
    'numbers': _("Numbers"),
    'options': _("Options"),
    # l10n: Name of a quality check: the tags such as {1} in a Phrase (Memsource) file
    'phrasetags': _("Phrase tags"),
    'printf': _("printf()"),
    'puncspacing': _("Punctuation spacing"),
    # l10n: Name of a quality check: Python format placeholders such as {name}
    'pythonbraceformat': _("Python braces"),
    # The placeholders a translation server's flag names.
    'placeholders': _("Placeholders"),
    'purepunc': _("Pure punctuation"),
    'sentencecount': _("Number of sentences"),
    'short': _("Short"),
    'simplecaps': _("Simple capitalization"),
    'simpleplurals': _("Simple plural(s)"),
    'singlequoting': _("Single quotes"),
    'spellcheck': _("Spelling"),
    'startcaps': _("Starting capitalization"),
    'startpunc': _("Starting punctuation"),
    'startwhitespace': _("Starting whitespace"),
    'tabs': _("Tabs"),
    'unchanged': _("Unchanged"),
    'urls': _("URLs"),
    'validchars': _("Valid characters"),
    'variables': _("Placeholders"),
    # A check failing on the Weblate server.
    'weblate': _("Weblate"),
    'xmltags': _("XML tags"),
    # Consider:
    #  -  hassuggestion
    #  -  isreview
}


class ChecksController(BaseController):
    """Controller for quality checks."""

    __gtype_name__ = 'ChecksController'
    __gsignals__ = {
        'checker-set': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'unit-checked': (GObject.SignalFlags.RUN_FIRST, None, (object, object, object)),
    }

    CHECK_TIMEOUT = 500
    """Time to wait before performing checks on the current unit."""

    # INITIALIZERS #
    def __init__(self, main_controller):
        GObjectWrapper.__init__(self)

        self.main_controller = main_controller
        self.main_controller.checks_controller = self
        self.store_controller = main_controller.store_controller

        main_controller.store_controller.connect('store-loaded', self._on_store_loaded)
        main_controller.unit_controller.connect('unit-modified', self._on_unit_modified)
        if main_controller.lang_controller:
            main_controller.lang_controller.connect(
                'target-lang-changed', self._on_target_lang_changed
            )
        else:
            main_controller.connect('controller-registered', self._on_controller_registered)

        self.code = None
        self._checker = None
        self._check_timer_active = False
        self._checker_code_to_name = {
            None: _('Default'),
            # we keep the "openoffice" key for compatibility with the toolkit
            # and existing file headers
            "openoffice": _('LibreOffice'),
            "mozilla": _('Mozilla'),
            "kde": _('KDE'),
            "qt": _('Qt'),
            "gnome": _('GNOME'),
            "drupal": _('Drupal'),
        }
        self._checker_name_to_code = dict(
            [(value, key) for (key, value) in self._checker_code_to_name.items()]
        )
        self._checker_info = None
        self._checker_menu_items = {}
        self._cursor_connection = ()
        self.last_unit = None

        self._projview = None
        self._unitview = None

        if self.store_controller.get_store():
            # We are too late for the initial 'store-loaded'
            self._on_store_loaded(self.store_controller)

    # ACCESSORS #
    def _get_checker_info(self):
        if not self._checker_info:
            from virtaal.support.checkerspec import checker_classes

            # A new checker code also needs a name in _checker_code_to_name.
            self._checker_info = checker_classes()
        return self._checker_info

    checker_info = property(_get_checker_info)

    def _get_projview(self):
        from virtaal.views.checksprojview import ChecksProjectView

        if self._projview is None:
            self._projview = ChecksProjectView(self)
            self._projview.show()
        return self._projview

    projview = property(_get_projview)

    def focus(self):
        """Focus the status bar's Checks button."""
        self.projview.btn_proj.grab_focus()

    def _get_unitview(self):
        from virtaal.views.checksunitview import ChecksUnitView

        if self._unitview is None:
            self._unitview = ChecksUnitView(self)
            self._unitview.show()
        return self._unitview

    unitview = property(_get_unitview)

    def default_checker_code(self, store):
        """The project style for C{store}: its own, else its format's;
        none without a file (a folder of files still to pair)."""
        if store is None:
            return None
        code = store.getprojectstyle()
        if code:
            return code
        from translate.storage import qm, ts2

        format_codes = (
            (ts2.tsfile, 'qt'),
            (qm.qmfile, 'qt'),
        )
        for store_class, code in format_codes:
            if isinstance(store, store_class):
                return code
        return None

    def get_checker(self):
        return self._checker

    def set_checker_by_name(self, name):
        self.set_checker_by_code(self._checker_name_to_code.get(name, None))

    def set_checker_by_code(self, code):
        target_lang = self.main_controller.lang_controller.target_lang.code
        if not target_lang:
            target_lang = None
        self._checker = self.checker_info.get(code, self.checker_info[None])()
        self._checker.config.updatetargetlanguage(target_lang)
        from virtaal.support.extrachecks import with_extra_checks

        store = self.store_controller.get_store()
        self._checker = with_extra_checks(
            self._checker, store._trans_store if store is not None else None
        )

        self.emit('checker-set', code)
        self.projview.set_checker_name(
            self._checker_code_to_name.get(code, self._checker_code_to_name[None])
        )
        self.code = code
        if self.main_controller.unit_controller.current_unit:
            self.check_unit(self.main_controller.unit_controller.current_unit)

    # METHODS #
    def check_unit(self, unit):
        checker = self.get_checker()
        if not checker:
            logging.debug('No checker instantiated :(')
            return
        from virtaal.support.extrachecks import ExtraChecksFilter

        if isinstance(checker, ExtraChecksFilter):
            everything = checker.run_filters(unit, with_dismissed=True)
        else:
            everything = checker.run_filters(unit)
        self.last_failures = dismissedchecks.filter_failures(unit, everything)
        if self.last_failures:
            logging.debug('Failures: %s' % (self.last_failures))
        dismissed = {k: v for k, v in everything.items() if k not in self.last_failures}
        self.unitview.update(self.last_failures, dismissed, dismissedchecks.can_dismiss(unit))
        self.emit('unit-checked', unit, checker, self.last_failures)
        return self.last_failures

    def dismiss_check(self, check, dismiss=True):
        """Dismiss check's result on the current unit as fine, or take
        that back (#3276)."""
        unit = self.last_unit
        if unit is None or not dismissedchecks.set_dismissed(unit, check, dismiss):
            return
        unit._modified = True
        self.store_controller.mark_unit_modified(unit)
        # So crash recovery keeps it too.
        self.main_controller.unit_controller.emit('unit-modified', unit)
        self.check_unit(unit)

    def _check_timer_expired(self, unit):
        self._check_timer_active = False
        if unit is not self.last_unit:
            return
        self.check_unit(unit)

    def _start_check_timer(self):
        if self._check_timer_active:
            return
        if not self.last_unit:
            # haven't changed units yet, probably strange timing issue
            return
        self._check_timer_active = True
        GLib.timeout_add(
            self.CHECK_TIMEOUT,
            self._check_timer_expired,
            self.last_unit,
            priority=GLib.PRIORITY_LOW,
        )

    def get_check_name(self, check):
        """Return the human readable form of the given check name."""
        name = check_names.get(check, None)
        if not name and check.startswith('check-'):
            check = check[len('check-') :]
            name = check_names.get(check, None)
        if not name:
            name = check
        return name

    # EVENT HANDLERS #
    def _on_controller_registered(self, main_controller, controller):
        if controller is main_controller.lang_controller:
            controller.connect('target-lang-changed', self._on_target_lang_changed)

    def _on_cursor_changed(self, cursor):
        self.last_unit = cursor.deref()
        # No unit in a folder whose files all wait to be paired.
        if self.last_unit is not None:
            self.check_unit(self.last_unit)

    def _on_target_lang_changed(self, lang_controller, langcode):
        current_checker = self.get_checker()
        if current_checker:
            current_checker.config.updatetargetlanguage(langcode)
            self.emit('checker-set', self.code)
            if self.last_unit:
                self.check_unit(self.last_unit)

    def _on_store_loaded(self, store_controller):
        self.set_checker_by_code(self.default_checker_code(store_controller.store._trans_store))
        if self._cursor_connection:
            widget, connect_id = self._cursor_connection
            widget.disconnect(connect_id)
        self._cursor_connection = (
            store_controller.cursor,
            store_controller.cursor.connect('cursor-changed', self._on_cursor_changed),
        )
        self._on_cursor_changed(store_controller.cursor)

    def _on_unit_modified(self, unit_controller, unit):
        self._start_check_timer()
