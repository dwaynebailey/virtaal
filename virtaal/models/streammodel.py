#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Several translation files edited as one list of units.

Each file is a L{FileSegment}: a slice of the stream's indices. A file
is read (a L{StoreModel}) only once the cursor comes near it. Until then
its units are L{PeekUnit}s from the stats cache, enough to draw, count
and search them. Stats and checks are kept for the whole stream, in
stream indices."""

import logging
import os
from bisect import bisect_left, bisect_right

from gi.repository import GObject

from virtaal.support import sourcehistory

from .basemodel import BaseModel
from .storemodel import StoreModel, fix_indexes


class PeekUnit:
    """A unit of a file not read yet, as the stats cache has it."""

    __slots__ = ('_id', '_state', 'source', 'target')

    # Only tested for truth: theme.unit_style() then uses get_state_n().
    STATE = {'peek': True}

    def __init__(self, unitid, source, target, state):
        self._id = unitid
        self.source = source or ''
        self.target = target or ''
        self._state = state

    def getid(self):
        return self._id

    # The stats cache keeps plain text only, so a row of a file not read
    # yet shows that text, without the tags of a tag-only segment.
    @property
    def rich_source(self):
        return []

    @property
    def rich_target(self):
        return []

    def getnotes(self, origin=None):
        return ''

    def getlocations(self):
        return []

    def get_state_n(self):
        return self._state

    def get_state_id(self, n=None):
        return self._state

    def isfuzzy(self):
        from translate.storage.workflow import StateEnum

        return StateEnum.NEEDS_WORK <= self._state < StateEnum.UNREVIEWED

    def istranslated(self):
        from translate.storage.workflow import StateEnum

        return self._state >= StateEnum.UNREVIEWED

    def istranslatable(self):
        return True

    def isheader(self):
        return False

    def hasplural(self):
        return False

    def __repr__(self):
        return 'PeekUnit(%r)' % self._id


class FileSegment:
    """One file's place in the stream."""

    def __init__(self, entry, offset, peek, stats, valid):
        self.entry = entry
        # Each unit's index in the file's own list, header included.
        self.valid = valid
        self.offset = offset
        self.count = len(peek)
        self.peek = peek
        # Local (file) indices, as StoreModel.stats has them.
        self.stats = stats
        self.model = None
        self.modified = False
        self._ids = None

    @property
    def path(self):
        return self.entry.path

    @property
    def loaded(self):
        return self.model is not None

    def unit(self, local):
        if self.model is not None:
            return self.model.get_unit(local)
        return self.peek[local]

    def units(self):
        if self.model is not None:
            return self.model.get_units()
        return self.peek

    def local_index(self, unit):
        """The unit's index in this file, if it is one of its read units."""
        if self.model is None:
            return None
        if self._ids is None:
            self._ids = {id(u): i for i, u in enumerate(self.model.get_units())}
        local = self._ids.get(id(unit))
        if local is not None and self.model.get_unit(local) is unit:
            return local
        return None


def _peek_stats(rows):
    """StoreModel.stats for a file from its stats cache rows."""
    from virtaal.support import statsdb

    stats = statsdb.emptyfilestats()
    stats['extended'] = {}
    for local, (_index, _id, _source, _target, state, e_state) in enumerate(rows):
        stats[statsdb.state_strings[state]].append(local)
        stats['extended'].setdefault(e_state, []).append(local)
        stats['total'].append(local)
    return stats


class StreamModel(BaseModel):
    """The units of every file in a L{FileSet}, one after the other."""

    __gtype_name__ = 'StreamModel'
    __gsignals__ = {
        # A file was read: (segment number, its PeekUnits now replaced).
        'file-loaded': (GObject.SignalFlags.RUN_FIRST, None, (int, object)),
        # The files whose checks were left out are checked.
        'checks-filled': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    # Read the next or previous file once the cursor is this close to it.
    PREFETCH_UNITS = 10
    # How long the git histories of monolingual files may take at open.
    RECALL_SECONDS = 10

    # The target language the files were picked for, if any.
    language = None
    # The language given to TMX files that name none.
    _adopted_language = None

    def __init__(self, fileset, controller):
        super().__init__()
        self.controller = controller
        self.fileset = fileset
        self.segments = []
        self.skipped = []
        self.current = 0
        self.generation = 0
        self._checker = None
        self.checks = None
        # Files whose checks are left out of self.checks while a child
        # process checks them.
        self.checks_pending = 0
        # A child couldn't check them: check here, as for a read file.
        self._check_here = False
        self.stats = None
        # Stream indices that may differ from the files on disk.
        self._edited_units = set()
        self._offsets = []
        self._build_segments()
        if not self.segments and not fileset.unpaired:
            raise ValueError('The files contain nothing to translate.')
        if self.segments:
            self.load_segment(0)
        self.update_stats()

    def _build_segments(self):
        from virtaal.support import statsdb, statsworker

        statsworker.wait()
        cache = statsdb.StatsCache()
        # Reading a file for its stats is slow and, with the cyclic
        # collector off, never gives the memory back: a child does it.
        uncached = [
            entry.path
            for entry in self.fileset
            if entry.template is None and not cache.is_cached(entry.path)
        ]
        if uncached:
            statsworker.fill(uncached)
        recall = sourcehistory.budgeted(self.RECALL_SECONDS)
        offset = 0
        for entry in self.fileset:
            try:
                if entry.template is not None:
                    segment = self._paired_segment(entry, offset, recall)
                else:
                    segment = self._cached_segment(entry, offset, cache)
            except Exception as exc:
                logging.warning('Leaving out %s: %s', entry.path, exc)
                segment = None
            if segment is None:
                self.skipped.append(entry)
                continue
            self.segments.append(segment)
            offset += segment.count
        self._offsets = [segment.offset for segment in self.segments]
        self._len = offset

    @staticmethod
    def _cached_segment(entry, offset, cache):
        rows = cache.file_units(entry.path)
        if not rows:
            return None
        peek = [PeekUnit(r[1], r[2], r[3], r[5]) for r in rows]
        valid = [r[0] for r in rows]
        return FileSegment(entry, offset, peek, _peek_stats(rows), valid)

    def _paired_segment(self, entry, offset, recall):
        """A monolingual file, read now: the stats cache can't read it
        paired with its template."""
        from .monolingual import pairing

        model = StoreModel(
            entry.path, self.controller, fill_stats_cache=False, **pairing(entry.template, recall)
        )
        if not len(model):
            return None
        segment = FileSegment(entry, offset, model.get_units(), model.stats, model._valid_units)
        segment.model = model
        segment.peek = None
        return segment

    def insert_file(self, entry):
        """Add the monolingual file C{entry}, a FileEntry of L{fileset}
        just paired with its template, in its place, read now, without
        reading the others again: the files after it, their stats,
        checks and edits move along.
        @returns: Its segment, or C{None} if it has no units."""
        entries = self.fileset.entries
        position = entries.index(entry)
        number = sum(1 for s in self.segments if entries.index(s.entry) < position)
        offset = self.segments[number].offset if number < len(self.segments) else self._len
        segment = self._paired_segment(entry, offset, sourcehistory.budgeted(self.RECALL_SECONDS))
        if segment is None:
            return None
        count = segment.count
        for later in self.segments[number:]:
            later.offset += count
        if self.segments and self.current >= number:
            self.current += 1
        self.segments.insert(number, segment)
        self._offsets = [s.offset for s in self.segments]
        self._len += count

        def moved(index):
            return index + count if index >= offset else index

        self._edited_units = {moved(index) for index in self._edited_units}
        if self.checks is not None:
            checks = {key: [moved(i) for i in indices] for key, indices in self.checks.items()}
            if self._checker is not None:
                for key, indices in segment.model.update_checks(self._checker).items():
                    checks.setdefault(key, []).extend(i + offset for i in indices)
            self.checks = {key: sorted(indices) for key, indices in checks.items()}
        self.generation += 1
        self.update_stats()
        return segment

    # SPECIAL METHODS #
    def __getitem__(self, index):
        return self.get_unit(index)

    def __len__(self):
        return self._len

    def __iter__(self):
        for segment in self.segments:
            yield from segment.units()

    # SEGMENTS #
    def segment_of(self, index):
        """The segment number holding stream index C{index}."""
        if index < 0:
            index += self._len
        if not 0 <= index < self._len:
            raise IndexError(index)
        return bisect_right(self._offsets, index) - 1

    def locate(self, index):
        """(segment, index in its file) for stream index C{index}."""
        if index < 0:
            index += self._len
        segment = self.segments[self.segment_of(index)]
        return segment, index - segment.offset

    def load_segment(self, number):
        segment = self.segments[number]
        if segment.loaded:
            return segment
        # Its stats were just read from the cache.
        model = StoreModel(segment.path, self.controller, fill_stats_cache=False)
        if len(model) != segment.count:
            # Changed on disk since its stats were read.
            logging.warning('%s now has %d units, not %d', segment.path, len(model), segment.count)
        segment.model = model
        if model.get_checker() is None and self._checker is not None:
            model._checker = self._checker
        if self._adopted_language:
            model.adopt_target_language(self._adopted_language)
        peek, segment.peek = segment.peek, None
        self.generation += 1
        self.emit('file-loaded', number, peek)
        return segment

    def approach(self, index):
        """The cursor is at C{index}: read its file, and a neighbour it is
        close to. Returns whether the current file changed."""
        number = self.segment_of(index)
        try:
            self.load_segment(number)
        except Exception:
            logging.exception('Could not read %s', self.segments[number].path)
            return False
        segment = self.segments[number]
        local = index - segment.offset
        if local >= segment.count - self.PREFETCH_UNITS and number + 1 < len(self.segments):
            self._load_later(number + 1)
        if local < self.PREFETCH_UNITS and number > 0:
            self._load_later(number - 1)
        changed = number != self.current
        self.current = number
        return changed

    def _load_later(self, number):
        if self.segments[number].loaded:
            return
        from gi.repository import GLib

        def load():
            if number < len(self.segments):
                self.load_segment(number)
            return False

        GLib.idle_add(load, priority=GLib.PRIORITY_LOW)

    @property
    def current_segment(self):
        return self.load_segment(self.current)

    @property
    def current_model(self):
        """The current file's model; C{None} while no file has units (a
        folder whose files all wait to be paired)."""
        if not self.segments:
            return None
        return self.current_segment.model

    def file_offsets(self):
        """(stream index of its first unit, FileEntry) for each file."""
        return [(segment.offset, segment.entry) for segment in self.segments]

    def segment_counts(self):
        """(translated, fuzzy, total) units of each file, from the
        stream's sorted stats lists."""
        stats = self.stats or {}
        counts = []
        for segment in self.segments:
            start, end = segment.offset, segment.offset + segment.count
            counts.append(
                tuple(
                    bisect_left(stats.get(key, ()), end) - bisect_left(stats.get(key, ()), start)
                    for key in ('translated', 'fuzzy', 'total')
                )
            )
        return counts

    def file_start(self, index):
        """The file whose first unit is at stream index C{index}, or None."""
        number = bisect_right(self._offsets, index) - 1
        if number >= 0 and self._offsets[number] == index:
            return self.segments[number].entry
        return None

    def loaded_models(self):
        return [segment.model for segment in self.segments if segment.loaded]

    # UNITS #
    def get_unit(self, index):
        segment, local = self.locate(index)
        if not segment.loaded:
            self.load_segment(self.segments.index(segment))
        return segment.model.get_unit(local)

    def peek_unit(self, index):
        """The unit at C{index} without reading its file."""
        segment, local = self.locate(index)
        return segment.unit(local)

    def get_units(self):
        """Every unit: unread files give L{PeekUnit}s."""
        return list(self)

    def index_of(self, unit):
        for segment in self.segments:
            local = segment.local_index(unit)
            if local is not None:
                return segment.offset + local
        return None

    def mark_modified(self, index):
        self.locate(index)[0].modified = True

    def modified_entries(self):
        return [segment.entry for segment in self.segments if segment.modified]

    def file_parts(self):
        """(file name, units) of each file read."""
        return [
            (segment.path, segment.model.get_units()) for segment in self.segments if segment.loaded
        ]

    # FILE-LEVEL ACCESSORS (the current file) #
    @property
    def filename(self):
        if not self.segments:
            return self.fileset.root
        return self.segments[self.current].path

    def get_filename(self):
        return self.filename

    def _current(self, method, default=None):
        """C{method} of the current file's model, or C{default} with none."""
        model = self.current_model
        return default if model is None else getattr(model, method)()

    @property
    def _trans_store(self):
        model = self.current_model
        return None if model is None else model._trans_store

    @property
    def nplurals(self):
        model = self.current_model
        return None if model is None else model.nplurals

    def get_declared_nplurals(self):
        return self._current('get_declared_nplurals')

    def get_store_type(self):
        return self._current('get_store_type', '')

    def get_checker(self):
        return self._checker

    def get_source_language(self):
        return self._current('get_source_language')

    def get_target_language(self):
        return self.language or self._current('get_target_language')

    def set_source_language(self, langcode):
        return self._set_language('set_source_language', langcode)

    def set_target_language(self, langcode):
        return self._set_language('set_target_language', langcode)

    def adopt_target_language(self, langcode):
        """Give each TMX read, and those read later, C{langcode} as the
        language it is translated into, if it names none."""
        self._adopted_language = langcode
        for model in self.loaded_models():
            model.adopt_target_language(langcode)

    def _set_language(self, method, langcode):
        # Filled in as a file opens, the language is written with the
        # file's next edit, not by saving another file.
        edit = not getattr(self.controller, 'loading_store', False)
        changed = False
        for segment in self.segments:
            if segment.loaded and getattr(segment.model, method)(langcode):
                segment.modified = segment.modified or edit
                changed = True
        return changed

    def get_stats_totals(self):
        return self._current('get_stats_totals', {})

    def get_live_stats_totals(self):
        return self._current('get_live_stats_totals', {})

    # STATS AND CHECKS #
    def update_stats(self):
        # Every list, even with no files to read.
        stats = {'total': [], 'translated': [], 'fuzzy': [], 'untranslated': [], 'extended': {}}
        for segment in self.segments:
            local_stats = segment.model.update_stats() if segment.loaded else segment.stats
            segment.stats = local_stats
            for key, indices in local_stats.items():
                if key == 'extended':
                    for state, state_indices in indices.items():
                        stats['extended'].setdefault(state, []).extend(
                            i + segment.offset for i in state_indices
                        )
                    continue
                stats.setdefault(key, []).extend(i + segment.offset for i in indices)
        self.stats = stats
        return stats

    update_unit_stats = StoreModel.update_unit_stats
    update_unit_checks = StoreModel.update_unit_checks
    _recheck_unit = StoreModel._recheck_unit

    def update_checks(self, checker=None, filename=None):
        from virtaal.support import statsdb, statsworker

        if checker is None:
            checker = self._checker
        else:
            self._checker = checker
            for model in self.loaded_models():
                model._checker = checker
        statsworker.wait()
        cache = statsdb.StatsCache()
        pending = self._check_unread_in_background(cache, checker)
        checks = {}
        for segment in self.segments:
            if not os.path.isfile(segment.path) or segment.path in pending:
                continue
            if segment.entry.template is not None:
                # Checked in memory, edits included.
                local = segment.model.update_checks(checker)
            else:
                store = segment.model._trans_store if segment.loaded else None
                errors = cache.filechecks(segment.path, checker, store)
                valid = segment.model._valid_units if segment.loaded else segment.valid
                local = fix_indexes(errors, valid)
            for key, indices in local.items():
                checks.setdefault(key, []).extend(i + segment.offset for i in indices)
        for index in self._edited_units:
            self._recheck_unit(checks, index)
        self.checks = checks
        return checks

    def _check_unread_in_background(self, cache, checker):
        """Start checking, in a child process, the files not read yet
        whose checks aren't cached. Returns their paths."""
        unread = [
            segment.path
            for segment in self.segments
            if not segment.loaded
            and os.path.isfile(segment.path)
            and not cache.has_checks(segment.path, checker)
        ]
        if self._check_here or not unread:
            self.checks_pending = 0
            return set()
        from virtaal.support import statsworker

        if not statsworker.check_in_background(unread, checker, self._on_checked):
            self.checks_pending = 0
            return set()
        self.checks_pending = len(unread)
        return set(unread)

    def _on_checked(self, succeeded):
        if not succeeded:
            self._check_here = True
        self.checks_pending = 0
        self.emit('checks-filled')

    # SAVING #
    def save_file(self, filename=None):
        """Write every modified file."""
        if filename is not None:
            raise ValueError('Several files are open: they are saved in place.')
        for segment in self.segments:
            if not segment.modified:
                continue
            segment.model.save_file()
            segment.modified = False
            end = segment.offset + segment.count
            self._edited_units = {i for i in self._edited_units if not segment.offset <= i < end}
        self.update_stats()

    def update_file(self, filename):
        raise ValueError('Several files are open.')
