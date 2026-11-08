#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Overrides pyinstaller-hooks-contrib's own hook-enchant.py, which
collects the pyenchant wheel's own enchant and dictionaries: the bundle
has build_enchant.ps1's (virtaal.spec)."""

binaries = []
datas = []
excludedimports = ['enchant.tests']
