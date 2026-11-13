#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The translation bundle open as a source of suggestions and terms: its
TMX and TBX files, in the language open, while it is open."""

from types import SimpleNamespace

import pytest
from gi.repository import GLib
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.common import pan_app
from virtaal.controllers.placeablescontroller import PlaceablesController
from virtaal.plugins.terminology import termview
from virtaal.plugins.terminology.termcontroller import TerminologyController
from virtaal.plugins.tm.models import bundle as bundle_tm
from virtaal.plugins.tm.tmcontroller import TMController
from virtaal.support import _docsamples, documents, packages
from virtaal.test.test_scaffolding import TestScaffolding

TMX = (
    b'<?xml version="1.0"?><tmx version="1.4"><header srclang="en"/><body>'
    b'<tu><tuv xml:lang="en"><seg>Press the red button.</seg></tuv>'
    b'<tuv xml:lang="zu"><seg>Cindezela inkinobho ebomvu.</seg></tuv>'
    b'<tuv xml:lang="xh"><seg>Cofa iqhosha elibomvu.</seg></tuv></tu>'
    b'<tu><tuv xml:lang="en"><seg>Close the window.</seg></tuv>'
    b'<tuv xml:lang="xh"><seg>Vala ifestile.</seg></tuv></tu>'
    b'</body></tmx>'
)
TBX = (
    b'<?xml version="1.0"?><martif type="TBX" xml:lang="en"><text><body>'
    b'<termEntry><langSet xml:lang="en"><tig><term>button</term></tig></langSet>'
    b'<langSet xml:lang="zu"><tig><term>inkinobho</term></tig></langSet>'
    b'<langSet xml:lang="xh"><tig><term>iqhosha</term></tig></langSet></termEntry>'
    b'</body></text></martif>'
)
OTHERS = [
    '_dummytm',
    'amagama',
    'apertium',
    'currentfile',
    'deepl',
    'google_translate',
    'localtm',
    'microsoft_translator',
    'moses',
    'remotetm',
]


class TestBundleSources(TestScaffolding):
    def setup_class(self):
        TestScaffolding.setup_class(self)
        PlaceablesController(self.main_controller)

    @pytest.fixture(autouse=True)
    def _environment(self, monkeypatch, tmp_path, tmp_path_factory):
        monkeypatch.setattr(pan_app, 'get_config_dir', lambda: str(tmp_path))
        state = tmp_path_factory.mktemp('state') / 'packages'
        monkeypatch.setattr(packages, 'packages_dir', lambda: str(state))
        monkeypatch.setattr(GLib, 'idle_add', lambda func, *args, **kwargs: func(*args) and None)
        self.dir = tmp_path
        self.controllers = []
        self.store_controller.close_file()
        yield
        self.store_controller.close_file()
        for controller in self.controllers:
            controller.destroy()

    def _bundle(self, languages=('zu',), name='manual'):
        folder = self.dir / name
        folder.mkdir()
        path = folder / 'usage.odt'
        path.write_bytes(_docsamples.odt(['Press the red button.']))
        bundle = documents.from_documents([str(path)], 'en')
        for language in languages:
            bundle.add_language(language)
        bundle.contents['tm/project.tmx'] = TMX
        bundle.contents['terms/project.tbx'] = TBX
        saved = str(self.dir / ('%s.virtaal' % name))
        bundle.save(saved)
        return bundle, saved

    def _tm(self):
        controller = TMController(self.main_controller, {'disabled_models': OTHERS})
        self.controllers.append(controller)
        return controller.plugin_controller.plugins['bundle']

    def _matches(self, model, source):
        found = []
        handler = model.connect('match-found', lambda _m, _query, matches: found.extend(matches))
        unit = self.store_controller.get_store().get_units()[0].copy()
        unit.source = source
        model.query(None, unit)
        model.disconnect(handler)
        return [(m['target'], m['tmsource']) for m in found]

    def test_its_translations_into_the_language_open_are_suggested(self):
        _bundle, path = self._bundle()
        model = self._tm()

        assert self.main_controller.open_file(path)

        assert self._matches(model, 'Press the red button.') == [
            ('Cindezela inkinobho ebomvu.', 'manual.virtaal')
        ]
        assert self._matches(model, 'Close the window.') == []

    def test_its_suggestions_go_when_it_closes_or_another_file_opens(self):
        _bundle, path = self._bundle()
        model = self._tm()

        for leave in (self.store_controller.close_file, lambda: self._open_other()):
            assert self.main_controller.open_file(path)
            assert model.matcher is not None
            leave()
            assert model.matcher is None

    def _open_other(self):
        assert self.main_controller.open_file(self.testfile[1])

    def test_a_split_bundle_suggests_its_own_language(self):
        master, _path = self._bundle(languages=('zu', 'xh'))
        out = self.dir / 'split'
        out.mkdir()
        _zu, xh = master.split(str(out))
        model = self._tm()

        assert self.main_controller.open_file(xh)

        assert self._matches(model, 'Close the window.') == [
            ('Vala ifestile.', 'manual.xh.virtaal')
        ]

    def test_its_suggestions_rank_first_unless_the_user_says_otherwise(self):
        _bundle, path = self._bundle()
        model = self._tm()
        assert self.main_controller.open_file(path)
        assert model.match_weight({}) == bundle_tm.FIRST_WEIGHT

        model.update_settings({'first': False})

        assert model.match_weight({}) == 1.0
        assert pan_app.load_config(str(self.dir / 'tm.ini'), 'bundle')['first'] == 'False'
        assert self._tm().match_weight({}) == 1.0

    def _terms(self):
        controller = TerminologyController(
            self.main_controller,
            {
                'disabled_models': [
                    'localfile',
                    'autoterm',
                    'pontoon',
                    'microsoft',
                    'platformglossary',
                ]
            },
        )
        self.controllers.append(controller)
        return controller.plugin_controller.plugins['bundle']

    def test_its_terms_in_the_language_open_are_offered_first(self):
        _bundle, path = self._bundle(languages=('zu', 'xh'))
        model = self._terms()

        assert self.main_controller.open_file(path)
        self.store_controller.open_stream_language('xh')

        assert model.matcher in TerminologyPlaceable.matchers
        assert [u.target for u in model.matcher.matches('the button')] == ['iqhosha']
        assert self._candidates() == ['iqhosha', 'aqhosha']
        model.config['first'] = False
        model._reload()
        assert self._candidates() == ['aqhosha', 'iqhosha']
        self.store_controller.close_file()
        assert model.matcher is None
        assert model.matcher not in TerminologyPlaceable.matchers

    @staticmethod
    def _candidates():
        elem = TerminologyPlaceable(['button'])
        elem.translations = ['aqhosha', 'iqhosha']
        info = termview.TerminologyGUIInfo.__new__(termview.TerminologyGUIInfo)
        info.elem = elem
        return info.get_insert_candidates()


def test_a_bundles_matches_rank_above_other_sources_of_the_same_kind():
    controller = TMController.__new__(TMController)
    controller.storecursor = True
    controller.current_query = 'Press the button.'
    controller.matches = []
    controller.max_matches = 5
    controller.view = SimpleNamespace(display_matches=lambda matches: None)
    bundle = SimpleNamespace(display_name='Bundle', match_weight=lambda m: bundle_tm.FIRST_WEIGHT)
    local = SimpleNamespace(display_name='Local')

    controller.accept_response(
        local,
        'Press the button.',
        [
            {'source': 'Press the button.', 'target': 'Exact', 'quality': 100},
            {'source': 'Press the buttons.', 'target': 'Local', 'quality': 99},
            {'source': 'Press a button.', 'target': 'Same', 'quality': 95},
        ],
    )
    controller.accept_response(
        bundle,
        'Press the button.',
        [
            {'source': 'Press the button now.', 'target': 'Bundle', 'quality': 75},
            {'source': 'Press a button.', 'target': 'Same', 'quality': 95},
        ],
    )

    assert [(m['target'], m['tmsource']) for m in controller.matches] == [
        ('Exact', 'Local'),
        ('Same', 'Bundle'),
        ('Bundle', 'Bundle'),
        ('Local', 'Local'),
    ]
