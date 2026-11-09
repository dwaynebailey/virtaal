#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import io

from lxml import etree

from virtaal.support import xmlinplace


def test_minimal_keeps_what_did_not_change_as_it_was_written():
    old = "<unit id='1'>\r\n <target>Old &amp; <ph id='1'/></target></unit>"
    new = '<unit id="1">\n <target>New &amp; <ph id="1"></ph></target></unit>'

    assert (
        xmlinplace.minimal(old, new)
        == "<unit id='1'>\r\n <target>New &amp; <ph id='1'/></target></unit>"
    )


def test_minimal_takes_attributes_in_any_order():
    old = '<entry id="g1" xmlns:my="urn:my" my:a="1"><t>Old</t></entry>'
    new = '<entry xmlns:my="urn:my" id="g1" my:a="1"><t>New</t></entry>'

    assert (
        xmlinplace.minimal(old, new)
        == '<entry id="g1" xmlns:my="urn:my" my:a="1"><t>New</t></entry>'
    )


def test_a_greater_than_sign_in_an_attribute_value_stays_in_its_tag():
    old = '<pc id="1" dispEnd="&lt;/b>">Old</pc>'
    new = '<pc id="1" dispEnd="&lt;/b&gt;">New</pc>'

    assert xmlinplace.minimal(old, new) == '<pc id="1" dispEnd="&lt;/b>">New</pc>'


class _Store:
    encoding = 'UTF-8'

    def __init__(self, data):
        self.document = etree.fromstring(data).getroottree()


def test_units_in_a_comment_are_not_units_and_ids_can_have_another_name():
    data = (
        b'<doc><!--<seg segmentId="0">x</seg>--><seg segmentId="1"><t>Old</t></seg>'
        b'<![CDATA[<seg segmentId="9">]]><seg segmentId="2"/></doc>'
    )
    store = _Store(data)
    xmlinplace.remember(store, data, 'seg', 'segmentId')

    assert store._inplace_original is not None
    store.document.getroot().find('seg')[0].text = 'New'
    out = io.BytesIO()
    xmlinplace.serialize(store, out, None)
    assert out.getvalue() == data.replace(b'<t>Old</t>', b'<t>New</t>')
