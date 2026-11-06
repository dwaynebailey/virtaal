#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import importlib
import logging
import sys
from types import SimpleNamespace

from virtaal.controllers.plugincontroller import PluginController


def test_enabling_without_ipython_logs_no_error(monkeypatch, caplog):
    # The plug-in is on by default in a source run, where IPython is optional.
    monkeypatch.setitem(sys.modules, 'IPython', None)
    for name in (
        'virtaal.plugins._ipython_console',
        'virtaal.plugins._ipython_console.ipython_view',
    ):
        # Put back (or drop) whatever this test imports.
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name))
        del sys.modules[name]
    pc = PluginController(SimpleNamespace(), classname='Plugin')
    pc.PLUGIN_MODULES = ['virtaal.plugins']
    caplog.set_level(logging.DEBUG)

    assert pc.enable_plugin('_ipython_console') is None
    assert pc.get_plugin_info('_ipython_console')['display_name']

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert importlib.import_module('virtaal.plugins._ipython_console').IPythonView is None
