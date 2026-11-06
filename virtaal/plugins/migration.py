#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Plugin to import data from other applications.

Currently there is some support for importing settings from Poedit and
Lokalize, and Translation Memory from Lokalize. (Poedit's and KBabel's TM
import both relied on the Python 2-only bsddb module and were removed.)
"""

import configparser as ConfigParser
import logging
import os
from io import StringIO
from os import path
from sqlite3 import dbapi2

from virtaal.common import pan_app
from virtaal.common.platform import platform
from virtaal.controllers.baseplugin import BasePlugin
from virtaal.support import tmdb


class Plugin(BasePlugin):
    description = _('Migrate settings from KBabel, Lokalize and/or Poedit to Virtaal')
    display_name = _('Migration Assistant')
    version = 0.1

    default_config = {"tmdb": path.join(pan_app.get_config_dir(), "tm.db")}

    def __init__(self, internal_name, main_controller):
        self.internal_name = internal_name
        self.main_controller = main_controller
        self.load_config()
        self._init_plugin()

    def _init_plugin(self):
        # Source paths need to be set up before the evidence check below.
        if platform.is_mac:
            self.poedit_dir = path.expanduser('~/Library/Preferences')
        else:
            self.poedit_dir = path.expanduser('~/.poedit')

        # KDE moved app configs/data from ~/.kde/share/{config,apps}/ to
        # the XDG base dirs with the KDE4->Frameworks 5 transition
        # (~2014) - these are the current locations, not the ~/.kde/...
        # ones this code originally targeted.
        xdg_config_home = os.environ.get('XDG_CONFIG_HOME') or path.expanduser('~/.config')
        xdg_data_home = os.environ.get('XDG_DATA_HOME') or path.expanduser('~/.local/share')
        self.lokalize_rc = path.join(xdg_config_home, 'lokalizerc')
        self.lokalize_tm_dir = path.join(xdg_data_home, 'lokalize')
        # Poedit 2 and later keep their TM as a Lucene index here, which
        # only Poedit can read; it exports it as TMX.
        if platform.is_mac:
            poedit_data = path.join(
                path.expanduser('~'), 'Library', 'Application Support', 'Poedit'
            )
        elif platform.is_windows:
            poedit_data = path.join(os.environ.get('APPDATA', ''), 'Poedit')
        else:
            poedit_data = path.join(xdg_data_home, 'poedit')
        self.poedit_tm_dir = path.join(poedit_data, 'TranslationMemory')

        # No evidence of anything to migrate - stay silent, and persist
        # that with write() so it sticks across launches.
        if not self._has_anything_to_migrate():
            logging.debug('Migration: nothing found to import - staying silent')
            pan_app.settings.plugin_state[self.internal_name] = "disabled"
            pan_app.settings.write()
            return

        message = _('Should Virtaal try to import settings and data from other applications?')
        must_migrate = self.main_controller.show_prompt(
            _('Import data from other applications?'), message
        )
        if not must_migrate:
            logging.debug('Migration not done due to user choice')
        else:
            # We'll store the tm here:
            self.tmdb = tmdb.TMDB(self.config["tmdb"])
            # We actually need source, target, context, targetlanguage
            self.migrated = []

            self.poedit_settings_import()
            self.lokalize_settings_import()
            self.lokalize_tm_import()

            if self.migrated:
                message = _('Migration was successfully completed') + '\n\n'
                message += _('The following items were migrated:') + '\n\n'
                message += "\n".join([" • %s" % item for item in self.migrated])
                #   (we can mark this ^^^ for translation if somebody asks)
                title = _('Migration completed')
            else:
                message = _("Virtaal was not able to migrate any settings or data")
                title = _('Nothing migrated')
            if self._poedit_tm_exists():
                message += '\n\n' + self.poedit_tm_hint()
            self.main_controller.show_info(title, message)
            logging.debug('Migration plugin executed')

        pan_app.settings.plugin_state[self.internal_name] = "disabled"
        pan_app.settings.write()

    def _has_anything_to_migrate(self):
        """Cheap existence-only checks (no parsing) for whether *any*
        supported source has real data to offer - the gate for
        whether to prompt at all."""
        if self._poedit_config_exists() or self._poedit_tm_exists():
            return True
        if path.exists(self.lokalize_rc):
            return True
        if path.isdir(self.lokalize_tm_dir):
            if any(
                name.endswith('.db') and not name.endswith('-journal.db')
                for name in os.listdir(self.lokalize_tm_dir)
            ):
                return True
        return False

    def _poedit_tm_exists(self):
        return path.isdir(self.poedit_tm_dir) and bool(os.listdir(self.poedit_tm_dir))

    @staticmethod
    def poedit_tm_hint():
        return _(
            # l10n: Use Poedit's own translation of its menu names where it
            # has one; the last one is Virtaal's File menu item.
            "Poedit's translation memory can't be imported directly. In Poedit, "
            "choose Preferences > Translation Memory > Manage… > Export to TMX…, "
            "then add that file in Virtaal with File > Import into Translation Memory…"
        )

    def _poedit_config_exists(self):
        if platform.is_mac:
            config_filename = path.join(self.poedit_dir, 'net.poedit.Poedit.cfg')
        else:
            config_filename = path.join(self.poedit_dir, 'config')
        if path.exists(config_filename):
            return True
        if not platform.is_windows:
            return False
        try:
            import winreg
        except ImportError:
            return False
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Vaclav Slavik\Poedit")
            return True
        except OSError:
            return False

    def poedit_settings_import(self):
        """Attempt to import the settings from Poedit."""
        if platform.is_mac:
            config_filename = path.join(self.poedit_dir, 'net.poedit.Poedit.cfg')
        else:
            config_filename = path.join(self.poedit_dir, 'config')
        if not path.exists(config_filename):
            # winreg (no underscore) is the Python 3 name; the Python 2
            # name was _winreg.
            try:
                import winreg
            except Exception:
                return

            def get_thing(item):
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Vaclav Slavik\Poedit")
                    return winreg.QueryValueEx(key, item)[0]
                except OSError:
                    return None

        else:
            self.poedit_config = ConfigParser.ConfigParser()
            with open(config_filename, encoding='utf-8') as poedit_config_file:
                contents = StringIO('[poedit_headerless_file]\n' + poedit_config_file.read())
            self.poedit_config.read_file(contents)

            def get_thing(item):
                return self.poedit_config.get('poedit_headerless_file', item, fallback=None)

        lastdir = get_thing('last_file_path')
        name = get_thing('translator_name')
        translator_email = get_thing('translator_email')

        if lastdir:
            pan_app.settings.general['lastdir'] = lastdir
        if name:
            pan_app.settings.translator['name'] = name
        if translator_email:
            pan_app.settings.translator['email'] = translator_email

        if lastdir or name or translator_email:
            pan_app.settings.write()
            self.migrated.append(_("Poedit settings"))

    # poedit_tm_import() and kbabel_tm_import() used to live here: both
    # imported Poedit's/KBabel's Translation Memory out of Berkeley DB
    # files via the stdlib `bsddb` module. `bsddb` was Python 2-only and
    # was never ported to Python 3 (no replacement in the standard
    # library), so both were permanently unreachable dead code under
    # Python 3 - the try/except above always left bsddb as None, and both
    # methods bailed out in their very first line whenever that was the
    # case. Removed rather than ported, along with the now-unused
    # `_prepare_db_string()` helper (translate.storage.pypo.extractpoline,
    # which it needed, was itself removed upstream - not worth chasing
    # for code nothing could ever call).

    def lokalize_settings_import(self):
        """Attempt to import the settings from Lokalize."""
        if not path.exists(self.lokalize_rc):
            return

        lokalize_config = ConfigParser.ConfigParser()
        lokalize_config.read(self.lokalize_rc)
        lokalize_identity = dict(lokalize_config.items('Identity'))

        pan_app.settings.translator['name'] = lokalize_identity['authorname']
        pan_app.settings.translator['email'] = lokalize_identity['authoremail']
        pan_app.settings.translator['team'] = lokalize_identity['defaultmailinglist']
        pan_app.settings.general['lastdir'] = path.dirname(
            dict(lokalize_config.items('State'))['project']
        )

        pan_app.settings.write()
        self.migrated.append(_("Lokalize settings"))

    def lokalize_tm_import(self):
        """Attempt to import the Translation Memory used in Lokalize."""
        if not path.isdir(self.lokalize_tm_dir):
            return
        # Each configured TM in Lokalize is a "<name>.db" sqlite file
        # directly under this directory (see src/tm/dbfilesmodel.cpp and
        # the TM_DATABASE_EXTENSION/REMOTETM_DATABASE_EXTENSION macros in
        # src/tm/jobs.h upstream) - "*-journal.db" is sqlite's own journal
        # file, not a database, and "*.remotedb" just points at a remote
        # TM server, with nothing local to read.
        for name in os.listdir(self.lokalize_tm_dir):
            if not name.endswith('.db') or name.endswith('-journal.db'):
                continue
            filename = path.join(self.lokalize_tm_dir, name)
            if path.isfile(filename):
                self.do_lokalize_tm_import(filename)

    def do_lokalize_tm_import(self, filename):
        """Import the given Translation Memory file used by Lokalize."""
        connection = dbapi2.connect(filename)
        cursor = connection.cursor()
        try:
            # tm_config holds one row per (key, value): key 2 is the
            # source language code, key 3 the target - see setConfig()/
            # getConfig() in src/tm/jobs.cpp upstream. Older/emptied
            # databases may not have it, hence the fallback below.
            cursor.execute("SELECT value FROM tm_config WHERE key = 2;")
            row = cursor.fetchone()
            source_lang = row[0] if row else "en"
            cursor.execute("SELECT value FROM tm_config WHERE key = 3;")
            row = cursor.fetchone()
            target_lang = row[0] if row else self.main_controller.lang_controller.target_lang.code

            # Lokalize normalizes its schema across source_strings/
            # target_strings/main (foreign keys), not a single flat table -
            # see initSqliteDb() in src/tm/jobs.cpp upstream.
            cursor.execute("""
                SELECT main.rowid, source_strings.source, target_strings.target
                FROM main
                JOIN source_strings ON main.source = source_strings.id
                JOIN target_strings ON main.target = target_strings.id;
            """)
            # Keyed by Lokalize's row: one source can have several targets.
            units = [
                {"source": source, "target": target, "context": "", "key": str(rowid)}
                for rowid, source, target in cursor
            ]
        finally:
            connection.close()

        if units:
            self.tmdb.sync_store(
                path.realpath(filename), units, source_lang, target_lang, kind=tmdb.STORE_IMPORTED
            )
            self.migrated.append(
                _("Lokalize's Translation Memory: %(database_name)s")
                % {"database_name": path.basename(filename)}
            )
