#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Review in the unit editor, for a work copy of a platform that
can_suggest or can_comment (#3337).

Shown a unit of a work copy, the editor asks its server (review.py),
a moment after the unit settles, and then shows:

- the unit's comments, on its source text too, with its translator
  notes, as a Trados or memoQ file's comments are ("Comment from
  anna: ...");
- others' suggestions below them, each with Accept, which makes it the
  unit's text (to go with the next Submit), and for a reviewer
  (WorkCopy.may_review()) Reject, which dismisses it on the server.

The source and target boxes' context menus add "Add Comment…" and
"Report a Source Problem…": a comment about this translation, or one
about the source text for the project's maintainers (#4199), sent at
once. A platform that can do neither shows none of this.
"""

from gi.repository import GLib, Gtk, Pango

from virtaal.views.widgets.storecellrenderer import compute_optimal_height
from virtaal.views.widgets.util import run_dialog

from .review import Reviews

DELAY = 300
"""Milliseconds a unit is shown before its server is asked."""


def _forms(target):
    return '\n'.join(target)


def suggestion_text(suggestion):
    """ "Suggestion from anna: Stoor" """
    if suggestion.user:
        # l10n: Someone's suggested translation of the unit, from a translation server such as Weblate
        text = _('Suggestion from %(user)s: %(text)s')
    else:
        # l10n: A suggested translation of the unit by someone not named, from a translation server
        text = _('Suggestion: %(text)s')
    return text % {'user': suggestion.user, 'text': _forms(suggestion.target)}


def comment_lines(comments):
    """A server's comments as lines of the translator notes."""
    from virtaal.views.unitview import _comment_lines

    return _comment_lines((c.user, c.text) for c in comments)


class SuggestionRow(Gtk.Box):
    """A suggestion's text and its buttons, side by side."""

    def __init__(self, text):
        super().__init__(spacing=6)
        self.label = Gtk.Label(label=text, xalign=0, hexpand=True)
        self.label.set_line_wrap(True)
        self.label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label.set_selectable(True)
        self.pack_start(self.label, True, True, 0)
        self.buttons = []

    def add_button(self, label, action):
        button = Gtk.Button(label=label)
        button.connect('clicked', lambda _button: action())
        self.pack_start(button, False, False, 0)
        self.buttons.append(button)
        return button


@compute_optimal_height.register(SuggestionRow)
def _row_height(row, width, limit=0):
    """The editor's measure: the text gets the width the buttons leave
    (the buttons' own labels keep theirs)."""
    if width >= 0:
        taken = sum(b.get_preferred_width()[1] + row.get_spacing() for b in row.buttons)
        width = max(0, width - taken)
    compute_optimal_height(row.label, width, limit)


class CommentDialog:
    """Asks for a comment's text: about the translation, or a problem
    with source (its text shown)."""

    def __init__(self, parent, source=None):
        self.dialog = Gtk.Dialog(transient_for=parent, modal=True)
        if source is None:
            # l10n: The title of the dialog writing a comment on a unit, for a translation server
            self.dialog.set_title(_('Add Comment'))
        else:
            # l10n: The title of the dialog telling a translation server's project of a mistake or unclear wording in a unit's source text
            self.dialog.set_title(_('Report a Source Problem'))
        self.dialog.set_default_size(420, -1)
        self.dialog.add_button(_('_Cancel'), Gtk.ResponseType.CANCEL)
        self.ok = self.dialog.add_button(_('_Submit'), Gtk.ResponseType.OK)
        self.dialog.set_default_response(Gtk.ResponseType.OK)
        content = self.dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(6)
        if source is not None:
            shown = Gtk.Label(xalign=0, wrap=True, selectable=True, max_width_chars=60)
            shown.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            shown.set_markup('<b>%s</b>' % GLib.markup_escape_text(source))
            content.pack_start(shown, False, False, 0)
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.text.get_accessible().set_name(self.dialog.get_title())
        scrolled = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(96)
        scrolled.add(self.text)
        content.pack_start(scrolled, True, True, 0)
        buffer = self.text.get_buffer()
        buffer.connect('changed', lambda _buffer: self._sync())
        self._sync()
        content.show_all()

    def get_text(self):
        buffer = self.text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()

    def set_text(self, text):
        self.text.get_buffer().set_text(text)

    def _sync(self):
        self.ok.set_sensitive(bool(self.get_text()))

    def run(self):
        """The comment's text, or None if cancelled."""
        self.text.grab_focus()
        response = run_dialog(self.dialog)
        text = self.get_text() if response == Gtk.ResponseType.OK else None
        self.dialog.destroy()
        return text or None


class ReviewPanel:
    """The review parts of the unit editor, for the registry's work
    copies (see the module's comment)."""

    def __init__(self, registry):
        self.registry = registry
        self.main_controller = registry.main_controller
        self.reviews = Reviews(registry.platform_of, registry._server_of)
        self.unit_view = self.main_controller.unit_controller.view
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.box.set_margin_start(12)
        self.box.set_margin_end(12)
        self.box.set_no_show_all(True)
        self.unit_view._widgets['vbox_below'].pack_start(self.box, False, True, 0)
        self.unit_view.note_sources.append(self._note_lines)
        self._handlers = []
        self._menus = set()
        self._timer = None
        self._shown = None
        """(copy, index in its side record, the unit) of the unit shown."""
        self._watch_boxes()

    def destroy(self):
        if self._note_lines in self.unit_view.note_sources:
            self.unit_view.note_sources.remove(self._note_lines)
        for source, handler in self._handlers:
            source.disconnect(handler)
        self._handlers = []
        self._stop_timer()
        self.box.destroy()

    # The unit shown #

    def where(self, unit):
        """(work copy, index in its side record) of unit, or (None, None)."""
        store_controller = self.main_controller.store_controller
        for copy in (getattr(store_controller, 'work_copies', None) or {}).values():
            r = copy.index_of(unit)
            if r is not None and self.registry.platform_of(copy) is not None:
                return copy, r
        return None, None

    def _note_lines(self, unit):
        """The comments known on unit, for the translator notes; also
        shows its suggestions and asks its server for both."""
        copy, r = self.where(unit)
        self._stop_timer()
        self._place()
        if copy is None or not self.reviews.wanted(copy):
            self._shown = None
            self._show_suggestions(())
            return []
        self._shown = (copy, r, unit)
        known = self.reviews.known(copy, r)
        if known is None:
            self._show_suggestions(())
            self._timer = GLib.timeout_add(DELAY, self._ask)
            return []
        self._show_suggestions(known[0])
        return comment_lines(known[1])

    def _place(self):
        """The suggestions go last, below the translator notes."""
        parent = self.box.get_parent()
        if parent is not None:
            parent.reorder_child(self.box, -1)

    def _stop_timer(self):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _ask(self):
        self._timer = None
        shown = self._shown
        if shown is not None:
            copy, r, unit = shown
            self.reviews.fetch(copy, r, lambda *_found: self._arrived(unit))
        return False

    def _arrived(self, unit):
        """The server's answer for unit: shown, if it still is."""
        if self._shown is None or self._shown[2] is not unit or self.unit_view.unit is not unit:
            return
        self.refresh()

    def refresh(self):
        """Shows what is known of the unit shown again."""
        self.unit_view.refresh_notes()
        store_view = getattr(self.main_controller.store_controller, 'view', None)
        renderer = getattr(getattr(store_view, '_treeview', None), 'renderer', None)
        if renderer is not None:
            renderer.regrow(self.unit_view)

    # Suggestions #

    def _show_suggestions(self, suggestions):
        for child in self.box.get_children():
            child.destroy()
        copy = self._shown[0] if self._shown else None
        for suggestion in suggestions:
            self.box.pack_start(self._suggestion_row(copy, suggestion), False, True, 0)
        self.box.set_visible(bool(suggestions))

    def _suggestion_row(self, copy, suggestion):
        row = SuggestionRow(suggestion_text(suggestion))
        if not self.unit_view.locked:
            # l10n: Makes someone's suggested translation, from a translation server, the unit's translation (sent with the next Submit)
            row.add_button(_('Accept'), lambda: self.accept(suggestion))
        if copy is not None and self.reviews.can_dismiss(copy):
            # l10n: Rejects someone's suggested translation on the translation server
            row.add_button(_('Reject'), lambda: self.dismiss(suggestion))
        row.show_all()
        return row

    def accept(self, suggestion):
        """Makes suggestion the shown unit's text, as if typed."""
        if self._shown is None:
            return
        unit_controller = self.main_controller.unit_controller
        for n, form in enumerate(suggestion.target):
            if n < len(self.unit_view.targets):
                unit_controller.set_unit_target(n, form)
        self.unit_view.targets[0].grab_focus()

    def dismiss(self, suggestion):
        if self._shown is None:
            return
        copy, r, unit = self._shown
        self.reviews.dismiss(
            copy, r, suggestion, lambda: self._arrived(unit), lambda e: self._failed(e, copy)
        )

    def _failed(self, error, copy):
        from .serversdialog import error_message

        platform = self.registry.platform_of(copy)
        self.main_controller.show_error(error_message(error, platform))

    # Comments #

    def _watch_boxes(self):
        view = self.unit_view
        for signal in ('sources-created', 'targets-created'):
            handler = view.connect(signal, lambda _view, boxes: self._watch_menus(boxes))
            self._handlers.append((view, handler))
        self._watch_menus(view.sources + view.targets)

    def _watch_menus(self, boxes):
        for box in boxes:
            if box not in self._menus:
                self._menus.add(box)
                handler = box.connect('populate-popup', self._on_populate_popup)
                self._handlers.append((box, handler))

    def _on_populate_popup(self, _box, menu):
        shown = self._shown
        if shown is None or not self.reviews.can_comment(shown[0]):
            return
        menu.append(Gtk.SeparatorMenuItem())
        # l10n: The context menu of a unit from a translation server: write a comment on its translation there
        add = Gtk.MenuItem.new_with_mnemonic(_('Add _Comment…'))
        add.connect('activate', lambda _item: self.add_comment(about_source=False))
        menu.append(add)
        # l10n: The context menu of a unit from a translation server: tell the project there of a mistake or unclear wording in the source text
        report = Gtk.MenuItem.new_with_mnemonic(_('Report a _Source Problem…'))
        report.connect('activate', lambda _item: self.add_comment(about_source=True))
        menu.append(report)
        menu.show_all()

    def ask_comment(self, source):
        """The comment's text from its dialog, or None."""
        parent = self.main_controller.view.main_window
        return CommentDialog(parent, source).run()

    def add_comment(self, about_source):
        """Asks for a comment on the unit shown and sends it."""
        if self._shown is None:
            return False
        copy, r, unit = self._shown
        text = self.ask_comment(str(unit.source) if about_source else None)
        if not text:
            return False
        self.reviews.add_comment(
            copy,
            r,
            text,
            about_source,
            lambda _comment: self._arrived(unit),
            lambda e: self._failed(e, copy),
        )
        return True
