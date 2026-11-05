#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.
#
# Loosely descended from translate-toolkit's translate/services/tmserver.py
# (dropped upstream, no replacement, after release 3.18.1) - vendored at
# first, now rewritten on Bottle for routing and request/response
# handling in place of the vendored selector.py router and hand-rolled
# WSGI plumbing. Still served over cheroot, via Bottle's own server
# adapter.

"""
A translation memory server using tmdb for storage, communicates with
clients using JSON over HTTP.
"""

import json
import logging
import os
import sys
from argparse import ArgumentParser
from io import BytesIO
from urllib.parse import unquote_to_bytes

from bottle import Bottle, request, response
from translate.storage import base, factory

from virtaal.support import (
    fluent_compat,  # noqa: F401 - applies its patch on import
    poparser_compat,  # noqa: F401 - applies its patch on import
    sdlxliff_compat,  # noqa: F401 - applies its patch on import
    tmdb,
    trados_compat,  # noqa: F401 - applies its patch on import
)

logger = logging.getLogger(__name__)


def _decode_path_from_request_uri() -> None:
    """Route on the fully decoded request path.

    cheroot decodes every escape in PATH_INFO except %2F, so a source
    containing "/" reached the TM lookup as "%2F"."""
    environ = request.environ
    uri = environ.get("REQUEST_URI")
    if not uri:
        return
    raw = uri.split("?", 1)[0].encode("latin-1")
    # Decoded the same way Bottle decodes PATH_INFO.
    path = unquote_to_bytes(raw).decode("utf-8", "ignore")
    script_name = environ.get("SCRIPT_NAME", "")
    if script_name and path.startswith(script_name):
        path = path[len(script_name) :]
    environ["PATH_INFO"] = path


class TMServer:
    """A RESTful JSON TM server."""

    def __init__(
        self,
        tmdbfile,
        tmfiles,
        max_candidates=3,
        min_similarity=75,
        max_length=1000,
        prefix="",
        source_lang=None,
        target_lang=None,
        abstract=True,
    ) -> None:
        if not isinstance(tmdbfile, str):
            tmdbfile = tmdbfile.decode(sys.getfilesystemencoding())

        self.tmdb = tmdb.TMDB(tmdbfile, max_candidates, min_similarity, max_length, abstract)

        if tmfiles:
            self._load_files(tmfiles, source_lang, target_lang)
        self.tmdb.start_backfill()

        # initialize url dispatcher
        self.rest = Bottle()
        self.rest.add_hook("before_request", _decode_path_from_request_uri)
        # Bottle's path filter is ".+?", which doesn't match a newline.
        unit_path = rf"{prefix}/<slang>/<tlang>/unit/<uid:re:[\s\S]+>"
        # A store is its file's path; the legacy store's is empty.
        store_path = rf"{prefix}/<slang>/<tlang>/store/<sid:re:[\s\S]*>"
        self.rest.route(unit_path, "GET", self.translate_unit)
        self.rest.route(unit_path, "POST", self.update_unit)
        self.rest.route(unit_path, "PUT", self.add_unit)
        self.rest.route(unit_path, "DELETE", self.forget_unit)
        self.rest.route(store_path, "GET", self.get_store_stats)
        self.rest.route(store_path, "PUT", self.upload_store)
        self.rest.route(store_path, "POST", self.add_store)
        self.rest.route(store_path, "DELETE", self.forget_store)
        self.rest.route(f"{prefix}/stores", "GET", self.list_stores)
        self.rest.route(f"{prefix}/<slang>/<tlang>/export", "GET", self.export)

    def _load_files(self, tmfiles, source_lang, target_lang) -> None:
        if not isinstance(tmfiles, list):
            tmfiles = [tmfiles]
        for tmfile in tmfiles:
            store = _read_store(tmfile, source_lang, target_lang)
            self.tmdb.import_store(store, os.path.realpath(tmfile), source_lang, target_lang)

    def translate_unit(self, slang, tlang, uid):
        response.content_type = "text/plain"
        candidates = self.tmdb.translate_unit(uid, slang, tlang)
        logger.debug("candidates: %s", candidates)
        body = json.dumps(candidates, indent=4)
        callback = request.query.callback
        if callback:
            body = f"{callback}({body})"
        return body

    def add_unit(self, slang, tlang, uid):
        response.content_type = "text/plain"
        data = json.loads(request.body.read())
        unit = base.TranslationUnit(data["source"])
        unit.target = data["target"]
        self.tmdb.add_unit(unit, slang, tlang)
        return ""

    def update_unit(self, slang, tlang, uid):
        response.content_type = "text/plain"
        data = json.loads(request.body.read())
        unit = base.TranslationUnit(data["source"])
        unit.target = data["target"]
        self.tmdb.add_unit(unit, slang, tlang)
        return ""

    def forget_unit(self, slang, tlang, uid):
        """Remove the target in the JSON body, {"target", "context"},
        from every store."""
        data = _json_body()
        if not isinstance(data, dict) or "target" not in data:
            return _json_reply({"error": "no target"}, 400)
        removed = self.tmdb.forget_unit(uid, data["target"], slang, tlang, data.get("context"))
        return _json_reply({"removed": removed})

    def get_store_stats(self, slang, tlang, sid):
        stats = self.tmdb.store_stats(sid, slang, tlang, request.query.member)
        if stats is None:
            return _json_reply({"error": "unknown store"}, 404)
        return _json_reply(stats)

    def upload_store(self, slang, tlang, sid):
        """Import the uploaded file as the store at its path."""
        data = BytesIO(request.body.read())
        data.name = sid
        try:
            store = _read_store(data, slang, tlang)
        except Exception as e:
            logger.warning("Can't read the uploaded %s: %s", sid, e)
            return _json_reply({"error": "unreadable file"}, 400)
        counts = self.tmdb.import_store(store, sid, slang, tlang, request.query.member)
        return _json_reply(counts if counts is not None else {"excluded": True})

    def add_store(self, slang, tlang, sid):
        """Sync the store at C{sid} with the POSTed units,
        C{{"units": [...], "replace": true, "member": ...}}, or set
        whether it is excluded, C{{"exclude": true}}.

        A bare list of units is added to the legacy store, replacing
        nothing, as for any client of the older server."""
        data = json.loads(request.body.read())
        if isinstance(data, list):
            response.content_type = "text/plain"
            count = self.tmdb.add_list(data, slang, tlang)
            return f"added {count} units from {sid}"
        if not isinstance(data, dict) or not sid:
            return _json_reply({"error": "no units for a store"}, 400)
        member = data.get("member", "")
        if "units" in data:
            counts = self.tmdb.sync_store(
                sid, data["units"], slang, tlang, member, replace=data.get("replace", True)
            )
            return _json_reply(counts if counts is not None else {"excluded": True})
        if "exclude" in data:
            self.tmdb.set_exclude(sid, data["exclude"], member)
            return _json_reply({"exclude": bool(data["exclude"])})
        return _json_reply({"error": "no units for a store"}, 400)

    def forget_store(self, slang, tlang, sid):
        removed = self.tmdb.forget_store(sid, slang, tlang, request.query.member)
        if removed is None:
            return _json_reply({"error": "unknown store"}, 404)
        return _json_reply({"removed": removed})

    def list_stores(self):
        return _json_reply(self.tmdb.list_stores())

    def export(self, slang, tlang):
        """The pair's entries as TMX, only those of the store at
        ?store=<path> if given."""
        path = request.query.getunicode("store")
        tmx = self.tmdb.export_tmx(slang, tlang, path, request.query.member)
        if tmx is None:
            return _json_reply({"error": "unknown store"}, 404)
        response.content_type = "application/x-tmx+xml"
        return tmx


def _read_store(storefile, source_lang, target_lang):
    store = factory.getobject(storefile)  # ty:ignore[invalid-argument-type]
    if hasattr(store, "select_languages"):
        # A termbase of many languages gives the pair asked for.
        store.select_languages(source_lang, target_lang)
    return store


def _json_body():
    body = request.body.read()
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _json_reply(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data)


def build_parser() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument(
        "-d",
        "--tmdb",
        dest="tmdbfile",
        default=":memory:",
        help="translation memory database file",
    )
    parser.add_argument(
        "-f",
        "--import-translation-file",
        dest="tmfiles",
        action="append",
        help="translation file to import into the database",
    )
    parser.add_argument(
        "-t",
        "--import-target-lang",
        dest="target_lang",
        help="target language of translation files",
    )
    parser.add_argument(
        "-s",
        "--import-source-lang",
        dest="source_lang",
        help="source language of translation files",
    )
    parser.add_argument(
        "-b",
        "--bind",
        dest="bind",
        default="localhost",
        help="address to bind server to (default: %(default)s)",
    )
    parser.add_argument(
        "-p",
        "--port",
        dest="port",
        type=int,
        default=8888,
        help="port to listen on (default: %(default)s)",
    )
    parser.add_argument(
        "--max-candidates",
        dest="max_candidates",
        type=int,
        default=3,
        help="Maximum number of candidates",
    )
    parser.add_argument(
        "--min-similarity",
        dest="min_similarity",
        type=int,
        default=75,
        help="minimum similarity",
    )
    parser.add_argument(
        "--max-length",
        dest="max_length",
        type=int,
        default=1000,
        help="Maximum string length",
    )
    parser.add_argument(
        "--raw-quality",
        action="store_false",
        dest="abstract",
        help="match and score on the raw text, not past placeables and accelerators",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        dest="debug",
        default=False,
        help="enable debugging features",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    # setup debugging
    format = "%(asctime)s %(levelname)s %(message)s"
    level = logging.DEBUG if args.debug else logging.WARNING
    if args.debug:
        format = "%(levelname)7s %(module)s.%(funcName)s:%(lineno)d: %(message)s"

    logging.basicConfig(level=level, format=format)

    application = TMServer(
        args.tmdbfile,
        args.tmfiles,
        max_candidates=args.max_candidates,
        min_similarity=args.min_similarity,
        max_length=args.max_length,
        prefix="/tmserver",
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        abstract=args.abstract,
    )
    logger.info("Starting server, listening on port %s", args.port)
    try:
        application.rest.run(host=args.bind, port=args.port, server="cheroot", quiet=not args.debug)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
