#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

from types import SimpleNamespace

from .basetmmodel import BaseTMModel, unescape_html_entities


def test_unescape_html_entities():
    """Test the unescaping of &amp; and &#39; type HTML escapes"""
    assert unescape_html_entities("This &amp; That") == "This & That"
    assert unescape_html_entities("&#39;n Vertaler") == "'n Vertaler"
    assert (
        unescape_html_entities("Copyright &copy; 2009 Virtaa&#7741;") == "Copyright © 2009 Virtaaḽ"
    )


def test_unescape_html_entities_keeps_what_isnt_an_entity():
    # Machine translation results can contain text like "AT&T;".
    assert unescape_html_entities("AT&T; and &nosuch;") == "AT&T; and &nosuch;"
    assert unescape_html_entities("&#99999999; &amp;") == "&#99999999; &"


def test_a_new_checker_drops_the_cached_matches():
    # amaGama's matches depend on the project style sent with the query.
    model = SimpleNamespace(checker='gnome', cache={'File': None}, set_checker=lambda checker: None)

    BaseTMModel._set_checker(model, None, 'mozilla')

    assert model.checker == 'mozilla'
    assert model.cache == {}
