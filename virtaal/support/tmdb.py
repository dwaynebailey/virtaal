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
import hashlib
import json
import logging
import math
import operator
import os
import re
import threading
import time
from sqlite3 import dbapi2

from translate.lang import data
from translate.misc.xml_helpers import getXMLspace
from translate.search.lshtein import LevenshteinComparer
from translate.storage import tmx

from virtaal.support import inlinecodes, shareddb
from virtaal.support.abstraction import abstract, adapt
from virtaal.support.tmx_compat import tuv_lang

logger = logging.getLogger(__name__)
STRIP_REGEXP = re.compile(r"\W", re.UNICODE)

SCHEMA_VERSION = 7
"""PRAGMA user_version of the schema below; 0 is a file an older Virtaal made."""

SCHEMA_ABSTRACTION = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1}
"""The abstraction algorithm whose abstracts each schema version stores.
A new algorithm needs a new schema version, whose migration clears the
abstracts for the backfill to recompute, so that one file never mixes
two algorithms."""

CLEAR_ABSTRACTS = (
    "UPDATE sources SET abstract = NULL",
    "UPDATE targets SET abstract = NULL",
)

BACKFILL_BATCH = 500

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

# A target's source and target with their XLIFF or TMX inline codes, as
# XLIFF inline markup; NULL for plain text.
SCHEMA_V2 = (
    "ALTER TABLE targets ADD COLUMN source_markup VARCHAR DEFAULT NULL",
    "ALTER TABLE targets ADD COLUMN target_markup VARCHAR DEFAULT NULL",
)


def _fill_plain_lengths(cursor):
    rows = cursor.execute(
        "SELECT tid, source_markup FROM targets WHERE source_markup IS NOT NULL"
    ).fetchall()
    cursor.executemany(
        "UPDATE targets SET plain_length = ? WHERE tid = ?",
        [(_plain_length(markup), tid) for tid, markup in rows],
    )


# The length of a target's source without the native code of its inline
# codes (inlinecodes.plain_text), for a target with markup; NULL otherwise.
SCHEMA_V3 = (
    "ALTER TABLE targets ADD COLUMN plain_length INTEGER DEFAULT NULL",
    _fill_plain_lengths,
)

# A plural target's source plural (msgid_plural) and every one of its
# forms, as a JSON list whose first is the target's text; NULL otherwise.
SCHEMA_V4 = (
    "ALTER TABLE targets ADD COLUMN plural_source VARCHAR DEFAULT NULL",
    "ALTER TABLE targets ADD COLUMN plural_targets VARCHAR DEFAULT NULL",
)


def _bare_monolingual_keys(cursor):
    """Key a monolingual file's origins by their bare keys, not by the
    "NoName\x04<key>" ids its working XLIFF store gave them."""
    from virtaal.support.converters import MONOLINGUAL_CONVERTERS

    extensions = {ext for converter in MONOLINGUAL_CONVERTERS for ext in converter.extensions}
    prefix = "NoName\x04"
    stores = [
        store_id
        for store_id, path in cursor.execute("SELECT store_id, path FROM stores").fetchall()
        if os.path.splitext(path)[1][1:].lower() in extensions
    ]
    for store_id in stores:
        cursor.execute(
            "UPDATE OR IGNORE origins SET unit_key = substr(unit_key, ?) "
            "WHERE store_id = ? AND substr(unit_key, 1, ?) = ?",
            (len(prefix) + 1, store_id, len(prefix), prefix),
        )
        # Those whose bare key is already there.
        cursor.execute(
            "DELETE FROM origins WHERE store_id = ? AND substr(unit_key, 1, ?) = ?",
            (store_id, len(prefix), prefix),
        )


SCHEMA_V5 = (_bare_monolingual_keys,)

# Online services' suggestions fetched for a whole file (#1679): kept
# apart from the user's own translations, so never matched, listed or
# exported as theirs. Each is the JSON list of matches the service gave
# for one source, an empty list for none, and when it was fetched.
SCHEMA_V6 = (
    """CREATE TABLE cached (
       service VARCHAR NOT NULL,
       source_lang VARCHAR NOT NULL,
       target_lang VARCHAR NOT NULL,
       source VARCHAR NOT NULL,
       matches VARCHAR NOT NULL,
       fetched INTEGER NOT NULL,
       PRIMARY KEY (source_lang, target_lang, source, service)
) WITHOUT ROWID""",
    "CREATE INDEX cached_fetched_idx ON cached (fetched)",
)

# How a plural target's forms are laid out (pluralmap): its file's
# gettext Plural-Forms, or CLDR's categories for a monolingual file's.
# NULL for those stored before, read as the language's usual layout.
SCHEMA_V7 = ("ALTER TABLE targets ADD COLUMN plural_layout VARCHAR DEFAULT NULL",)

MIGRATIONS = {
    1: SCHEMA_V0 + SCHEMA_V1,
    2: SCHEMA_V2,
    3: SCHEMA_V3,
    4: SCHEMA_V4,
    5: SCHEMA_V5,
    6: SCHEMA_V6,
    7: SCHEMA_V7,
}
"""The statements, or functions of a cursor, bringing a file up to each
schema version from the one before. A later version that changes only
the abstraction algorithm migrates with CLEAR_ABSTRACTS."""

# Only a process whose schema version is the file's writes an abstract:
# an older one leaves NULL for the newer one to fill. Read in the same
# statement as the write, so a migration can't come in between.
_ABSTRACT_IF_CURRENT = "CASE WHEN (SELECT user_version FROM pragma_user_version) = ? THEN ? END"


def _has_layouts():
    """Whether this schema has targets.plural_layout (from version 7)."""
    return SCHEMA_VERSION >= 7


def _layout_sql(sql):
    return sql if _has_layouts() else ""


def _layout_values(layout):
    return (layout,) if _has_layouts() else ()


def _layout_select():
    return "t.plural_layout" if _has_layouts() else "NULL"


class LanguageError(Exception):
    def __init__(self, value) -> None:
        self.value = value

    def __str__(self) -> str:
        return str(self.value)


class TMFile:
    """A TM database file, brought up to the current schema version on
    opening, with a connection per thread."""

    _tm_dbs = {}

    def __init__(self, db_file) -> None:
        if not isinstance(db_file, str):
            db_file = str(db_file)  # don't know which encoding
        self.db_file = db_file
        # share connections to same database file between different instances
        if db_file not in self._tm_dbs:
            self._tm_dbs[db_file] = {}
        self._tm_db = self._tm_dbs[db_file]
        self.init_database()

    def _connect(self):
        # Other Virtaal processes' TM servers use the same file (#1340).
        return shareddb.connect(self.db_file)

    def _get_connection(self, index):
        current_thread = threading.current_thread()
        if current_thread not in self._tm_db:
            connection = self._connect()
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
        if self._user_version() == 0 and self._has_table("sources"):
            self._back_up()
        self._begin()
        # Another process may have migrated the file while this one waited.
        version = self._user_version()
        if version >= SCHEMA_VERSION:
            self.connection.commit()
            return
        for step in range(version + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[step]:
                if callable(statement):
                    statement(self.cursor)
                else:
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


class SuggestionCache(TMFile):
    """The online services' suggestions kept in the TM file for offline
    use (#1679), for the Virtaal process itself: it waits for another
    process's lock only briefly, so as not to hold up the window, and
    raises dbapi2.Error if the file can't be used."""

    _tm_dbs = {}
    LOCK_WAIT = 2
    """Seconds to wait for another process's lock."""
    CHUNK = 500
    """Sources per statement: SQLite takes 999 parameters at most."""

    def _connect(self):
        return shareddb.connect(self.db_file, timeout=self.LOCK_WAIT)

    def _attach_unattributed(self):
        # The TM server files them; this would wait on its lock.
        pass

    def close(self):
        for connection, _cursor in self._tm_dbs.pop(self.db_file, {}).values():
            connection.close()

    def put(self, service, source_lang, target_lang, found, now=None):
        """Keep C{found}, {source: [match, ...]}, as C{service}'s answers,
        fetched C{now} (seconds since the epoch)."""
        now = int(time.time() if now is None else now)
        langs = (data.normalize_code(source_lang), data.normalize_code(target_lang))
        rows = [
            (service, *langs, source, json.dumps(matches), now) for source, matches in found.items()
        ]
        self._write(
            "INSERT OR REPLACE INTO cached "
            "(service, source_lang, target_lang, source, matches, fetched) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )

    def get(self, source_lang, target_lang, sources):
        """{service: {source: [match, ...]}} for those of C{sources} kept."""
        langs = (data.normalize_code(source_lang), data.normalize_code(target_lang))
        found = {}
        for chunk in _chunks(list(set(sources)), self.CHUNK):
            rows = self.cursor.execute(
                "SELECT service, source, matches FROM cached "
                "WHERE source_lang = ? AND target_lang = ? AND source IN (%s)"
                % ",".join("?" * len(chunk)),
                (*langs, *chunk),
            ).fetchall()
            for service, source, matches in rows:
                try:
                    matches = json.loads(matches)
                except ValueError:
                    continue
                if isinstance(matches, list):
                    found.setdefault(service, {})[source] = matches
        self.connection.commit()
        return found

    def forget(self, source_lang, target_lang, sources):
        """Drop every service's answers for C{sources}, translated now."""
        langs = (data.normalize_code(source_lang), data.normalize_code(target_lang))
        self._write(
            "DELETE FROM cached WHERE source_lang = ? AND target_lang = ? AND source = ?",
            [(*langs, source) for source in set(sources)],
        )

    def expire(self, days, now=None):
        """Drop the answers fetched more than C{days} days before C{now}."""
        now = time.time() if now is None else now
        self._write("DELETE FROM cached WHERE fetched < ?", [(int(now - days * 86400),)])

    def counts(self):
        """{service: how many of its answers are kept}, for every language
        pair."""
        rows = self.cursor.execute("SELECT service, COUNT(*) FROM cached GROUP BY service")
        counts = dict(rows.fetchall())
        self.connection.commit()
        return counts

    def clear(self, services):
        """Drop every answer the services gave, for every language pair."""
        self._write("DELETE FROM cached WHERE service = ?", [(service,) for service in services])

    def _write(self, statement, rows):
        if not rows:
            return
        try:
            self._begin()
            self.cursor.executemany(statement, rows)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise


def _chunks(items, size):
    return [items[i : i + size] for i in range(0, len(items), size)]


class TMDB(TMFile):
    def __init__(
        self, db_file, max_candidates=3, min_similarity=75, max_length=1000, abstract=True
    ) -> None:
        self.max_candidates = max_candidates
        self.min_similarity = min_similarity
        self.max_length = max_length
        self.abstract = abstract
        """Whether matching looks past placeables and accelerators: if
        not, candidates and scores are on the raw text alone."""

        # A corrupt or unreadable db_file raises here, so the TM server
        # exits and Local TM gives no suggestions.
        super().__init__(db_file)
        self.fulltext = False
        self.init_fulltext()

        self.comparer = LevenshteinComparer(self.max_length)

        self.preload_db()

    def backfill_abstracts(self, batch_size=BACKFILL_BATCH):
        """Fill in the abstracts that are missing, such as those of entries
        an older Virtaal added, a batch per transaction. Stops early when
        another process holds the file, or when it has another schema
        version. Returns how many it filled."""
        filled = 0
        for table, key in (("sources", "sid"), ("targets", "tid")):
            last = 0
            while True:
                if self._user_version() != SCHEMA_VERSION:
                    return filled
                rows = self.cursor.execute(
                    "SELECT %s, text FROM %s WHERE %s > ? AND abstract IS NULL "
                    "ORDER BY %s LIMIT ?" % (key, table, key, key),
                    (last, batch_size),
                ).fetchall()
                self.connection.commit()
                if not rows:
                    break
                last = rows[-1][0]
                values = [(_abstract(text), row_id, SCHEMA_VERSION) for row_id, text in rows]
                try:
                    self._begin()
                    self.cursor.executemany(
                        "UPDATE %s SET abstract = ? WHERE %s = ? AND abstract IS NULL "
                        "AND (SELECT user_version FROM pragma_user_version) = ?" % (table, key),
                        values,
                    )
                    self.connection.commit()
                except dbapi2.OperationalError as e:
                    self.connection.rollback()
                    if not shareddb.is_locked(e):
                        raise
                    # The next open carries on.
                    logger.warning("Left TM abstracts for later: %s", e)
                    return filled
                filled += len(rows)
        return filled

    def start_backfill(self):
        """Run backfill_abstracts() in a thread of its own, with its own
        connection, so the server answers meanwhile."""
        if self.db_file == ":memory:" or self.db_file.startswith("file:"):
            # Another thread's connection is another database.
            return None

        def run():
            try:
                filled = self.backfill_abstracts()
                if filled:
                    logger.debug("Filled %d TM abstracts", filled)
            except Exception:
                logger.exception("Could not fill TM abstracts")
            finally:
                connection, _cursor = self._tm_db.pop(threading.current_thread(), (None, None))
                if connection is not None:
                    connection.close()

        thread = threading.Thread(target=run, name="tm-abstracts", daemon=True)
        thread.start()
        return thread

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
        source_lang, target_lang = unit_languages(unit, source_lang, target_lang)
        source_markup, target_markup = inlinecodes.unit_markup(unit)
        unitdict = {
            "source": unit.source,
            "target": unit.target,
            "context": unit.getcontext(),
            "source_markup": source_markup,
            "target_markup": target_markup,
            **plural_fields(unit.source, unit.target, unit),
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
                "INSERT INTO sources (text, context, lang, length, abstract) "
                "VALUES(?, ?, ?, ?, %s)" % _ABSTRACT_IF_CURRENT,
                (
                    unit["source"],
                    unit["context"],
                    source_lang,
                    len(unit["source"]),
                    SCHEMA_VERSION,
                    _abstract(unit["source"]),
                ),
            )
            sid = self.cursor.lastrowid
        except dbapi2.IntegrityError:
            # source string already exists in db, run query to find sid
            self.cursor.execute(
                "SELECT sid FROM sources WHERE text=? AND context=? and lang=?",
                (unit["source"], unit["context"], source_lang),
            )
            (sid,) = self.cursor.fetchone()
        source_markup, target_markup = _markup(unit)
        plain_length = _plain_length(source_markup)
        plural_source, plural_targets = _plurals(unit)
        plural_layout = _plural_layout(unit) if plural_targets is not None else None
        try:
            self.cursor.execute(
                "INSERT INTO targets "
                "(sid, text, lang, time, abstract, source_markup, target_markup, plain_length, "
                "plural_source, plural_targets%s) "
                "VALUES (?, ?, ?, ?, %s, ?, ?, ?, ?, ?%s)"
                % (_layout_sql(", plural_layout"), _ABSTRACT_IF_CURRENT, _layout_sql(", ?")),
                (
                    sid,
                    unit["target"],
                    target_lang,
                    int(time.time()),
                    SCHEMA_VERSION,
                    _abstract(unit["target"]),
                    source_markup,
                    target_markup,
                    plain_length,
                    plural_source,
                    plural_targets,
                    *_layout_values(plural_layout),
                ),
            )
            return self.cursor.lastrowid
        except dbapi2.IntegrityError:
            self.cursor.execute(
                "SELECT tid FROM targets WHERE sid=? AND text=? AND lang=?",
                (sid, unit["target"], target_lang),
            )
            (tid,) = self.cursor.fetchone()
            if plural_targets is not None:
                # The latest forms; a singular unit keeps those it has.
                self.cursor.execute(
                    "UPDATE targets SET plural_source = ?, plural_targets = ?%s "
                    "WHERE tid = ? AND (plural_source IS NOT ? OR plural_targets IS NOT ?%s)"
                    % (
                        _layout_sql(", plural_layout = ?"),
                        _layout_sql(" OR plural_layout IS NOT ?"),
                    ),
                    (
                        plural_source,
                        plural_targets,
                        *_layout_values(plural_layout),
                        tid,
                        plural_source,
                        plural_targets,
                        *_layout_values(plural_layout),
                    ),
                )
            if source_markup is not None:
                # The latest codes; plain text keeps those it has.
                self.cursor.execute(
                    "UPDATE targets SET source_markup = ?, target_markup = ?, plain_length = ? "
                    "WHERE tid = ? AND (source_markup IS NOT ? OR target_markup IS NOT ? "
                    "OR plain_length IS NOT ?)",
                    (
                        source_markup,
                        target_markup,
                        plain_length,
                        tid,
                        source_markup,
                        target_markup,
                        plain_length,
                    ),
                )
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

    def sync_store(
        self,
        path,
        units,
        source_lang,
        target_lang,
        member="",
        kind=STORE_SAVED,
        replace=True,
    ):
        """Make the store at C{path} hold C{units} for this language pair,
        dictionaries with source, target, context and an optional key.

        With C{replace}, a unit whose target changed or that is no longer
        in C{units} loses its old target, which goes once no other store
        holds it.

        @return: counts of units added, replaced and removed, or None if
            the store is excluded from the TM."""
        source_lang = data.normalize_code(source_lang)
        target_lang = data.normalize_code(target_lang)
        try:
            self._begin()
            counts = self._sync(path, member, kind, {(source_lang, target_lang): units}, replace)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return counts

    def import_store(self, store, path, source_lang=None, target_lang=None, member=""):
        """Sync the translated units of the toolkit C{store} as the
        imported store at C{path}, each under its own languages if it has
        them."""
        pairs = {}
        for unit in store.units:
            if not (unit.istranslatable() and unit.istranslated()):
                continue
            for langs, source, target, markup in unit_translations(unit, source_lang, target_lang):
                pair = tuple(data.normalize_code(lang) for lang in langs)
                pairs.setdefault(pair, []).append(
                    {
                        "source": source,
                        "target": target,
                        "context": unit.getcontext(),
                        "key": unit.getid(),
                        "source_markup": markup[0],
                        "target_markup": markup[1],
                        **plural_fields(source, target, unit),
                        **plural_marker(unit),
                    }
                )
        if not pairs and source_lang and target_lang:
            # Nothing translated any more.
            pairs[(data.normalize_code(source_lang), data.normalize_code(target_lang))] = []
        try:
            self._begin()
            counts = self._sync(path, member, STORE_IMPORTED, pairs, replace=True)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return counts

    def _sync(self, path, member, kind, pairs, replace):
        if not path:
            raise ValueError("a store needs a path")
        now = int(time.time())
        store_id, exclude = self._store(path, member, kind, now)
        if exclude:
            return None
        counts = {"added": 0, "replaced": 0, "removed": 0}
        unlinked = set()
        for target_lang, current in self._add_targets(pairs).items():
            self.cursor.execute(
                "SELECT unit_key, tid FROM origins WHERE store_id = ? AND lang = ?",
                (store_id, target_lang),
            )
            linked = dict(self.cursor.fetchall())
            for key, tid in current.items():
                old = linked.get(key)
                if old is None:
                    counts["added"] += 1
                    self.cursor.execute(
                        "INSERT INTO origins (store_id, lang, unit_key, tid, time) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (store_id, target_lang, key, tid, now),
                    )
                elif old != tid:
                    counts["replaced"] += 1
                    unlinked.add(old)
                    self.cursor.execute(
                        "UPDATE origins SET tid = ?, time = ? "
                        "WHERE store_id = ? AND lang = ? AND unit_key = ?",
                        (tid, now, store_id, target_lang, key),
                    )
            if replace:
                gone = [(key, tid) for key, tid in linked.items() if key not in current]
                counts["removed"] += len(gone)
                unlinked.update(tid for _key, tid in gone)
                self.cursor.executemany(
                    "DELETE FROM origins WHERE store_id = ? AND lang = ? AND unit_key = ?",
                    [(store_id, target_lang, key) for key, _tid in gone],
                )
        if replace:
            self._drop_unheld(unlinked)
        else:
            # Kept, but no longer this file's.
            self._file_under_legacy()
        return counts

    def _add_targets(self, pairs):
        """Add the units of C{pairs}, giving each one's tid by target
        language, then unit key."""
        # A store's units are linked by target language alone, so pairs
        # into one target language (a multilingual TMX) are synced as one.
        by_target = {}
        for (source_lang, target_lang), units in pairs.items():
            current = by_target.setdefault(target_lang, {})
            for unit in join_plurals(units):
                unit = {**unit, "context": unit.get("context") or ""}
                current[unit_key(unit)] = self._add_target(unit, source_lang, target_lang)
        return by_target

    def _find_store(self, path, member):
        self.cursor.execute(
            "SELECT store_id, exclude FROM stores WHERE path = ? AND member = ?", (path, member)
        )
        return self.cursor.fetchone()

    def _store(self, path, member, kind, now):
        """The id and exclude flag of the store at C{path}, made C{kind}
        unless excluded."""
        row = self._find_store(path, member)
        if row is not None and row[1]:
            return row
        if row is None:
            self.cursor.execute(
                "INSERT INTO stores (path, member, kind, added, updated) VALUES (?, ?, ?, ?, ?)",
                (path, member, kind, now, now),
            )
            return self.cursor.lastrowid, 0
        self.cursor.execute(
            "UPDATE stores SET kind = ?, updated = ? WHERE store_id = ?", (kind, now, row[0])
        )
        return row

    def _drop_unheld(self, tids):
        """Delete those of C{tids} no store holds, and sources left
        without a target. Returns how many targets went."""
        sids = set()
        deleted = 0
        for tid in tids:
            self.cursor.execute(
                "SELECT sid FROM targets t WHERE tid = ? "
                "AND NOT EXISTS (SELECT 1 FROM origins o WHERE o.tid = t.tid)",
                (tid,),
            )
            row = self.cursor.fetchone()
            if row is None:
                continue
            sids.add(row[0])
            self.cursor.execute("DELETE FROM targets WHERE tid = ?", (tid,))
            deleted += 1
        # The full-text trigger removes each from the index.
        self.cursor.executemany(
            "DELETE FROM sources WHERE sid = ? "
            "AND NOT EXISTS (SELECT 1 FROM targets WHERE targets.sid = sources.sid)",
            [(sid,) for sid in sids],
        )
        return deleted

    def forget_store(self, path, source_lang, target_lang, member=""):
        """Remove what the store at C{path} added for this language pair,
        except targets another store also holds. The store goes once it
        holds nothing, unless it is excluded (which it remembers).

        @return: how many units it held, or None for an unknown store."""
        source_lang = data.normalize_code(source_lang)
        target_lang = data.normalize_code(target_lang)
        try:
            self._begin()
            row = self._find_store(path, member)
            if row is None:
                self.connection.commit()
                return None
            store_id = row[0]
            self.cursor.execute(
                "SELECT o.unit_key, o.tid FROM origins o JOIN targets t ON t.tid = o.tid "
                "JOIN sources s ON s.sid = t.sid "
                "WHERE o.store_id = ? AND o.lang = ? AND s.lang = ?",
                (store_id, target_lang, source_lang),
            )
            held = self.cursor.fetchall()
            self.cursor.executemany(
                "DELETE FROM origins WHERE store_id = ? AND lang = ? AND unit_key = ?",
                [(store_id, target_lang, key) for key, _tid in held],
            )
            self._drop_unheld({tid for _key, tid in held})
            self.cursor.execute(
                "DELETE FROM stores WHERE store_id = ? AND exclude = 0 "
                "AND NOT EXISTS (SELECT 1 FROM origins WHERE store_id = ?)",
                (store_id, store_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return len(held)

    def set_exclude(self, path, exclude, member=""):
        """Keep the store at C{path} out of the TM, removing what it added,
        or let it in again."""
        try:
            self._begin()
            row = self._find_store(path, member)
            if row is None:
                now = int(time.time())
                self.cursor.execute(
                    "INSERT INTO stores (path, member, kind, added, updated) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (path, member, STORE_SAVED, now, now),
                )
                store_id = self.cursor.lastrowid
            else:
                store_id = row[0]
            self.cursor.execute(
                "UPDATE stores SET exclude = ? WHERE store_id = ?", (int(bool(exclude)), store_id)
            )
            if exclude:
                self.cursor.execute("SELECT tid FROM origins WHERE store_id = ?", (store_id,))
                tids = {tid for (tid,) in self.cursor.fetchall()}
                self.cursor.execute("DELETE FROM origins WHERE store_id = ?", (store_id,))
                self._drop_unheld(tids)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def store_stats(self, path, source_lang, target_lang, member=""):
        """The store at C{path}: its unit count for this language pair,
        when it was last synced, its kind and exclude flag; None if
        unknown."""
        self.cursor.execute(
            "SELECT store_id, kind, exclude, updated FROM stores WHERE path = ? AND member = ?",
            (path, member),
        )
        row = self.cursor.fetchone()
        if row is None:
            return None
        store_id, kind, exclude, updated = row
        self.cursor.execute(
            "SELECT COUNT(*) FROM origins o JOIN targets t ON t.tid = o.tid "
            "JOIN sources s ON s.sid = t.sid "
            "WHERE o.store_id = ? AND o.lang = ? AND s.lang = ?",
            (store_id, data.normalize_code(target_lang), data.normalize_code(source_lang)),
        )
        (units,) = self.cursor.fetchone()
        return {"units": units, "updated": updated, "kind": kind, "exclude": bool(exclude)}

    def list_stores(self):
        """Every store, with its unit count and last change per language
        pair, and whether its file is still there (None for the legacy
        store, which has none)."""
        self.cursor.execute(
            "SELECT st.store_id, st.path, st.member, st.kind, st.exclude, st.added, st.updated, "
            "p.slang, p.tlang, COUNT(p.tlang), MAX(p.time) FROM stores st LEFT JOIN "
            "(SELECT o.store_id, s.lang AS slang, o.lang AS tlang, o.time FROM origins o "
            "JOIN targets t ON t.tid = o.tid JOIN sources s ON s.sid = t.sid) p "
            "ON p.store_id = st.store_id "
            "GROUP BY st.store_id, p.slang, p.tlang ORDER BY st.store_id, p.slang, p.tlang"
        )
        stores = {}
        for row in self.cursor.fetchall():
            store_id, path, member, kind, exclude, added, updated = row[:7]
            slang, tlang, units, changed = row[7:]
            if store_id not in stores:
                stores[store_id] = {
                    "id": store_id,
                    "path": path,
                    "member": member,
                    "kind": kind,
                    "exclude": bool(exclude),
                    "added": added,
                    "updated": updated,
                    "exists": os.path.exists(path) if path else None,
                    "pairs": [],
                }
            if tlang is not None:
                stores[store_id]["pairs"].append(
                    {
                        "source_lang": slang,
                        "target_lang": tlang,
                        "units": units,
                        "updated": changed,
                    }
                )
        return list(stores.values())

    def export_tmx(self, source_lang, target_lang, path=None, member=""):
        """The TM's entries for this language pair as TMX bytes; only the
        store at C{path}'s if given (None if it is unknown)."""
        query = (
            "SELECT s.text, t.text, s.context, t.source_markup, t.target_markup, "
            "t.plural_source, t.plural_targets, %s "
            "FROM sources s JOIN targets t ON s.sid = t.sid "
            "WHERE s.lang = ? AND t.lang = ?" % _layout_select()
        )
        params = [data.normalize_code(source_lang), data.normalize_code(target_lang)]
        if path is not None:
            row = self._find_store(path, member)
            if row is None:
                return None
            query += " AND t.tid IN (SELECT tid FROM origins WHERE store_id = ?)"
            params.append(row[0])
        self.cursor.execute(query + " ORDER BY s.sid, t.tid", params)
        # TMX takes BCP 47 tags.
        source_tag = source_lang.replace("_", "-")
        target_tag = target_lang.replace("_", "-")
        store = tmx.tmxfile(sourcelanguage=source_tag, targetlanguage=target_tag)
        for row in self.cursor.fetchall():
            source, target, context, source_markup, target_markup = row[:5]
            store.addtranslation(source, source_tag, target, target_tag, context=context or None)
            if source_markup is not None and target_markup is not None:
                _write_codes(store.units[-1], source_markup, target_markup)
            forms = _forms(*row[5:7])
            if forms:
                group = plural_group_key(params[:2], context, source, row[5], forms)
                _add_forms(store, (source_tag, target_tag), group, row[5], forms, context, row[7])
        return bytes(store)

    def forget_unit(self, source, target, source_lang, target_lang, context=None):
        """Remove a target from the TM, whichever stores hold it; any
        context if C{context} is None. Returns how many targets went."""
        query = (
            "SELECT t.tid FROM sources s JOIN targets t ON s.sid = t.sid "
            "WHERE s.text = ? AND t.text = ? AND s.lang = ? AND t.lang = ?"
        )
        params = [
            source,
            target,
            data.normalize_code(source_lang),
            data.normalize_code(target_lang),
        ]
        if context is not None:
            query += " AND s.context IS ?"
            params.append(context)
        try:
            self._begin()
            self.cursor.execute(query, params)
            tids = [tid for (tid,) in self.cursor.fetchall()]
            self.cursor.executemany("DELETE FROM origins WHERE tid = ?", [(tid,) for tid in tids])
            deleted = self._drop_unheld(tids)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return deleted

    def translate_unit(self, unit_source, source_langs, target_langs, plain=None):
        """Return TM suggestions for unit_source.

        Each has the quality its target gets once rebuilt with
        unit_source's placeables (see abstraction.adapt), so a source that
        differs only in placeables or accelerator is found and kept however
        low its raw quality, which is given as raw_quality.

        plain, for a unit of a format with inline codes, is its source
        without their native code (inlinecodes.plain_source). It is matched
        against an entry with codes on the entry's texts without theirs,
        and against an entry without codes as unit_source is too, keeping
        the better score."""
        source_in, source_params = _language_match("s.lang", source_langs)
        target_in, target_params = _language_match("t.lang", target_langs)
        langs_in = "%s AND %s" % (source_in, target_in)
        lang_params = (*source_params, *target_params)

        queries = [(unit_source, self._query_abstract(unit_source), "s.length")]
        if plain is not None:
            queries.append(
                (plain, self._query_abstract(plain), "COALESCE(t.plain_length, s.length)")
            )
        windows = []
        for text, query_abstract, column in queries:
            windows.append((len(text), column))
            if self.abstract:
                # Either window: abstraction may only add candidates.
                windows.append((len(query_abstract), "length(COALESCE(s.abstract, s.text))"))
        window_sql = " OR ".join("%s BETWEEN ? AND ?" % column for _length, column in windows)
        window_params = []
        for length, _column in windows:
            window_params += [
                min_levenshtein_length(length, self.min_similarity),
                max_levenshtein_length(length, self.min_similarity, self.max_length),
            ]

        # split source into words (the plain source's, if given), remove
        # punctuation and special chars, keep words at least 3 chars long
        unit_words = STRIP_REGEXP.sub(" ", queries[-1][1]).split()
        unit_words = list(filter(lambda word: len(word) > 2, unit_words))

        columns = (
            "s.text, t.text, s.context, s.abstract, t.source_markup, t.target_markup, "
            "t.plural_source, t.plural_targets, %s" % _layout_select()
        )
        if self.fulltext and len(unit_words) > 3:
            logger.debug("fulltext matching")
            query = """SELECT %s FROM sources s JOIN targets t ON s.sid = t.sid JOIN fulltext f ON s.sid = f.docid
                       WHERE %s AND (%s)
                       AND fulltext MATCH ?""" % (columns, langs_in, window_sql)
            # Quoted, so a word such as AND, NOT or NEAR isn't read as an
            # FTS operator (#4332). STRIP_REGEXP has removed any quote.
            search_str = " OR ".join('"%s"' % word for word in unit_words)
            self.cursor.execute(query, (*lang_params, *window_params, search_str))
        else:
            logger.debug("nonfulltext matching")
            query = """SELECT %s FROM sources s JOIN targets t ON s.sid = t.sid
            WHERE %s AND (%s)""" % (columns, langs_in, window_sql)
            self.cursor.execute(query, (*lang_params, *window_params))

        results = []
        for row in self.cursor.fetchall():
            scores = [self._score(*texts) for texts in _match_texts(queries, row)]
            scores = [score for score in scores if score is not None]
            if not scores:
                continue
            quality, raw_quality = max(scores)
            match = {"source": row[0], "target": row[1], "context": row[2], "quality": quality}
            if self.abstract:
                match["raw_quality"] = raw_quality
            results.append(_with_plurals(_with_markup(match, row), row))
        results.sort(key=operator.itemgetter("quality"), reverse=True)
        results = results[: self.max_candidates]
        logger.debug("results: %s", results)
        return results

    def _query_abstract(self, text):
        return _abstract(text) if self.abstract else text

    def _score(self, query, query_abstract, source, target, source_abstract):
        """(quality, raw quality) of the entry source with target for
        query, or None below the minimum."""
        raw_quality = self.comparer.similarity(query, source, self.min_similarity)
        if not self.abstract:
            return (raw_quality, raw_quality) if raw_quality >= self.min_similarity else None
        if raw_quality < self.min_similarity:
            if source_abstract is None:
                source_abstract = _abstract(source)
            # Penalties only lower it: an upper bound on the quality.
            bound = self.comparer.similarity(query_abstract, source_abstract, self.min_similarity)
            if round(bound) < self.min_similarity:
                return None
            # Cut short below the minimum: worked out in full.
            raw_quality = self.comparer.similarity(query, source, 0)
        adapted = adapt(query, source, target, raw_quality)
        if adapted.quality < self.min_similarity:
            return None
        return adapted.quality, adapted.raw_quality


def _match_texts(queries, row):
    """(query, its abstract, source, target, source abstract) to score for
    each of the queries that applies to the entry in row."""
    source, target, _context, source_abstract, source_markup, target_markup = row[:6]
    (text, query_abstract, _column), *coded = queries
    if not coded:
        return [(text, query_abstract, source, target, source_abstract)]
    plain, plain_abstract, _column = coded[0]
    plain_source = inlinecodes.plain_text(source_markup) if source_markup is not None else None
    plain_target = inlinecodes.plain_text(target_markup) if target_markup is not None else None
    if plain_source is not None and plain_target is not None:
        return [(plain, plain_abstract, plain_source, plain_target, None)]
    # Without codes, it may hold the native code as text.
    texts = [(text, query_abstract, source, target, source_abstract)]
    if plain != text:
        texts.append((plain, plain_abstract, source, target, source_abstract))
    return texts


def _with_markup(match, row):
    """match, with the markup of a target with inline codes."""
    source_markup, target_markup = row[4:6]
    if source_markup is not None and target_markup is not None:
        match["source_markup"] = source_markup
        match["target_markup"] = target_markup
    return match


def _with_plurals(match, row):
    """match, with the source plural and forms of a plural target."""
    plural_source, plural_targets = row[6:8]
    forms = _forms(plural_source, plural_targets)
    if forms is not None:
        match["plural_source"] = plural_source
        match["plural_targets"] = forms
        if len(row) > 8 and row[8]:
            match["plural_layout"] = row[8]
    return match


def _forms(plural_source, plural_targets):
    """The forms a target's plural columns hold, or None."""
    if plural_source is None or plural_targets is None:
        return None
    try:
        forms = json.loads(plural_targets)
    except ValueError:
        return None
    return forms if isinstance(forms, list) else None


# TMX has no plurals: each form is a translation unit of its own, the
# first with the singular source and the others with the source plural,
# as gettext pairs them. These properties put the forms back together.
PLURAL_GROUP_PROP = "x-plural-group"
PLURAL_FORM_PROP = "x-plural-form"
PLURAL_LAYOUT_PROP = "x-plural-layout"


def plural_group_key(langs, context, source, plural_source, forms):
    """The x-plural-group of a plural's TMX units: a hash of the plural
    itself, so that re-exports give the same key and merged exports'
    plurals do not share one unless they are the same."""
    text = json.dumps([*langs, context or "", source, plural_source, *forms], ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _add_forms(store, tags, group, plural_source, forms, context, layout=None):
    """Add the plural forms after the first to the TMX C{store}, whose
    last unit is the first, marking each as one of C{group}."""
    source_tag, target_tag = tags
    _mark_plural(store.units[-1], group, 0, layout)
    for form, text in enumerate(forms[1:], 1):
        store.addtranslation(plural_source, source_tag, text, target_tag, context=context or None)
        _mark_plural(store.units[-1], group, form)


def _mark_plural(unit, group, form, layout=None):
    """Mark the TMX unit as form C{form} of the plural C{group}, the
    first with the plural's layout if known."""
    tuv = unit.xmlelement.find(unit.namespaced(unit.languageNode))
    props = [(PLURAL_GROUP_PROP, group), (PLURAL_FORM_PROP, str(form))]
    if layout:
        props.append((PLURAL_LAYOUT_PROP, layout))
    for prop_type, value in props:
        prop = unit.xmlelement.makeelement(unit.namespaced("prop"), {"type": prop_type})
        prop.text = value
        tuv.addprevious(prop)


def plural_marker(unit):
    """The "plural_group" and "plural_form" a unit dictionary gives the
    TMX unit's plural properties (see export_tmx); none if it has none."""
    if not isinstance(unit, tmx.tmxunit):
        return {}
    props = {}
    for prop in unit.xmlelement.iterfind(unit.namespaced("prop")):
        if prop.get("type") in (PLURAL_GROUP_PROP, PLURAL_FORM_PROP, PLURAL_LAYOUT_PROP):
            props.setdefault(prop.get("type"), (prop.text or "").strip())
    group = props.get(PLURAL_GROUP_PROP)
    form = props.get(PLURAL_FORM_PROP, "")
    if not group or not (form.isascii() and form.isdigit()):
        return {}
    marker = {"plural_group": group, "plural_form": int(form)}
    if props.get(PLURAL_LAYOUT_PROP):
        marker["plural_layout"] = props[PLURAL_LAYOUT_PROP]
    return marker


def join_plurals(units):
    """units (dictionaries), with the forms of each complete plural
    group (see plural_marker) joined into its first form's entry, which
    gains the source plural and every form."""
    groups = {}
    for unit in units:
        group, form = unit.get("plural_group"), unit.get("plural_form")
        if isinstance(group, str) and isinstance(form, int):
            groups.setdefault(group, []).append((form, unit))
    joined = {}
    for members in groups.values():
        forms = _group_forms(members)
        if forms is None:
            continue
        joined.update((id(unit), None) for _form, unit in members)
        joined[id(forms[0])] = {
            "plural_source": forms[1]["source"],
            "plural_targets": [unit["target"] for unit in forms],
        }
        if forms[0].get("plural_layout"):
            joined[id(forms[0])]["plural_layout"] = forms[0]["plural_layout"]
    if not joined:
        return units
    return [
        {**unit, **joined[id(unit)]} if joined.get(id(unit)) else unit
        for unit in units
        if id(unit) not in joined or joined[id(unit)]
    ]


def _group_forms(members):
    """The units of a plural group's (form, unit) C{members}, one per form
    in order, or None if they are not one plural's complete forms. A form
    repeated with the same text (merged copies of one export) counts once."""
    by_form = {}
    for form, unit in members:
        if by_form.setdefault(form, unit) is not unit:
            other = by_form[form]
            if (other["source"], other["target"]) != (unit["source"], unit["target"]):
                return None
    if sorted(by_form) != list(range(len(by_form))) or len(by_form) < 2:
        return None
    forms = [by_form[form] for form in range(len(by_form))]
    if len({unit["source"] for unit in forms[1:]}) != 1 or not all(u["target"] for u in forms):
        return None
    return forms


def plural_fields(source, target, unit=None):
    """The "plural_source" and "plural_targets" a unit dictionary gives a
    plural unit's C{source} and C{target} (multistrings) with every form
    translated, and its "plural_layout" if C{unit} says; none for any
    other."""
    targets = [str(form) for form in getattr(target, "strings", ())]
    if len(targets) < 2 or not all(targets):
        return {}
    # .qm and .ts: one source for every form.
    sources = getattr(source, "strings", None) or [source]
    fields = {"plural_source": str(sources[min(1, len(sources) - 1)]), "plural_targets": targets}
    layout = plural_layout(unit) if unit is not None else None
    if layout:
        fields["plural_layout"] = layout
    return fields


def plural_layout(unit):
    """How C{unit}'s plural forms are laid out (pluralmap's names): CLDR's
    for a monolingual file's plural, its file's Plural-Forms for a PO
    file's, else None (not known)."""
    from virtaal.support import pluralmap

    if getattr(unit, "plural_tags", None):
        return pluralmap.CLDR_ORDINAL if getattr(unit, "plural_ordinal", False) else pluralmap.CLDR
    store = getattr(unit, "_store", None)
    getheaderplural = getattr(store, "getheaderplural", None)
    if getheaderplural is None:
        return None
    nplurals, plural = getheaderplural()
    try:
        return pluralmap.gettext_name(int(nplurals), plural) if plural else None
    except (TypeError, ValueError):
        return None


def _plural_layout(unit):
    layout = unit.get("plural_layout")
    return layout if isinstance(layout, str) and layout else None


def _plurals(unit):
    """C{unit}'s (source plural, forms as JSON) if it has usable ones,
    else (None, None)."""
    plural_source = unit.get("plural_source")
    forms = unit.get("plural_targets")
    if (
        not isinstance(plural_source, str)
        or not isinstance(forms, list)
        or len(forms) < 2
        or not all(isinstance(form, str) and form for form in forms)
        or forms[0] != str(unit["target"])
    ):
        return None, None
    return plural_source, json.dumps(forms, ensure_ascii=False)


def _write_codes(unit, source_markup, target_markup):
    """Give the TMX unit's segments their inline codes."""
    segs = [next(child for child in node if child.tag == "seg") for node in unit.getlanguageNodes()]
    numbers = {}
    for seg, markup in zip(segs, (source_markup, target_markup), strict=True):
        inlinecodes.write_tmx(seg, markup, numbers)


def _plain_length(source_markup):
    if source_markup is None:
        return None
    plain = inlinecodes.plain_text(source_markup)
    return len(plain) if plain is not None else None


def _markup(unit):
    """C{unit}'s (source markup, target markup) if both are its texts with
    inline codes, else (None, None)."""
    source = inlinecodes.checked(unit.get("source_markup"), unit["source"])
    target = inlinecodes.checked(unit.get("target_markup"), unit["target"])
    if source is None or target is None:
        return None, None
    return source, target


def _abstract(text):
    """text's abstract; text itself should the placeable parsers fail."""
    try:
        return abstract(text)[0]
    except Exception:
        logger.debug("No abstract for %r", text, exc_info=True)
        return text


def unit_languages(unit, source_lang=None, target_lang=None):
    """C{unit}'s own languages, or the passed ones for a unit that has none."""
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
    return source_lang, target_lang


def _language_match(column, languages):
    """An SQL test of C{column} for any of C{languages}, and its
    parameters. A language matches itself and, across a region only, its
    regional variants or its bare language: en matches en-us and en-us
    matches en, but en-us doesn't match en-gb, nor sr sr-latin."""
    if not isinstance(languages, list):
        languages = [languages]
    exact = set()
    variants = []
    for language in languages:
        language = data.normalize_code(language)
        exact.add(language)
        primary, _sep, rest = language.partition("-")
        if not rest:
            variants.append(language)
        elif _is_region(rest):
            exact.add(primary)
    exact = sorted(exact)
    # One placeholder per language: "IN (?)" with "en,fr" matches neither.
    tests = ["%s IN (%s)" % (column, ",".join("?" * len(exact)))]
    params = list(exact)
    for language in variants:
        tests.append("%s GLOB ? OR %s GLOB ?" % (column, column))
        params += [language + "-[a-z][a-z]", language + "-[0-9][0-9][0-9]"]
    return "(%s)" % " OR ".join(tests), params


def _is_region(subtag):
    return (len(subtag) == 2 and subtag.isalpha()) or (len(subtag) == 3 and subtag.isdigit())


def _language_tag_key(language):
    """An XML language tag in a form to compare: en_US and en-us match."""
    return language.replace("_", "-").lower() if language else None


def unit_translations(unit, source_lang=None, target_lang=None):
    """C{unit}'s ((source language, target language), source, target,
    (source markup, target markup)) for each translation it holds: one,
    or for a TMX unit one per language after its source's. The markup is
    None for text with no inline codes (see inlinecodes)."""
    nodes = unit.getlanguageNodes() if isinstance(unit, tmx.tmxunit) else []
    if len(nodes) < 2:
        langs = unit_languages(unit, source_lang, target_lang)
        return [(langs, unit.source, unit.target, inlinecodes.unit_markup(unit))]
    # The unit's own srclang, else the header's: read from the XML, as
    # translate-toolkit before 3.18 doesn't take it from the header.
    header = unit.xmlelement.getroottree().getroot().find("header")
    srclang = unit.xmlelement.get("srclang") or (header is not None and header.get("srclang"))
    xml_space = getXMLspace(unit.xmlelement, unit._default_xml_space)
    langs = [tuv_lang(node) for node in nodes]
    texts = [unit.getNodeText(node, xml_space) for node in nodes]
    markups = [inlinecodes.seg_markup(node) for node in nodes]
    wanted = _language_tag_key(srclang)
    source_index = next((i for i, lang in enumerate(langs) if _language_tag_key(lang) == wanted), 0)
    source = texts[source_index]
    source_markup = markups[source_index]
    source_lang = langs[source_index] or source_lang
    if not source_lang:
        raise LanguageError("undefined source language")
    translations = []
    for i, (lang, text) in enumerate(zip(langs, texts, strict=True)):
        if i == source_index or not text:
            continue
        if not (lang or target_lang):
            raise LanguageError("undefined target language")
        markup = inlinecodes.text_pair(source_markup, markups[i], source, text)
        translations.append(((source_lang, lang or target_lang), source, text, markup))
    return translations


def unit_key(unit):
    """What identifies a unit in its file: its key, else its context and
    source as a PO file's id joins them."""
    if unit.get("key"):
        return unit["key"]
    if unit.get("context"):
        return "%s\x04%s" % (unit["context"], unit["source"])
    return unit["source"]


def min_levenshtein_length(length, min_similarity):
    return math.ceil(max(length * (min_similarity / 100.0), 2))


def max_levenshtein_length(length, min_similarity, max_length):
    return math.floor(min(length / (min_similarity / 100.0), max_length))
