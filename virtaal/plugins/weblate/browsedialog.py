#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The Weblate browser: a server's projects, each project's components
and each component's languages with how much is translated (#3337).

Projects load a page at a time: the next page when the list is
scrolled near its end, or while the filter shows too few rows (a
large server, such as hosted.weblate.org, has thousands). Weblate's
project list has no search parameter, so the filter matches the loaded
projects and more are loaded to find others. A project's components
and a component's languages load when its row is first expanded.
Nothing is downloaded or opened yet (phase W1)."""

from gi.repository import GLib, Gtk

from virtaal.support.libi18n.numbers import format_percent
from virtaal.views.widgets.util import run_dialog

from . import client as weblate
from .serversdialog import error_message

COL_NAME, COL_INFO, COL_KIND, COL_PROJECT, COL_SLUG, COL_LOADED = range(6)
PROJECT, COMPONENT, LANGUAGE, PENDING = 'project', 'component', 'language', 'pending'


def language_name(translation):
    """A translation's language, named in the UI language when Virtaal
    knows it, else as the server names it."""
    from virtaal.models.langmodel import LanguageModel

    language = translation.get('language') or {}
    code = translation.get('language_code') or language.get('code', '')
    name = LanguageModel(code).name
    if name == code:
        name = language.get('name') or code
    return name


class BrowseDialog:
    """Browses one of a ServerList's servers, the last one used first."""

    ROWS_WANTED = 50
    """Projects are loaded until the filter shows at least this many."""
    FILTER_DELAY = 300
    """Milliseconds of no typing before the filter applies."""

    def __init__(self, servers, parent=None, client_factory=weblate.Client):
        self.servers = servers
        self.client_factory = client_factory
        self.client = None

        self.dialog = Gtk.Dialog(title=_('Weblate'), transient_for=parent, modal=True)
        self.dialog.set_default_size(560, 480)
        self.dialog.add_button(_('_Close'), Gtk.ResponseType.CLOSE)

        self.server_combo = Gtk.ComboBoxText()
        self.server_combo.get_accessible().set_name(_('Weblate Servers'))
        for url in servers.urls():
            self.server_combo.append(url, url)
        self.filter_entry = Gtk.SearchEntry(hexpand=True)
        # 'changed', not 'search-changed': the delay is FILTER_DELAY.
        self.filter_entry.connect('changed', lambda _entry: self._on_filter_changed())
        self._filter_timer = None
        self._next = None
        self._more = False
        self._fetching = False

        self.store = Gtk.TreeStore(str, str, str, str, str, bool)
        self.filtered = self.store.filter_new()
        self.filtered.set_visible_func(self._visible)
        self.treeview = Gtk.TreeView(model=self.filtered)
        self.treeview.get_accessible().set_name(self.dialog.get_title())
        self.treeview.append_column(
            Gtk.TreeViewColumn(_('Name'), Gtk.CellRendererText(), text=COL_NAME)
        )
        self.treeview.get_column(0).set_expand(True)
        info = Gtk.CellRendererText(xalign=1.0)
        self.treeview.append_column(Gtk.TreeViewColumn(_('Translated'), info, text=COL_INFO))
        self.treeview.connect('row-expanded', self._on_row_expanded)

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
        self.server_combo.set_visible(len(servers.urls()) > 1)

        self.server_combo.connect('changed', lambda _combo: self._on_server_changed())
        urls = servers.urls()
        first = servers.last_url if servers.last_url in urls else (urls[0] if urls else None)
        if first:
            self.server_combo.set_active_id(first)

    # Loading #

    def _busy(self, delta):
        self._loading = getattr(self, '_loading', 0) + delta
        busy = self._loading > 0
        self.spinner.set_visible(busy)
        self.spinner.set_property('active', busy)

    def _failed(self, error):
        self._busy(-1)
        self.status.set_text(error_message(error))

    def _on_server_changed(self):
        url = self.server_combo.get_active_id()
        if url is None:
            return
        if self.client is not None:
            self.client.cancel()
        self._loading = 0
        self._busy(0)
        self.store.clear()
        self.status.set_text('')
        if self.servers.last_url != url:
            self.servers.last_url = url
            self.servers.save()
        self.client = self.client_factory(url, self.servers.token(url))
        self._next = None
        self._more = True
        self._fetching = False
        self._fetch_projects()

    def _fetch_projects(self):
        if self._fetching or not self._more:
            return
        self._fetching = True
        self._busy(1)
        self.client.projects_page(self._next, self._add_projects, self._projects_failed)

    def _add_projects(self, projects, following):
        self._fetching = False
        self._busy(-1)
        for project in projects:
            row = [project.get('name', ''), '', PROJECT, project.get('slug', ''), '', False]
            self._with_pending(self.store.append(None, row))
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
        self.store.append(it, ['…', '', PENDING, '', '', False])

    def _on_row_expanded(self, _treeview, filter_it, _path):
        it = self.filtered.convert_iter_to_child_iter(filter_it)
        if self.store[it][COL_LOADED]:
            return
        self.store[it][COL_LOADED] = True
        kind = self.store[it][COL_KIND]
        ref = Gtk.TreeRowReference.new(self.store, self.store.get_path(it))
        if kind == PROJECT:
            project = self.store[it][COL_PROJECT]
            self._load(ref, lambda *cbs: self.client.components(project, *cbs), self._component_row)
        elif kind == COMPONENT:
            project, slug = self.store[it][COL_PROJECT], self.store[it][COL_SLUG]
            self._load(
                ref, lambda *cbs: self.client.translations(project, slug, *cbs), self._language_row
            )

    def _load(self, ref, ask, make_row):
        """Fills ref's row with ask()'s items as pages come in, then
        drops its pending row."""

        def parent():
            path = ref.get_path() if ref.valid() else None
            return self.store.get_iter(path) if path is not None else None

        def page(items):
            it = parent()
            for item in items if it is not None else ():
                child = self.store.append(it, make_row(item, self.store[it][COL_PROJECT]))
                if self.store[child][COL_KIND] == COMPONENT:
                    self._with_pending(child)

        def done(_count):
            self._busy(-1)
            it = parent()
            if it is not None:
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
        ask(page, done, failed)

    def _component_row(self, component, project):
        # l10n: Marks a Weblate component that is a glossary, not strings to translate
        info = _('Glossary') if component.get('is_glossary') else ''
        slug = weblate.component_slug(component)
        return [component.get('name', ''), info, COMPONENT, project, slug, False]

    def _language_row(self, translation, project):
        percent = format_percent(float(translation.get('translated_percent') or 0) / 100)
        code = translation.get('language_code') or ''
        return [language_name(translation), percent, LANGUAGE, project, code, True]

    # Filtering #

    def _on_filter_changed(self):
        if self._filter_timer is not None:
            GLib.source_remove(self._filter_timer)
        self._filter_timer = GLib.timeout_add(self.FILTER_DELAY, self._apply_filter)

    def _apply_filter(self):
        self._filter_timer = None
        self.filtered.refilter()
        self._want_more()
        return False

    def _visible(self, model, it, _data):
        text = self.filter_entry.get_text().strip().casefold()
        if not text or model.iter_parent(it) is not None:
            return True
        return text in model[it][COL_NAME].casefold() or text in model[it][COL_PROJECT].casefold()

    # Choice #

    def selected(self):
        """(project, component, language code) of the chosen language,
        or None."""
        model, it = self.treeview.get_selection().get_selected()
        if it is None or model[it][COL_KIND] != LANGUAGE:
            return None
        component = model.iter_parent(it)
        return model[it][COL_PROJECT], model[component][COL_SLUG], model[it][COL_SLUG]

    def run(self):
        run_dialog(self.dialog)
        if self._filter_timer is not None:
            GLib.source_remove(self._filter_timer)
            self._filter_timer = None
        if self.client is not None:
            self.client.cancel()
        self.dialog.destroy()
