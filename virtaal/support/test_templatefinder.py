#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import os

import pytest

from virtaal.support import templatefinder
from virtaal.support.templatefinder import (
    compile_mask,
    fill,
    find_template,
    guess_template,
    read_crowdin,
    read_l10n_toml,
    read_transifex,
)


def _touch(root, *relpaths):
    paths = []
    for relpath in relpaths:
        path = root.joinpath(*relpath.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('a=b\n', encoding='utf-8')
        paths.append(str(path))
    return paths


def _config(root, relpath, text):
    path = root.joinpath(*relpath.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def test_a_mask_fills_its_template_with_what_the_wildcards_took():
    match = compile_mask('src/**/*_{lang}.properties').match('src/a/b/Messages_af.properties')
    assert match.group('lang') == 'af'
    assert fill('src/**/*.properties', match) == 'src/a/b/Messages.properties'


@pytest.mark.parametrize('mask', ['', '/abs/{lang}.po', '../{lang}.po', '{lang}/{lang}.po'])
def test_a_malformed_mask_is_refused(mask):
    with pytest.raises(ValueError):
        compile_mask(mask)


def test_crowdin_placeholders_become_a_mask():
    text = """
base_path: app
files:
  - source: /en/messages.properties
    translation: /%two_letters_code%/%original_file_name%
  - source: /src/**/*.ftl
    translation: /src/**/%file_name%.%locale%.ftl
    skip_untranslated_strings: true
  - source: /no-language.properties
    translation: /out.properties
"""
    assert read_crowdin(text) == [
        ('app/{lang}/messages.properties', 'app/en/messages.properties', False),
        ('app/src/**/*.{lang}.ftl', 'app/src/**/*.ftl', True),
    ]


def test_an_unreadable_crowdin_config_gives_nothing():
    assert read_crowdin('files: [') == []
    assert read_crowdin('just text') == []


def test_transifex_file_filter_and_trans_entries():
    text = """
[main]
host = https://www.transifex.com

[o:org:p:app:r:messages]
source_file = locale/messages.properties
file_filter = locale/messages_<lang>.properties
trans.af_ZA = custom/afrikaans.properties
"""
    assert sorted(read_transifex(text)) == [
        ('custom/afrikaans.properties', 'locale/messages.properties', False),
        ('locale/messages_{lang}.properties', 'locale/messages.properties', False),
    ]


def test_l10n_toml_paths_with_env_variables():
    pytest.importorskip('tomllib')
    text = """
basepath = "."

[env]
    l = "{l10n_base}/{locale}/"

[[paths]]
    reference = "locales/en-US/**"
    l10n = "locales/{locale}/**"
"""
    assert read_l10n_toml(text) == [('locales/{lang}/**', 'locales/en-US/**', True)]


def test_crowdin_yml_finds_the_template(tmp_path):
    _config(
        tmp_path,
        'crowdin.yml',
        'files:\n  - source: /i18n/en.properties\n    translation: /i18n/%two_letters_code%.properties\n',
    )
    en, af = _touch(tmp_path, 'i18n/en.properties', 'i18n/af.properties')

    match = find_template(af)

    assert match == templatefinder.TemplateMatch(en, 'af', 'crowdin.yml', False)


def test_transifex_config_finds_the_template(tmp_path):
    _config(
        tmp_path,
        '.tx/config',
        '[app.messages]\nsource_file = l/Messages.properties\nfile_filter = l/Messages_<lang>.properties\n',
    )
    en, af = _touch(tmp_path, 'l/Messages.properties', 'l/Messages_af.properties')

    assert find_template(af).template == en
    assert find_template(af).how == '.tx/config'


def test_l10n_toml_finds_a_fluent_template(tmp_path):
    pytest.importorskip('tomllib')
    _config(
        tmp_path,
        'l10n.toml',
        '[[paths]]\nreference = "locales/en-US/**"\nl10n = "locales/{locale}/**"\n',
    )
    en, af = _touch(tmp_path, 'locales/en-US/app/main.ftl', 'locales/af/app/main.ftl')

    assert find_template(af) == templatefinder.TemplateMatch(en, 'af', 'l10n.toml', True)


def test_a_config_whose_template_is_missing_falls_back_to_the_layout(tmp_path):
    _config(
        tmp_path,
        'crowdin.yml',
        'files:\n  - source: /gone.properties\n    translation: /%two_letters_code%.properties\n',
    )
    en, af = _touch(tmp_path, 'en.properties', 'af.properties')

    assert find_template(af) == templatefinder.TemplateMatch(en, 'af', 'layout')


def test_the_nearest_config_wins(tmp_path):
    _config(
        tmp_path,
        'crowdin.yml',
        'files:\n  - source: /sub/outer.properties\n    translation: /sub/%two_letters_code%.properties\n',
    )
    _config(
        tmp_path,
        'sub/crowdin.yml',
        'files:\n  - source: /inner.properties\n    translation: /%two_letters_code%.properties\n',
    )
    _outer, inner, af = _touch(
        tmp_path, 'sub/outer.properties', 'sub/inner.properties', 'sub/af.properties'
    )

    assert find_template(af).template == inner


@pytest.mark.parametrize(
    'template, target',
    [
        ('Messages.properties', 'Messages_af.properties'),
        ('Messages.properties', 'Messages_af_ZA.properties'),
        ('Messages_en.properties', 'Messages_af.properties'),
        ('en.properties', 'af.properties'),
        ('en-US.ftl', 'af.ftl'),
        ('locales/en-US/main.ftl', 'locales/af/main.ftl'),
        ('locales/en/main.ftl', 'locales/af/main.ftl'),
        ('l10n/en/app/main.ftl', 'l10n/af/app/main.ftl'),
        ('en.json', 'af.json'),
        ('public/locales/en/translation.json', 'public/locales/af/translation.json'),
        ('lib/l10n/app_en.arb', 'lib/l10n/app_af.arb'),
        ('res/values/strings.xml', 'res/values-af/strings.xml'),
        ('res/values/strings.xml', 'res/values-af-rZA/strings.xml'),
        ('en.lproj/Localizable.strings', 'af.lproj/Localizable.strings'),
        ('Base.lproj/Localizable.strings', 'af.lproj/Localizable.strings'),
    ],
)
def test_layouts_find_the_template(tmp_path, template, target):
    template, target = _touch(tmp_path, template, target)

    match = find_template(target)

    assert match.template == template
    assert match.how == 'layout'
    assert match.language in ('af', 'af_ZA')


def test_no_template_when_nothing_matches(tmp_path):
    (af,) = _touch(tmp_path, 'translations.properties')
    assert find_template(af) is None


def test_guess_prefers_a_file_in_a_source_language_or_none(tmp_path):
    _de, en, target = _touch(tmp_path, 'de.properties', 'english.properties', 'mine.properties')
    assert guess_template(target) == en


def test_no_guess_when_only_other_translations_are_beside_it(tmp_path):
    _de, target = _touch(tmp_path, 'de.properties', 'mine.properties')
    assert guess_template(target) is None


def test_template_paths_are_native(tmp_path):
    en, af = _touch(tmp_path, 'locales/en/main.ftl', 'locales/af/main.ftl')
    assert find_template(af).template == os.path.join(str(tmp_path), 'locales', 'en', 'main.ftl')
    assert en == find_template(af).template


def test_translations_of_a_source_beside_its_translations(tmp_path):
    en, af, de = _touch(tmp_path, 'en.properties', 'af.properties', 'de.properties')

    assert templatefinder.translations_of(en) == [af, de]
    assert templatefinder.translations_of(af) == []


def test_translations_of_a_source_in_a_language_folder(tmp_path):
    en, af = _touch(tmp_path, 'locales/en-US/app.ftl', 'locales/af/app.ftl')

    assert templatefinder.translations_of(en) == [af]


def test_translations_of_a_crowdin_source(tmp_path):
    _config(
        tmp_path,
        'crowdin.yml',
        'files:\n  - source: /src/Strings.properties\n'
        '    translation: /src/Strings-%two_letters_code%.properties\n',
    )
    en, af = _touch(tmp_path, 'src/Strings.properties', 'src/Strings-af.properties')

    assert templatefinder.translations_of(en) == [af]


def test_a_file_nothing_translates_has_no_translations(tmp_path):
    (lone,) = _touch(tmp_path, 'lone.properties')
    assert templatefinder.translations_of(lone) == []


def test_translations_of_android_and_apple_sources(tmp_path):
    values, af, ru = _touch(
        tmp_path,
        'res/values/strings.xml',
        'res/values-af/strings.xml',
        'res/values-ru/strings.xml',
    )
    en, de = _touch(tmp_path, 'en.lproj/Localizable.strings', 'de.lproj/Localizable.strings')

    assert templatefinder.translations_of(values) == [af, ru]
    assert templatefinder.translations_of(en) == [de]


@pytest.mark.parametrize(
    'folder, expected',
    [
        ('values', True),
        ('Base.lproj', True),
        ('en.lproj', True),
        ('af.lproj', False),
        ('src', False),
    ],
)
def test_source_folders(folder, expected):
    assert templatefinder.is_source_folder(folder) is expected
