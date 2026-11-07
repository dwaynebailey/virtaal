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


PLURAL_SEPARATOR = '\x1e'


def plural_text(forms):
    """One text for a plural message's forms, as a baseline or a
    history compares them."""
    return PLURAL_SEPARATOR.join(str(form) for form in forms)


def unit_text(value, unit=None):
    """The text of a unit's source or target: a plural unit's forms as
    L{plural_text()}, empty when none is translated."""
    if unit is not None and getattr(unit, 'plural_tags', None):
        forms = [str(form) for form in getattr(value, 'strings', [value or ''])]
        return plural_text(forms) if any(forms) else ''
    return str(value) if value else ''


def add_plural_unit(store, key, template_forms, target_forms, categories, form_keys, ordinal=False):
    """Add the plural message C{key} to the PO store C{store}: its
    template forms ({category: text}) give the source (the "one" form as
    singular, "other" as plural), and C{target_forms} the forms of the
    target language's C{categories}. The unit also carries the
    categories, the template's forms as L{plural_text()} (its
    C{file_source}) and the key each form had in Virtaal before forms
    were joined (C{form_keys}); C{ordinal} for ordinal forms."""
    from translate.misc.multistring import multistring

    other = template_forms.get('other', next(iter(template_forms.values())))
    unit = store.addsourceunit(multistring([template_forms.get('one', other), other]))
    unit.addlocation(key)
    if any(target_forms):
        unit.target = multistring(list(target_forms))
    unit.plural_tags = list(categories)
    unit.file_source = plural_text(
        template_forms[category] for category in CLDR_CATEGORIES if category in template_forms
    )
    unit.form_keys = list(form_keys)
    unit.plural_ordinal = ordinal
    return unit


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
        C{translations} ({key: text}, a list of forms for a plural
        message) translated, and every other message
        in the template's text, or left out with C{skip_untranslated}.
        C{target_path} is the file being replaced, if any."""
        raise NotImplementedError

    def values(self, path):
        """{key: text} of a file in this format."""
        return self.values_of(_read(path))

    def target_values(self, template_path, target_path):
        """{key: text} of a target file, its plural messages keyed and
        given as L{plural_text()} as the pair's units are: those of
        L{values()} plus the template's keys the target translates."""
        found = self.values(target_path)
        for unit in self.to_bilingual(template_path, target_path).units:
            if unit.isheader() or not unit.getlocations():
                continue
            text = unit_text(unit.target, unit)
            if text:
                found[unit.getlocations()[0]] = text
        return found

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


_EXAMPLE_NUMBERS = (*range(200), 1000, 10000, 100000, 1000000)
_EXAMPLE_FRACTIONS = ('0.5', '1.5', '2.5')


def plural_examples(language, ordinal=False, count=3):
    """{category: [numbers]} of up to C{count} numbers that take each of
    C{language}'s CLDR plural categories, smallest first; fractions for
    a category no whole number takes. Empty if the language isn't
    known."""
    from decimal import Decimal

    import babel

    try:
        locale = babel.Locale.parse((language or '').replace('-', '_'))
    except (ValueError, babel.UnknownLocaleError):
        return {}
    rule = locale.ordinal_form if ordinal else locale.plural_form
    found = {}
    numbers = list(_EXAMPLE_NUMBERS) + ([] if ordinal else [Decimal(f) for f in _EXAMPLE_FRACTIONS])
    for number in numbers:
        category = rule(number)
        whole = isinstance(number, int)
        if whole or not any(isinstance(n, int) for n in found.get(category, [])):
            found.setdefault(category, [])
            if len(found[category]) < count:
                found[category].append(number)
    return found


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


def _plural_message_key(base, ordinal):
    """The key of an i18next plural message: its base, with _ordinal for
    ordinal forms, as i18next names them."""
    return base + ('_ordinal' if ordinal else '')


class _JsonMessage:
    """A string to translate in a JSON file, or a plural message: its
    C{forms} ({category: text}) then, C{text} otherwise."""

    def __init__(self, path, text, start, end, plural=None):
        self.path = path
        self.text = text
        self.start = start
        self.end = end
        self.forms = None
        self.ordinal = False
        self.base = None
        if plural is not None:
            self.base, category, self.ordinal = plural
            self.path = path[:-1] + (_plural_message_key(self.base, self.ordinal),)
            self.forms = {category: text}

    @property
    def key(self):
        return json_key(self.path)

    def form_key(self, category):
        """The key the form C{category} has in the file."""
        return json_key(self.path[:-1] + (_plural_key(self.base, category, self.ordinal),))

    def add_form(self, category, text, start, end):
        self.forms[category] = text
        self.start = min(self.start, start)
        self.end = max(self.end, end)


def _json_messages(data):
    """[_JsonMessage] of the bytes of a JSON file in file order: an
    i18next plural group (key_one, key_other... with its "other" form) as
    one message where its first form is."""
    leaves = [
        leaf
        for leaf in _JsonLeaves(data.decode('utf-8-sig')).found
        if leaf[0] and not _is_metadata(leaf[0])
    ]
    siblings = {}
    for path, text, _start, _end in leaves:
        if isinstance(path[-1], str):
            siblings.setdefault(path[:-1], {})[path[-1]] = text
    grouped = {
        parent + (key,)
        for parent, mapping in siblings.items()
        for keys in _plural_groups(mapping).values()
        for key in keys.values()
    }
    messages = []
    plurals = {}
    for path, text, start, end in leaves:
        if path not in grouped:
            messages.append(_JsonMessage(path, text, start, end))
            continue
        plural = _plural_suffix(path[-1])
        message = _JsonMessage(path, text, start, end, plural)
        if message.key in plurals:
            plurals[message.key].add_form(plural[1], text, start, end)
        else:
            plurals[message.key] = message
            messages.append(message)
    return messages


def _forms_text(forms):
    """A file's plural forms ({category: text}) as L{plural_text()}, in
    CLDR's order."""
    return plural_text(forms[c] for c in CLDR_CATEGORIES if c in forms)


def _categories(language, ordinal, forms):
    """The plural categories a target in C{language} has: CLDR's, or
    the template's C{forms} if the language isn't known."""
    return plural_categories(language, ordinal) or [c for c in CLDR_CATEGORIES if c in forms]


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
        """Write a plural group in the target language's forms: all of
        them once one is translated (the template's text for the rest),
        else the template's text in each, or none if untranslated keys
        are left out."""
        keys = groups.pop(group, None)
        if keys is None:
            return
        base, ordinal = group
        forms = self.translations.get(json_key(path + (_plural_message_key(base, ordinal),)))
        forms = list(forms or [])
        if self.skip and not any(forms):
            return
        templates = {category: node[key] for category, key in keys.items()}
        for index, category in enumerate(_categories(self.language, ordinal, templates)):
            text = forms[index] if index < len(forms) else ''
            output[_plural_key(base, category, ordinal)] = text or templates.get(
                category, templates['other']
            )

    def _metadata(self, output, key, value):
        if key == '@@locale':
            output[key] = self.language or value
        elif key in self.target:
            output[key] = value


class JsonConverter(MonolingualConverter):
    """JSON key-value files: flat or nested, and Flutter's ARB. An
    i18next plural group (key_one, key_other..., or key_ordinal_one...)
    is one plural message, in the target language's CLDR forms."""

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

    def _raw_values(self, path):
        """{key: text} of every string in a JSON file, plural forms each
        under its own key."""
        leaves = _JsonLeaves(_read(path).decode('utf-8-sig')).found
        return {
            json_key(leaf[0]): leaf[1] for leaf in leaves if leaf[0] and not _is_metadata(leaf[0])
        }

    def values_of(self, data):
        return {
            message.key: message.text if message.forms is None else _forms_text(message.forms)
            for message in _json_messages(data)
        }

    def spans(self, data):
        text = data.decode('utf-8-sig')
        return {
            message.key: (
                text.count('\n', 0, message.start) + 1,
                text.count('\n', 0, message.end) + 1,
            )
            for message in _json_messages(data)
        }

    @staticmethod
    def _load(path):
        import json

        return json.loads(_read(path).decode('utf-8-sig'))

    def to_bilingual(self, template_path, target_path):
        from translate.storage import po

        data = _read(template_path)
        notes = _arb_notes(self._load(template_path))
        targets = self._raw_values(target_path)
        language = _json_language(target_path)
        store = po.pofile()
        for message in _json_messages(data):
            if message.forms is not None:
                categories = _categories(language, message.ordinal, message.forms)
                add_plural_unit(
                    store,
                    message.key,
                    message.forms,
                    [targets.get(message.form_key(c), '') for c in categories],
                    categories,
                    [message.form_key(c) for c in categories],
                    message.ordinal,
                )
                continue
            unit = store.addsourceunit(message.text)
            unit.addlocation(message.key)
            unit.target = targets.get(message.key, '')
            if notes.get(message.key):
                unit.addnote(notes[message.key], 'developer')
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


# Android


_ANDROID_TAGS = ('string', 'plurals', 'string-array')
_EMPTY_RESOURCES = re.compile(rb'<resources\b[^>]*(?:/>|>\s*</resources>)')


def _android_root(data):
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, strip_cdata=False)
    return etree.fromstring(data, parser)


def _android_codec(element):
    """A unit whose XML text helpers decode and encode Android's
    escaping on C{element}, in the markup style C{element} has
    (translate-toolkit 3.20 keeps one style per unit)."""
    from translate.storage import aresource

    return aresource.AndroidResourceUnit(None, xmlelement=element)


def _android_text(element):
    """The text of an Android string or item, unescaped."""
    return _android_codec(element).get_xml_text_value(element)


def _android_entries(root):
    """(key, element, items) of each message to translate in a resources
    element: a <string> (items None), a <string-array> item (key
    name[index], items None) or a <plurals> (key its name, items
    {quantity: item}). Those marked translatable="false" are left out."""
    for element in root:
        name = element.get('name') if isinstance(element.tag, str) else None
        if element.tag not in _ANDROID_TAGS or not name:
            continue
        if element.get('translatable') == 'false':
            continue
        items = [item for item in element if item.tag == 'item']
        if element.tag == 'string':
            yield name, element, None
        elif element.tag == 'plurals':
            quantities = {item.get('quantity'): item for item in items if item.get('quantity')}
            if quantities:
                yield name, element, quantities
        else:
            for index, item in enumerate(items):
                yield '%s[%d]' % (name, index), item, None


def _android_forms(items):
    """{quantity: text} of a <plurals>' items."""
    return {quantity: _android_text(item) for quantity, item in items.items()}


def _android_note(element):
    """The comment just before C{element}, if any."""
    previous = element.getprevious()
    if previous is not None and not isinstance(previous.tag, str) and previous.text:
        if 'Comment' in str(previous.tag):
            return previous.text.strip()
    return None


class AndroidConverter(MonolingualConverter):
    """Android string resources (res/values-af/strings.xml): strings,
    string arrays (an item per message) and plurals (one plural message
    each, in the target language's CLDR quantities)."""

    id = 'android'
    name = 'Android String Resources'
    extensions = ('xml',)
    modules = ('lxml', 'babel', 'translate.storage.aresource')
    leaves_out_untranslated = True

    def handles(self, path):
        folder = os.path.basename(os.path.dirname(os.path.abspath(path)))
        if folder != 'values' and not folder.startswith('values-'):
            return False
        try:
            data = _read(path)
        except OSError:
            return True
        # A new translation may hold no strings yet.
        return bool(_EMPTY_RESOURCES.search(data)) or any(
            b'<' + tag.encode() in data for tag in _ANDROID_TAGS
        )

    def in_folder(self, path, root):
        from virtaal.models.fileset import path_language

        folder = os.path.dirname(os.path.abspath(path))
        return (
            self.is_source_path(path) or path_language(folder, os.path.dirname(folder)) is not None
        )

    def is_source_path(self, path):
        return os.path.basename(os.path.dirname(os.path.abspath(path))) == 'values'

    def values_of(self, data):
        return {
            key: _android_text(element) if items is None else _forms_text(_android_forms(items))
            for key, element, items in _android_entries(_android_root(data))
        }

    def spans(self, data):
        from lxml import etree

        found = {}
        for key, element, _items in _android_entries(_android_root(data)):
            lines = etree.tostring(element, with_tail=False).count(b'\n')
            found[key] = (element.sourceline, element.sourceline + lines)
        return found

    @staticmethod
    def _targets(target_path):
        """{key: text or {quantity: text}} of a target file."""
        if not target_path or not os.path.isfile(target_path):
            return {}
        return {
            key: _android_text(element) if items is None else _android_forms(items)
            for key, element, items in _android_entries(_android_root(_read(target_path)))
        }

    def to_bilingual(self, template_path, target_path):
        from translate.storage import po

        root = _android_root(_read(template_path))
        targets = self._targets(target_path)
        language = _target_language(target_path)
        store = po.pofile()
        for key, element, items in _android_entries(root):
            note = _android_note(element if element.tag != 'item' else element.getparent())
            if element.tag == 'item' and element.getprevious() is not None:
                note = None
            if items is None:
                unit = store.addsourceunit(_android_text(element))
                unit.addlocation(key)
                target = targets.get(key, '')
                unit.target = target if isinstance(target, str) else ''
            else:
                forms = _android_forms(items)
                categories = _categories(language, False, forms)
                target = targets.get(key)
                target = target if isinstance(target, dict) else {}
                unit = add_plural_unit(
                    store,
                    key,
                    forms,
                    [target.get(c, '') for c in categories],
                    categories,
                    ['%s[%s]' % (key, c) for c in categories],
                )
            if note:
                unit.addnote(note, 'developer')
        return store

    def to_monolingual(self, template_path, target_path, translations, skip_untranslated=False):
        from lxml import etree

        data = _read(template_path)
        root = _android_root(data)
        writer = _AndroidWriter(
            translations, skip_untranslated, plural_categories(_target_language(target_path))
        )
        writer.write(root)
        declaration = re.match(rb'<\?xml[^>]*\?>\s*', data)
        body = etree.tostring(root, encoding='utf-8', with_tail=False)
        return (declaration.group(0) if declaration else b'') + body + b'\n'


def _drop(element):
    """Remove C{element}, the comment just before it, and its line."""
    parent = element.getparent()
    previous = element.getprevious()
    if previous is not None and 'Comment' in str(previous.tag):
        _drop(previous)
        previous = element.getprevious()
    if element.getnext() is None:
        if previous is not None:
            previous.tail = element.tail
        else:
            parent.text = element.tail
    parent.remove(element)


class _AndroidWriter:
    """A template's resources translated in place: untranslated strings
    keep the template's text or are left out, plurals get the target
    language's quantities, untranslatable strings are left out."""

    def __init__(self, translations, skip_untranslated, categories):
        self.translations = translations
        self.skip = skip_untranslated
        self.categories = categories

    def write(self, root):
        for element in list(root):
            name = element.get('name') if isinstance(element.tag, str) else None
            if element.tag not in _ANDROID_TAGS or not name:
                continue
            if element.get('translatable') == 'false':
                _drop(element)
            elif element.tag == 'string':
                self._string(element, name)
            elif element.tag == 'plurals':
                self._plurals(element, name)
            else:
                self._array(element, name)

    def _string(self, element, name):
        if name in self.translations:
            _set_text(self.translations[name], element)
        elif self.skip:
            _drop(element)

    def _array(self, element, name):
        items = [item for item in element if item.tag == 'item']
        keys = ['%s[%d]' % (name, index) for index in range(len(items))]
        if self.skip and not any(key in self.translations for key in keys):
            _drop(element)
            return
        for key, item in zip(keys, items, strict=True):
            if key in self.translations:
                _set_text(self.translations[key], item)

    def _plurals(self, element, name):
        """The target language's quantities: all of them once one is
        translated (the template's text for the rest), else the
        template's text in each, or none if untranslated strings are
        left out."""
        items = {item.get('quantity'): item for item in element if item.tag == 'item'}
        forms = list(self.translations.get(name) or [])
        if self.skip and not any(forms):
            _drop(element)
            return
        if not items:
            return
        categories = self.categories or [c for c in CLDR_CATEGORIES if c in items]
        fallback = items.get('other', next(iter(items.values())))
        model = next(iter(items.values()))
        last_tail = list(items.values())[-1].tail
        for item in items.values():
            element.remove(item)
        item = None
        for index, category in enumerate(categories):
            item = self._item(element, model, category)
            text = forms[index] if index < len(forms) else ''
            if text:
                item = _set_text(text, item)
            else:
                _copy_text(items.get(category, fallback), item)
        item.tail = last_tail

    @staticmethod
    def _item(element, model, category):
        import copy

        item = copy.copy(model)
        for child in list(item):
            item.remove(child)
        item.text = None
        item.set('quantity', category)
        element.append(item)
        return item


def _set_text(text, element):
    """Write C{text} into C{element} with Android's escaping, keeping
    the whitespace after it. translate-toolkit may put a new element in
    its place: that one is returned."""
    tail = element.tail
    parent = element.getparent()
    index = parent.index(element)
    _android_codec(element).set_xml_text_value(text, element)
    element = parent[index]
    element.tail = tail
    return element


def _copy_text(source, item):
    import copy

    item.text = source.text
    item.extend(copy.deepcopy(child) for child in source)


MONOLINGUAL_CONVERTERS = [
    PropertiesConverter(),
    FluentConverter(),
    StringsConverter(),
    JsonConverter(),
    AndroidConverter(),
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
