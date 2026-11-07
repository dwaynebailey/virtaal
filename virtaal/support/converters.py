#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Converters for formats translate-toolkit can't open as a translation
store by themselves.

A monolingual format keeps one file per language, so a translation is
the target file paired with its source-language template:
C{to_bilingual()} merges the two into a PO store in memory, and
C{to_monolingual()} writes that store back in the target's own format,
laid out like the template. Nothing here touches a file on disk."""

import io
import os
import re


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


class MonolingualConverter:
    """One monolingual format."""

    id = ''
    name = ''
    """The format's name, translated where it's shown."""
    extensions = ()
    modules = ()
    """Modules the converter needs; without them it isn't offered."""

    leaves_out_untranslated = False
    """Whether a target leaves out untranslated keys unless the project
    or the user says otherwise: the platform falls back to the
    template's text for a missing key."""

    def available(self):
        import importlib.util

        return all(importlib.util.find_spec(module) for module in self.modules)

    def handles(self, path):
        """Whether C{path}, with one of L{extensions}, is in this format."""
        return True

    def in_folder(self, path, root):
        """Whether a folder scan of C{root} takes in C{path}."""
        return True

    def is_source_path(self, path):
        """Whether C{path}'s place says it's a source-language file."""
        return False

    def to_bilingual(self, template_path, target_path):
        """A PO store with the template's units, translated from the
        target's. Each unit's first location is its key."""
        raise NotImplementedError

    def to_monolingual(self, template_path, target_path, translations, skip_untranslated=False):
        """The bytes of the target file: the template with each key in
        C{translations} ({key: text}) translated, and every other message
        in the template's text, or left out with C{skip_untranslated}.
        C{target_path} is the file being replaced, if any."""
        raise NotImplementedError

    def values(self, path):
        """{key: text} of a file in this format."""
        return self.values_of(_read(path))

    def values_of(self, data):
        """{key: text} of the bytes of a file in this format."""
        raise NotImplementedError

    def spans(self, data):
        """{key: (first line, last line)}, counted from 1, of each
        message in the bytes of a file in this format."""
        raise NotImplementedError


def _continues(line):
    """Whether a .properties line goes on to the next."""
    line = line.rstrip(b'\r')
    return (len(line) - len(line.rstrip(b'\\'))) % 2 == 1


def properties_personality(data):
    """The .properties dialect that reads C{data}: UTF-8 when it has
    non-ASCII text that decodes as UTF-8, else Java's Latin-1 with
    \\u escapes."""
    if data.isascii():
        return 'java'
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return 'java'
    return 'java-utf8'


class PropertiesConverter(MonolingualConverter):
    id = 'properties'
    name = 'Java Properties'
    extensions = ('properties',)
    modules = ('translate.convert.prop2po', 'translate.convert.po2prop')

    @staticmethod
    def _parse(data, path=None):
        from translate.storage import properties

        store = properties.propfile(personality=properties_personality(data))
        store.filename = path
        store.parse(data)
        return store

    @classmethod
    def _store(cls, path):
        return cls._parse(_read(path), path)

    def values_of(self, data):
        return {unit.name: unit.source for unit in self._parse(data).units if not unit.isblank()}

    def spans(self, data):
        lines = data.split(b'\n')
        spans = []
        number = 0
        while number < len(lines):
            line = lines[number]
            number += 1
            stripped = line.strip()
            if not stripped or stripped[:1] in (b'#', b'!'):
                continue
            start = number
            while _continues(line) and number < len(lines):
                line = lines[number]
                number += 1
            spans.append((start, number))
        names = [unit.name for unit in self._parse(data).units if not unit.isblank()]
        if len(names) != len(spans):
            return {}
        return dict(zip(names, spans, strict=True))

    def to_bilingual(self, template_path, target_path):
        from translate.convert import prop2po

        return prop2po.prop2po().mergestore(self._store(template_path), self._store(target_path))

    def to_monolingual(self, template_path, target_path, translations, skip_untranslated=False):
        from translate.convert import po2prop
        from translate.storage import po

        template = _read(template_path)
        target = _read(target_path) if target_path and os.path.isfile(target_path) else b''
        personality = properties_personality(target)
        text = template.decode(
            'iso-8859-1' if properties_personality(template) == 'java' else 'utf-8'
        )
        if personality == 'java':
            try:
                text.encode('iso-8859-1')
            except UnicodeEncodeError:
                personality = 'java-utf8'
        encoding = 'iso-8859-1' if personality == 'java' else 'utf-8'
        inputstore = po.pofile()
        # An untranslated key gets the template's text unless removed.
        for key, source in self.values(template_path).items():
            unit = inputstore.addsourceunit(source)
            unit.addlocation(key)
            unit.target = translations.get(key, '')
        writer = po2prop.reprop(
            io.BytesIO(text.encode(encoding)),
            inputstore,
            personality,
            encoding,
            remove_untranslated=skip_untranslated,
        )
        return writer.convertstore()


_FLUENT_ENTRY = re.compile(rb'(-?[A-Za-z][A-Za-z0-9_-]*)[ \t]*=')


class FluentConverter(MonolingualConverter):
    id = 'fluent'
    name = 'Fluent file'
    extensions = ('ftl',)
    modules = ('fluent.syntax', 'translate.convert.fluent2po')

    @staticmethod
    def _parse(data):
        from translate.storage import fluent

        from virtaal.support import fluent_compat  # noqa: F401 - applies its patch on import

        return fluent.FluentFile(io.BytesIO(data))

    @classmethod
    def _store(cls, path):
        store = cls._parse(_read(path))
        store.filename = path
        return store

    @staticmethod
    def _messages(store):
        return [unit for unit in store.units if not unit.isheader() and unit.istranslatable()]

    def values_of(self, data):
        return {unit.getid(): unit.source for unit in self._messages(self._parse(data))}

    def spans(self, data):
        found = {}
        key = None
        last = 0
        for number, line in enumerate(data.split(b'\n'), 1):
            if line[:1].isspace() or not line.strip():
                if key is not None and line.strip():
                    last = number
                continue
            if key is not None:
                found[key] = (found[key][0], last)
            match = _FLUENT_ENTRY.match(line)
            key = match.group(1).decode('utf-8') if match else None
            if key is not None:
                found[key] = (number, number)
                last = number
        if key is not None:
            found[key] = (found[key][0], last)
        return found

    def to_bilingual(self, template_path, target_path):
        from translate.convert import fluent2po

        return fluent2po.fluent2po().merge_store(
            self._store(template_path), self._store(target_path)
        )

    def to_monolingual(self, template_path, target_path, translations, skip_untranslated=False):
        store = self._store(template_path)
        for unit in self._messages(store):
            if unit.getid() in translations:
                unit.source = translations[unit.getid()]
            elif skip_untranslated:
                store.removeunit(unit)
        return bytes(store)


# Apple .strings


_STRINGS_ENTRY = re.compile(rb'^\s*("(?:\\.|[^"\\])*"|[\w.$:-]+)\s*=')
_STRINGS_END = re.compile(rb'"\s*;\s*(?:/[/*].*)?\r?$')


def _strings_end(lines, start):
    """The line, counted from 1, where the .strings entry on line
    C{start} ends."""
    for number in range(start, len(lines) + 1):
        if _STRINGS_END.search(lines[number - 1]):
            return number
    return start


def strings_personality(data):
    """The .strings dialect that reads C{data}: UTF-16 with a byte order
    mark, else UTF-8."""
    return 'strings' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'strings-utf8'


class StringsConverter(PropertiesConverter):
    """Apple's .strings (Localizable.strings and others)."""

    id = 'strings'
    name = 'Apple Strings'
    extensions = ('strings',)

    def is_source_path(self, path):
        from virtaal.support.templatefinder import is_source_folder

        return is_source_folder(os.path.basename(os.path.dirname(os.path.abspath(path))))

    @staticmethod
    def _parse(data, path=None):
        from translate.storage import properties

        store = properties.propfile(personality=strings_personality(data))
        store.filename = path
        store.parse(data)
        return store

    def spans(self, data):
        if strings_personality(data) == 'strings':
            # Git's line numbers don't fit UTF-16.
            return {}
        lines = data.split(b'\n')
        starts = [number for number, line in enumerate(lines, 1) if _STRINGS_ENTRY.match(line)]
        names = [unit.name for unit in self._parse(data).units if not unit.isblank()]
        if len(names) != len(starts):
            return {}
        return {
            name: (start, _strings_end(lines, start))
            for name, start in zip(names, starts, strict=True)
        }

    def to_monolingual(self, template_path, target_path, translations, skip_untranslated=False):
        store = self._store(template_path)
        for unit in list(store.units):
            if unit.isblank():
                continue
            if unit.name in translations:
                unit.target = translations[unit.name]
            elif skip_untranslated:
                store.removeunit(unit)
        text = bytes(store).decode(store.encoding)
        target = _read(target_path) if target_path and os.path.isfile(target_path) else b''
        return text.encode('utf-16' if strings_personality(target) == 'strings' else 'utf-8')


# JSON


CLDR_CATEGORIES = ('zero', 'one', 'two', 'few', 'many', 'other')


def plural_categories(language, ordinal=False):
    """The CLDR plural categories of C{language}, in CLDR's order, or
    C{None} if it isn't known."""
    import babel

    if not language:
        return None
    try:
        locale = babel.Locale.parse(language.replace('-', '_'))
    except (ValueError, babel.UnknownLocaleError):
        return None
    tags = (locale.ordinal_form if ordinal else locale.plural_form).tags | {'other'}
    return [category for category in CLDR_CATEGORIES if category in tags]


def _target_language(target_path):
    from virtaal.models.fileset import path_language

    return path_language(target_path) if target_path else None


_JSON_SPACE = re.compile(r'[ \t\n\r]*')


class _JsonLeaves:
    """Each string in a JSON text, with its path of keys and indices and
    where it is: [(path, text, start, end)] in file order."""

    def __init__(self, text):
        import json

        json.loads(text)  # Malformed JSON raises ValueError here.
        self.text = text
        self.found = []
        self._decoder = json.JSONDecoder()
        self._value(0, ())

    def _space(self, pos):
        return _JSON_SPACE.match(self.text, pos).end()

    def _value(self, pos, path):
        from json.decoder import scanstring

        pos = self._space(pos)
        first = self.text[pos : pos + 1]
        if first == '{':
            return self._object(pos + 1, path)
        if first == '[':
            return self._array(pos + 1, path)
        if first == '"':
            value, end = scanstring(self.text, pos + 1)
            self.found.append((path, value, pos, end))
            return end
        return self._decoder.raw_decode(self.text, pos)[1]

    def _object(self, pos, path):
        from json.decoder import scanstring

        pos = self._space(pos)
        if self.text[pos] == '}':
            return pos + 1
        while True:
            key, pos = scanstring(self.text, self._space(pos) + 1)
            pos = self._value(self._space(pos) + 1, path + (key,))
            pos = self._space(pos)
            if self.text[pos] == '}':
                return pos + 1
            pos += 1

    def _array(self, pos, path):
        pos = self._space(pos)
        if self.text[pos] == ']':
            return pos + 1
        index = 0
        while True:
            pos = self._space(self._value(pos, path + (index,)))
            index += 1
            if self.text[pos] == ']':
                return pos + 1
            pos += 1


def json_key(path):
    """A JSON string's key: its path's keys and indices joined by dots,
    as i18next names nested keys."""
    return '.'.join(str(part) for part in path)


def _is_metadata(path):
    """ARB's @key and @@locale entries, and a top-level $schema."""
    return path[0].startswith('@') or path[0] == '$schema'


def _plural_suffix(key):
    """(base, category, ordinal) of an i18next plural key like
    item_one or place_ordinal_two, else None."""
    base, _sep, category = key.rpartition('_')
    if not base or category not in CLDR_CATEGORIES:
        return None
    if base.endswith('_ordinal'):
        return base[: -len('_ordinal')], category, True
    return base, category, False


def _plural_groups(mapping):
    """{(base, ordinal): {category: key}} of the i18next plural keys in a
    JSON object: those whose "other" form is there."""
    groups = {}
    for key, value in mapping.items():
        found = _plural_suffix(key)
        if found and isinstance(value, str):
            base, category, ordinal = found
            groups.setdefault((base, ordinal), {})[category] = key
    return {group: keys for group, keys in groups.items() if 'other' in keys}


def _plural_key(base, category, ordinal):
    return '%s_%s%s' % (base, 'ordinal_' if ordinal else '', category)


def _arb_notes(template):
    """{key: description} of an ARB template's @key entries."""
    if not isinstance(template, dict):
        return {}
    return {
        key[1:]: value['description']
        for key, value in template.items()
        if key.startswith('@')
        and not key.startswith('@@')
        and isinstance(value, dict)
        and isinstance(value.get('description'), str)
    }


def _json_language(target_path):
    """The target's language: from its path, else an ARB's @@locale."""
    language = _target_language(target_path)
    if language or not target_path or not os.path.isfile(target_path):
        return language
    try:
        locale = JsonConverter._load(target_path).get('@@locale')
    except (ValueError, AttributeError, OSError):
        return None
    return locale if isinstance(locale, str) and locale else None


def _json_indent(text):
    """The indent of a JSON text's first indented line: a tab, or so
    many spaces; 2 if it has none."""
    match = re.search(r'\n([ \t]+)\S', text)
    if not match:
        return 2
    indent = match.group(1)
    return '\t' if indent.startswith('\t') else len(indent)


class _JsonWriter:
    """A JSON template's tree with its strings translated: untranslated
    ones keep the template's text or are left out, plural keys follow
    the target language's forms, and ARB's @@locale is the target's.
    ARB @key entries stay only where the target has them."""

    def __init__(self, translations, skip_untranslated, language, target):
        self.translations = translations
        self.skip = skip_untranslated
        self.language = language
        self.target = target

    def write(self, node, path):
        """The output for C{node} at C{path}; C{None} to leave it out."""
        if isinstance(node, dict):
            return self._object(node, path)
        if isinstance(node, list):
            items = [self.write(value, path + (index,)) for index, value in enumerate(node)]
            return [item for item in items if item is not None] or (None if node else [])
        if isinstance(node, str) and path and not _is_metadata(path):
            return self._text(json_key(path), node)
        return node

    def _text(self, key, source):
        if key in self.translations:
            return self.translations[key]
        return None if self.skip else source

    def _object(self, node, path):
        groups = _plural_groups(node)
        grouped = {key: group for group, keys in groups.items() for key in keys.values()}
        output = {}
        for key, value in node.items():
            if key in grouped:
                self._plural(output, node, grouped[key], groups, path)
            elif not path and key.startswith('@'):
                self._metadata(output, key, value)
            else:
                written = self.write(value, path + (key,))
                if written is not None:
                    output[key] = written
        return output if output or not node else None

    def _plural(self, output, node, group, groups, path):
        keys = groups.pop(group, None)
        if keys is None:
            return
        for name, source in JsonConverter._forms(node, group, keys, self.language):
            text = self._text(json_key(path + (name,)), source)
            if text is not None:
                output[name] = text

    def _metadata(self, output, key, value):
        if key == '@@locale':
            output[key] = self.language or value
        elif key in self.target:
            output[key] = value


class JsonConverter(MonolingualConverter):
    """JSON key-value files: flat or nested (i18next, with its _one,
    _other... plural keys given the target language's forms), and
    Flutter's ARB."""

    id = 'json'
    name = 'JSON file'
    extensions = ('json', 'arb')
    modules = ('babel',)

    def in_folder(self, path, root):
        from virtaal.models.fileset import path_language

        return path.lower().endswith('.arb') or path_language(path, root) is not None

    @staticmethod
    def _text(data):
        return data.decode('utf-8-sig')

    def _strings(self, data):
        """[(key, text, start, end)] of the strings to translate."""
        leaves = _JsonLeaves(self._text(data)).found
        return [
            (json_key(path), text, start, end)
            for path, text, start, end in leaves
            if path and not _is_metadata(path)
        ]

    def values_of(self, data):
        return {key: text for key, text, _start, _end in self._strings(data)}

    def spans(self, data):
        text = self._text(data)
        return {
            key: (text.count('\n', 0, start) + 1, text.count('\n', 0, end) + 1)
            for key, _text, start, end in self._strings(data)
        }

    @staticmethod
    def _load(path):
        import json

        return json.loads(_read(path).decode('utf-8-sig'))

    def _units(self, template, language):
        """[(key, source)] the template gives for C{language}."""
        found = []
        self._walk_units(template, (), language, found)
        return found

    def _walk_units(self, node, path, language, found):
        if isinstance(node, list):
            for index, value in enumerate(node):
                self._walk_units(value, path + (index,), language, found)
        elif isinstance(node, dict):
            self._object_units(node, path, language, found)
        elif isinstance(node, str) and path and not _is_metadata(path):
            found.append((json_key(path), node))

    def _object_units(self, node, path, language, found):
        groups = _plural_groups(node)
        grouped = {key for keys in groups.values() for key in keys.values()}
        for key, value in node.items():
            if key not in grouped:
                self._walk_units(value, path + (key,), language, found)
                continue
            group = next(g for g, keys in groups.items() if key in keys.values())
            if key != next(iter(groups[group].values())):
                continue
            for name, source in self._forms(node, group, groups[group], language):
                found.append((json_key(path + (name,)), source))

    @staticmethod
    def _forms(node, group, keys, language):
        """(key, source) of each plural form C{language} needs, with the
        template's "other" text where the template lacks the form."""
        base, ordinal = group
        categories = plural_categories(language, ordinal) or list(keys)
        for category in categories:
            source = node[keys.get(category, keys['other'])]
            yield _plural_key(base, category, ordinal), source

    def to_bilingual(self, template_path, target_path):
        from translate.storage import po

        template = self._load(template_path)
        notes = _arb_notes(template)
        targets = self.values(target_path)
        store = po.pofile()
        for key, source in self._units(template, _json_language(target_path)):
            unit = store.addsourceunit(source)
            unit.addlocation(key)
            unit.target = targets.get(key, '')
            if notes.get(key):
                unit.addnote(notes[key], 'developer')
        return store

    def to_monolingual(self, template_path, target_path, translations, skip_untranslated=False):
        import json

        template_text = _read(template_path).decode('utf-8-sig')
        target = self._load(target_path) if target_path and os.path.isfile(target_path) else {}
        writer = _JsonWriter(
            translations,
            skip_untranslated,
            _json_language(target_path),
            target if isinstance(target, dict) else {},
        )
        tree = writer.write(json.loads(template_text), ())
        tree = {} if tree is None else tree
        text = json.dumps(tree, ensure_ascii=False, indent=_json_indent(template_text))
        return (text + '\n').encode('utf-8')


MONOLINGUAL_CONVERTERS = [
    PropertiesConverter(),
    FluentConverter(),
    StringsConverter(),
    JsonConverter(),
]


def _by_extension():
    table = {}
    for converter in MONOLINGUAL_CONVERTERS:
        if converter.available():
            for extension in converter.extensions:
                table.setdefault(extension, []).append(converter)
    return table


def _extension(path):
    return os.path.splitext(path)[1][1:].lower()


def may_be_monolingual(name):
    """Whether a file named C{name} may be monolingual: its extension is
    one a monolingual format has."""
    return _extension(name) in _by_extension()


def monolingual_converter(path):
    """The converter for C{path}'s format if it's monolingual, else C{None}."""
    for converter in _by_extension().get(_extension(path), ()):
        if converter.handles(path):
            return converter
    return None


def is_monolingual(path):
    return monolingual_converter(path) is not None


def in_folder_scan(path, root):
    """Whether a folder scan of C{root} takes in the monolingual file
    C{path}: a JSON file only where its path names a language, an
    Android resource only in a language's values folder."""
    converter = monolingual_converter(path)
    return converter is not None and converter.in_folder(path, root)


def is_source_path(path):
    """Whether a monolingual file's place says it's a source-language
    file, such as Android's res/values/strings.xml."""
    converter = monolingual_converter(path)
    return converter is not None and converter.is_source_path(path)


def open_formats():
    """(name, extensions) of the monolingual formats the storage factory
    doesn't list itself, for the Open dialog's filters."""
    from translate.storage import factory

    listed = {ext for _name, extensions, _mimes in factory.supported_files() for ext in extensions}
    return [
        (converter.name, list(converter.extensions))
        for converter in MONOLINGUAL_CONVERTERS
        if converter.available() and not set(converter.extensions) <= listed
    ]
