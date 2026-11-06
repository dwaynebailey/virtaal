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

    def available(self):
        import importlib.util

        return all(importlib.util.find_spec(module) for module in self.modules)

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
        raise NotImplementedError


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
    def _store(path):
        from translate.storage import properties

        data = _read(path)
        store = properties.propfile(personality=properties_personality(data))
        store.filename = path
        store.parse(data)
        return store

    def values(self, path):
        return {unit.name: unit.source for unit in self._store(path).units if not unit.isblank()}

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


class FluentConverter(MonolingualConverter):
    id = 'fluent'
    name = 'Fluent file'
    extensions = ('ftl',)
    modules = ('fluent.syntax', 'translate.convert.fluent2po')

    @staticmethod
    def _store(path):
        from translate.storage import fluent

        from virtaal.support import fluent_compat  # noqa: F401 - applies its patch on import

        with open(path, 'rb') as f:
            return fluent.FluentFile(f)

    @staticmethod
    def _messages(store):
        return [unit for unit in store.units if not unit.isheader() and unit.istranslatable()]

    def values(self, path):
        return {unit.getid(): unit.source for unit in self._messages(self._store(path))}

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


MONOLINGUAL_CONVERTERS = [PropertiesConverter(), FluentConverter()]


def _by_extension():
    table = {}
    for converter in MONOLINGUAL_CONVERTERS:
        if converter.available():
            for extension in converter.extensions:
                table[extension] = converter
    return table


def monolingual_converter(path):
    """The converter for C{path}'s format if it's monolingual, else C{None}."""
    extension = os.path.splitext(path)[1][1:].lower()
    return _by_extension().get(extension) if extension else None


def is_monolingual(path):
    return monolingual_converter(path) is not None


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
