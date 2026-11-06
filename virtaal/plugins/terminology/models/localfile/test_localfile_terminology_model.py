#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os
import shutil
import subprocess
from types import SimpleNamespace

from translate.search.match import terminologymatcher
from translate.storage import factory
from translate.storage.placeables.terminology import TerminologyPlaceable

from virtaal.common.configurable import Configurable
from virtaal.plugins.terminology.models.localfile import TerminologyModel
from virtaal.support.multiterm import MissingReaderError


def _make_model(config, monkeypatch, internal_name='localfile'):
    """A TerminologyModel with a controlled self.config, bypassing the
    real on-disk terminology.ini that Configurable.load_config()
    would otherwise read/depend on."""
    monkeypatch.setattr(Configurable, 'load_config', lambda self: None)
    model = TerminologyModel.__new__(TerminologyModel)
    model.internal_name = internal_name
    model.default_config = TerminologyModel.default_config
    model.config = config
    return model


def _fake_store(units, filename=''):
    return SimpleNamespace(units=units, filename=filename)


def _fake_unit(source, target):
    return SimpleNamespace(source=source, target=target)


# get_duplicates() #


def test_get_duplicates_finds_an_exact_source_and_target_match(monkeypatch):
    model = _make_model({}, monkeypatch)
    model.stores = [_fake_store([_fake_unit('hello', 'hallo')])]

    assert model.get_duplicates('hello', 'hallo') is True


def test_get_duplicates_strips_whitespace_before_comparing(monkeypatch):
    model = _make_model({}, monkeypatch)
    model.stores = [_fake_store([_fake_unit('hello', 'hallo')])]

    assert model.get_duplicates('  hello  ', '  hallo  ') is True


def test_get_duplicates_is_false_when_only_source_matches(monkeypatch):
    model = _make_model({}, monkeypatch)
    model.stores = [_fake_store([_fake_unit('hello', 'hallo')])]

    assert model.get_duplicates('hello', 'something else') is False


def test_get_duplicates_searches_across_multiple_stores(monkeypatch):
    model = _make_model({}, monkeypatch)
    model.stores = [
        _fake_store([_fake_unit('one', 'een')]),
        _fake_store([_fake_unit('hello', 'hallo')]),
    ]

    assert model.get_duplicates('hello', 'hallo') is True


# add_term() #


def _po_glossary_model(monkeypatch, tmp_path, terms=()):
    filename = str(tmp_path / 'terms.po')
    store = factory.getobject(filename)
    for source, target in terms:
        store.addsourceunit(source).target = target
    store.save()
    model = _make_model({}, monkeypatch)
    model.stores = [store]
    model.matcher = terminologymatcher(model.stores)
    return model, store, filename


def _term_ids(filename):
    return [u.getid() for u in factory.getobject(filename).units if not u.isheader()]


def test_add_term_keeps_a_po_glossary_valid_for_a_second_translation(monkeypatch, tmp_path):
    model, store, filename = _po_glossary_model(monkeypatch, tmp_path, [('cat', 'kat')])

    model.add_term(store, 'cat', 'katjie')

    ids = _term_ids(filename)
    assert len(ids) == len(set(ids)) == 2
    if shutil.which('msgfmt'):
        result = subprocess.run(
            ['msgfmt', '-c', '-o', str(tmp_path / 'terms.mo'), filename],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_add_term_leaves_the_first_po_unit_without_a_context(monkeypatch, tmp_path):
    model, store, filename = _po_glossary_model(monkeypatch, tmp_path)

    model.add_term(store, 'cat', 'kat')
    model.add_term(store, 'cat', 'katjie')

    assert _term_ids(filename) == ['cat', 'katjie\x04cat']


def test_add_term_finds_a_free_context_when_the_target_is_taken(monkeypatch, tmp_path):
    model, store, filename = _po_glossary_model(monkeypatch, tmp_path, [('cat', 'poes')])
    unit = store.addsourceunit('cat')
    unit.target = 'mies'
    unit.setcontext('kat')
    store.save()

    model.add_term(store, 'cat', 'kat')

    ids = _term_ids(filename)
    assert len(ids) == len(set(ids)) == 3


def test_add_term_does_not_add_an_exact_duplicate(monkeypatch, tmp_path):
    model, store, filename = _po_glossary_model(monkeypatch, tmp_path, [('cat', 'kat')])

    assert model.add_term(store, ' cat ', 'kat') is None
    assert _term_ids(filename) == ['cat']


def test_add_term_adds_the_comment_as_a_note(monkeypatch, tmp_path):
    model, store, filename = _po_glossary_model(monkeypatch, tmp_path)

    model.add_term(store, 'cat', 'kat', 'feline')

    assert 'feline' in factory.getobject(filename).units[-1].getnotes()


def test_add_term_makes_every_translation_matchable(monkeypatch, tmp_path):
    model, store, _filename = _po_glossary_model(monkeypatch, tmp_path, [('cat', 'kat')])

    model.add_term(store, 'cat', 'katjie')
    model.add_term(store, 'Dog', 'Hond')

    matches = {(u.source, u.target) for u in model.matcher.matches('The cat and the dog')}
    assert matches == {('cat', 'kat'), ('cat', 'katjie'), ('dog', 'Hond')}


def test_add_term_adds_no_context_outside_po(monkeypatch, tmp_path):
    filename = str(tmp_path / 'terms.csv')
    store = factory.getobject(filename)
    model = _make_model({}, monkeypatch)
    model.stores = [store]
    model.matcher = terminologymatcher(model.stores)

    model.add_term(store, 'cat', 'kat')
    model.add_term(store, 'cat', 'katjie')

    assert [u.getcontext() for u in factory.getobject(filename).units] == ['', '']


# get_units_with_source() #


def test_get_units_with_source_is_case_insensitive_and_strips_whitespace(monkeypatch):
    model = _make_model({}, monkeypatch)
    unit = _fake_unit('  Hello  ', 'hallo')
    model.stores = [_fake_store([unit])]

    assert model.get_units_with_source('hello') == [unit]


def test_get_units_with_source_returns_every_match_across_stores(monkeypatch):
    model = _make_model({}, monkeypatch)
    unit1 = _fake_unit('hello', 'hallo')
    unit2 = _fake_unit('hello', 'goeie dag')
    model.stores = [_fake_store([unit1]), _fake_store([unit2])]

    assert model.get_units_with_source('hello') == [unit1, unit2]


def test_get_units_with_source_returns_empty_list_for_no_match(monkeypatch):
    model = _make_model({}, monkeypatch)
    model.stores = [_fake_store([_fake_unit('hello', 'hallo')])]

    assert model.get_units_with_source('goodbye') == []


# get_extend_store() / get_store_for_filename() #


def test_get_extend_store_finds_the_configured_extend_file(monkeypatch):
    model = _make_model({'extendfile': '/tmp/terms.po'}, monkeypatch)
    target_store = _fake_store([], filename='/tmp/terms.po')
    model.stores = [_fake_store([], filename='/tmp/other.po'), target_store]

    assert model.get_extend_store() is target_store


def test_get_extend_store_returns_none_when_not_found(monkeypatch):
    model = _make_model({'extendfile': '/tmp/terms.po'}, monkeypatch)
    model.stores = [_fake_store([], filename='/tmp/other.po')]

    assert model.get_extend_store() is None


def test_get_store_for_filename_matches_by_absolute_path(monkeypatch):
    model = _make_model({}, monkeypatch)
    target_store = _fake_store([], filename='relative.po')
    model.stores = [target_store]

    import os

    assert model.get_store_for_filename(os.path.abspath('relative.po')) is target_store


# load_config() #


def test_load_config_keeps_only_existing_files(monkeypatch, tmp_path):
    real_file = tmp_path / 'real.po'
    real_file.write_text('')
    model = _make_model(
        {'files': '%s,%s' % (real_file, tmp_path / 'missing.po'), 'extendfile': str(real_file)},
        monkeypatch,
    )

    model.load_config()

    assert model.config['files'] == [str(real_file)]


def test_load_config_falls_back_extendfile_to_the_first_configured_file(monkeypatch, tmp_path):
    real_file = tmp_path / 'real.po'
    real_file.write_text('')
    model = _make_model(
        {'files': str(real_file), 'extendfile': str(tmp_path / 'nonexistent-extendfile.po')},
        monkeypatch,
    )

    model.load_config()

    assert model.config['extendfile'] == str(real_file)


def test_load_config_falls_back_extendfile_to_the_default_when_nothing_exists(
    monkeypatch, tmp_path
):
    model = _make_model(
        {'files': '', 'extendfile': str(tmp_path / 'nonexistent-extendfile.po')},
        monkeypatch,
    )

    model.load_config()

    assert model.config['extendfile'] == TerminologyModel.default_config['extendfile']


def test_load_config_never_picks_a_read_only_termbase_to_extend(monkeypatch, tmp_path):
    real_file = tmp_path / 'real.po'
    real_file.write_text('')
    model = _make_model({'files': f'{MILI},{real_file}', 'extendfile': MILI}, monkeypatch)

    model.load_config()

    assert model.config['extendfile'] == str(real_file)


def test_load_config_falls_back_to_the_default_when_only_read_only_termbases_exist(monkeypatch):
    model = _make_model({'files': MILI, 'extendfile': ''}, monkeypatch)

    model.load_config()

    assert model.config['extendfile'] == TerminologyModel.default_config['extendfile']
    assert model.config['files'] == [MILI, model.config['extendfile']]


def test_load_config_always_includes_extendfile_in_files(monkeypatch, tmp_path):
    real_file = tmp_path / 'real.po'
    real_file.write_text('')
    model = _make_model({'files': '', 'extendfile': str(real_file)}, monkeypatch)

    model.load_config()

    assert str(real_file) in model.config['files']


# MultiTerm termbases #

MILI = os.path.join('devsupport', 'testfiles', 'trados', 'kaur2k-termconv', 'mili_1.xml')


def _lang_model(monkeypatch, files, source='en', target='et'):
    model = _make_model({'files': files}, monkeypatch)
    lang_controller = SimpleNamespace(
        source_lang=SimpleNamespace(code=source), target_lang=SimpleNamespace(code=target)
    )
    rescans = []
    model.controller = SimpleNamespace(
        main_controller=SimpleNamespace(lang_controller=lang_controller),
        rescan_current_unit=lambda: rescans.append(True),
    )
    model.matcher = None
    model._loaded = {}
    return model, lang_controller, rescans


def test_load_files_takes_a_multiterm_export_in_the_current_pair(monkeypatch):
    model, _langs, _rescans = _lang_model(monkeypatch, [MILI])
    monkeypatch.setattr(TerminologyPlaceable, 'matchers', [])

    model.load_files()

    store = model.stores[0]
    assert store.gettargetlanguage() == 'ET'
    assert [(m.source, m.target) for m in model.matcher.matches('Abort it.')] == [
        ('abort', 'katkestus'),
    ]


def test_a_language_change_gives_a_termbase_the_new_pair_and_rescans(monkeypatch):
    model, langs, rescans = _lang_model(monkeypatch, [MILI])
    monkeypatch.setattr(TerminologyPlaceable, 'matchers', [])
    model.load_files()

    langs.target_lang = SimpleNamespace(code='fr')
    model._on_lang_changed(None, 'fr')

    assert [(m.source, m.target) for m in model.matcher.matches('Abort it.')] == [
        ('abort', 'avorter'),
    ]
    assert rescans == [True]
    model._on_lang_changed(None, 'fr')
    assert rescans == [True]


def test_load_files_leaves_out_a_termbase_it_has_no_reader_for(monkeypatch, tmp_path, caplog):
    def no_reader(filename):
        raise MissingReaderError('access-parser')

    model, _langs, _rescans = _lang_model(monkeypatch, [str(tmp_path / 'terms.sdltb')])
    monkeypatch.setattr(TerminologyPlaceable, 'matchers', [])
    monkeypatch.setattr(factory, 'getobject', no_reader)

    model.load_files()

    assert model.stores == []
    assert 'terms.sdltb' in caplog.text
