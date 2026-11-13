#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Browsing a translation server: its projects, each project's
components (if the platform has them) and their languages with how
much is translated (#3337).

Projects load a page at a time: the next page when the list is
scrolled near its end, or while the filter shows too few rows (a large
server, such as hosted.weblate.org, has thousands). A platform that
supports_search filters on the server; for one that doesn't, such as
Weblate, the filter matches the loaded projects and more are loaded
to find others. A project's components and a component's languages
load when its row is first expanded.

Open (or a double click on a language) downloads the language into its
work copy (workcopy.py) and closes the dialog; run() gives the work
file to open. A work copy holding work not yet submitted is opened as
it is, without downloading over it: its language is in bold, and says
how many changes wait when chosen, and once opened its notice offers
Update from Server (Registry).

Several languages open together, as one list of units with the Files
Panel: those chosen at once (Ctrl or Shift and a click, such as some
languages of a component), or a language in every component of its
project ("Open in All Components" in a language's menu, by a right
click or the Menu key). They download one after the other with their
progress and Cancel (openmany.py); those that fail are listed, but not
those the server doesn't have, and the rest open (opened_all)."""

from gi.repository import Gdk, GLib, Gtk, Pango

from virtaal.support.libi18n.numbers import format_number, format_percent
from virtaal.views.widgets.util import run_dialog, tell

from . import HTTP, PlatformError, location_of, pending_message, workcopy
from .serversdialog import error_message
from .submit import BUDGET, CANCELLED

COL_NAME, COL_INFO, COL_KIND, COL_PROJECT, COL_SLUG, COL_LOADED, COL_WEIGHT = range(7)
PROJECT, COMPONENT, LANGUAGE, PENDING = 'project', 'component', 'language', 'pending'
COMBO_ID, COMBO_TYPE, COMBO_URL = range(3)
RESPONSE_OPEN = 1
NORMAL, BOLD = int(Pango.Weight.NORMAL), int(Pango.Weight.BOLD)


def _gtk_text(msgid):
    """GTK's own translation of msgid, kept out of virtaal.pot."""
    return GLib.dgettext('gtk30', msgid)


def language_name(code, name):
    """A language named in the UI language when Virtaal knows it, else
    as the server names it."""
    from virtaal.models.langmodel import LanguageModel

    known = LanguageModel(code).name
    return known if known != code else (name or code)


def _downloading(number, total):
    # l10n: While downloading several translations from a translation server
    return _('Downloading %(number)s of %(total)s…') % {
        'number': format_number(number),
        'total': format_number(total),
    }


def _counted_failures(count):
    from virtaal.common.pan_app import ngettext

    text = ngettext(
        # l10n: Followed by the translations, one a line, each with why it failed
        '%(count)s translation could not be downloaded:',
        '%(count)s translations could not be downloaded:',
        count,
    )
    return text % {'count': format_number(count)}


def _failure_reason(error, platform, result):
    """Why a translation wasn't downloaded."""
    from .submitdialog import used_up_message

    if result.stop == BUDGET and error.status == 429 and result.reset_in is not None:
        return used_up_message(result.reset_in)
    if error.status == 404:
        # The server answered the browser: it has no such translation.
        error = PlatformError(HTTP, 404, error.detail)
    return error_message(error, platform)


class BrowseDialog:
    """Browses one of a ServerList's servers, the last one used first."""

    ROWS_WANTED = 50
    """Projects are loaded until the filter shows at least this many."""
    FILTER_DELAY = 300
    """Milliseconds of no typing before the filter applies."""

    def __init__(self, servers, parent=None):
        self.servers = servers
        self.server = None
        self.session = None
        self._filter_timer = None
        self._next = None
        self._more = False
        self._fetching = False
        self._loading = 0
        self._query = None
        self._downloading = False
        self.opened = None
        """The work file of the language opened, once it is (the first,
        with several)."""
        self.opened_all = []
        """The work files of every language opened."""
        self.failed = None
        """The message listing the languages that failed to download, if
        any did."""
        self.progress = None
        self._runner = None

        self.dialog = Gtk.Dialog(transient_for=parent, modal=True)
        self.dialog.set_default_size(560, 480)
        self.dialog.add_button(_('_Cancel'), Gtk.ResponseType.CLOSE)
        self.open_button = self.dialog.add_button(_gtk_text('_Open'), RESPONSE_OPEN)
        self.dialog.set_default_response(RESPONSE_OPEN)
        self.open_button.set_sensitive(False)
        self.dialog.connect('response', self._on_response)

        self.server_combo = self._server_combo(servers)
        self.filter_entry = Gtk.SearchEntry(hexpand=True, activates_default=True)
        # l10n: Placeholder of the box filtering a translation server's projects
        self.filter_entry.set_placeholder_text(_('Filter projects'))
        # 'changed', not 'search-changed': the delay is FILTER_DELAY.
        self.filter_entry.connect('changed', lambda _entry: self._on_filter_changed())

        self.store = Gtk.TreeStore(str, str, str, str, str, bool, int)
        self.filtered = self.store.filter_new()
        self.filtered.set_visible_func(self._visible)
        self.treeview = self._treeview(self.filtered)

        scrolled = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.treeview)
        self.vadjustment = scrolled.get_vadjustment()
        self.vadjustment.connect('value-changed', lambda _adj: self._want_more())

        self.spinner = Gtk.Spinner()
        self.status = Gtk.Label(wrap=True, xalign=0, selectable=True, hexpand=True)
        status_box = Gtk.Box(spacing=6)
        status_box.pack_start(self.spinner, False, False, 0)
        status_box.pack_start(self.status, True, True, 0)

        top = Gtk.Box(spacing=6)
        top.pack_start(self.server_combo, False, False, 0)
        top.pack_start(self.filter_entry, True, True, 0)
        content = self.dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(6)
        content.pack_start(top, False, False, 0)
        content.pack_start(scrolled, True, True, 0)
        content.pack_start(status_box, False, False, 0)
        content.show_all()
        self.server_combo.set_visible(len(servers.servers) > 1)

        self.server_combo.connect('changed', lambda _combo: self._on_server_changed())
        first = servers.last if servers.last in servers.servers else None
        first = first or (servers.servers[0] if servers.servers else None)
        if first is not None:
            self.server_combo.set_active(servers.servers.index(first))

    def _treeview(self, model):
        """The projects, components and languages: several languages can
        be chosen at once, and a language has a menu."""
        treeview = Gtk.TreeView(model=model)
        treeview.append_column(
            Gtk.TreeViewColumn(_('Name'), Gtk.CellRendererText(), text=COL_NAME, weight=COL_WEIGHT)
        )
        treeview.get_column(0).set_expand(True)
        info = Gtk.CellRendererText(xalign=1.0)
        treeview.append_column(Gtk.TreeViewColumn(_('Translated'), info, text=COL_INFO))
        treeview.connect('row-expanded', self._on_row_expanded)
        treeview.connect('row-activated', lambda *_args: self.open_selected())
        treeview.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        treeview.get_selection().connect('changed', lambda _sel: self._on_selected())
        treeview.connect('button-press-event', self._on_button_press)
        treeview.connect('popup-menu', lambda _view: self._popup(None))
        return treeview

    def _server_combo(self, servers):
        """The servers by URL, with their platform's name when there is
        more than one kind."""
        model = Gtk.ListStore(int, str, str)
        for i, server in enumerate(servers.servers):
            model.append([i, server.platform.name, server.url])
        combo = Gtk.ComboBox(model=model)
        # l10n: The servers of translation platforms, such as Weblate
        combo.get_accessible().set_name(_('Translation Servers'))
        if len({s.platform for s in servers.servers}) > 1:
            kind = Gtk.CellRendererText()
            combo.pack_start(kind, False)
            combo.add_attribute(kind, 'text', COMBO_TYPE)
        url = Gtk.CellRendererText()
        combo.pack_start(url, True)
        combo.add_attribute(url, 'text', COMBO_URL)
        return combo

    # Loading #

    def _busy(self, delta):
        self._loading += delta
        busy = self._loading > 0
        self.spinner.set_visible(busy)
        self.spinner.set_property('active', busy)

    def _failed(self, error):
        self._busy(-1)
        self.status.set_text(error_message(error, self.server.platform))

    def _on_server_changed(self):
        index = self.server_combo.get_active()
        if index < 0:
            return
        server = self.servers.servers[index]
        if self.servers.last is not server:
            self.servers.last = server
            self.servers.save()
        self.server = server
        # l10n: Title of File > Open from Translation Server; %(platform)s is the kind of server, such as Weblate
        self.dialog.set_title(_('Open from %(platform)s') % {'platform': server.platform.name})
        self.treeview.get_accessible().set_name(server.platform.name)
        self._restart()

    def _restart(self):
        """Lists the projects of self.server again from the start."""
        if self.session is not None:
            self.session.cancel()
        self._loading = 0
        self._busy(0)
        self.store.clear()
        self.status.set_text('')
        self.session = self.server.session()
        self._query = self._search_text() if self.server.platform.supports_search else None
        self._next = None
        self._more = True
        self._fetching = False
        self._downloading = False
        self._sync_open()
        self._fetch_projects()

    def _fetch_projects(self):
        if self._fetching or not self._more:
            return
        self._fetching = True
        self._busy(1)
        self.session.projects(self._query, self._next, self._add_projects, self._projects_failed)

    def _add_projects(self, projects, following):
        self._fetching = False
        self._busy(-1)
        for project in projects:
            it = self.store.append(
                None, [project.name, '', PROJECT, project.slug, '', False, NORMAL]
            )
            self._with_pending(it)
        self._next = following
        self._more = following is not None
        self._want_more()

    def _projects_failed(self, error):
        self._fetching = False
        self._more = False
        self._failed(error)

    def _near_the_end(self):
        adj = self.vadjustment
        page = adj.get_page_size()
        return page > 0 and adj.get_value() + 2 * page >= adj.get_upper()

    def _want_more(self):
        """Loads the next page of projects if the list needs it."""
        if not self._more or self._fetching:
            return
        if self.filtered.iter_n_children(None) < self.ROWS_WANTED or self._near_the_end():
            self._fetch_projects()

    def _with_pending(self, it):
        """A child row, so the row can be expanded before it is loaded."""
        self.store.append(it, ['…', '', PENDING, '', '', False, NORMAL])

    def _on_row_expanded(self, _treeview, filter_it, _path):
        it = self.filtered.convert_iter_to_child_iter(filter_it)
        if self.store[it][COL_LOADED]:
            return
        self.store[it][COL_LOADED] = True
        kind = self.store[it][COL_KIND]
        ref = Gtk.TreeRowReference.new(self.store, self.store.get_path(it))
        project = self.store[it][COL_PROJECT]
        if kind == PROJECT and self.server.platform.has_components:
            self._load(ref, lambda *cbs: self.session.components(project, *cbs), COMPONENT)
        elif kind in (PROJECT, COMPONENT):
            component = self.store[it][COL_SLUG] if kind == COMPONENT else None
            self._load(ref, lambda *cbs: self.session.languages(project, component, *cbs), LANGUAGE)

    def _load(self, ref, ask, kind):
        """Fills ref's row with ask()'s items, then drops its pending row."""

        def parent():
            path = ref.get_path() if ref.valid() else None
            return self.store.get_iter(path) if path is not None else None

        def done(items):
            self._busy(-1)
            it = parent()
            if it is None:
                return
            component = self.store[it][COL_SLUG] if self.store[it][COL_KIND] == COMPONENT else None
            for item in items:
                child = self.store.append(
                    it, self._row(item, kind, self.store[it][COL_PROJECT], component)
                )
                if kind == COMPONENT:
                    self._with_pending(child)
            child = self.store.iter_children(it)
            if child is not None and self.store[child][COL_KIND] == PENDING:
                self.store.remove(child)

        def failed(error):
            it = parent()
            if it is not None:
                # Expanding it again asks again.
                self.store[it][COL_LOADED] = False
            self._failed(error)

        self._busy(1)
        ask(done, failed)

    def _row(self, item, kind, project, component=None):
        if kind == COMPONENT:
            # l10n: Marks a component (a part of a project) that is a glossary, not strings to translate
            info = _('Glossary') if item.glossary else item.info
            return [item.name, info, COMPONENT, project, item.slug, False, NORMAL]
        percent = format_percent(item.translated) if item.translated is not None else ''
        name = language_name(item.slug, item.name)
        waiting = self._local(project, component, item.slug)
        weight = BOLD if waiting is not None and waiting.unsubmitted() else NORMAL
        return [name, percent, LANGUAGE, project, item.slug, True, weight]

    def _local(self, project, component, language):
        """The work copy already downloaded of a language, or None."""
        server = self.server
        return workcopy.existing(server.platform.id, server.url, project, component, language)

    # Filtering #

    def _search_text(self):
        return self.filter_entry.get_text().strip()

    def _on_filter_changed(self):
        if self._filter_timer is not None:
            GLib.source_remove(self._filter_timer)
        self._filter_timer = GLib.timeout_add(self.FILTER_DELAY, self._apply_filter)

    def _apply_filter(self):
        self._filter_timer = None
        if self.server is not None and self.server.platform.supports_search:
            if self._search_text() != self._query:
                self._restart()
        else:
            self.filtered.refilter()
            self._want_more()
        return False

    def _visible(self, model, it, _data):
        text = self._search_text().casefold()
        if not text or model.iter_parent(it) is not None:
            return True
        if self.server is not None and self.server.platform.supports_search:
            # The server has filtered them.
            return True
        return text in model[it][COL_NAME].casefold() or text in model[it][COL_PROJECT].casefold()

    # Choice #

    def _language_of(self, model, it):
        """(project, component or None, language code) of a language row."""
        parent = model.iter_parent(it)
        component = model[parent][COL_SLUG] if model[parent][COL_KIND] == COMPONENT else None
        return model[it][COL_PROJECT], component, model[it][COL_SLUG]

    def _names_of(self, model, it):
        """(project, component or None, language) of a language row, as
        shown."""
        parent = model.iter_parent(it)
        if model[parent][COL_KIND] != COMPONENT:
            return model[parent][COL_NAME], None, model[it][COL_NAME]
        project = model.iter_parent(parent)
        return model[project][COL_NAME], model[parent][COL_NAME], model[it][COL_NAME]

    def chosen(self):
        """Wanted for each language chosen, in the list's order."""
        from .openmany import Wanted

        model, paths = self.treeview.get_selection().get_selected_rows()
        found = []
        for path in paths:
            it = model.get_iter(path)
            if model[it][COL_KIND] == LANGUAGE:
                found.append(Wanted(*self._language_of(model, it), self._names_of(model, it)))
        return found

    def selected(self):
        """(project, component or None, language code) of the language
        chosen, or None unless one is."""
        chosen = self.chosen()
        return tuple(chosen[0][:3]) if len(chosen) == 1 else None

    def _on_selected(self):
        """Languages with changes not yet submitted say so."""
        self._sync_open()
        if self._downloading:
            return
        copies = [self._local(*wanted[:3]) for wanted in self.chosen()]
        copies = [copy for copy in copies if copy is not None and copy.unsubmitted()]
        count = sum(copy.unsubmitted() for copy in copies)
        self.status.set_text(pending_message(count, location_of(copies)) if count else '')

    def _sync_open(self):
        some = bool(self.chosen()) or self.selected() is not None
        self.open_button.set_sensitive(not self._downloading and some)

    # A language's menu #

    def _on_button_press(self, treeview, event):
        if not event.triggers_context_menu() or event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        found = treeview.get_path_at_pos(int(event.x), int(event.y))
        if found is None:
            return False
        selection = treeview.get_selection()
        if not selection.path_is_selected(found[0]):
            selection.unselect_all()
            selection.select_path(found[0])
        return self._popup(event)

    def _popup(self, event):
        """The menu of the one language chosen, on a platform with
        components. Whether it is shown."""
        chosen = self.chosen()
        if len(chosen) != 1 or self.server is None or not self.server.platform.has_components:
            return False
        menu = Gtk.Menu()
        # l10n: In a language's menu in the translation server's browser: open that
        # language of every component of its project together
        item = Gtk.MenuItem.new_with_mnemonic(_('Open in All _Components'))
        item.connect('activate', lambda _item: self.open_in_all_components(chosen[0]))
        menu.append(item)
        menu.show_all()
        menu.attach_to_widget(self.treeview, None)
        self._menu = menu
        if event is not None:
            menu.popup_at_pointer(event)
        else:
            menu.popup_at_widget(
                self.treeview,
                Gdk.Gravity.CENTER,
                Gdk.Gravity.NORTH_WEST,
                Gtk.get_current_event(),
            )
        return True

    def _on_response(self, dialog, response):
        if response == RESPONSE_OPEN:
            # Opening takes a download: the dialog stays until it's done.
            dialog.stop_emission_by_name('response')
            self.open_selected()

    def open_selected(self):
        """Downloads the chosen language into its work copy, then closes
        the dialog with it as opened; several chosen, as open_many()."""
        if len(self.chosen()) > 1:
            self.open_many(self.chosen())
            return
        chosen = self.selected()
        if chosen is None or self._downloading:
            return
        server = self.server
        project, component, language = chosen
        copy = self._local(project, component, language)
        if copy is not None and copy.has_local_changes():
            self._opened(copy.path)
            return

        def done(download):
            self._downloading = False
            self._busy(-1)
            try:
                copy = workcopy.create(
                    server.platform, server.url, project, component, language, download
                )
            except OSError as e:
                self.status.set_text(str(e))
                self._sync_open()
                return
            self._opened(copy.path)

        def failed(error):
            self._downloading = False
            self._failed(error)
            self._sync_open()

        self._downloading = True
        self._busy(1)
        self._sync_open()
        self.status.set_text('')
        self.session.download(project, component, language, done, failed)

    def _opened(self, path):
        self._opened_all([path])

    def _opened_all(self, paths):
        self.opened = paths[0]
        self.opened_all = list(paths)
        self.dialog.response(Gtk.ResponseType.CLOSE)

    # Several at once #

    def open_in_all_components(self, wanted):
        """The language of wanted in every component of its project."""
        from .openmany import Wanted, components_with

        if self._downloading:
            return
        project = wanted.project
        project_name = wanted.names[0] if wanted.names else project
        progress = self._start_progress(project_name)
        progress.waiting(project_name)

        def found(pairs):
            if progress is self.progress:
                names = [(project_name, c.name, language_name(i.slug, i.name)) for c, i in pairs]
                self.open_many(
                    [
                        Wanted(project, c.slug, wanted.language, n)
                        for (c, _i), n in zip(pairs, names, strict=True)
                    ]
                )

        def failed(error):
            if progress is self.progress:
                self._end_progress()
                self.status.set_text(error_message(error, self.server.platform))

        components_with(self.session, project, wanted.language, found, failed)

    def _start_progress(self, title):
        from .submitdialog import ProgressDialog

        self._downloading = True
        self._busy(1)
        self._sync_open()
        self.status.set_text('')
        self.progress = ProgressDialog(self.dialog, self._cancel_many, title, _downloading)
        self.progress.show()
        return self.progress

    def _end_progress(self):
        if self.progress is not None:
            self.progress.destroy()
            self.progress = None
        self._downloading = False
        self._busy(-1)
        self._sync_open()

    def _cancel_many(self):
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.cancel()
        else:
            self.session.cancel()
            self._end_progress()

    def open_many(self, wanted):
        """Downloads each of wanted (Wanted) into its work copy, with the
        progress and Cancel, then closes the dialog with them opened; the
        ones that failed are listed (failed), unless all did."""
        from .openmany import Downloads, title_of

        if not wanted:
            if self.progress is not None:
                self._end_progress()
            return
        if self.progress is None:
            self._start_progress(title_of(wanted))
        self.failed = None

        def done(result):
            self._runner = None
            self._end_progress()
            self._many_done(result)

        self._runner = Downloads(self.server, self.session, wanted, self.progress.update, done)
        self._runner.start()

    def _many_done(self, result):
        if result.stop == CANCELLED:
            return
        platform = self.server.platform
        if result.failures:
            lines = [_counted_failures(len(result.failures))]
            for wanted, error in result.failures:
                lines.append('  ' + wanted.label)
                lines.append('    ' + _failure_reason(error, platform, result))
            self.failed = '\n'.join(lines)
        if not result.paths:
            failed = result.failures or result.missing
            if failed:
                self.status.set_text(_failure_reason(failed[0][1], platform, result))
            return
        if self.failed:
            self._tell_failed(self.failed)
        self._opened_all(result.paths)

    def _tell_failed(self, text):
        tell(self.dialog, text, Gtk.MessageType.WARNING)

    def run(self):
        """Shows the dialog until it's closed. The work file opened, or
        None."""
        run_dialog(self.dialog)
        if self._filter_timer is not None:
            GLib.source_remove(self._filter_timer)
            self._filter_timer = None
        if self.session is not None:
            self.session.cancel()
        self.dialog.destroy()
        return self.opened
