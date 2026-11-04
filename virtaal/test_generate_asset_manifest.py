#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Tests for devsupport/testing/generate_asset_manifest.py's Apertium
pairs (#4678). No network: the fetches are replaced."""

import importlib.util
import json
import os
import sys
from urllib.error import URLError

import pytest
import yaml

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "devsupport",
    "testing",
    "generate_asset_manifest.py",
)


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("generate_asset_manifest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reply(*pairs, status=200):
    return {
        "responseStatus": status,
        "responseData": [{"sourceLanguage": s, "targetLanguage": t} for s, t in pairs],
        "responseDetails": None,
    }


def _no_published(url):
    raise URLError("offline")


def test_the_pairs_come_sorted_from_the_listpairs_reply(gen):
    def fetch(url):
        assert url == gen.APERTIUM_PAIRS_URL
        return _reply(("spa", "eng"), ("eng", "spa"), ("afr", "nld"), ("eng", "spa"))

    note, section = gen.build_apertium_section(fetch, _no_published)

    assert section == {"pairs": [["afr", "nld"], ["eng", "spa"], ["spa", "eng"]]}
    assert "3 Apertium pair(s)" in note


@pytest.mark.parametrize(
    "fetch",
    [
        pytest.param(_no_published, id="unreachable"),
        pytest.param(lambda url: _reply(("eng", "spa"), status=500), id="non-200"),
        pytest.param(lambda url: _reply(), id="no-pairs"),
        pytest.param(lambda url: {"responseStatus": 200, "responseData": "x"}, id="bad-shape"),
        pytest.param(
            lambda url: {"responseStatus": 200, "responseData": [{"sourceLanguage": "eng"}]},
            id="pair-missing-a-side",
        ),
        pytest.param(lambda url: ["not", "a", "dict"], id="not-a-dict"),
    ],
)
def test_a_failed_apertium_keeps_the_published_pairs(gen, fetch):
    published = {"dictionary": {}, "apertium": {"pairs": [["eng", "spa"]]}}

    note, section = gen.build_apertium_section(fetch, lambda url: published)

    assert section == {"pairs": [["eng", "spa"]]}
    assert "kept the 1 published" in note


def test_a_failed_apertium_with_nothing_published_leaves_the_section_out(gen):
    note, section = gen.build_apertium_section(_no_published, _no_published)

    assert section is None
    assert "left out" in note


def test_unreadable_published_pairs_are_not_kept(gen):
    published = {"apertium": {"pairs": [["eng"]]}}

    note, section = gen.build_apertium_section(_no_published, lambda url: published)

    assert section is None


def _stub_libreoffice(gen, monkeypatch):
    monkeypatch.setattr(
        gen,
        "build_dictionary_and_thesaurus_manifests",
        lambda: ([], {"af_ZA": {"folder": "af_ZA", "files": []}}, {}),
    )
    monkeypatch.setattr(gen, "build_autocorrect_manifest", lambda: ([], {}))


def test_main_writes_the_pairs_into_the_manifest(gen, monkeypatch, tmp_path):
    _stub_libreoffice(gen, monkeypatch)
    monkeypatch.setattr(gen, "fetch_apertium_pairs", lambda url: _reply(("eng", "spa")))
    out = tmp_path / "asset-manifest.yaml"
    monkeypatch.setattr(sys, "argv", ["gen", "--manifest-out", str(out)])

    assert gen.main() == 0

    manifest = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert manifest["apertium"] == {"pairs": [["eng", "spa"]]}
    assert set(manifest) == {"dictionary", "thesaurus", "autocorrect", "apertium"}


def test_main_still_publishes_the_dictionaries_when_apertium_is_down(gen, monkeypatch, tmp_path):
    _stub_libreoffice(gen, monkeypatch)
    monkeypatch.setattr(gen, "fetch_apertium_pairs", _no_published)
    monkeypatch.setattr(gen, "fetch_published_manifest", _no_published)
    out = tmp_path / "asset-manifest.yaml"
    monkeypatch.setattr(sys, "argv", ["gen", "--manifest-out", str(out)])

    assert gen.main() == 0

    manifest = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert "apertium" not in manifest
    assert manifest["dictionary"] == {"af_ZA": {"folder": "af_ZA", "files": []}}


def test_fetch_apertium_pairs_sends_no_identifying_headers(gen, monkeypatch):
    seen = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(_reply(("eng", "spa"))).encode("utf-8")

    def urlopen(request, timeout):
        seen.append(request)
        return _Response()

    monkeypatch.setattr(gen.urllib.request, "urlopen", urlopen)

    assert gen.fetch_apertium_pairs(gen.APERTIUM_PAIRS_URL)["responseStatus"] == 200
    assert seen[0].full_url == "https://www.apertium.org/apy/listPairs"
    assert not seen[0].headers
