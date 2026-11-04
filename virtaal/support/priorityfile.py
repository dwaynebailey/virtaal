#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Priority files: a project's messages by translation priority level,
such as po/virtaal.priorities.yaml. See docs/priority_files.rst for the
format."""

import glob
import logging
import os
from collections import namedtuple

FORMAT = 1
SUFFIX = '.priorities.yaml'
HEADER = 'X-Priority-File'

Level = namedtuple('Level', 'id name description order')


class PriorityFile:
    def __init__(self, path, levels, domains):
        self.path = path
        # Most important first.
        self.levels = sorted(levels, key=lambda level: level.order)
        # {domain: {level id: frozenset(key, ...)}}
        self.domains = domains

    def domain_for(self, keys):
        """The domain sharing most of keys (the units' keys), or None if
        none shares any. A tie goes to the domain the file is named
        after, then the first by name."""
        named = os.path.basename(self.path)[: -len(SUFFIX)]

        def shared(domain):
            return len(keys & frozenset().union(*self.domains[domain].values()))

        ranked = sorted(self.domains, key=lambda domain: (-shared(domain), domain != named, domain))
        if ranked and shared(ranked[0]):
            return ranked[0]
        return None

    def level_keys(self, keys):
        """{level id: {key, ...}}: each level's keys in the domain
        domain_for(keys) picks. A key listed at two levels is at the more
        important one."""
        result = {level.id: set() for level in self.levels}
        domain = self.domain_for(keys)
        if domain is None:
            return result
        seen = set()
        for level in self.levels:
            level_keys = self.domains[domain].get(level.id, frozenset()) - seen
            result[level.id] = set(level_keys)
            seen |= level_keys
        return result


def unit_key(unit):
    """A unit's key, as a priority file lists it: msgctxt\\x04msgid, or
    just msgid, the singular for a plural."""
    source = str(unit.source)
    context = unit.getcontext()
    return '%s\x04%s' % (context, source) if context else source


def _level_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('priority id %r is not a string' % (value,))
    return str(value)


def parse(data, path=''):
    """A PriorityFile from a priority file's YAML data; ValueError if it
    isn't a format 1 file."""
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        raise ValueError('not a format %d priority file' % FORMAT)
    priorities = data.get('priorities')
    if not isinstance(priorities, list) or not priorities:
        raise ValueError('no priorities')
    levels = []
    for priority in priorities:
        if not isinstance(priority, dict):
            raise ValueError('priority %r is not a mapping' % (priority,))
        name, order = priority.get('name'), priority.get('order')
        if not isinstance(name, str) or not name:
            raise ValueError('priority %r has no name' % (priority,))
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError('priority %r has no whole-number order' % (priority,))
        description = priority.get('description') or ''
        if not isinstance(description, str):
            raise ValueError('priority %r: description is not a string' % (priority,))
        levels.append(Level(_level_id(priority.get('id')), name, description, order))
    ids = [level.id for level in levels]
    if len(set(ids)) != len(ids):
        raise ValueError('two priorities share an id')
    domains = data.get('domains') or {}
    if not isinstance(domains, dict):
        raise ValueError('domains is not a mapping')
    parsed = {}
    for domain, by_level in domains.items():
        if not isinstance(by_level, dict):
            raise ValueError('domain %s is not a mapping' % domain)
        parsed[str(domain)] = {}
        for level_id, keys in by_level.items():
            level_id = _level_id(level_id)
            if level_id not in ids:
                raise ValueError('domain %s: priority %r is not defined' % (domain, level_id))
            if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
                raise ValueError(
                    'domain %s, priority %s: not a list of strings' % (domain, level_id)
                )
            parsed[str(domain)][level_id] = frozenset(keys)
    return PriorityFile(path, levels, parsed)


def load(path):
    """The PriorityFile at path, or None (logged) if it can't be read or
    isn't a format 1 file."""
    import yaml

    try:
        with open(path, encoding='utf-8') as f:
            return parse(yaml.safe_load(f), path)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as e:
        logging.warning("Not using priority file %s: %s", path, e)
        return None


def candidates(filename, header=None):
    """The files to try as filename's priority file, in order: the one its
    X-Priority-File header names (relative to its directory),
    <stem>.priorities.yaml beside it, then any *.priorities.yaml beside it
    or in its parent directory."""
    directory = os.path.dirname(os.path.abspath(filename))
    paths = []
    if header:
        paths.append(os.path.join(directory, header))
    stem = os.path.basename(filename).split('.')[0]
    paths.append(os.path.join(directory, stem + SUFFIX))
    for folder in (directory, os.path.dirname(directory)):
        paths.extend(sorted(glob.glob(os.path.join(glob.escape(folder), '*' + SUFFIX))))
    paths = [os.path.normpath(path) for path in paths]
    return [path for path in dict.fromkeys(paths) if os.path.isfile(path)]


def find(filename, header=None):
    """The first of candidates() that loads, or None."""
    for path in candidates(filename, header):
        priority_file = load(path)
        if priority_file is not None:
            return priority_file
    return None
