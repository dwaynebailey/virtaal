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
Update from Server (Registry)."""

from gi.repository import GLib, Gtk, Pango

from virtaal.support.libi18n.numbers import format_percent
from virtaal.views.widgets.util import run_dialog

from . import pending_message, workcopy
from .serversdialog import error_message

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
        """The work file of the language opened, once it is."""

        self.dialog = Gtk.Dialog(transient_for=parent, modal=True)
        self.dialog.set_default_size(560, 480)
        self.dialog.add_button(_('_Close'), Gtk.ResponseType.CLOSE)
        self.open_button = self.dialog.add_button(_gtk_text('_Open'), RESPONSE_OPEN)
        self.open_button.set_sensitive(False)
        self.dialog.connect('response', self._on_response)

        self.server_combo = self._server_combo(servers)
        self.filter_entry = Gtk.SearchEntry(hexpand=True)
        # 'changed', not 'search-changed': the delay is FILTER_DELAY.
        self.filter_entry.connect('changed', lambda _entry: self._on_filter_changed())

        self.store = Gtk.TreeStore(str, str, str, str, str, bool, int)
        self.filtered = self.store.filter_new()
        self.filtered.set_visible_func(self._visible)
        self.treeview = Gtk.TreeView(model=self.filtered)
        self.treeview.append_column(
            Gtk.TreeViewColumn(_('Name'), Gtk.CellRendererText(), text=COL_NAME, weight=COL_WEIGHT)
        )
        self.treeview.get_column(0).set_expand(True)
        info = Gtk.CellRendererText(xalign=1.0)
        self.treeview.append_column(Gtk.TreeViewColumn(_('Translated'), info, text=COL_INFO))
        self.treeview.connect('row-expanded', self._on_row_expanded)
        self.treeview.connect('row-activated', lambda *_args: self.open_selected())
        self.treeview.get_selection().connect('changed', lambda _sel: self._on_selected())

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
        self.dialog.set_title(server.platform.name)
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
            info = _('Glossary') if item.glossary else ''
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

    def selected(self):
        """(project, component or None, language code) of the chosen
        language, or None."""
        model, it = self.treeview.get_selection().get_selected()
        if it is None or model[it][COL_KIND] != LANGUAGE:
            return None
        parent = model.iter_parent(it)
        component = model[parent][COL_SLUG] if model[parent][COL_KIND] == COMPONENT else None
        return model[it][COL_PROJECT], component, model[it][COL_SLUG]

    def _on_selected(self):
        """A language with changes not yet submitted says so."""
        self._sync_open()
        chosen = self.selected()
        if chosen is None or self._downloading:
            return
        copy = self._local(*chosen)
        count = copy.unsubmitted() if copy is not None else 0
        self.status.set_text(pending_message(count, copy.location()) if count else '')

    def _sync_open(self):
        self.open_button.set_sensitive(not self._downloading and self.selected() is not None)

    def _on_response(self, dialog, response):
        if response == RESPONSE_OPEN:
            # Opening takes a download: the dialog stays until it's done.
            dialog.stop_emission_by_name('response')
            self.open_selected()

    def open_selected(self):
        """Downloads the chosen language into its work copy, then closes
        the dialog with it as opened."""
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
        self.opened = path
        self.dialog.response(Gtk.ResponseType.CLOSE)

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
