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
    return {unit.getlocations()[0]: unit.target for unit in store.units if not unit.isheader()}


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
