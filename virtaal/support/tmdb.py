#
# Copyright 2009, 2013 Zuza Software Foundation
# Copyright 2013 F Wolff
#
# This file is part of the Translate Toolkit.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.
#
# --- Vendored into Virtaal ---
# Copied verbatim from translate-toolkit (PyPI: translate-toolkit), file
# translate/storage/tmdb.py, at:
#   repo:      https://github.com/translate/translate
#   tag:       3.18.1
#   commit:    315ffec2522f2f2b234f10bed55ce3c2d80fe3da
#   blob sha:  d0ab100c759248093440dd6634f0187974736fa9
# tmserver.py (and its selector/wsgi helpers, plus tmdb.py) were removed
# from translate-toolkit with no replacement between releases 3.18.1 and
# 3.19.0. The localtm TM plugin's whole point is a zero-config local TM
# server it spawns itself, so this is vendored rather than dropped.
# Nothing below this banner has been altered yet in this commit. See the
# follow-up commit for the Python-3-only / standalone adaptation.

"""Module to provide a translation memory database."""

import contextlib
import logging
import math
import operator
import os
import re
import threading
import time
from sqlite3 import dbapi2

from translate.lang import data
from translate.search.lshtein import LevenshteinComparer

from virtaal.support import shareddb

logger = logging.getLogger(__name__)
STRIP_REGEXP = re.compile(r"\W", re.UNICODE)

SCHEMA_VERSION = 1
"""PRAGMA user_version of the schema below; 0 is a file an older Virtaal made."""

STORE_LEGACY = 0
"""Entries added before files were recorded, or without a file."""
STORE_SAVED = 1
STORE_IMPORTED = 2
LEGACY_STORE_ID = 0

# What an older Virtaal creates, and keeps creating if missing: it finds
# its tables, indexes and full-text triggers by these names, so they
# never change, and nothing new on sources or targets may be NOT NULL
# without a default.
SCHEMA_V0 = (
    """CREATE TABLE IF NOT EXISTS sources (
       sid INTEGER PRIMARY KEY AUTOINCREMENT,
       text VARCHAR NOT NULL,
       context VARCHAR DEFAULT NULL,
       lang VARCHAR NOT NULL,
       length INTEGER NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS sources_context_idx ON sources (context)",
    "CREATE INDEX IF NOT EXISTS sources_lang_idx ON sources (lang)",
    "CREATE INDEX IF NOT EXISTS sources_length_idx ON sources (length)",
    "CREATE UNIQUE INDEX IF NOT EXISTS sources_uniq_idx ON sources (text, context, lang)",
    """CREATE TABLE IF NOT EXISTS targets (
       tid INTEGER PRIMARY KEY AUTOINCREMENT,
       sid INTEGER NOT NULL,
       text VARCHAR NOT NULL,
       lang VARCHAR NOT NULL,
       time INTEGER DEFAULT NULL,
       FOREIGN KEY (sid) references sources(sid)
)""",
    "CREATE INDEX IF NOT EXISTS targets_sid_idx ON targets (sid)",
    "CREATE INDEX IF NOT EXISTS targets_lang_idx ON targets (lang)",
    "CREATE INDEX IF NOT EXISTS targets_time_idx ON targets (time)",
    "CREATE UNIQUE INDEX IF NOT EXISTS targets_uniq_idx ON targets (sid, text, lang)",
)

# Which file (store) and unit each target came from. An older Virtaal
# ignores these; the targets it adds get no origin until the next open.
SCHEMA_V1 = (
    """CREATE TABLE stores (
       store_id INTEGER PRIMARY KEY,
       path VARCHAR NOT NULL,
       member VARCHAR NOT NULL DEFAULT '',
       kind INTEGER NOT NULL,
       exclude INTEGER NOT NULL DEFAULT 0,
       added INTEGER,
       updated INTEGER,
       UNIQUE (path, member)
)""",
    """CREATE TABLE origins (
       store_id INTEGER NOT NULL,
       lang VARCHAR NOT NULL,
       unit_key VARCHAR NOT NULL,
       tid INTEGER NOT NULL,
       time INTEGER,
       PRIMARY KEY (store_id, lang, unit_key)
) WITHOUT ROWID""",
    "CREATE INDEX origins_tid_idx ON origins (tid)",
    # The placeable-free form of each string, filled in later.
    "ALTER TABLE sources ADD COLUMN abstract VARCHAR DEFAULT NULL",
    "ALTER TABLE targets ADD COLUMN abstract VARCHAR DEFAULT NULL",
)


class LanguageError(Exception):
    def __init__(self, value) -> None:
        self.value = value

    def __str__(self) -> str:
        return str(self.value)


class TMDB:
    _tm_dbs = {}

    def __init__(self, db_file, max_candidates=3, min_similarity=75, max_length=1000) -> None:
        self.max_candidates = max_candidates
        self.min_similarity = min_similarity
        self.max_length = max_length

        if not isinstance(db_file, str):
            db_file = str(db_file)  # don't know which encoding
        self.db_file = db_file
        # share connections to same database file between different instances
        if db_file not in self._tm_dbs:
            self._tm_dbs[db_file] = {}
        self._tm_db = self._tm_dbs[db_file]

        # A corrupt or unreadable db_file raises here, so the TM server
        # exits and Local TM gives no suggestions.
        self.init_database()
        self.fulltext = False
        self.init_fulltext()

        self.comparer = LevenshteinComparer(self.max_length)

        self.preload_db()

    def _get_connection(self, index):
        current_thread = threading.current_thread()
        if current_thread not in self._tm_db:
            # Other Virtaal processes' TM servers use the same file (#1340).
            connection = shareddb.connect(self.db_file)
            cursor = connection.cursor()
            self._tm_db[current_thread] = (connection, cursor)
        return self._tm_db[current_thread][index]

    connection = property(lambda self: self._get_connection(0))
    cursor = property(lambda self: self._get_connection(1))

    def init_database(self) -> None:
        """Creates database tables and indices, or brings an older file's
        up to date."""
        try:
            self.connection.commit()
            if self._user_version() < SCHEMA_VERSION:
                self._migrate()
            self._attach_unattributed()
        except Exception:
            self.connection.rollback()
            raise

    def _user_version(self):
        (version,) = self.cursor.execute("PRAGMA user_version").fetchone()
        return version

    def _has_table(self, name):
        self.cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        )
        return self.cursor.fetchone() is not None

    def _begin(self):
        # IMMEDIATE: a deferred transaction that reads first can't wait
        # for another process's write lock when it comes to write.
        if not self.connection.in_transaction:
            self.cursor.execute("BEGIN IMMEDIATE")

    def _migrate(self):
        if self._has_table("sources"):
            self._back_up()
        self._begin()
        # Another process may have migrated the file while this one waited.
        if self._user_version() >= SCHEMA_VERSION:
            self.connection.commit()
            return
        for statement in SCHEMA_V0 + SCHEMA_V1:
            self.cursor.execute(statement)
        self._file_under_legacy()
        self.cursor.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        self.connection.commit()

    def _back_up(self):
        """Copy the file as an older Virtaal made it, once, beside it."""
        if self.db_file == ":memory:" or self.db_file.startswith("file:"):
            return
        root, ext = os.path.splitext(self.db_file)
        backup = "%s-v0%s" % (root, ext)
        if os.path.exists(backup):
            return
        partial = "%s.%d.tmp" % (backup, os.getpid())
        try:
            copy = dbapi2.connect(partial)
            try:
                self.connection.backup(copy)
                (version,) = copy.execute("PRAGMA user_version").fetchone()
            finally:
                copy.close()
            # Otherwise another process migrated the file first.
            if version == 0:
                os.replace(partial, backup)
        except (OSError, dbapi2.Error):
            logger.warning("Could not back up %s to %s", self.db_file, backup, exc_info=True)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(partial)

    def _attach_unattributed(self):
        """File targets with no origin, which an older Virtaal added, under
        the legacy store."""
        self.cursor.execute(
            "SELECT 1 FROM targets t WHERE NOT EXISTS "
            "(SELECT 1 FROM origins o WHERE o.tid = t.tid) LIMIT 1"
        )
        if self.cursor.fetchone() is None:
            return
        try:
            self._begin()
            self._file_under_legacy()
            self.connection.commit()
        except dbapi2.OperationalError as e:
            if not shareddb.is_locked(e):
                raise
            # They stay suggestions; the next start files them.
            self.connection.rollback()
            logger.warning("Left unattributed TM entries for later: %s", e)

    def _file_under_legacy(self):
        now = int(time.time())
        self.cursor.execute(
            "INSERT INTO origins (store_id, lang, unit_key, tid, time) "
            "SELECT ?, t.lang, CAST(t.tid AS TEXT), t.tid, t.time FROM targets t "
            "WHERE NOT EXISTS (SELECT 1 FROM origins o WHERE o.tid = t.tid)",
            (LEGACY_STORE_ID,),
        )
        if self.cursor.rowcount:
            self._legacy_store(now)

    def _legacy_store(self, now):
        self.cursor.execute(
            "INSERT OR IGNORE INTO stores (store_id, path, member, kind, added, updated) "
            "VALUES (?, '', '', ?, ?, ?)",
            (LEGACY_STORE_ID, STORE_LEGACY, now, now),
        )
        self.cursor.execute(
            "UPDATE stores SET updated = ? WHERE store_id = ?", (now, LEGACY_STORE_ID)
        )

    def init_fulltext(self) -> None:
        """Detects if fts3 fulltext indexing module exists, initializes fulltext table if it does."""
        # HACKISH: no better way to detect fts3 support except trying to
        # construct a dummy table?! A temporary one, which other processes
        # opening the same file can't drop halfway through (#1340).
        try:
            script = """
CREATE VIRTUAL TABLE temp.test_for_fts3 USING fts3;
DROP TABLE temp.test_for_fts3;
"""
            self.cursor.executescript(script)
            logger.debug("fts3 supported")
            # for some reason CREATE VIRTUAL TABLE doesn't support IF NOT
            # EXISTS syntax check if fulltext index table exists manually
            self.cursor.execute("SELECT name FROM sqlite_master WHERE name = 'fulltext'")
            if not self.cursor.fetchone():
                # create fulltext index table, and index all strings in sources
                script = """
CREATE VIRTUAL TABLE fulltext USING fts3(text);
"""
                logger.debug("fulltext table not exists, creating")
                self.cursor.executescript(script)
                logger.debug("created fulltext table")
            else:
                logger.debug("fulltext table already exists")

            # create triggers that would sync sources table with fulltext index
            script = """
INSERT INTO fulltext (rowid, text) SELECT sid, text FROM sources WHERE sid NOT IN (SELECT rowid FROM fulltext);
CREATE TRIGGER IF NOT EXISTS sources_insert_trig AFTER INSERT ON sources FOR EACH ROW
BEGIN
    INSERT INTO fulltext (docid, text) VALUES (NEW.sid, NEW.text);
END;
CREATE TRIGGER IF NOT EXISTS sources_update_trig AFTER UPDATE OF text ON sources FOR EACH ROW
BEGIN
    UPDATE fulltext SET text = NEW.text WHERE docid = NEW.sid;
END;
CREATE TRIGGER IF NOT EXISTS sources_delete_trig AFTER DELETE ON sources FOR EACH ROW
BEGIN
    DELETE FROM fulltext WHERE docid = OLD.sid;
END;
"""
            self.cursor.executescript(script)
            self.connection.commit()
            logger.debug("created fulltext triggers")
            self.fulltext = True

        except dbapi2.OperationalError as e:
            self.fulltext = False
            logger.exception("failed to initialize fts3 support")
            if shareddb.is_locked(e):
                # FTS3 works: other processes keep their triggers (#1340).
                return
            script = """
DROP TRIGGER IF EXISTS sources_insert_trig;
DROP TRIGGER IF EXISTS sources_update_trig;
DROP TRIGGER IF EXISTS sources_delete_trig;
"""
            self.cursor.executescript(script)

    def preload_db(self):
        """
        Ugly hack to force caching of sqlite db file in memory for improved
        performance.
        """
        if self.fulltext:
            query = """SELECT COUNT(*) FROM sources s JOIN fulltext f ON s.sid = f.docid JOIN targets t on s.sid = t.sid"""
        else:
            query = """SELECT COUNT(*) FROM sources s JOIN targets t on s.sid = t.sid"""
        self.cursor.execute(query)
        (numrows,) = self.cursor.fetchone()
        logger.debug("tmdb has %d records", numrows)
        return numrows

    def add_unit(self, unit, source_lang=None, target_lang=None, commit=True) -> None:
        """Inserts unit in the database."""
        # A unit's own languages win over the passed ones, which are the
        # fallback for a unit that has none.
        #
        # A bare TranslationUnit (as add_unit/tmserver.py builds one from a
        # single {"source", "target"} dict, with no store behind it) has no
        # language of its own: current translate-toolkit's
        # getsourcelanguage()/gettargetlanguage() unconditionally delegate to
        # self._store, which raises AttributeError instead of returning None
        # for such a unit - hence the guard here.
        try:
            if unit.getsourcelanguage():
                source_lang = unit.getsourcelanguage()
        except AttributeError:
            pass
        try:
            if unit.gettargetlanguage():
                target_lang = unit.gettargetlanguage()
        except AttributeError:
            pass

        if not source_lang:
            raise LanguageError("undefined source language")
        if not target_lang:
            raise LanguageError("undefined target language")

        unitdict = {
            "source": unit.source,
            "target": unit.target,
            "context": unit.getcontext(),
        }
        self.add_dict(unitdict, source_lang, target_lang, commit)

    def add_dict(self, unit, source_lang, target_lang, commit=True) -> None:
        """Inserts units represented as dictionaries in database."""
        source_lang = data.normalize_code(source_lang)
        target_lang = data.normalize_code(target_lang)
        try:
            tid = self._add_target(unit, source_lang, target_lang)
            # Added without a file: the legacy store holds it.
            now = int(time.time())
            self._legacy_store(now)
            self.cursor.execute(
                "INSERT OR IGNORE INTO origins (store_id, lang, unit_key, tid, time) "
                "VALUES (?, ?, ?, ?, ?)",
                (LEGACY_STORE_ID, target_lang, str(tid), tid, now),
            )

            if commit:
                self.connection.commit()
        except Exception:
            if commit:
                self.connection.rollback()
            raise

    def _add_target(self, unit, source_lang, target_lang):
        """The tid of C{unit}'s target, inserted with its source if new."""
        try:
            self.cursor.execute(
                "INSERT INTO sources (text, context, lang, length) VALUES(?, ?, ?, ?)",
                (unit["source"], unit["context"], source_lang, len(unit["source"])),
            )
            sid = self.cursor.lastrowid
        except dbapi2.IntegrityError:
            # source string already exists in db, run query to find sid
            self.cursor.execute(
                "SELECT sid FROM sources WHERE text=? AND context=? and lang=?",
                (unit["source"], unit["context"], source_lang),
            )
            (sid,) = self.cursor.fetchone()
        try:
            self.cursor.execute(
                "INSERT INTO targets (sid, text, lang, time) VALUES (?, ?, ?, ?)",
                (sid, unit["target"], target_lang, int(time.time())),
            )
            return self.cursor.lastrowid
        except dbapi2.IntegrityError:
            self.cursor.execute(
                "SELECT tid FROM targets WHERE sid=? AND text=? AND lang=?",
                (sid, unit["target"], target_lang),
            )
            (tid,) = self.cursor.fetchone()
            return tid

    def add_store(self, store, source_lang, target_lang, commit=True):
        """Insert all units in store in database."""
        count = 0
        for unit in store.units:
            if unit.istranslatable() and unit.istranslated():
                self.add_unit(unit, source_lang, target_lang, commit=False)
                count += 1
        if commit:
            self.connection.commit()
        return count

    def add_list(self, units, source_lang, target_lang, commit=True):
        """
        Insert all units in list into the database, units are represented as
        dictionaries.
        """
        count = 0
        for unit in units:
            self.add_dict(unit, source_lang, target_lang, commit=False)
            count += 1
        if commit:
            self.connection.commit()
        return count

    def translate_unit(self, unit_source, source_langs, target_langs):
        """Return TM suggestions for unit_source."""
        if not isinstance(source_langs, list):
            source_langs = [source_langs]
        source_langs = [data.normalize_code(lang) for lang in source_langs]
        if not isinstance(target_langs, list):
            target_langs = [target_langs]
        target_langs = [data.normalize_code(lang) for lang in target_langs]
        # One placeholder per language: "IN (?)" with "en,fr" matches neither.
        langs_in = "s.lang IN (%s) AND t.lang IN (%s)" % (
            ",".join("?" * len(source_langs)),
            ",".join("?" * len(target_langs)),
        )

        minlen = min_levenshtein_length(len(unit_source), self.min_similarity)
        maxlen = max_levenshtein_length(len(unit_source), self.min_similarity, self.max_length)

        # split source into words, remove punctuation and special
        # chars, keep words that are at least 3 chars long
        unit_words = STRIP_REGEXP.sub(" ", unit_source).split()
        unit_words = list(filter(lambda word: len(word) > 2, unit_words))

        if self.fulltext and len(unit_words) > 3:
            logger.debug("fulltext matching")
            query = (
                """SELECT s.text, t.text, s.context, s.lang, t.lang FROM sources s JOIN targets t ON s.sid = t.sid JOIN fulltext f ON s.sid = f.docid
                       WHERE %s AND s.length BETWEEN ? AND ?
                       AND fulltext MATCH ?"""
                % langs_in
            )
            # Quoted, so a word such as AND, NOT or NEAR isn't read as an
            # FTS operator (#4332). STRIP_REGEXP has removed any quote.
            search_str = " OR ".join('"%s"' % word for word in unit_words)
            self.cursor.execute(query, (*source_langs, *target_langs, minlen, maxlen, search_str))
        else:
            logger.debug("nonfulltext matching")
            query = (
                """SELECT s.text, t.text, s.context, s.lang, t.lang FROM sources s JOIN targets t ON s.sid = t.sid
            WHERE %s
            AND s.length >= ? AND s.length <= ?"""
                % langs_in
            )
            self.cursor.execute(query, (*source_langs, *target_langs, minlen, maxlen))

        results = []
        for row in self.cursor:
            quality = self.comparer.similarity(unit_source, row[0], self.min_similarity)
            if quality >= self.min_similarity:
                results.append(
                    {
                        "source": row[0],
                        "target": row[1],
                        "context": row[2],
                        "quality": quality,
                    }
                )
        results.sort(key=operator.itemgetter("quality"), reverse=True)
        results = results[: self.max_candidates]
        logger.debug("results: %s", results)
        return results


def min_levenshtein_length(length, min_similarity):
    return math.ceil(max(length * (min_similarity / 100.0), 2))


def max_levenshtein_length(length, min_similarity, max_length):
    return math.floor(min(length / (min_similarity / 100.0), max_length))
