#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""What can be done with documents and translation bundles
(virtaal.support.documents): opening, adding documents and languages,
splitting by language, merging what comes back, and exporting."""

import logging
import os

from virtaal.common.pan_app import ngettext


def _counted(text, count):
    from virtaal.support.libi18n.numbers import format_number

    return text % {'count': format_number(count)}


class BundleActions:
    """The File > Translation Bundle menu and opening documents, for
    the main controller."""

    def __init__(self, main_controller):
        self.main_controller = main_controller

    @property
    def store_controller(self):
        return self.main_controller.store_controller

    @property
    def view(self):
        return self.main_controller.view

    def bundle(self):
        return self.store_controller.bundle() if self.store_controller else None

    def _languages(self):
        lang = self.main_controller.lang_controller
        source = lang.source_lang.code if lang and lang.source_lang else 'en'
        target = lang.target_lang.code if lang and lang.target_lang else None
        return source, target

    def _error(self, exc, where=''):
        logging.exception('Translation bundle')
        prefix = where + ':\n' if where else ''
        self.main_controller.show_error(
            prefix
            + _("Could not open file.\n\n%(error_message)s\n\nTry opening a different file.")
            % {'error_message': str(exc)}
        )

    def _export_error(self, exc):
        logging.exception('Translation bundle export')
        self.main_controller.show_error(
            _("Could not export file.\n\n%(error_message)s\n\nTry saving to a different location.")
            % {'error_message': str(exc)}
        )

    def _open(self, make, where):
        """Open the bundle C{make()} gives, asking first about unsaved
        changes. @returns: Whether it opened."""
        if not self.main_controller.confirm_leaving():
            return False
        try:
            with self.view.busy(_("Loading '%s'…") % os.path.basename(where)):
                bundle = make()
                self.store_controller.open_bundle(bundle)
                self.main_controller.mode_controller.refresh_mode()
        except Exception as exc:
            self.main_controller.last_open_error = str(exc)
            self._error(exc, where)
            return False
        if bundle.changed_documents():
            self._offer_update()
        elif bundle.read_only():
            self._offer_language()
        return True

    def _offer_update(self):
        # l10n: Shown on opening a translation bundle whose source document
        # was changed since its translation was last brought up to date
        text = _("The original document has changed. Update the translation?")
        # l10n: The button of that notice
        update = _("_Update")
        self.view.show_info_notice(text, action=(update, self.update_changed))

    def update_changed(self):
        """Bring the translations up to the source documents as they are
        now, beside the bundle."""
        bundle = self.bundle()
        if bundle is None:
            return False
        self.store_controller.flush_bundle()
        try:
            for name, path in bundle.changed_documents():
                bundle.update_from(path, name)
        except Exception as exc:
            self._error(exc)
            return False
        self._changed()
        return True

    def update_from_document(self):
        """File > Update from Template on a bundle: a new version of one
        of its documents, for every language."""
        bundle = self.bundle()
        paths = self.view.ask_documents()
        if bundle is None or not paths:
            return False
        self.store_controller.flush_bundle()
        try:
            bundle.update_from(paths[0])
        except Exception as exc:
            self._error(exc, paths[0])
            return False
        self._changed()
        return True

    def open_documents(self, paths):
        """Open documents to translate, as one new bundle."""
        from virtaal.support import documents

        if len(paths) == 1:
            for existing in documents.bundles_for(paths[0]):
                if self.main_controller.show_prompt(
                    msg=_(
                        # l10n: Asked on opening a document already being translated in a
                        # translation bundle beside it; %(bundle)s is its file name
                        "%(bundle)s already holds this document's translation. Open it instead?"
                    )
                    % {'bundle': os.path.basename(existing)}
                ):
                    return self.open_bundle(existing)
                break
        source, target = self._languages()
        return self._open(lambda: documents.from_documents(paths, source, target), paths[0])

    def open_bundle(self, path):
        from virtaal.support.documents import DocumentBundle

        return self._open(lambda: DocumentBundle.open(path), path)

    def _offer_language(self):
        # l10n: Shown on opening a translation bundle with documents but no language
        # to translate them into yet
        text = _("This bundle has no target language yet. Add one to translate its documents.")
        self.view.show_info_notice(text, action=(_("Add _Language…"), self.add_language))

    def _changed(self, language=None):
        try:
            self.store_controller.reopen_bundle(language)
            self.main_controller.mode_controller.refresh_mode()
        except Exception as exc:
            self._error(exc)

    def add_document(self):
        bundle = self.bundle()
        paths = self.view.ask_documents()
        if bundle is None or not paths:
            return False
        self.store_controller.flush_bundle()
        try:
            for path in paths:
                bundle.add_document(path)
        except Exception as exc:
            self._error(exc, path)
            return False
        self._changed()
        return True

    def add_language(self):
        bundle = self.bundle()
        if bundle is None:
            return False
        language = self.view.ask_language(bundle.source_language, self._languages()[1])
        if not language or language in bundle.languages:
            return False
        self.store_controller.flush_bundle()
        bundle.add_language(language)
        self.view.hide_info_notice()
        self._changed(language)
        return True

    def _saved_first(self):
        """Whether the bundle is saved, asking to save it if it isn't."""
        if self.store_controller.is_modified() or self.bundle().path is None:
            return self.main_controller.save_file()
        return True

    def split(self):
        bundle = self.bundle()
        if bundle is None or not self._saved_first():
            return False
        languages = bundle.languages
        if len(languages) > 1:
            languages = self.view.ask_split_languages(languages)
            if not languages:
                return False
        folder = self.view.ask_folder(_("Save"), os.path.dirname(bundle.path))
        if not folder:
            return False
        try:
            written = bundle.split(folder, languages)
            bundle.save()
        except Exception as exc:
            self._export_error(exc)
            return False
        self.view.show_info_notice(
            _counted(
                ngettext(
                    # l10n: After File > Translation Bundle > Split by Language
                    'Saved %(count)s bundle, one for each language.',
                    'Saved %(count)s bundles, one for each language.',
                    len(written),
                ),
                len(written),
            )
        )
        return True

    def merge(self):
        from virtaal.support.documents import DocumentBundle

        bundle = self.bundle()
        if bundle is None or not self._saved_first():
            return False
        merged = 0
        for path in self.view.ask_bundles():
            try:
                returned = DocumentBundle.open_aside(path)
                try:
                    plan = bundle.plan_merge(returned)
                finally:
                    returned.discard()
            except Exception as exc:
                self._error(exc, path)
                continue
            choices = self.view.ask_merge_conflicts(plan.conflicts) if plan.conflicts else {}
            if choices is None:
                continue
            merged += bundle.apply_merge(plan, choices)
        if bundle.modified:
            self._changed()
            self.view.show_info_notice(
                _counted(
                    ngettext(
                        # l10n: After File > Translation Bundle > Merge Returned Bundles
                        'Merged %(count)s translation from the returned bundles.',
                        'Merged %(count)s translations from the returned bundles.',
                        merged,
                    ),
                    merged,
                )
            )
        return merged

    def update_from_refreshed(self):
        """File > Translation Bundle > Update from Refreshed Bundle: take
        in a newer split of the split bundle open, keeping the work in it.
        @returns: Whether it was updated."""
        from virtaal.support.documents import DocumentBundle

        bundle = self.bundle()
        if bundle is None or not bundle.split_from():
            return False
        paths = self.view.ask_bundles()
        if not paths:
            return False
        self.store_controller.flush_bundle()
        try:
            refreshed = DocumentBundle.open_aside(paths[0])
            try:
                plan = bundle.plan_refresh(refreshed)
            finally:
                refreshed.discard()
        except Exception as exc:
            self._error(exc, paths[0])
            return False
        if plan.removed_work and not self._removing_confirmed(plan.removed_work):
            return False
        choices = self.view.ask_refresh_conflicts(plan.conflicts) if plan.conflicts else {}
        if choices is None:
            return False
        taken = bundle.apply_refresh(plan, choices)
        self._changed(plan.language)
        self.view.show_info_notice(
            _counted(
                ngettext(
                    # l10n: After File > Translation Bundle > Update from Refreshed Bundle
                    'Updated the bundle, with %(count)s translation from the refreshed one.',
                    'Updated the bundle, with %(count)s translations from the refreshed one.',
                    taken,
                ),
                taken,
            )
        )
        return True

    def _removing_confirmed(self, names):
        """Whether to update from a refreshed bundle that no longer has
        the documents C{names}, though they have translations."""
        return self.main_controller.show_prompt(
            msg=ngettext(
                # l10n: Asked on updating a translator's bundle from a newer copy that
                # no longer has some of its documents; %(documents)s are their names
                'The refreshed bundle no longer has %(documents)s, and your translations '
                'in it will be removed. Update anyway?',
                'The refreshed bundle no longer has %(documents)s, and your translations '
                'in them will be removed. Update anyway?',
                len(names),
            )
            % {'documents': ', '.join(names)}
        )

    def _language(self):
        """The language the files open are in."""
        bundle = self.bundle()
        language = self.store_controller.stream_language
        if language in bundle.languages:
            return language
        return bundle.languages[0] if bundle.languages else None

    def export_documents(self, all_languages=False):
        """Write the translated documents out: of the language open, or
        of every language into a folder for each."""
        bundle = self.bundle()
        language = self._language()
        if bundle is None or language is None or not self._saved_first():
            return False
        names = [d['name'] for d in bundle.documents()]
        try:
            if len(names) == 1 and (len(bundle.languages) == 1 or not all_languages):
                return self._export_one(bundle, names[0], language)
            folder = self.view.ask_folder(_("Export"), self._export_folder(bundle))
            if not folder:
                return False
            if all_languages:
                paths = bundle.export_all_paths(folder)
            else:
                paths = bundle.export_paths(language, folder)
            if not self._replace_confirmed(bundle, paths):
                return False
            if all_languages:
                bundle.export_all(folder)
            else:
                bundle.export_language(language, folder)
            bundle.save()
        except Exception as exc:
            self._export_error(exc)
            return False
        return True

    def _replace_confirmed(self, bundle, paths):
        """Whether to write over translated documents someone edited
        after they were exported."""
        for path in paths:
            bundle.check_export(path)
        edited = bundle.edited_exports(paths)
        if not edited:
            return True
        return self.main_controller.show_prompt(
            msg=_counted(
                ngettext(
                    # l10n: Asked before exporting translated documents over files of the
                    # same name that are not as Virtaal last exported them
                    '%(count)s file there was changed after it was exported, or is '
                    'another file. Replace it?',
                    '%(count)s files there were changed after they were exported, or are '
                    'other files. Replace them?',
                    len(edited),
                ),
                len(edited),
            )
        )

    def _export_folder(self, bundle):
        originals = [d.get('original') for d in bundle.documents() if d.get('original')]
        if originals:
            return os.path.dirname(originals[0])
        return os.path.dirname(bundle.path) if bundle.path else ''

    def _export_one(self, bundle, name, language):
        stem, extension = os.path.splitext(name)
        suggested = os.path.join(
            self._export_folder(bundle), '%s.%s%s' % (stem, language, extension)
        )
        path = self.view.show_save_dialog(current_filename=suggested, title=_("Export"))
        if not path or not self._replace_confirmed(bundle, [path]):
            return False
        bundle.export_document(name, language, path)
        bundle.save()
        return True

    def export_tipp(self, all_languages=False):
        bundle = self.bundle()
        language = self._language()
        if bundle is None or language is None or not self._saved_first():
            return False
        try:
            if all_languages and len(bundle.languages) > 1:
                folder = self.view.ask_folder(_("Export"), self._export_folder(bundle))
                if folder:
                    bundle.export_tipp_all(folder)
                return bool(folder)
            suggested = os.path.join(
                self._export_folder(bundle), '%s.%s.tipp' % (bundle.name, language)
            )
            path = self.view.show_save_dialog(current_filename=suggested, title=_("Export"))
            if path:
                bundle.export_tipp(language, path)
            return bool(path)
        except Exception as exc:
            self._export_error(exc)
            return False
