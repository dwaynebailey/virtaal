#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The translation memory of the translation bundle open: its TMX files'
translations into the language open, while it is open, listed before
every other source's unless the user says otherwise. Nothing goes into
the local translation memory."""

from translate.search import match
from translate.storage import po

from virtaal.common.pan_app import config_bool
from virtaal.support import concordance
from virtaal.support.match_compat import matcher
from virtaal.views.widgets.settingsdialog import BOOL, Field

from .basetmmodel import BaseTMModel

FIRST_WEIGHT = 1000.0
"""The weight that ranks a match above any other source's of the same
kind (exact or not): above any quality times amaGama's highest weight."""


class TMModel(BaseTMModel):
    __gtype_name__ = 'BundleTMModel'
    # l10n: A source of suggestions and terms: the translation bundle (.virtaal) open
    display_name = _('Translation Bundle')
    description = _('The translation memory in the translation bundle open')

    PREFETCH_MAX = 0
    BULK_MAX = 0
    default_config = {'prefetch': 0, 'first': True}
    settings = (
        # l10n: A setting of the Translation Bundle source of suggestions
        Field('first', _('_List its suggestions first'), BOOL),
    )

    def __init__(self, internal_name, controller):
        self.internal_name = internal_name
        self.matcher = None
        self.origin = None
        self.pairs = []
        self.load_config()
        super().__init__(controller)
        store_controller = controller.main_controller.store_controller
        self._signal_tracker.connect(store_controller, 'store-loaded', self._reload)
        self._signal_tracker.connect(store_controller, 'store-closed', self._drop)
        self._reload()

    def first(self):
        """Whether its matches are listed before other sources'."""
        return config_bool(self.config.get('first', True))

    def match_weight(self, match):
        return FIRST_WEIGHT if self.first() else 1.0

    def _bundle(self):
        controller = getattr(self, 'controller', None)
        store_controller = getattr(
            controller and controller.main_controller, 'store_controller', None
        )
        bundle = getattr(store_controller, 'bundle', None)
        return bundle() if bundle is not None else None

    def _reload(self, *_args):
        """Read the bundle open's translations into its language."""
        self._drop()
        bundle = self._bundle()
        pairs = bundle.memory(self.target_lang) if bundle and self.target_lang else []
        if not pairs:
            return
        self.pairs = pairs
        store = po.pofile()
        for source, target in pairs:
            store.addsourceunit(source).target = target
        self.matcher = matcher(
            [store],
            max_candidates=self.controller.max_matches,
            min_similarity=self.controller.min_quality,
            max_length=1000,
            lang=self.source_lang,
        )
        self.origin = self.display_name

    def _drop(self, *_args):
        self.matcher = None
        self.origin = None
        self.pairs = []
        self.cache = {}

    def concordance(self, query, on_done, on_error):
        """Search the bundle's translations for query's phrase, with
        TMClient.concordance's options: C{on_done(matches, total, True)}."""
        langs = {'source': self.source_lang, 'target': self.target_lang}
        phrase = concordance.Query(
            query['text'], query.get('case'), query.get('whole_words'), langs
        )
        sides = concordance.SIDES.get(query.get('side'), concordance.SIDES['source'])
        found = []
        for source, target in self.pairs:
            hits = concordance.match_hits(phrase, sides, {'source': [source], 'target': [target]})
            if hits:
                match = {'source': source, 'target': target, 'origins': [], 'time': 0}
                found.append(concordance.describe(match, hits))
        ranked = concordance.rank(found)[: query.get('limit', concordance.LIMIT)]
        on_done(ranked, len(found), True)

    def set_source_lang(self, language):
        self._reload()

    def set_target_lang(self, language):
        self._reload()

    def query(self, tmcontroller, unit):
        found = self._matches(unit)
        if found:
            self.emit('match-found', unit.source, found)

    def lookup(self, unit, on_done):
        on_done(self._matches(unit))

    def _matches(self, unit):
        if self.matcher is None:
            return []
        found = []
        for candidate in self.matcher.matches(str(unit.source)):
            found_match = match.unit2dict(candidate)
            found_match['tmsource'] = self.origin
            found.append(found_match)
        return found
