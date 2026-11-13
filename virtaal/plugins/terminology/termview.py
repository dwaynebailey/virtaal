#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import locale

from virtaal.common import SignalTracker
from virtaal.views.baseview import BaseView
from virtaal.views.placeablesguiinfo import StringElemGUI
from virtaal.views.theme import base_color, is_inverse

first_matchers = []
"""The terminology matchers whose translations are offered first."""

_default_fg = '#006600'
_default_bg = '#eeffee'
_inverse_fg = '#c7ffff'
_inverse_bg = '#003700'


class TerminologyGUIInfo(StringElemGUI):
    """
    GUI info object for terminology placeables. A placeable with more than
    one match offers them as insert candidates to choose from.
    """

    # MEMBERS #
    fg = _default_fg
    bg = _default_bg

    def __init__(self, elem, textbox, **kwargs):
        assert elem.__class__.__name__ == 'TerminologyPlaceable'
        super().__init__(elem, textbox, **kwargs)

    # METHODS #
    def get_insert_candidates(self):
        if len(self.elem.translations) > 1:
            first = {
                unit.target
                for matcher in first_matchers
                for unit in matcher.matches(str(self.elem))
            }
            # translations comes from a set(), so sort for a stable order (#3912).
            return sorted(
                self.elem.translations, key=lambda text: (text not in first, locale.strxfrm(text))
            )
        return None

    @classmethod
    def update_style(self, widget):
        from gi.repository import Gtk

        _style = widget.get_style_context()
        fg = _style.get_color(Gtk.StateType.NORMAL)
        bg = base_color(_style)
        if is_inverse(fg, bg):
            self.fg = _inverse_fg
            self.bg = _inverse_bg
        else:
            self.fg = _default_fg
            self.bg = _default_bg


class TerminologyView(BaseView):
    """
    Does general GUI setup for the terminology plug-in.
    """

    # INITIALIZERS #
    def __init__(self, controller):
        self.controller = controller
        self._signal_tracker = SignalTracker()

    # METHODS #
    def destroy(self):
        self._signal_tracker.disconnect_all()

    def select_backends(self, parent):
        from virtaal.views.backendselect import select_backends

        select_backends(
            self.controller.main_controller,
            self.controller.plugin_controller,
            self.controller.config,
            'basetermmodel',
            title=_('Select Terminology Sources'),
            message=_('Select the sources of terminology suggestions'),
            size=(self.controller.config['backends_dialog_width'], 300),
            parent=parent,
        )
