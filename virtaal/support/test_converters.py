#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import pytest

from virtaal.support import converters
from virtaal.support.converters import (
    FluentConverter,
    PropertiesConverter,
    properties_personality,
)

needs_fluent = pytest.mark.skipif(
    not FluentConverter().available(), reason='translate-toolkit before 3.18 has no fluent2po'
)

EN_PROPERTIES = """\
# Messages for the demo app

# The window title
app.title=File Manager
greeting=Hello, {0}!
quit=Quit
"""

EN_FTL = """\
# Title of the window
app-title = File Manager
greeting = Hello, { $name }!
login = Log in
    .title = Log in to your account
"""


def _write(path, text, encoding='utf-8'):
    path.write_bytes(text.encode(encoding))
    return str(path)


def _translations(store):
    return {unit.getlocations()[0]: _forms(unit) for unit in store.units if not unit.isheader()}


def _forms(unit):
    """A unit's target: a plural's forms as a list."""
    if getattr(unit, 'plural_tags', None):
        return list(unit.target.strings) if unit.target else [''] * len(unit.plural_tags)
    return unit.target


def _units(store):
    return [
        (u.getlocations()[0], _sources(u), _forms(u), getattr(u, 'plural_tags', None))
        for u in store.units
        if not u.isheader()
    ]


def _sources(unit):
    return list(unit.source.strings) if getattr(unit, 'plural_tags', None) else unit.source


@pytest.mark.parametrize(
    'name, expected',
    [
        ('af.properties', PropertiesConverter),
        ('Messages_af.PROPERTIES', PropertiesConverter),
        pytest.param('af.ftl', FluentConverter, marks=needs_fluent),
        ('af.po', type(None)),
        ('README', type(None)),
    ],
)
def test_monolingual_converter_is_found_by_extension(name, expected):
    assert isinstance(converters.monolingual_converter(name), expected)


def test_open_formats_lists_only_what_the_storage_factory_lacks():
    names = [name for name, _extensions in converters.open_formats()]
    assert 'Java Properties' in names
    assert 'Fluent file' not in names


@pytest.mark.parametrize(
    'data, expected',
    [
        (b'a=b', 'java'),
        (b'a=\\u00eab', 'java'),
        ('a=êb'.encode(), 'java-utf8'),
        ('a=êb'.encode('iso-8859-1'), 'java'),
    ],
)
def test_properties_personality_follows_the_file(data, expected):
    assert properties_personality(data) == expected


def test_properties_pair_shows_the_template_as_source(tmp_path):
    en = _write(tmp_path / 'en.properties', EN_PROPERTIES)
    af = _write(tmp_path / 'af.properties', 'app.title=Lêerbestuurder\nold=Ou\n')

    store = PropertiesConverter().to_bilingual(en, af)

    units = {unit.getlocations()[0]: unit for unit in store.units if not unit.isheader()}
    assert {key: (u.source, u.target) for key, u in units.items()} == {
        'app.title': ('File Manager', 'Lêerbestuurder'),
        'greeting': ('Hello, {0}!', ''),
        'quit': ('Quit', ''),
    }
    assert 'The window title' in units['app.title'].getnotes()


def test_properties_written_back_give_untranslated_keys_the_english(tmp_path):
    en = _write(tmp_path / 'en.properties', EN_PROPERTIES)
    af = _write(tmp_path / 'af.properties', 'app.title=Ou\n')

    data = PropertiesConverter().to_monolingual(en, af, {'quit': 'Verlaat'})

    assert data.decode('iso-8859-1').splitlines()[-3:] == [
        'app.title=File Manager',
        'greeting=Hello, {0}!',
        'quit=Verlaat',
    ]


def test_properties_written_back_follow_the_template_and_can_leave_out_untranslated(tmp_path):
    en = _write(tmp_path / 'en.properties', EN_PROPERTIES)
    af = _write(tmp_path / 'af.properties', 'app.title=Ou\n')

    data = PropertiesConverter().to_monolingual(
        en, af, {'app.title': 'Lêerbestuurder', 'quit': 'Verlaat'}, skip_untranslated=True
    )

    assert data.decode('iso-8859-1') == (
        '# Messages for the demo app\n'
        '\n'
        '# The window title\n'
        'app.title=L\u00eaerbestuurder\n'
        'quit=Verlaat\n'
    )


def test_properties_written_back_stay_utf8_when_the_target_is(tmp_path):
    en = _write(tmp_path / 'en.properties', EN_PROPERTIES)
    af = _write(tmp_path / 'af.properties', 'quit=Hou op – nou\n')

    data = PropertiesConverter().to_monolingual(en, af, {'quit': 'Verlaat ê'})

    assert 'quit=Verlaat ê\n' in data.decode('utf-8')


def test_properties_round_trip_keeps_every_translation(tmp_path):
    en = _write(tmp_path / 'en.properties', EN_PROPERTIES)
    af = tmp_path / 'af.properties'
    _write(af, 'greeting=Hallo, {0}!\n')
    converter = PropertiesConverter()
    wanted = {'app.title': 'Twee\nreëls', 'greeting': 'Hallo, {0}!', 'quit': 'Verlaat'}

    af.write_bytes(converter.to_monolingual(en, str(af), wanted))

    assert _translations(converter.to_bilingual(en, str(af))) == wanted
    assert converter.values(str(af)) == wanted


@needs_fluent
def test_fluent_pair_shows_the_template_as_source(tmp_path):
    en = _write(tmp_path / 'en.ftl', EN_FTL)
    af = _write(tmp_path / 'af.ftl', 'app-title = Lêerbestuurder\n')

    store = FluentConverter().to_bilingual(en, af)

    assert {u.getlocations()[0]: (u.source, u.target) for u in store.units[1:]} == {
        'app-title': ('File Manager', 'Lêerbestuurder'),
        'greeting': ('Hello, { $name }!', ''),
        'login': ('Log in\n.title = Log in to your account', ''),
    }


@needs_fluent
def test_fluent_written_back_keeps_template_comments_and_attributes(tmp_path):
    en = _write(tmp_path / 'en.ftl', EN_FTL)
    af = _write(tmp_path / 'af.ftl', '')

    data = FluentConverter().to_monolingual(
        en,
        af,
        {'app-title': 'Lêerbestuurder', 'login': 'Teken aan\n.title = Teken aan by jou rekening'},
        skip_untranslated=True,
    )

    assert data.decode('utf-8') == (
        '# Title of the window\n'
        'app-title = Lêerbestuurder\n'
        'login = Teken aan\n'
        '    .title = Teken aan by jou rekening\n'
    )


@needs_fluent
def test_fluent_round_trip_keeps_every_translation(tmp_path):
    en = _write(tmp_path / 'en.ftl', EN_FTL)
    af = tmp_path / 'af.ftl'
    _write(af, '')
    converter = FluentConverter()
    wanted = {'app-title': 'Lêerbestuurder', 'greeting': 'Hallo, { $name }!'}

    af.write_bytes(converter.to_monolingual(en, str(af), wanted))

    assert converter.values(str(af)) == dict(
        wanted, login='Log in\n.title = Log in to your account'
    )


def test_properties_spans_cover_each_messages_lines():
    data = (
        b'# A comment\n! Another\n\na=1\nb : two \\\n   lines\nc\\:d=3\n'
        b'# trailing\n\ng\nh=\\\\\ni=5\r\nj=x\\\r\n y\r\n'
    )

    assert PropertiesConverter().spans(data) == {
        'a': (4, 4),
        'b': (5, 6),
        'c\\:d': (7, 7),
        'g': (10, 10),
        'h': (11, 11),
        'i': (12, 12),
        'j': (13, 14),
    }


@needs_fluent
def test_fluent_spans_cover_each_messages_lines():
    data = (
        b'# A comment\nhello = Hello\n    world\n\nbtn = Press\n    .title = Tip\n\n'
        b'-brand = Firefox\n'
    )

    assert FluentConverter().spans(data) == {
        'hello': (2, 3),
        'btn': (5, 6),
        '-brand': (8, 8),
    }


def test_values_of_reads_bytes_as_values_reads_a_file(tmp_path):
    path = tmp_path / 'af.properties'
    path.write_bytes('a=Lêer\nb=Twee\n'.encode())
    converter = PropertiesConverter()

    assert converter.values_of(path.read_bytes()) == converter.values(str(path))


# Apple .strings

EN_STRINGS = """\
/* The window title */
"app.title" = "File Manager";

"greeting" = "Hello, %@!";
"quit" = "Quit";
"""


def test_strings_pair_shows_the_template_as_source(tmp_path):
    en = _write(tmp_path / 'en.strings', EN_STRINGS)
    af = _write(tmp_path / 'af.strings', '"app.title" = "Lêerbestuurder";\n', 'utf-16')

    store = converters.StringsConverter().to_bilingual(en, af)

    assert _translations(store) == {'app.title': 'Lêerbestuurder', 'greeting': '', 'quit': ''}
    assert store.units[1].getnotes() == 'The window title'


def test_strings_written_back_escape_quotes_and_keep_the_target_s_encoding(tmp_path):
    en = _write(tmp_path / 'en.strings', EN_STRINGS)
    af = _write(tmp_path / 'af.strings', '"quit" = "Verlaat";\n', 'utf-16')

    data = converters.StringsConverter().to_monolingual(
        en, af, {'app.title': 'Die "lêer"', 'quit': 'Verlaat'}, skip_untranslated=True
    )

    assert data.startswith(b'\xff\xfe') or data.startswith(b'\xfe\xff')
    text = data.decode('utf-16')
    assert '"app.title" = "Die \\"lêer\\"";' in text
    assert 'greeting' not in text and '/* The window title */' in text
    assert converters.StringsConverter().values_of(data) == {
        'app.title': 'Die "lêer"',
        'quit': 'Verlaat',
    }


def test_strings_spans_cover_each_messages_lines():
    assert converters.StringsConverter().spans(EN_STRINGS.encode()) == {
        'app.title': (2, 2),
        'greeting': (4, 4),
        'quit': (5, 5),
    }


# JSON

EN_JSON = """\
{
  "$schema": "https://inlang.com/schema/inlang-message-format",
  "title": "Notes",
  "menu": {
    "open": "Open",
    "save": "Save"
  },
  "item_one": "{{count}} item",
  "item_other": "{{count}} items",
  "list": ["Red", "Green"],
  "version": 3
}
"""


def _json_project(tmp_path, language, target):
    for code, text in (('en', EN_JSON), (language, target)):
        (tmp_path / 'locales' / code).mkdir(parents=True, exist_ok=True)
        _write(tmp_path / 'locales' / code / 'common.json', text)
    en = str(tmp_path / 'locales' / 'en' / 'common.json')
    return en, str(tmp_path / 'locales' / language / 'common.json')


def test_json_pair_nests_keys_with_dots_and_gives_the_target_its_plural_forms(tmp_path):
    en, ru = _json_project(tmp_path, 'ru', '{"title": "Заметки", "item_one": "{{count}} заметка"}')

    store = converters.JsonConverter().to_bilingual(en, ru)

    assert _units(store) == [
        ('title', 'Notes', 'Заметки', None),
        ('menu.open', 'Open', '', None),
        ('menu.save', 'Save', '', None),
        # One plural message, in Russian's four CLDR forms.
        (
            'item',
            ['{{count}} item', '{{count}} items'],
            ['{{count}} заметка', '', '', ''],
            ['one', 'few', 'many', 'other'],
        ),
        ('list.0', 'Red', '', None),
        ('list.1', 'Green', '', None),
    ]
    item = [u for u in store.units if u.getlocations() == ['item']][0]
    assert item.form_keys == ['item_one', 'item_few', 'item_many', 'item_other']
    assert item.file_source == '{{count}} item\x1e{{count}} items'


def test_json_written_back_follows_the_template_and_the_target_language(tmp_path):
    import json

    en, ru = _json_project(tmp_path, 'ru', '{}')
    wanted = {'menu.open': 'Открыть', 'item': ['', '{{count}} заметки'], 'list.1': 'Зелёный'}

    data = converters.JsonConverter().to_monolingual(en, ru, wanted, skip_untranslated=True)

    assert json.loads(data) == {
        '$schema': 'https://inlang.com/schema/inlang-message-format',
        'menu': {'open': 'Открыть'},
        # Every form once one is translated, the English in the others.
        'item_one': '{{count}} item',
        'item_few': '{{count}} заметки',
        'item_many': '{{count}} items',
        'item_other': '{{count}} items',
        'list': ['Зелёный'],
        'version': 3,
    }
    # Indented as the template is.
    assert data.decode().startswith('{\n  "$schema"')
    full = json.loads(converters.JsonConverter().to_monolingual(en, ru, wanted))
    assert list(full) == [
        '$schema',
        'title',
        'menu',
        'item_one',
        'item_few',
        'item_many',
        'item_other',
        'list',
        'version',
    ]
    assert full['title'] == 'Notes' and full['list'] == ['Red', 'Зелёный']


def test_json_round_trip_keeps_every_translation(tmp_path):
    en, af = _json_project(tmp_path, 'af', '{}')
    converter = converters.JsonConverter()
    wanted = {'title': 'Notas', 'menu.save': 'Stoor', 'item': ['1 nota', 'notas']}

    _write(
        tmp_path / 'locales' / 'af' / 'common.json',
        converter.to_monolingual(en, af, wanted, True).decode(),
    )

    found = _translations(converter.to_bilingual(en, af))
    assert {k: v for k, v in found.items() if any(v)} == wanted


def test_json_spans_and_values_skip_metadata():
    converter = converters.JsonConverter()
    spans = converter.spans(EN_JSON.encode())

    assert '$schema' not in spans
    assert spans['menu.save'] == (6, 6)
    assert spans['list.1'] == (10, 10)
    # A plural message: from its first form's line to its last's.
    assert spans['item'] == (8, 9)
    assert converter.values_of(EN_JSON.encode())['item'] == '{{count}} item\x1e{{count}} items'


def test_json_plurals_follow_a_language_the_path_doesn_t_name(tmp_path):
    en = _write(tmp_path / 'en.json', '{"item_one": "1 note", "item_other": "n notes"}')
    target = _write(tmp_path / 'messages.json', '{}')

    store = converters.JsonConverter().to_bilingual(en, target, 'ru')

    assert store.units[1].plural_tags == ['one', 'few', 'many', 'other']


def test_an_arb_s_own_locale_wins_over_a_language_it_was_given(tmp_path):
    en = _write(tmp_path / 'intl_en.arb', '{"@@locale": "en", "a": "A"}')
    target = _write(tmp_path / 'intl_messages.arb', '{"@@locale": "de", "a": "Ä"}')

    data = converters.JsonConverter().to_monolingual(en, target, {'a': 'Ä'}, language='af')

    assert '"@@locale": "de"' in data.decode()


def test_json_that_is_malformed_raises():
    with pytest.raises(ValueError):
        converters.JsonConverter().values_of(b'{"a": ')


# JSON written back in the file's own layout

LAYOUT_EN = (
    '{\n'
    '  "title": "Notes",\n'
    '  "list": ["Red", "Green"],\n'
    '  "inline": {"a": "A", "b": "B"},\n'
    '\n'
    '  "menu": {\n'
    '    "open": "Open",\n'
    '    "save": "Save"\n'
    '  },\n'
    '  "count": 3\n'
    '}\n'
)


def _layout(tmp_path, target=None, template=LAYOUT_EN, newline='\n'):
    for code, text in (('en', template), ('af', target)):
        (tmp_path / code).mkdir(exist_ok=True)
        if text is not None:
            (tmp_path / code / 'a.json').write_bytes(text.replace('\n', newline).encode())
    return str(tmp_path / 'en' / 'a.json'), str(tmp_path / 'af' / 'a.json')


def test_json_unchanged_is_written_back_byte_for_byte(tmp_path):
    target = LAYOUT_EN.replace('Notes', 'Notas').replace('"Open"', '"Maak oop"')
    en, af = _layout(tmp_path, target, newline='\r\n')
    converter = converters.JsonConverter()
    wanted = {k: v for k, v in _translations(converter.to_bilingual(en, af)).items() if v != ''}
    wanted = {k: v for k, v in wanted.items() if converter.values(en).get(k) != v}

    data = converter.to_monolingual(en, af, wanted)

    assert data == (tmp_path / 'af' / 'a.json').read_bytes()


def test_json_one_line_arrays_and_objects_stay_on_one_line(tmp_path):
    target = '{\n  "list": ["Rooi", "Groen"],\n  "inline": {"a": "A"},\n  "count": 3\n}\n'
    en, af = _layout(tmp_path, target)

    data = converters.JsonConverter().to_monolingual(
        en, af, {'list.0': 'Rooi', 'list.1': 'Groen', 'inline.a': 'Ä', 'inline.b': 'Bé'}, True
    )

    assert data.decode() == (
        '{\n  "list": ["Rooi", "Groen"],\n  "inline": {"a": "Ä", "b": "Bé"},\n  "count": 3\n}\n'
    )


def test_json_new_keys_go_beside_their_template_neighbour(tmp_path):
    target = '{\n  "title": "Notas",\n  "menu": {\n    "open": "Maak oop"\n  },\n  "count": 3\n}\n'
    en, af = _layout(tmp_path, target)
    wanted = {'title': 'Notas', 'menu.open': 'Maak oop', 'menu.save': 'Stoor', 'list.0': 'Rooi'}

    data = converters.JsonConverter().to_monolingual(en, af, wanted, True)

    assert data.decode() == (
        '{\n'
        '  "title": "Notas",\n'
        '  "list": ["Rooi"],\n'
        '  "menu": {\n'
        '    "open": "Maak oop",\n'
        '    "save": "Stoor"\n'
        '  },\n'
        '  "count": 3\n'
        '}\n'
    )


def test_json_a_dropped_key_leaves_no_comma_behind(tmp_path):
    target = '{\n  "title": "Notas",\n  "gone": "weg",\n  "menu": {"open": "Maak oop", "x": "y"}\n}'
    en, af = _layout(tmp_path, target)

    data = converters.JsonConverter().to_monolingual(
        en, af, {'title': 'Notas', 'menu.open': 'Maak oop'}, True
    )

    # No final newline, as the target had none.
    assert data.decode() == (
        '{\n  "title": "Notas",\n  "menu": {"open": "Maak oop"},\n  "count": 3\n}'
    )


def test_json_keeps_crlf_and_the_file_s_escaping(tmp_path):
    target = '{\n  "title": "\\u00c4",\n  "count": 3\n}\n'
    en, af = _layout(tmp_path, target, newline='\r\n')

    data = converters.JsonConverter().to_monolingual(
        en, af, {'title': '\u00c4', 'menu.save': 'Stoorë'}, True
    )

    assert data.decode() == (
        '{\r\n  "title": "\\u00c4",\r\n  "menu": {\r\n    "save": "Stoor\\u00eb"\r\n  },\r\n'
        '  "count": 3\r\n}\r\n'
    )


def test_a_new_json_target_takes_the_template_s_layout(tmp_path):
    en, af = _layout(tmp_path)

    data = converters.JsonConverter().to_monolingual(en, af, {'list.1': 'Groen'})

    assert data.decode() == LAYOUT_EN.replace('"Green"', '"Groen"')


def test_json_nested_i18next_plurals_are_written_in_place(tmp_path):
    template = '{\n  "notes": {\n    "item_one": "{{count}} note",\n    "item_other": "{{count}} notes"\n  }\n}\n'
    target = '{\n  "notes": {\n    "item_one": "{{count}} заметка"\n  }\n}\n'
    for code, text in (('en', template), ('ru', target)):
        (tmp_path / code).mkdir()
        (tmp_path / code / 'a.json').write_text(text, encoding='utf-8', newline='')
    en, ru = str(tmp_path / 'en' / 'a.json'), str(tmp_path / 'ru' / 'a.json')

    data = converters.JsonConverter().to_monolingual(
        en, ru, {'notes.item': ['{{count}} заметка', '{{count}} заметки', '', '{{count}} заметки']}
    )

    assert data.decode() == (
        '{\n  "notes": {\n'
        '    "item_one": "{{count}} заметка",\n'
        '    "item_few": "{{count}} заметки",\n'
        '    "item_many": "{{count}} notes",\n'
        '    "item_other": "{{count}} заметки"\n'
        '  }\n}\n'
    )


def test_json_the_layout_can_t_be_kept_for_is_written_whole(tmp_path, monkeypatch, caplog):
    import json

    en, af = _layout(tmp_path, '{"title": "Notas"}')

    def fail(self, wanted):
        raise ValueError('unmapped')

    monkeypatch.setattr(converters._JsonPatch, 'write', fail)
    data = converters.JsonConverter().to_monolingual(en, af, {'title': 'Notas'}, True)

    assert json.loads(data) == {'title': 'Notas', 'count': 3}
    assert "Couldn't keep the layout" in caplog.text


def test_an_arb_target_keeps_its_layout(tmp_path):
    en = _write(tmp_path / 'app_en.arb', ARB_EN)
    target = '{\n    "@@locale": "af",\n    "hello": "Hallo {name}"\n}\n'
    af = _write(tmp_path / 'app_af.arb', target)

    data = converters.JsonConverter().to_monolingual(
        en, af, {'hello': 'Hallo {name}', 'bye': 'Totsiens'}, True
    )

    assert data.decode() == (
        '{\n    "@@locale": "af",\n    "hello": "Hallo {name}",\n    "bye": "Totsiens"\n}\n'
    )


ARB_EN = """\
{
    "@@locale": "en",
    "hello": "Hello {name}",
    "@hello": {"description": "Greets the user", "placeholders": {"name": {}}},
    "bye": "Goodbye"
}
"""


def test_arb_pair_shows_descriptions_and_writes_its_own_locale(tmp_path):
    import json

    en = _write(tmp_path / 'app_en.arb', ARB_EN)
    af = _write(tmp_path / 'app_af.arb', '{"@@locale": "af", "hello": "Hallo {name}"}\n')
    converter = converters.JsonConverter()

    store = converter.to_bilingual(en, af)

    units = {u.getlocations()[0]: u for u in store.units if not u.isheader()}
    assert (units['hello'].target, units['hello'].getnotes()) == ('Hallo {name}', 'Greets the user')
    data = json.loads(converter.to_monolingual(en, af, {'bye': 'Totsiens'}, True))
    # The target had no @hello: the template's metadata stays out of it.
    assert data == {'@@locale': 'af', 'bye': 'Totsiens'}


@pytest.mark.parametrize(
    'language, ordinal, expected',
    [
        ('af', False, ['one', 'other']),
        ('ru', False, ['one', 'few', 'many', 'other']),
        ('ja', False, ['other']),
        ('pt_BR', False, ['one', 'many', 'other']),
        ('en', True, ['one', 'two', 'few', 'other']),
        ('xx', False, None),
        (None, False, None),
    ],
)
def test_plural_categories_are_cldr_s(language, ordinal, expected):
    assert converters.plural_categories(language, ordinal) == expected


# Android

EN_ANDROID = """\
<?xml version="1.0" encoding="utf-8"?>
<resources xmlns:tools="http://schemas.android.com/tools">
    <!-- The app's name -->
    <string name="app_name">Notes</string>
    <string name="greeting">Hello, %1$s! It\\'s <b>nice</b> here.</string>
    <string name="api_key" translatable="false">abc123</string>
    <plurals name="notes_count">
        <item quantity="one">%d note</item>
        <item quantity="other">%d notes</item>
    </plurals>
    <string-array name="colours">
        <item>Red</item>
        <item>Green</item>
    </string-array>
</resources>
"""


def _android_project(tmp_path, folder, target):
    for name, text in (('values', EN_ANDROID), (folder, target)):
        (tmp_path / 'res' / name).mkdir(parents=True, exist_ok=True)
        _write(tmp_path / 'res' / name / 'strings.xml', text)
    return str(tmp_path / 'res' / 'values' / 'strings.xml'), str(
        tmp_path / 'res' / folder / 'strings.xml'
    )


RU_ANDROID = """\
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">Заметки</string>
    <plurals name="notes_count">
        <item quantity="one">%d заметка</item>
    </plurals>
</resources>
"""


def test_android_pair_has_strings_plural_forms_and_array_items(tmp_path):
    en, ru = _android_project(tmp_path, 'values-ru', RU_ANDROID)

    store = converters.AndroidConverter().to_bilingual(en, ru)

    assert _units(store) == [
        ('app_name', 'Notes', 'Заметки', None),
        ('greeting', "Hello, %1$s! It's <b>nice</b> here.", '', None),
        (
            'notes_count',
            ['%d note', '%d notes'],
            ['%d заметка', '', '', ''],
            ['one', 'few', 'many', 'other'],
        ),
        ('colours[0]', 'Red', '', None),
        ('colours[1]', 'Green', '', None),
    ]
    assert store.units[1].getnotes() == "The app's name"
    plural = [u for u in store.units if u.getlocations() == ['notes_count']][0]
    assert plural.form_keys[1] == 'notes_count[few]'


def test_android_written_back_escapes_and_leaves_out_what_isn_t_translated(tmp_path):
    en, af = _android_project(tmp_path, 'values-af', '<resources/>\n')
    wanted = {
        'app_name': 'Notas',
        # Markup after plain text stays markup.
        'greeting': "Hallo, %1$s! Dit's <b>lekker</b> hier.",
        'notes_count': ['%d nota', ''],
        'colours[1]': 'Groen',
    }

    data = converters.AndroidConverter().to_monolingual(en, af, wanted, skip_untranslated=True)

    assert data.decode() == (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<resources xmlns:tools="http://schemas.android.com/tools">\n'
        '    <!-- The app\'s name -->\n'
        '    <string name="app_name">Notas</string>\n'
        '    <string name="greeting">Hallo, %1$s! Dit\\\'s <b>lekker</b> hier.</string>\n'
        '    <plurals name="notes_count">\n'
        '        <item quantity="one">%d nota</item>\n'
        '        <item quantity="other">%d notes</item>\n'
        '    </plurals>\n'
        '    <string-array name="colours">\n'
        '        <item>Red</item>\n'
        '        <item>Groen</item>\n'
        '    </string-array>\n'
        '</resources>\n'
    )
    assert converters.AndroidConverter().values_of(data)['greeting'] == wanted['greeting']


def test_android_written_back_keeps_untranslated_strings_in_english_when_asked(tmp_path):
    en, af = _android_project(tmp_path, 'values-af', '<resources/>\n')

    text = converters.AndroidConverter().to_monolingual(en, af, {}).decode()

    assert '<string name="app_name">Notes</string>' in text
    assert 'api_key' not in text and "The app's name" in text


def test_android_round_trip_keeps_every_translation(tmp_path):
    en, af = _android_project(tmp_path, 'values-af', '<resources/>\n')
    converter = converters.AndroidConverter()
    wanted = {
        'app_name': 'Notas & "kie"',
        'greeting': 'Hallo @ %1$s\nnuwe reël',
        'notes_count': ['%d nota', '%d notas'],
        'colours[0]': 'Rooi',
    }

    _write(
        tmp_path / 'res' / 'values-af' / 'strings.xml',
        converter.to_monolingual(en, af, wanted, True).decode(),
    )

    found = {k: v for k, v in _translations(converter.to_bilingual(en, af)).items() if any(v)}
    # An array is written whole: its untranslated items in English.
    assert found == dict(wanted, **{'colours[1]': 'Green'})


def test_android_spans_cover_each_string_and_item():
    spans = converters.AndroidConverter().spans(EN_ANDROID.encode())

    assert spans == {
        'app_name': (4, 4),
        'greeting': (5, 5),
        'notes_count': (7, 10),
        'colours[0]': (12, 12),
        'colours[1]': (13, 13),
    }


@pytest.mark.parametrize(
    'relpath, text, expected',
    [
        ('res/values-af/strings.xml', EN_ANDROID, converters.AndroidConverter),
        ('res/values/strings.xml', EN_ANDROID, converters.AndroidConverter),
        # Colours, or an XML file elsewhere, aren't string resources.
        (
            'res/values/colors.xml',
            '<resources><color name="a">#fff</color></resources>',
            type(None),
        ),
        ('layout/strings.xml', EN_ANDROID, type(None)),
        ('l10n/app_af.arb', '{}', converters.JsonConverter),
        ('af.json', '{}', converters.JsonConverter),
        ('af.lproj/Localizable.strings', EN_STRINGS, converters.StringsConverter),
    ],
)
def test_monolingual_converter_reads_the_folder_and_the_file(tmp_path, relpath, text, expected):
    path = tmp_path.joinpath(*relpath.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, text)
    assert isinstance(converters.monolingual_converter(str(path)), expected)


@pytest.mark.parametrize(
    'relpath, expected',
    [
        ('locales/af/common.json', True),
        ('af.json', True),
        ('package.json', False),
        ('l10n/intl_messages.arb', True),
        ('res/values-af/strings.xml', True),
        ('res/values-pt-rBR/strings.xml', True),
        ('res/values/strings.xml', True),
        ('res/values-night/strings.xml', False),
    ],
)
def test_a_folder_scan_takes_in_json_only_where_its_path_names_a_language(
    tmp_path, relpath, expected
):
    path = tmp_path.joinpath(*relpath.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, EN_ANDROID if relpath.endswith('.xml') else '{}')
    assert converters.in_folder_scan(str(path), str(tmp_path)) is expected
