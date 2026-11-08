#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from gi.repository import GObject, Gtk
from translate.storage.placeables import StringElem, general
from translate.storage.placeables import parse as parse_placeables

from virtaal.common import GObjectWrapper, pan_app
from virtaal.support import formatplaceables
from virtaal.support.invisiblechars import InvisiblePlaceable
from virtaal.views import placeablesguiinfo

from .basecontroller import BaseController


class PlaceablesController(BaseController):
    """Basic controller for placeable-related logic."""

    __gtype_name__ = 'PlaceablesController'
    __gsignals__ = {
        'parsers-changed': (GObject.SignalFlags.RUN_FIRST, None, tuple()),
    }

    own_parsers = [*formatplaceables.parsers, InvisiblePlaceable.parse]
    """Virtaal's parsers, beside translate-toolkit's."""

    first_parsers = formatplaceables.parsers
    """Virtaal's parsers that go before translate-toolkit's."""

    parsers = []
    """The list of parsers that should be used by the main placeables C{parse()}
    function.
    @see translate.storage.placeables.parse"""

    # INITIALIZERS #
    def __init__(self, main_controller):
        GObjectWrapper.__init__(self)

        self.main_controller = main_controller
        self.main_controller.placeables_controller = self
        self._init_parsers()
        self._init_parser_descriptions()
        self._init_notarget_list()

        self.main_controller.view.main_window.connect('style-set', self._on_style_set)
        self.main_controller.view.main_window.connect('style-updated', self._on_style_set)
        self._on_style_set(self.main_controller.view.main_window, None)
        self.main_controller.connect('quit', self._on_quit)

    def _init_notarget_list(self):
        self.non_target_placeables = [
            general.AltAttrPlaceable,
            general.CamelCasePlaceable,
            general.CapsPlaceable,
            general.EmailPlaceable,
            general.FilePlaceable,
            general.PunctuationPlaceable,
            general.UrlPlaceable,
        ]

    def _init_parsers(self):
        disabled = [
            name
            for name, state in pan_app.settings.placeable_state.items()
            if state.lower() == 'disabled'
        ]

        self.parsers = []
        for parser in general.parsers + self.own_parsers:
            classname = parser.__self__.__name__.lower()
            if classname in disabled:
                continue
            self.add_parsers(parser)

    def _init_parser_descriptions(self):
        self.parser_info = {}

        # Test for presence of parser classes by hand
        self.parser_info[general.CamelCasePlaceable.parse] = (
            # l10n: See http://en.wikipedia.org/wiki/CamelCase
            _('CamelCase'),
            _('Words with internal capitalization, such as some brand names and WikiWords'),
        )
        self.parser_info[general.CapsPlaceable.parse] = (
            _('Capitals'),
            # l10n: this refers to "UPPERCASE" / "CAPITAL" letters
            _('Words containing uppercase letters only'),
        )
        self.parser_info[general.OptionPlaceable.parse] = (
            _('Command Line Options'),
            _('Application command line options, such as --help, -h and -I'),
        )
        self.parser_info[general.EmailPlaceable.parse] = (_('E-mail'), _('E-mail addresses'))
        self.parser_info[general.FilePlaceable.parse] = (
            _('File Names'),
            _('Paths referring to file locations'),
        )
        self.parser_info[general.FormattingPlaceable.parse] = (
            _('Placeholders (printf)'),
            _('Placeholders used in "printf" strings'),
        )
        self.parser_info[general.PythonFormattingPlaceable.parse] = (
            _('Placeholders (Python)'),
            _('Placeholders in Python strings'),
        )
        self.parser_info[general.JavaMessageFormatPlaceable.parse] = (
            _('Placeholders (Java)'),
            _('Placeholders in Java strings'),
        )
        self.parser_info[general.QtFormattingPlaceable.parse] = (
            _('Placeholders (Qt)'),
            _('Placeholders in Qt strings'),
        )
        self.parser_info[general.NumberPlaceable.parse] = (
            _('Numbers'),
            # l10n: 'decimal fractions' refer to numbers like 0.2 or 499,99
            _('Integer numbers and decimal fractions'),
        )
        self.parser_info[general.PunctuationPlaceable.parse] = (
            _('Punctuation'),
            _('Symbols and less frequently used punctuation marks'),
        )
        self.parser_info[general.UrlPlaceable.parse] = (
            _('URLs'),
            _('URLs, hostnames and IP addresses'),
        )
        self.parser_info[general.XMLEntityPlaceable.parse] = (
            # l10n: see http://en.wikipedia.org/wiki/Character_entity_reference
            _('XML Entities'),
            _('Entity references, such as &amp; and &#169;'),
        )
        self.parser_info[general.XMLTagPlaceable.parse] = (
            _('XML Tags'),
            _('XML tags, such as <b> and </i>'),
        )
        self.parser_info[InvisiblePlaceable.parse] = (
            _('Invisible Characters'),
            # l10n: Characters that show as nothing or as an ordinary space
            _('No-break spaces, zero-width characters and direction marks'),
        )
        self._init_format_parser_descriptions()
        # This code should eventually be used to add the SpacesPlaceable, but
        # it is not working well yet. We add the strings for translation so
        # that we won't need to break the string freeze later when it works.
        #        self.parser_info[general.AltAttrPlaceable.parse] = (
        #            _('"alt" Attributes'),
        #            _('Placeable for "alt" attributes (as found in HTML)')
        #        )
        #        self.parser_info[general.SpacesPlaceable.parse] = (
        #            _('Spaces'),
        #            _('Double spaces and spaces in unexpected positions')
        #        )

        (_('Spaces'),)
        _('Double spaces and spaces in unexpected positions')
        (_('"alt" Attributes'),)
        _('Placeable for "alt" attributes (as found in HTML)')
        (_('Placeholders (Drupal)'),)
        _('Placeholders in Drupal strings')

    def _init_format_parser_descriptions(self):
        fp = formatplaceables
        self.parser_info[fp.BracePlaceable.parse] = (
            _('Placeholders (brace)'),
            _('Placeholders such as {name} in Python, C# and Perl strings'),
        )
        self.parser_info[fp.DotNetPlaceable.parse] = (
            _('Placeholders (.NET)'),
            _('Format items such as {0:N2} in .NET strings'),
        )
        self.parser_info[fp.I18NextPlaceable.parse] = (
            # l10n: i18next is a JavaScript localisation library
            _('Placeholders (i18next)'),
            _('Placeholders such as {{count}} and $t(key) in i18next files'),
        )
        self.parser_info[fp.VuePlaceable.parse] = (
            # l10n: vue-i18n is a JavaScript localisation library
            _('Placeholders (vue-i18n)'),
            _('Placeholders such as {name} and @:key in vue-i18n files'),
        )
        self.parser_info[fp.FluentPlaceable.parse] = (
            # l10n: Fluent is Mozilla's localisation format
            _('Placeables (Fluent)'),
            _('Placeables such as { $user } in Fluent files'),
        )
        self.parser_info[fp.ICUPlaceable.parse] = (
            # l10n: ICU MessageFormat, as used in Flutter's ARB files
            _('Placeholders (ICU)'),
            _('Arguments such as {count} and the parts of plurals, in ARB files'),
        )
        self.parser_info[fp.ApplePlaceable.parse] = (
            _('Placeholders (Apple)'),
            _('Placeholders such as %@ in Apple strings'),
        )
        self.parser_info[fp.RubyPlaceable.parse] = (
            _('Placeholders (Ruby)'),
            _('Placeholders such as %{name} in Ruby strings'),
        )
        self.parser_info[fp.ShellVariablePlaceable.parse] = (
            _('Variables (shell)'),
            _('Variables such as $HOME in shell strings'),
        )

    # METHODS #
    def add_parsers(self, *newparsers):
        """Add the specified parsers to the list of placeables parser functions."""
        if [f for f in newparsers if not callable(f)]:
            raise TypeError('newparsers may only contain callable objects.')

        sortedparsers = []

        # First the format parsers and general.parsers, in order
        for parser in self.first_parsers + general.parsers:
            if parser in (self.parsers + list(newparsers)):
                sortedparsers.append(parser)
        # Then the others, those already added first
        for parser in self.parsers + list(newparsers):
            if parser not in sortedparsers:
                sortedparsers.append(parser)

        self.parsers = sortedparsers
        self.emit('parsers-changed')

    def apply_parsers(self, elems, parsers=None):
        """Apply all selected placeable parsers to the list of string elements
        given.

        @param elems: The list of C{StringElem}s to apply the parsers to."""
        if not isinstance(elems, list) and isinstance(elems, StringElem):
            elems = [elems]

        if parsers is None:
            parsers = self.get_parsers()

        for elem in elems:
            parse_placeables(elem, parsers)
        return elems

    def get_parsers(self, unit=None):
        """The enabled parsers for unit (by default the current unit): a
        format's own parsers only for its format and gettext flags (see
        L{formatplaceables})."""
        if unit is None:
            unit = getattr(self.main_controller.unit_controller, 'current_unit', None)
        store_controller = getattr(self.main_controller, 'store_controller', None)
        store = store_controller.get_store() if store_controller is not None else None
        wanted = formatplaceables.parsers_for(
            formatplaceables.format_of(store, unit), formatplaceables.unit_flags(unit)
        )
        return [p for p in self.parsers if p not in self.first_parsers or p in wanted]

    def get_parsers_for_textbox(self, textbox):
        """Get the parsers that should be applied to the given text box.
        This is intended for use by C{TextBox} to supply it with appropriate
        parsers, based on whether the text box is used for source- or target
        text."""
        parsers = self.get_parsers()
        if textbox in self.main_controller.unit_controller.view.targets:
            return [p for p in parsers if p.__self__ not in self.non_target_placeables]
        return parsers

    def get_gui_info(self, placeable):
        """Get an appropriate C{StringElemGUI} or sub-class instance based on
        the type of C{placeable}. The mapping between placeables classes and
        GUI info classes is defined in
        L{virtaal.views.placeablesguiinfo.element_gui_map}."""
        if not isinstance(placeable, StringElem):
            raise ValueError('placeable must be a StringElem.')
        for plac_type, info_type in placeablesguiinfo.element_gui_map:
            if isinstance(placeable, plac_type):
                return info_type
        return placeablesguiinfo.StringElemGUI

    def remove_parsers(self, *parsers):
        changed = False
        for p in parsers:
            if p in self.parsers:
                self.parsers.remove(p)
                changed = True
        if changed:
            self.emit('parsers-changed')

    # EVENT HANDLERS #
    def _on_style_set(self, widget, prev_style=None):
        unitview = self.main_controller.unit_controller.view
        # is_visible(), not props.visible: unused plural targets are hidden by
        # their container and still hold the last plural unit's text.
        textboxes = [tb for tb in unitview.sources + unitview.targets if tb.is_visible()]
        # Before the refresh below, so text is redrawn in the new colours.
        # With no file open there is no text box yet; a bare TextView has
        # the same colours.
        placeablesguiinfo.update_style(textboxes[0] if textboxes else Gtk.TextView())

        # refresh() emits 'changed'; a colour redisplay is not an edit, so
        # suppress it the way load_unit() does.
        unitview.disable_signals(['modified', 'insert-text', 'delete-text'])
        for textbox in textboxes:
            textbox.refresh()
        unitview.enable_signals(['modified', 'insert-text', 'delete-text'])

    def _on_quit(self, main_ctrlr):
        for parser in general.parsers + self.own_parsers:
            classname = parser.__self__.__name__.lower()
            enabled = parser in self.parsers
            if classname in pan_app.settings.placeable_state or not enabled:
                pan_app.settings.placeable_state[classname] = enabled and 'enabled' or 'disabled'
