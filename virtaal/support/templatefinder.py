#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Find the source-language file (template) of a monolingual translation
file: from the project's localisation config files, then from the usual
file layouts. No GTK here."""

import configparser
import os
import posixpath
import re
from typing import NamedTuple

from virtaal.models.fileset import folder_language_code, known_language, path_language

SOURCE_CODES = ('en', 'en-US', 'en_US', 'en-GB', 'en_GB')
"""Language codes a template's path may use, in order of preference."""


class TemplateMatch(NamedTuple):
    template: str
    language: str | None
    """The target's language, if the match says."""
    how: str
    """What found it: a config file's name, or "layout"."""
    skip_untranslated: bool | None = None
    """Whether the project leaves untranslated keys out of the target
    (C{None}: it doesn't say)."""


# File masks, as amaGama reads them: a path relative to the config's
# directory, with * (within one directory), ** (any directories) and
# {lang}.

_TOKENS = re.compile(r'(\*\*/|\*\*|\*|\{lang\})')
_LANG_PATTERN = r'[a-z]{1,3}(?:[_+-][A-Za-z0-9]{1,8}|@[A-Za-z0-9]+)*'
_WILDCARDS = {'**/': '(?:.*/)?', '**': '.*', '*': '[^/]*?'}


def compile_mask(mask):
    """The regular expression for C{mask}; ValueError if it's malformed."""
    if not mask or mask.startswith('/') or '..' in mask.split('/') or mask.count('{lang}') > 1:
        raise ValueError('bad file mask %r' % mask)
    pattern = ''
    wildcard = 0
    for token in _TOKENS.split(mask):
        if token in _WILDCARDS:
            pattern += '(?P<w%d>%s)' % (wildcard, _WILDCARDS[token])
            wildcard += 1
        elif token == '{lang}':
            pattern += '(?P<lang>%s)' % _LANG_PATTERN
        else:
            pattern += re.escape(token)
    return re.compile(pattern + r'\Z')


def _wildcards(mask):
    return [token for token in _TOKENS.split(mask) if token in _WILDCARDS]


def fill(template, match):
    """C{template} with each wildcard replaced by what the same one in
    C{match} took, and {lang} by the match's language."""
    parts = []
    wildcard = 0
    for token in _TOKENS.split(template):
        if token in _WILDCARDS:
            parts.append(match.group('w%d' % wildcard))
            wildcard += 1
        elif token == '{lang}':
            parts.append(match.group('lang'))
        else:
            parts.append(token)
    return ''.join(parts)


def _clean(path):
    """A config's path, relative to its own directory, with / separators."""
    path = re.sub(r'/+', '/', str(path).strip().replace('\\', '/'))
    while path.startswith(('./', '/')):
        path = path[1:] if path.startswith('/') else path[2:]
    return posixpath.normpath(path) if path else ''


def _join(base, path):
    joined = posixpath.normpath(posixpath.join(base, _clean(path))) if base else _clean(path)
    return '' if joined == '.' else joined


# Config readers: each takes a config file's text and gives
# [(mask, template, skip_untranslated)], relative to the directory the
# config's paths are relative to.

_CROWDIN_LANGUAGE = (
    'locale',
    'locale_with_underscore',
    'two_letters_code',
    'three_letters_code',
    'android_code',
    'osx_locale',
)


def _crowdin_mask(translation, source):
    name = posixpath.basename(source)
    stem, extension = posixpath.splitext(name)
    own = {
        'original_file_name': name,
        'file_name': stem,
        'file_extension': extension[1:],
        'original_path': posixpath.dirname(source),
    }
    seen = []

    def replace(match):
        placeholder = match.group(1)
        if placeholder in own:
            return own[placeholder]
        if placeholder in _CROWDIN_LANGUAGE:
            seen.append(placeholder)
            return '{lang}' if len(seen) == 1 else '*'
        raise ValueError(placeholder)

    try:
        mask = re.sub(r'%(\w+)%', replace, _clean(translation))
    except ValueError:
        return None
    return posixpath.normpath(mask) if seen else None


def read_crowdin(text):
    import yaml

    try:
        # Every value as text: YAML 1.1 reads a language code "no" as false.
        config = yaml.load(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError:
        return []
    if not isinstance(config, dict) or not isinstance(config.get('files'), list):
        return []
    base = _clean(config.get('base_path') or '.')
    base = '' if base == '.' else base
    found = []
    for entry in config['files']:
        if not isinstance(entry, dict) or not entry.get('source') or not entry.get('translation'):
            continue
        mask = _crowdin_mask(entry['translation'], _clean(entry['source']))
        skip = str(entry.get('skip_untranslated_strings', '')).lower() == 'true'
        if mask:
            found.append((_join(base, mask), _join(base, entry['source']), skip))
    return found


def read_transifex(text):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return []
    found = []
    for section in parser.sections():
        if section == 'main':
            continue
        settings = parser[section]
        source = (settings.get('source_file') or '').strip()
        if not source:
            continue
        for key, value in settings.items():
            # trans.<lang> names one language's file outright.
            if key.startswith('trans.') and value.strip():
                found.append((_clean(value), _clean(source), False))
        file_filter = (settings.get('file_filter') or '').strip()
        if '<lang>' in file_filter:
            mask = file_filter.replace('<lang>', '{lang}', 1).replace('<lang>', '*')
            found.append((_clean(mask), _clean(source), False))
    return found


_L10N_LANGUAGE = ('{locale}', '{android_locale}')


def _expand(text, env):
    for _ in range(10):
        expanded = re.sub(r'\{(\w+)\}', lambda m: env.get(m.group(1), m.group(0)), text)
        if expanded == text:
            break
        text = expanded
    return text


def read_l10n_toml(text):
    try:
        import tomllib
    except ImportError:  # Python 3.10
        return []
    try:
        config = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    basepath = config.get('basepath', '.')
    base = _clean(basepath if isinstance(basepath, str) else '.')
    base = '' if base == '.' else base
    env = {key: value for key, value in (config.get('env') or {}).items() if isinstance(value, str)}
    env['l10n_base'] = ''
    found = []
    for entry in config.get('paths') or []:
        if not isinstance(entry, dict):
            continue
        reference, l10n = entry.get('reference'), entry.get('l10n')
        if not isinstance(reference, str) or not isinstance(l10n, str):
            continue
        l10n = _expand(l10n, env)
        for placeholder in _L10N_LANGUAGE:
            l10n = l10n.replace(placeholder, '{lang}', 1).replace(placeholder, '*')
        # Mozilla's tools fall back to English for a missing string.
        if '{lang}' in l10n:
            found.append((_join(base, l10n), _join(base, _expand(reference, env)), True))
    return found


# JavaScript and TypeScript configs are read without running them: string
# and string-array values of known keys, wherever they are in the file.

_JS_TOKENS = re.compile(
    r"""//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`""",
    re.DOTALL,
)
_JS_STRING = r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\$])*`)"""


def _without_js_comments(text):
    return _JS_TOKENS.sub(lambda m: '' if m.group(0)[0] == '/' else m.group(0), text)


def _js_string(literal):
    """The text of a JavaScript string literal (no ${...} in it)."""
    body = literal[1:-1]
    return re.sub(r'\\(.)', r'\1', body)


def js_values(text, names):
    """{name: text or [texts]} of the keys C{names} that a JavaScript or
    TypeScript config sets to a string or an array of strings. Nothing
    in it is run; a value built any other way is left out."""
    text = _without_js_comments(text)
    found = {}
    for name in names:
        key = r"""(?:\b%s|"%s"|'%s')\s*:\s*""" % ((re.escape(name),) * 3)
        match = re.search(key + _JS_STRING, text)
        if match:
            found[name] = _js_string(match.group(1))
            continue
        match = re.search(key + r'\[([^\]]*)\]', text)
        if match:
            found[name] = [_js_string(m) for m in re.findall(_JS_STRING, match.group(1))]
    return found


def _config_values(text, names):
    """{name: value} of the keys C{names} in a JSON, YAML or JavaScript
    config, at any depth for JavaScript and at the top or one level
    down for JSON and YAML."""
    import json

    import yaml

    try:
        config = json.loads(text)
    except ValueError:
        try:
            config = yaml.load(text, Loader=yaml.BaseLoader)
        except yaml.YAMLError:
            config = None
    if not isinstance(config, dict):
        return js_values(text, names)
    found = {}
    for level in [config] + [v for v in config.values() if isinstance(v, dict)]:
        for name in names:
            if name in level and name not in found:
                found[name] = level[name]
    return found


def _source_locale(values, *keys, locales='locales'):
    """The source language a config names, else English if it lists it,
    else its first language."""
    for key in keys:
        if isinstance(values.get(key), str) and values[key]:
            return values[key]
    listed = values.get(locales)
    if isinstance(listed, list) and listed and isinstance(listed[0], str):
        return next((code for code in listed if code.split('-')[0] == 'en'), listed[0])
    return 'en'


def _locale_mask(pattern, source, tokens, wildcards=()):
    """(mask, template) of a path pattern, its language tokens (the first
    becomes {lang}, the template's language) and wildcard tokens."""
    mask = template = pattern
    for token in tokens:
        mask = mask.replace(token, '{lang}', 1).replace(token, '*')
        template = template.replace(token, source)
    for token in wildcards:
        mask = mask.replace(token, '*')
        template = template.replace(token, '*')
    if '{lang}' not in mask:
        return None
    return _clean(mask), _clean(template)


def read_inlang(text):
    """An inlang project's settings.json: each plugin's pathPattern."""
    import json

    try:
        config = json.loads(text)
    except ValueError:
        return []
    if not isinstance(config, dict):
        return []
    source = _source_locale(config, 'baseLocale', 'sourceLanguageTag', locales='locales')
    found = []
    for key, plugin in config.items():
        if not key.startswith('plugin.') or not isinstance(plugin, dict):
            continue
        patterns = plugin.get('pathPattern')
        if isinstance(patterns, dict):
            patterns = list(patterns.values())
        for pattern in patterns if isinstance(patterns, list) else [patterns]:
            if isinstance(pattern, str):
                pair = _locale_mask(pattern, source, ('{languageTag}', '{locale}'))
                if pair:
                    found.append((*pair, None))
    return found


_LINGUI_JSON = ('minimal', 'json')


def _lingui_paths(text):
    """(settings, catalog paths) of a Lingui config."""
    import json

    try:
        config = json.loads(text)
    except ValueError:
        values = js_values(text, ('sourceLocale', 'locales', 'format', 'style'))
        path = r"""(?:\bpath|"path"|'path')\s*:\s*""" + _JS_STRING
        found = re.findall(path, _without_js_comments(text))
        return values, [_js_string(literal) for literal in found]
    if not isinstance(config, dict):
        return {}, []
    catalogs = config.get('catalogs')
    catalogs = catalogs if isinstance(catalogs, list) else []
    return config, [c.get('path') for c in catalogs if isinstance(c, dict)]


def read_lingui(text):
    """A Lingui config: its catalogs' paths, when they are JSON files
    (format "minimal", or @lingui/format-json's minimal style)."""
    values, paths = _lingui_paths(text)
    style = values.get('style') or values.get('format')
    if style not in _LINGUI_JSON:
        return []
    source = _source_locale(values, 'sourceLocale')
    found = []
    for path in paths:
        if not isinstance(path, str):
            continue
        path = path.replace('<rootDir>/', '').replace('<rootDir>', '')
        pair = _locale_mask(path + '.json', source, ('{locale}',), ('{name}',))
        if pair:
            found.append((*pair, None))
    return found


_I18NEXT_LANGUAGE = ('$LOCALE', '{{lng}}', '{{language}}')
_I18NEXT_NAMESPACE = ('$NAMESPACE', '{{ns}}', '{{namespace}}')
_I18NEXT_PATHS = ('output', 'savePath', 'loadPath')


def read_i18next(text):
    """An i18next-parser, i18next-scanner or i18next-cli config: where
    its catalogs are written (i18next-parser's default if it doesn't
    say)."""
    names = (*_I18NEXT_PATHS, 'locales', 'lngs', 'defaultLng', 'primaryLanguage')
    values = _config_values(text, names)
    listed = 'locales' if 'locales' in values else 'lngs'
    source = _source_locale(values, 'primaryLanguage', 'defaultLng', locales=listed)
    patterns = [values[key] for key in _I18NEXT_PATHS if isinstance(values.get(key), str)]
    found = []
    for pattern in patterns or ['locales/$LOCALE/$NAMESPACE.json']:
        pair = _locale_mask(pattern, source, _I18NEXT_LANGUAGE, _I18NEXT_NAMESPACE)
        if pair and (*pair, None) not in found:
            found.append((*pair, None))
    return found


_LINGUI_CONFIGS = tuple(
    'lingui.config.' + extension for extension in ('js', 'ts', 'mjs', 'cjs', 'json')
) + ('.linguirc',)
_I18NEXT_CONFIGS = tuple(
    '%s.config.%s' % (tool, extension)
    for tool in ('i18next-parser', 'i18next-scanner', 'i18next')
    for extension in ('js', 'ts', 'mjs', 'cjs', 'json', 'yaml', 'yml')
)


CONFIG_READERS = [
    (('crowdin.yml', 'crowdin.yaml', '.crowdin.yml', '.crowdin.yaml'), read_crowdin),
    ((os.path.join('.tx', 'config'),), read_transifex),
    (('l10n.toml',), read_l10n_toml),
    ((os.path.join('project.inlang', 'settings.json'),), read_inlang),
    (_LINGUI_CONFIGS, read_lingui),
    (_I18NEXT_CONFIGS, read_i18next),
]
"""(config file names, reader): a config file found in a folder above
the target is read for (mask, template) pairs relative to that folder."""


def _config_match(directory, relpath, name, reader):
    path = os.path.join(directory, name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            pairs = reader(f.read())
    except (OSError, UnicodeDecodeError):
        return None
    for mask, template, skip in pairs:
        try:
            match = compile_mask(mask).match(relpath)
        except ValueError:
            continue
        if not match:
            continue
        wanted = _wildcards(template)
        if wanted and wanted != _wildcards(mask)[: len(wanted)]:
            continue
        found = os.path.join(directory, *fill(template, match).split('/'))
        if os.path.isfile(found) and not os.path.samefile(found, os.path.join(directory, relpath)):
            how = name.replace(os.sep, '/')
            return TemplateMatch(found, match.groupdict().get('lang'), how, skip)
    return None


def from_config(target):
    """The template a project's config file gives for C{target}, or None.
    Config files in folders nearer the target win."""
    target = os.path.abspath(target)
    directory = os.path.dirname(target)
    while True:
        relpath = os.path.relpath(target, directory).replace(os.sep, '/')
        try:
            present = set(os.listdir(directory))
        except OSError:
            present = set()
        for names, reader in CONFIG_READERS:
            for name in names:
                if name.split(os.sep)[0] not in present:
                    continue
                match = _config_match(directory, relpath, name, reader)
                if match:
                    return match
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


# Layouts: each takes the target's path and gives candidate templates.


def _java_suffix(target):
    """Messages_af.properties, Messages_af_ZA.properties: Messages.properties."""
    directory, name = os.path.split(target)
    stem, extension = os.path.splitext(name)
    parts = stem.split('_')
    for cut in (2, 1):
        if len(parts) > cut and known_language('_'.join(parts[-cut:])):
            base = '_'.join(parts[:-cut])
            yield os.path.join(directory, base + extension)
            for code in SOURCE_CODES:
                yield os.path.join(directory, '%s_%s%s' % (base, code.replace('-', '_'), extension))


def _suffix_language(target):
    """The language of Messages_af.properties."""
    parts = os.path.splitext(os.path.basename(target))[0].split('_')
    for cut in (2, 1):
        code = len(parts) > cut and known_language('_'.join(parts[-cut:]))
        if code:
            return code
    return None


def _language_name(target):
    """af.ftl, af-ZA.properties: en.ftl beside it."""
    directory, name = os.path.split(target)
    stem, extension = os.path.splitext(name)
    if known_language(stem):
        for code in SOURCE_CODES:
            yield os.path.join(directory, code + extension)


def _language_directory(target):
    """locales/af/main.ftl: locales/en-US/main.ftl."""
    parts = os.path.abspath(target).split(os.sep)
    for index in range(len(parts) - 2, 0, -1):
        if known_language(parts[index]):
            for code in SOURCE_CODES:
                yield os.sep.join(parts[:index] + [code] + parts[index + 1 :])


def _android_values(target):
    """res/values-af/strings.xml: res/values/strings.xml."""
    directory, name = os.path.split(target)
    parent, folder = os.path.split(directory)
    if folder.startswith('values-') and known_language(folder_language_code(folder)):
        yield os.path.join(parent, 'values', name)


def _lproj(target):
    """af.lproj/Localizable.strings: en.lproj/ or Base.lproj/."""
    directory, name = os.path.split(target)
    parent, folder = os.path.split(directory)
    if folder.endswith('.lproj') and known_language(folder_language_code(folder)):
        for code in (*SOURCE_CODES, 'Base'):
            yield os.path.join(parent, code + '.lproj', name)


LAYOUTS = [_java_suffix, _language_name, _language_directory, _android_values, _lproj]


def is_source_folder(folder):
    """Whether an app's resource folder holds its source-language files:
    Android's values, Apple's Base.lproj and en.lproj."""
    if folder in ('values', 'Base.lproj'):
        return True
    if not folder.endswith('.lproj'):
        return False
    return (known_language(folder_language_code(folder)) or '').split('_')[0] == 'en'


def from_layout(target):
    """The template the file layout suggests for C{target}, or None."""
    target = os.path.abspath(target)
    for layout in LAYOUTS:
        for candidate in layout(target):
            if os.path.isfile(candidate) and candidate != target:
                return TemplateMatch(
                    candidate, path_language(target) or _suffix_language(target), 'layout'
                )
    return None


def find_template(target):
    """A L{TemplateMatch} for C{target}, or None."""
    return from_config(target) or from_layout(target)


def known_template(target):
    """The L{TemplateMatch} for C{target} found without asking: from the
    project, its layout, or the user's earlier choice. None if there's
    none."""
    from virtaal.support import baseline

    match = find_template(target)
    if match is None:
        remembered = baseline.remembered_template(target)
        if remembered:
            match = TemplateMatch(remembered, None, 'remembered')
    return match


def _is_english(folder):
    return (known_language(folder) or '').split('_')[0] == 'en'


def _translation_candidates(source):
    """Files that may translate C{source}: beside it in its format, and
    at the same place under the folders beside a source-language one."""
    directory, name = os.path.split(source)
    extension = os.path.splitext(name)[1].lower()
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    for other in names:
        if os.path.splitext(other)[1].lower() == extension:
            yield os.path.join(directory, other)
    parts = source.split(os.sep)
    for index in range(len(parts) - 2, 0, -1):
        if not is_source_folder(parts[index]) and not _is_english(parts[index]):
            continue
        try:
            siblings = sorted(os.listdir(os.sep.join(parts[:index])))
        except OSError:
            continue
        for sibling in siblings:
            yield os.sep.join(parts[:index] + [sibling] + parts[index + 1 :])


def translations_of(source):
    """The files whose template, by config or layout, is C{source}:
    empty unless C{source} is a source-language file."""
    source = os.path.abspath(source)
    found = []
    for candidate in _translation_candidates(source):
        if candidate == source or candidate in found or not os.path.isfile(candidate):
            continue
        match = find_template(candidate)
        if match and os.path.samefile(match.template, source):
            found.append(candidate)
    return found


def guess_template(target):
    """A file to preselect when asking for C{target}'s template: one
    beside it in the same format, in a source language or none."""
    target = os.path.abspath(target)
    directory = os.path.dirname(target)
    extension = os.path.splitext(target)[1].lower()
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return None
    candidates = [
        os.path.join(directory, name)
        for name in names
        if os.path.splitext(name)[1].lower() == extension
        and os.path.join(directory, name) != target
    ]

    def rank(path):
        code = path_language(path, directory)
        if code is None:
            return 1
        return 0 if code.split('_')[0] == 'en' else 2

    candidates.sort(key=rank)
    return candidates[0] if candidates and rank(candidates[0]) < 2 else None
