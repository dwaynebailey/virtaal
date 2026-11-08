#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The unit editor's offer to number a translation's printf
placeholders once they are out of the source's order (see
L{virtaal.support.positionalprintf}): a notice when it happens, and an
entry in the target's context menu."""

from gi.repository import Gtk

from virtaal.support import formatplaceables, positionalprintf


class PlaceholderNumbering:
    def __init__(self, unitview):
        self.view = unitview
        self._notice = None
        self._closed_for = None
        """The unit whose notice was closed: not shown again for it."""

    @property
    def _main_controller(self):
        return self.view.controller.main_controller

    def _applies(self):
        unit = getattr(self.view, 'unit', None)
        if unit is None:
            return False
        store_controller = getattr(self._main_controller, 'store_controller', None)
        store = store_controller.get_store() if store_controller is not None else None
        return positionalprintf.applies(
            formatplaceables.format_of(store, unit), formatplaceables.unit_flags(unit)
        )

    def forms(self):
        """(target text box, its text numbered) of each shown target (a
        plural form each) whose placeholders need numbers."""
        if not self._applies():
            return []
        found = []
        for textbox in self.view.targets:
            parent = textbox.get_parent()
            if parent is None or not parent.props.visible:
                continue
            text = positionalprintf.numbered(
                textbox.selector_textbox.get_text(), textbox.get_text()
            )
            if text is not None:
                found.append((textbox, text))
        return found

    def number(self, *_args):
        """Number the placeholders of every target form that needs it,
        one undo step each."""
        undo_controller = self._main_controller.undo_controller
        for textbox, text in self.forms():
            undo_controller.push_current_text(textbox)
            textbox.set_text(text)
        self.update()

    def update(self):
        """Offer numbering when a target needs it, else take the offer
        back."""
        if not self.forms():
            self._hide()
        elif self._notice is None and self._closed_for is not self.view.unit:
            self._show()

    def unit_loaded(self):
        self._closed_for = None
        self._hide()
        self.update()

    def populate_popup(self, textbox, menu):
        """Add the context menu entry to a target's menu when it needs
        numbers."""
        if textbox not in self.view.targets or not self.forms():
            return
        menu.append(Gtk.SeparatorMenuItem())
        # l10n: Changes %s, %d... in the translation into %1$s, %2$d... by
        # their order in the source, so that they can be in another order
        item = Gtk.MenuItem(label=_('Number the Placeholders'))
        item.connect('activate', self.number)
        menu.append(item)
        menu.show_all()

    def _show(self):
        mainview = self._main_controller.view

        def on_closed():
            self._notice = None
            self._closed_for = self.view.unit

        text = _(
            # l10n: The translation has placeholders like %s and %d in another
            # order than the source. Numbered (%2$d, %1$s), each keeps its value.
            'The placeholders are in a different order from the source, '
            'so they would show the wrong values. Numbering them keeps '
            "each one's value."
        )
        # l10n: Button: number the placeholders (%s to %1$s)
        button = _('_Number Them')
        self._notice = mainview.show_info_notice(
            text, action=(button, self.number), on_close=on_closed
        )

    def _hide(self):
        if self._notice is not None:
            notice, self._notice = self._notice, None
            self._main_controller.view.hide_info_notice(notice)
