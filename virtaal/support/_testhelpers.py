#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Test doubles shared by the downloader tests."""


class FakeClient:
    """An HTTPClient stand-in that records each get() call instead of
    making a request, so a test can drive a callback chain step by
    step."""

    def __init__(self):
        self.calls = []

    def get(self, url, callback, error_callback=None, download=False):
        self.calls.append((url, callback, error_callback))

    def last_url(self):
        return self.calls[-1][0]
