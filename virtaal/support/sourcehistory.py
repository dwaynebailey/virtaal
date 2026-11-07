#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The source text a monolingual file's translations were made for,
when there's no baseline to say: from the local TM, which recorded each
saved translation with its source and key. No GTK here."""

import logging
import os
import pathlib
import sqlite3


def local_tm_path():
    """The local TM's database, as its settings name it, if it exists."""
    from virtaal.common import pan_app

    config = pan_app.load_config(os.path.join(pan_app.get_config_dir(), 'tm.ini'), 'localtm')
    path = os.path.join(pan_app.get_config_dir(), os.path.expanduser(config.get('tmdb', 'tm.db')))
    return path if os.path.isfile(path) else None


def _same_file(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _stores(cursor, target):
    """The ids of the stores that may be C{target}: its own path first,
    then files of the same name elsewhere (a moved project), the latest
    saved first."""
    rows = cursor.execute(
        "SELECT store_id, path FROM stores WHERE member = '' ORDER BY updated DESC"
    ).fetchall()
    name = os.path.normcase(os.path.basename(target))
    same = [store_id for store_id, path in rows if _same_file(path, target)]
    moved = [
        store_id
        for store_id, path in rows
        if store_id not in same and os.path.normcase(os.path.basename(path)) == name
    ]
    return same + moved


def from_local_tm(db_path, target, targets):
    """{key: source} for those of C{targets} ({key: translation}) the
    local TM at C{db_path} holds with the same translation for
    C{target}, or for a file of its name."""
    if not db_path or not targets:
        return {}
    found = {}
    try:
        # Read-write but never written: a read-only connection would leave
        # its write-ahead log files behind. Never created if missing.
        uri = pathlib.Path(os.path.abspath(db_path)).as_uri() + '?mode=rw'
        connection = sqlite3.connect(uri, uri=True, timeout=1)
    except sqlite3.Error:
        logging.debug("Can't read the local TM %s", db_path, exc_info=True)
        return {}
    try:
        cursor = connection.cursor()
        for store_id in _stores(cursor, target):
            rows = cursor.execute(
                "SELECT o.unit_key, t.text, s.text FROM origins o "
                "JOIN targets t ON t.tid = o.tid JOIN sources s ON s.sid = t.sid "
                "WHERE o.store_id = ?",
                (store_id,),
            )
            for key, translation, source in rows:
                if key not in found and targets.get(key) == translation:
                    found[key] = source
            if len(found) == len(targets):
                break
    except sqlite3.Error:
        # An older file without origins, or a locked one.
        logging.debug("Can't read the local TM %s", db_path, exc_info=True)
    finally:
        connection.close()
    return found


def recall(pair, targets):
    """{key: source} of the translations in C{targets} ({key: text})
    that C{pair}'s history knows."""
    return from_local_tm(local_tm_path(), pair.target, targets)
