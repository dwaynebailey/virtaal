#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Test doubles shared by the TM model tests (outside models/, which
the TM plugin scans for backends)."""

from types import SimpleNamespace


def fake_controller(source_lang='en', target_lang='af'):
    """A TMController stand-in with just enough of main_controller for
    a model's __init__()."""

    def connectable():
        return SimpleNamespace(connect=lambda signal, handler, *a: 1)

    lang_controller = connectable()
    lang_controller.source_lang = SimpleNamespace(code=source_lang)
    lang_controller.target_lang = SimpleNamespace(code=target_lang)
    checks_controller = connectable()
    checks_controller.code = 'standard'
    main_controller = SimpleNamespace(
        lang_controller=lang_controller, checks_controller=checks_controller
    )
    controller = connectable()
    controller.main_controller = main_controller
    return controller


class FakeHTTPClient:
    """Records each request added instead of sending it."""

    def __init__(self):
        self.added = []

    def add(self, request):
        self.added.append(request)
