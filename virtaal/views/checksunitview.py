#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import locale

from gi.repository import Gdk, Gtk, Pango
from translate.lang import factory as lang_factory

from virtaal.common.pan_app import ngettext, ui_language
from virtaal.views.widgets.popupwidgetbutton import POS_SE_NE, PopupWidgetButton

from .baseview import BaseView


class ChecksUnitView(BaseView):
    """The unit specific view for quality checks."""

    # Room for the heading, a row and a scrollbar.
    MIN_HEIGHT = 80

    # INITIALIZERS #
    def __init__(self, controller):
        self.controller = controller
        main_controller = controller.main_controller
        main_window = main_controller.view.main_window

        self.popup_content = self._create_popup_content()
        self._create_checks_button(self.popup_content, main_window)
        self.popup_content.hide()
        self._create_menu_item()
        main_controller.store_controller.connect('store-closed', self._on_store_closed)
        main_controller.store_controller.connect('store-loaded', self._on_store_loaded)

        self._prev_failures = None
        self._listsep = lang_factory.getlanguage(ui_language).listseperator

    def _create_checks_button(self, widget, main_window):
        self.lbl_btnchecks = Gtk.Label()
        self.lbl_btnchecks.show()
        self.lbl_btnchecks.set_ellipsize(Pango.EllipsizeMode.END)
        self.btn_checks = PopupWidgetButton(
            widget, label=None, popup_pos=POS_SE_NE, main_window=main_window, sticky=True
        )
        self.btn_checks.set_property('relief', Gtk.ReliefStyle.NONE)
        self.btn_checks.set_update_popup_geometry_func(self.update_geometry)
        self.btn_checks.add(self.lbl_btnchecks)

    def _create_menu_item(self):
        mainview = self.controller.main_controller.view
        self.mnu_checks = mainview.gui.get_object('mnu_checks')
        self.mnu_checks.connect('activate', self._on_activated)

    def _create_popup_content(self):
        vb = Gtk.VBox(spacing=4, border_width=6)
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        frame.add(vb)

        # Labels, unlike TreeView rows, wrap to the pop-up's width and are
        # measured as soon as they are added.
        self._name_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self.check_rows = []
        header = [self._make_label(title) for title in (_('Quality Check'), _('Description'))]
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
        for label in header:
            label.get_style_context().add_class('dim-label')
            label.set_attributes(attrs)
        vb.pack_start(self._make_row(*header), False, False, 0)

        # Scrolls when there isn't room for every row (see update_geometry),
        # under the heading, with a scrollbar that shows there are more.
        self.box_checks = Gtk.VBox(spacing=4)
        self._scrolled = scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        scrolled.set_propagate_natural_width(True)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_overlay_scrolling(False)
        scrolled.add(self.box_checks)
        vb.pack_start(scrolled, True, True, 0)
        vb.show_all()

        return frame

    def _make_row(self, name_label, desc_label):
        row = Gtk.HBox(spacing=12)
        self._name_group.add_widget(name_label)
        row.pack_start(name_label, False, False, 0)
        row.pack_start(desc_label, True, True, 0)
        return row

    @staticmethod
    def _make_label(text, wrap=False):
        label = Gtk.Label(label=text, xalign=0, yalign=0)
        if wrap:
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        return label

    # METHODS #
    def show(self):
        parent = self.controller.main_controller.unit_controller.view._widgets['vbox_right']
        parent.pack_start(self.btn_checks, False, True, 0)
        self.btn_checks.show()

    def hide(self):
        if self.btn_checks.get_active():
            self.btn_checks.clicked()

    def update(self, failures, dismissed=None, can_dismiss=False):
        """Show failures, and the dismissed checks that fail too, with a
        button to dismiss or restore each if can_dismiss (#3276)."""
        # We don't want to show "untranslated"
        failures.pop('untranslated', None)
        dismissed = dismissed or {}
        state = (failures, dismissed, can_dismiss)
        if state == self._prev_failures:
            return
        self._prev_failures = state
        if not failures and not dismissed:
            # We want an empty button, but this causes a bug where subsequent
            # updates don't show, so we set it to an invisible character
            self.lbl_btnchecks.set_text("\u202a")
            # The button stays pressed, unseen, with nothing to pop up, and
            # out of the focus chain.
            self.btn_checks.set_opacity(0)
            self.btn_checks.set_can_focus(False)
            self.btn_checks.get_accessible().set_name(_("Quality checks"))
            self.popup_content.hide()
            self.btn_checks.update_popup()
            return

        for name_label, _desc_label in self.check_rows:
            name_label.get_parent().destroy()
        self.check_rows = []
        names = self._add_rows(failures, False, can_dismiss)
        dismissed_names = self._add_rows(dismissed, True, can_dismiss)
        self.box_checks.show_all()

        if names:
            name_str = self._listsep.join(names)
            accessible = name_str
        else:
            # Only dismissed ones: still there to restore, and not
            # dimmed, which would look disabled.
            # l10n: The quality checks a translator marked as fine on this unit
            accessible = _("Dismissed: %(checks)s") % {
                'checks': self._listsep.join(dismissed_names)
            }
            count = len(dismissed_names)
            # l10n: On the quality checks button: this many checks on the
            # unit were marked as fine. A check mark goes before it.
            name_str = '\u2713 ' + ngettext('%(count)d dismissed', '%(count)d dismissed', count) % {
                'count': count
            }
        self.lbl_btnchecks.set_text(name_str)
        self.btn_checks.get_accessible().set_name(accessible)
        self.btn_checks.set_opacity(1)
        self.btn_checks.set_can_focus(True)
        self.popup_content.show()
        self.btn_checks.update_popup()

    def _add_rows(self, failures, dismissed, can_dismiss):
        """A row per failure, sorted by name; returns the names."""
        nice_name = self.controller.get_check_name
        names = []
        for check, desc in sorted(failures.items(), key=lambda x: locale.strxfrm(nice_name(x[0]))):
            labels = (self._make_label(nice_name(check)), self._make_label(desc, wrap=True))
            self.check_rows.append(labels)
            row = self._make_row(*labels)
            if dismissed:
                for label in labels:
                    label.get_style_context().add_class('dim-label')
            if can_dismiss:
                row.pack_end(self._make_dismiss_button(check, dismissed), False, False, 0)
            self.box_checks.pack_start(row, False, False, 0)
            names.append(nice_name(check))
        return names

    def _make_dismiss_button(self, check, dismissed):
        if dismissed:
            # l10n: Show a quality check result again that was marked as fine
            button = Gtk.Button(label=_("Restore"))
            button.set_tooltip_text(_("Show this check’s result on this unit again"))
        else:
            # l10n: Mark a quality check result as fine (a false positive)
            button = Gtk.Button(label=_("Dismiss"))
            button.set_tooltip_text(
                _(
                    "Mark this result as fine: it stays hidden until the source or translation changes"
                )
            )
        button.set_valign(Gtk.Align.START)
        button.connect(
            'clicked', lambda _button: self.controller.dismiss_check(check, not dismissed)
        )
        return button

    def update_geometry(self, popup, popup_alloc, btn_alloc, btn_window_xy, geom):
        x, y, width, height = geom

        textbox = self.controller.main_controller.unit_controller.view.sources[0]
        alloc = textbox.get_allocation()

        max_width = int(alloc.width * 1.3)
        if width > max_width and self._limit_descriptions(max_width - width):
            size = popup.get_child().get_preferred_size()[1]
            width, height = size.width, size.height
        width = min(width, max_width)
        # It opens above the button: no taller than the room up to the top
        # of the screen, which clips it (macOS).
        height = self._unscrolled_height(height)
        room = btn_window_xy.y - self._top_limit()
        scrolls = height > room
        if scrolls:
            height = self._whole_rows(max(room, self.MIN_HEIGHT), height)
        # Only while it scrolls: a scrollbar's minimum length would
        # otherwise make a short list's pop-up taller than its rows.
        policy = Gtk.PolicyType.AUTOMATIC if scrolls else Gtk.PolicyType.NEVER
        if self._scrolled.get_policy()[1] != policy:
            self._scrolled.set_policy(Gtk.PolicyType.NEVER, policy)
        return x, y, width, height

    def _whole_rows(self, height, natural):
        """height, less any part of a row it would cut off."""
        fixed = natural - self.box_checks.get_preferred_height()[1]
        spacing = self.box_checks.get_spacing()
        used = 0
        for shown, row in enumerate(self.box_checks.get_children()):
            row_height = row.get_preferred_height()[1] + (spacing if shown else 0)
            if shown and fixed + used + row_height > height:
                break
            used += row_height
        return fixed + used if used else height

    def _unscrolled_height(self, height):
        """The pop-up's height without a scrollbar's minimum length,
        which would keep a short list's pop-up looking too tall."""
        return (
            height
            - self._scrolled.get_preferred_height()[1]
            + self.box_checks.get_preferred_height()[1]
        )

    def _limit_descriptions(self, change):
        """Wrap the descriptions change pixels narrower (a negative
        number). Returns whether that changed their wrapping."""
        labels = [desc_label for name_label, desc_label in self.check_rows]
        if not labels:
            return False
        widest = max(label.get_preferred_width()[1] for label in labels)
        # A label's maximum width in characters is measured with these
        # same metrics.
        metrics = labels[0].get_pango_context().get_metrics(None, None)
        char_width = (
            max(metrics.get_approximate_char_width(), metrics.get_approximate_digit_width())
            / Pango.SCALE
        )
        chars = max(int((widest + change) / char_width), 10)
        if all(label.get_max_width_chars() == chars for label in labels):
            return False
        for label in labels:
            label.set_max_width_chars(chars)
        return True

    def _top_limit(self):
        window = self.controller.main_controller.view.main_window.get_window()
        if window is None:
            return 0
        monitor = Gdk.Display.get_default().get_monitor_at_window(window)
        return monitor.get_workarea().y if monitor else 0

    # EVENT HANDLERS #
    def _on_activated(self, menu_iitem):
        self.btn_checks.clicked()

    def _on_store_closed(self, store_controller):
        self.mnu_checks.set_sensitive(False)
        self.hide()

    def _on_store_loaded(self, store_controller):
        self.mnu_checks.set_sensitive(True)
