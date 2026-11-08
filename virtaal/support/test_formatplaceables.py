#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import io
from types import SimpleNamespace

import pytest
from translate.storage import pypo
from translate.storage.placeables import StringElem, general, parse

from virtaal.support import formatplaceables as fp
from virtaal.support.invisiblechars import InvisiblePlaceable


def _pieces(text, fmt=None, flags=()):
    """(class name or None, text) of each piece of text as the editor
    parses it for fmt and flags."""
    parsers = fp.parsers_for(fmt, flags) + general.parsers + [InvisiblePlaceable.parse]
    return [
        (None if type(e) is StringElem else type(e).__name__, str(e))
        for e in parse(StringElem(text), parsers).flatten()
    ]


def _placeables(text, fmt=None, flags=()):
    return [piece for cls, piece in _pieces(text, fmt, flags) if cls is not None]


@pytest.mark.parametrize(
    ('text', 'fmt', 'flags', 'expected'),
    [
        ('{{count}} notes', 'json', (), ['{{count}}']),
        ('Hi {{ user.name }}', 'json', (), ['{{ user.name }}']),
        ('{{- html}} and {{val, number}}', 'json', (), ['{{- html}}', '{{val, number}}']),
        ('Press $t(common.save)', 'json', (), ['$t(common.save)']),
        ('Hello { $user }', 'fluent', (), ['{ $user }']),
        ('{ -brand-name } is here', 'fluent', (), ['{ -brand-name }']),
        ('See { msg.attr }', 'fluent', (), ['{ msg.attr }']),
        (
            'Size { NUMBER($n, minimumFractionDigits: 2) }',
            'fluent',
            (),
            ['{ NUMBER($n, minimumFractionDigits: 2) }'],
        ),
        ('Hi {name}', 'arb', (), ['{name}']),
        ('{n, number} items', 'arb', (), ['{n, number}']),
        ('Due {d, date, short}', 'arb', (), ['{d, date, short}']),
        ('Hello %@', None, (), ['%@']),
        ('Hi %1$@ and %2$@', None, (), ['%1$@', '%2$@']),
        ('%#@files@ left', 'strings', (), ['%#@files@']),
        ('Value {0:N2}', None, (), ['{0:N2}']),
        ('{1,-10} and {0}', None, (), ['{1,-10}', '{0}']),
        ('Hi {name}', None, ('python-brace-format',), ['{name}']),
        (
            '{0!r:>10} {user.name} {}',
            None,
            ('python-brace-format',),
            ['{0!r:>10}', '{user.name}', '{}'],
        ),
        ('Hi {name}', None, ('perl-brace-format',), ['{name}']),
        ('Hi %{name} and %<n>d', None, ('ruby-format',), ['%{name}', '%<n>d']),
        ('Path $HOME and ${dir}', None, ('sh-format',), ['$HOME', '${dir}']),
        ('Hi %1$s', 'android', (), ['%1$s']),
    ],
)
def test_a_format_placeholder_is_one_placeable(text, fmt, flags, expected):
    assert _placeables(text, fmt, flags) == expected


@pytest.mark.parametrize(
    ('text', 'flags', 'expected'),
    [
        ('{{lit}} {name}', ('python-brace-format',), ['{name}']),
        ('100%%@', (), []),
        ('100%%{x}', ('ruby-format',), []),
        ('$$HOME', ('sh-format',), []),
    ],
)
def test_an_escape_stays_text(text, flags, expected):
    tree = parse(StringElem(text), fp.parsers_for(None, flags))
    assert [str(e) for e in tree.flatten() if type(e) is not StringElem] == expected


def test_apple_positional_object_is_not_qt_number_one():
    assert _pieces('Hi %1$@') == [(None, 'Hi '), ('ApplePlaceable', '%1$@')]


def test_dotnet_format_item_is_not_two_numbers():
    assert _pieces('Value {0:N2}') == [(None, 'Value '), ('DotNetPlaceable', '{0:N2}')]


def test_a_bare_index_stays_java_message_format():
    assert _pieces('{0} files') == [('JavaMessageFormatPlaceable', '{0}'), (None, ' files')]


def test_printf_positional_keeps_its_dollar_with_shell_variables_on():
    assert _placeables('%1$s in $DIR', None, ('sh-format',)) == ['%1$s', '$DIR']


@pytest.mark.parametrize(
    ('text', 'fmt'),
    [
        ('Hi {name}', None),
        ('Hi {name}', 'json'),
        ('Hi {name}', 'android'),
        ('{{count}} notes', None),
        ('{{count}} notes', 'arb'),
        ('Hello { $user }', None),
        ('Hello { $user }', 'json'),
        ('{count, plural, one {# file} other {# files}}', None),
        ('{count, plural, one {# file} other {# files}}', 'fluent'),
        ('Hi %{name}', None),
        ('Path ${dir}', None),
    ],
)
def test_a_format_placeholder_is_text_where_its_format_doesnt_apply(text, fmt):
    assert not [p for p in _placeables(text, fmt) if p.startswith(('{', '%{', '$'))]


# ICU


def test_icu_plural_keeps_its_cases_text_for_translating():
    assert _pieces('{count, plural, one {# file} other {# files}}', 'arb') == [
        ('ICUPlaceable', '{count, plural, one {'),
        ('ICUPlaceable', '#'),
        (None, ' file'),
        ('ICUPlaceable', '} other {'),
        ('ICUPlaceable', '#'),
        (None, ' files'),
        ('ICUPlaceable', '}}'),
    ]


def test_icu_plural_case_holds_an_argument_and_exact_values():
    text = '{count, plural, offset:1 =0 {No files} one {{name} has # file} other {# files}} left'
    assert _pieces(text, 'arb') == [
        ('ICUPlaceable', '{count, plural, offset:1 =0 {'),
        (None, 'No files'),
        ('ICUPlaceable', '} one {'),
        ('ICUPlaceable', '{name}'),
        (None, ' has '),
        ('ICUPlaceable', '#'),
        (None, ' file'),
        ('ICUPlaceable', '} other {'),
        ('ICUPlaceable', '#'),
        (None, ' files'),
        ('ICUPlaceable', '}}'),
        (None, ' left'),
    ]


def test_icu_select_has_no_number_sign():
    assert _pieces('{g, select, male {He #1} other {They}}', 'arb') == [
        ('ICUPlaceable', '{g, select, male {'),
        (None, 'He #'),
        ('NumberPlaceable', '1'),
        ('ICUPlaceable', '} other {'),
        (None, 'They'),
        ('ICUPlaceable', '}}'),
    ]


def test_icu_nested_select_in_plural():
    text = '{n, plural, one {{g, select, male {his #} other {their #}}} other {#}}'
    placeables = _placeables(text, 'arb')
    assert placeables == [
        '{n, plural, one {',
        '{g, select, male {',
        '} other {',
        '}}',
        '} other {',
        '#',
        '}}',
    ]


def test_icu_quoting():
    assert _pieces("Don't '{'quoted'}' {name}", 'arb') == [
        (None, "Don't '{'quoted'}' "),
        ('ICUPlaceable', '{name}'),
    ]


def test_icu_parts_join_up_to_the_text():
    text = '{count, plural, one {# file} other {# files}}'
    assert ''.join(p for _cls, p in _pieces(text, 'arb')) == text


@pytest.mark.parametrize(
    'text',
    [
        '{count, plural, one {# file',
        '{count, plural, one {# file} other {# files}',
        '{count, plural, }',
        '{count, plural, one # file}',
        '{count, plural one {x}}',
        '{, number}',
        '{name',
        'name}',
        '{}',
        '}{',
        '{' * 100 + '}' * 100,
        "{n, plural, one {'{'}",
        '{n, plural, ' + 'one {{n, plural, ' * 40 + 'x',
    ],
)
def test_icu_malformed_is_text(text):
    pieces = _pieces(text, 'arb')
    assert ''.join(p for _cls, p in pieces) == text
    assert 'ICUPlaceable' not in [cls for cls, _p in pieces]


# Fluent

SELECT = (
    '{ $n ->\n    [one] You have one email from { $user }.\n   *[other] You have { $n } emails.\n}'
)


def test_fluent_select_keeps_its_variants_text_for_translating():
    assert _pieces(SELECT, 'fluent') == [
        ('FluentPlaceable', '{ $n ->'),
        ('NewlinePlaceable', '\n'),
        (None, '    '),
        ('FluentPlaceable', '[one]'),
        (None, ' You have one email from '),
        ('FluentPlaceable', '{ $user }'),
        (None, '.'),
        ('NewlinePlaceable', '\n'),
        (None, '   '),
        ('FluentPlaceable', '*[other]'),
        (None, ' You have '),
        ('FluentPlaceable', '{ $n }'),
        (None, ' emails.'),
        ('NewlinePlaceable', '\n'),
        ('FluentPlaceable', '}'),
    ]


def test_fluent_select_in_text_and_nested():
    text = 'Hi { $n ->\n [one] { $g ->\n   [f] her\n  *[m] his\n }\n *[other] their\n} file'
    placeables = _placeables(text, 'fluent')
    assert placeables[:3] == ['{ $n ->', '\n', '[one]']
    assert '{ $g ->' in placeables
    assert ''.join(p for _cls, p in _pieces(text, 'fluent')) == text
    assert ('FluentPlaceable', '*[m]') in _pieces(text, 'fluent')


def test_fluent_string_literal_with_a_brace():
    assert _placeables('A { "{" } b', 'fluent') == ['{ "{" }']


@pytest.mark.parametrize(
    'text',
    [
        '{ $n ->\n [one] x',
        '{ $n ->\n}',
        '{ $n -> }',
        '{ $user',
        '{ }',
        '{ "open }',
        '{ $a\n $b }',
        '{' * 50,
    ],
)
def test_fluent_malformed_is_text(text):
    pieces = _pieces(text, 'fluent')
    assert ''.join(p for _cls, p in pieces) == text
    assert 'FluentPlaceable' not in [cls for cls, _p in pieces]


# Which formats use which


def _pair(converter_id, target):
    return SimpleNamespace(
        pair=SimpleNamespace(converter=SimpleNamespace(id=converter_id), target=target)
    )


@pytest.mark.parametrize(
    ('store', 'expected'),
    [
        (_pair('json', '/x/af.json'), 'json'),
        (_pair('json', '/x/app_af.ARB'), 'arb'),
        (_pair('fluent', '/x/af.ftl'), 'fluent'),
        (_pair('strings', '/x/af.lproj/Localizable.strings'), 'strings'),
        (SimpleNamespace(pair=None), None),
        (None, None),
    ],
)
def test_format_of(store, expected):
    assert fp.format_of(store) == expected


def test_format_of_a_unit_in_a_stream_is_its_files():
    unit = object()
    fluent = SimpleNamespace(
        model=_pair('fluent', 'af.ftl'), local_index=lambda u: 0 if u is unit else None
    )
    json = SimpleNamespace(model=_pair('json', 'af.json'), local_index=lambda u: None)
    stream = SimpleNamespace(segments=[json, fluent], current_model=json.model)

    assert fp.format_of(stream, unit) == 'fluent'
    assert fp.format_of(stream, object()) == 'json'


def test_unit_flags_of_a_po_unit():
    store = pypo.pofile(io.BytesIO(b'#, fuzzy, python-brace-format\nmsgid "a {x}"\nmsgstr ""\n'))

    assert fp.unit_flags(store.units[-1]) == {'fuzzy', 'python-brace-format'}
    assert fp.unit_flags(SimpleNamespace()) == set()
    assert fp.unit_flags(None) == set()


def test_a_po_flag_turns_brace_on_for_that_unit_only():
    store = pypo.pofile(
        io.BytesIO(
            b'#, python-brace-format\nmsgid "Hi {name}"\nmsgstr ""\n\n'
            b'msgid "Set {name}"\nmsgstr ""\n'
        )
    )
    flagged, plain = store.units[-2:]

    assert fp.BracePlaceable.parse in fp.parsers_for(None, fp.unit_flags(flagged))
    assert fp.BracePlaceable.parse not in fp.parsers_for(None, fp.unit_flags(plain))


def test_parsers_for_keeps_the_parsing_order():
    found = fp.placeables_for('json', {'python-brace-format', 'ruby-format', 'sh-format'})
    assert found == [
        fp.I18NextPlaceable,
        fp.RubyPlaceable,
        fp.ApplePlaceable,
        fp.DotNetPlaceable,
        fp.ShellVariablePlaceable,
        fp.BracePlaceable,
    ]


def test_every_format_and_flag_parser_is_one_of_the_placeables():
    tables = list(fp.FORMAT_PARSERS.values()) + list(fp.FLAG_PARSERS.values()) + [fp.EVERYWHERE]
    assert {cls for classes in tables for cls in classes} <= set(fp.PLACEABLES)


def test_any_mix_of_syntax_characters_parses_back_to_itself():
    import random

    rng = random.Random(101)
    alphabet = '{}$#%@-*[]\'"\n ,:.=0123ab()t'
    parsers = fp.parsers + general.parsers
    for _ in range(3000):
        text = ''.join(rng.choice(alphabet) for _ in range(rng.randint(1, 30)))
        assert str(parse(StringElem(text), parsers)) == text


# Which JSON a file is

I18NEXT_JSON = {
    'notes_one': 'You have {{count}} note',
    'notes_other': 'You have {{count}} notes',
    'welcome': 'Welcome, {{ user.name }}',
    'save': 'Press $t(common.save) to keep it',
}
VUE_JSON = {
    'message': {
        'hello': 'Hello {name}!',
        'apples': 'no apples | one apple | {count} apples',
        'home': '@:common.home page',
        'mail': "{account}{'@'}{domain}",
    }
}
FORMATJS_JSON = {
    'files': 'You have {count, plural, one {# file} other {# files}}',
    'greeting': 'Hello {name}',
    'total': 'Total: {amount, number}',
}
PLAIN_JSON = {'open': 'Open', 'close': 'Close the window'}


def _write_json(folder, data):
    import json

    (folder / 'en').mkdir(parents=True)
    (folder / 'af').mkdir()
    (folder / 'en' / 'messages.json').write_bytes(json.dumps(data, indent=2).encode('utf-8'))
    (folder / 'af' / 'messages.json').write_bytes(b'{}\n')
    return folder / 'af' / 'messages.json', folder / 'en' / 'messages.json'


@pytest.mark.parametrize(
    ('data', 'expected'),
    [
        (I18NEXT_JSON, 'json'),
        (VUE_JSON, 'json-vue'),
        (FORMATJS_JSON, 'json-icu'),
        (PLAIN_JSON, 'json'),
    ],
)
def test_a_json_file_is_parsed_for_what_its_strings_hold(tmp_path, data, expected):
    from virtaal.models.monolingual import MonolingualPair

    target, template = _write_json(tmp_path, data)
    pair = MonolingualPair(str(target), str(template))

    assert fp.format_of(SimpleNamespace(pair=pair)) == expected
    assert pair.json_flavour == expected  # Judged once per file.


def test_an_arb_file_is_icu_without_looking():
    pair = SimpleNamespace(converter=SimpleNamespace(id='json'), target='app_af.arb')

    assert fp.format_of(SimpleNamespace(pair=pair)) == 'arb'


@pytest.mark.parametrize(
    ('texts', 'plural', 'expected'),
    [
        (['Hi {{name}}'], False, 'json'),
        (['Hello', 'World'], True, 'json'),  # i18next plural keys
        (['Hello {name}', '@:common.save'], False, 'json-vue'),
        (['{n, plural, one {x} other {y}}', 'Hi {name}'], False, 'json-icu'),
        (['Hello'], False, 'json'),
        (['Hi {{a}}', 'Hi {b}', 'Hi {c}'], False, 'json'),  # A tie: i18next.
    ],
)
def test_json_flavour(texts, plural, expected):
    assert fp.json_flavour((texts, plural)) == expected


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('Hello {name}!', ['{name}']),
        ('Item {0} of {1}', ['{0}', '{1}']),
        ("{account}{'@'}{domain}", ['{account}', "{'@'}", '{domain}']),
        ('@:common.home page', ['@:common.home']),
        ('See @.lower:message.title', ['@.lower:message.title']),
        ('no apples | one apple | {count} apples', ['|', '|', '{count}']),
        ('a|b', []),
        ('mail me@example.org', []),
    ],
)
def test_vue_placeholders(text, expected):
    pieces = _pieces(text, 'json-vue')
    assert [p for cls, p in pieces if cls == 'VuePlaceable'] == expected
