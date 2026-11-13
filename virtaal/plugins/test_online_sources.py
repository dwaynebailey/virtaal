#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Which sources send the text being translated to an outside service (#1301)."""

import importlib

import pytest

# (module, class, online_service, terms_url)
SOURCES = [
    ('virtaal.plugins.tm.models.amagama', 'TMModel', 'amaGama', None),
    ('virtaal.plugins.tm.models.apertium', 'TMModel', 'Apertium', None),
    (
        'virtaal.plugins.tm.models.deepl',
        'TMModel',
        'DeepL',
        'https://www.deepl.com/pro-license',
    ),
    (
        'virtaal.plugins.tm.models.google_translate',
        'TMModel',
        'Google',
        'https://cloud.google.com/terms',
    ),
    (
        'virtaal.plugins.tm.models.microsoft_translator',
        'TMModel',
        'Microsoft',
        'https://learn.microsoft.com/legal/cognitive-services/translator/data-privacy-security',
    ),
    # A server the user runs or picks themselves.
    ('virtaal.plugins.tm.models.moses', 'TMModel', None, None),
    ('virtaal.plugins.tm.models.remotetm', 'TMModel', None, None),
    ('virtaal.plugins.tm.models.localtm', 'TMModel', None, None),
    ('virtaal.plugins.tm.models.currentfile', 'TMModel', None, None),
    ('virtaal.plugins.tm.models.bundle', 'TMModel', None, None),
    # Fetch whole term lists by language, never the text.
    ('virtaal.plugins.terminology.models.pontoon', 'TerminologyModel', None, None),
    ('virtaal.plugins.terminology.models.autoterm', 'TerminologyModel', None, None),
    ('virtaal.plugins.terminology.models.localfile', 'TerminologyModel', None, None),
    # Opens a web page the user asked for; downloads thesaurus files.
    ('virtaal.plugins.lookup.models.weblookup', 'LookupModel', None, None),
    ('virtaal.plugins.lookup.models.thesaurus', 'LookupModel', None, None),
]


@pytest.mark.parametrize(('module', 'classname', 'service', 'terms_url'), SOURCES)
def test_each_source_declares_whether_it_sends_text_online(module, classname, service, terms_url):
    cls = getattr(importlib.import_module(module), classname)

    assert cls.online_service == service
    assert cls.terms_url == terms_url


@pytest.mark.parametrize(('module', 'classname', 'service', 'terms_url'), SOURCES)
def test_terms_links_use_https(module, classname, service, terms_url):
    cls = getattr(importlib.import_module(module), classname)

    assert cls.terms_url is None or cls.terms_url.startswith('https://')
