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
from bisect import bisect_right

from gi.repository import GObject

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
    }

    # Read the next or previous file once the cursor is this close to it.
    PREFETCH_UNITS = 10

    # The target language the files were picked for, if any.
    language = None

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
        self.stats = None
        # Stream indices that may differ from the files on disk.
        self._edited_units = set()
        self._offsets = []
        self._build_segments()
        if not self.segments:
            raise ValueError('The files contain nothing to translate.')
        self.load_segment(0)
        self.update_stats()

    def _build_segments(self):
        from virtaal.support import statsdb, statsworker

        statsworker.wait()
        cache = statsdb.StatsCache()
        # Reading a file for its stats is slow and, with the cyclic
        # collector off, never gives the memory back: a child does it.
        uncached = [entry.path for entry in self.fileset if not cache.is_cached(entry.path)]
        if uncached:
            statsworker.fill(uncached)
        offset = 0
        for entry in self.fileset:
            try:
                rows = cache.file_units(entry.path)
            except Exception as exc:
                logging.warning('Leaving out %s: %s', entry.path, exc)
                self.skipped.append(entry)
                continue
            if not rows:
                self.skipped.append(entry)
                continue
            peek = [PeekUnit(r[1], r[2], r[3], r[5]) for r in rows]
            valid = [r[0] for r in rows]
            self.segments.append(FileSegment(entry, offset, peek, _peek_stats(rows), valid))
            offset += len(peek)
        self._offsets = [segment.offset for segment in self.segments]
        self._len = offset

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
        return self.current_segment.model

    def file_offsets(self):
        """(stream index of its first unit, FileEntry) for each file."""
        return [(segment.offset, segment.entry) for segment in self.segments]

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
        return self.segments[self.current].path

    def get_filename(self):
        return self.filename

    @property
    def _trans_store(self):
        return self.current_model._trans_store

    @property
    def nplurals(self):
        return self.current_model.nplurals

    def get_declared_nplurals(self):
        return self.current_model.get_declared_nplurals()

    def get_store_type(self):
        return self.current_model.get_store_type()

    def get_checker(self):
        return self._checker

    def get_source_language(self):
        return self.current_model.get_source_language()

    def get_target_language(self):
        return self.language or self.current_model.get_target_language()

    def set_source_language(self, langcode):
        return self._set_language('set_source_language', langcode)

    def set_target_language(self, langcode):
        return self._set_language('set_target_language', langcode)

    def _set_language(self, method, langcode):
        changed = False
        for segment in self.segments:
            if segment.loaded and getattr(segment.model, method)(langcode):
                segment.modified = True
                changed = True
        return changed

    def get_stats_totals(self):
        return self.current_model.get_stats_totals()

    def get_live_stats_totals(self):
        return self.current_model.get_live_stats_totals()

    # STATS AND CHECKS #
    def update_stats(self):
        stats = {'extended': {}}
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
        checks = {}
        for segment in self.segments:
            if not os.path.isfile(segment.path):
                continue
            store = segment.model._trans_store if segment.loaded else None
            errors = cache.filechecks(segment.path, checker, store)
            valid = segment.model._valid_units if segment.loaded else segment.valid
            for key, indices in fix_indexes(errors, valid).items():
                checks.setdefault(key, []).extend(i + segment.offset for i in indices)
        for index in self._edited_units:
            self._recheck_unit(checks, index)
        self.checks = checks
        return checks

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
