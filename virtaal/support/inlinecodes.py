#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""XLIFF and TMX inline codes as the translation memory keeps them.

The local TM matches on a unit's plain text, as translate-toolkit gives
it, and keeps the unit's source and target with their inline codes beside
it, written as XLIFF 1.2 inline markup: C{g}, C{x}, C{bx}, C{ex}, C{ph},
C{bpt}, C{ept} and C{it}, with their ids.

Like abstraction, this needs only translate-toolkit, lxml and the
standard library, so the local TM server process can use it.
"""

from xml.sax.saxutils import escape

from lxml import etree
from translate.storage.placeables import StringElem, base
from translate.storage.placeables.xliff import UnknownXML

CODES = frozenset(('g', 'x', 'bx', 'ex', 'ph', 'bpt', 'ept', 'it'))
"""The XLIFF 1.2 inline elements kept."""

_NATIVE = frozenset(('ph', 'bpt', 'ept', 'it'))
"""Codes holding their native code as text."""

_EMPTY = frozenset(('x', 'bx', 'ex'))

_ATTRIBUTES = ('id', 'rid', 'pos')

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def _local(tag):
    return etree.QName(tag).localname if isinstance(tag, str) else None


def _code_tag(elem):
    """The XLIFF element name of a rich-tree placeable, or None for text."""
    for cls, tag in (
        (base.G, 'g'),
        (base.X, 'x'),
        (base.Bx, 'bx'),
        (base.Ex, 'ex'),
        (base.Ph, 'ph'),
        (base.Bpt, 'bpt'),
        (base.Ept, 'ept'),
        (base.It, 'it'),
    ):
        if isinstance(elem, cls):
            return tag
    if isinstance(elem, UnknownXML):
        tag = _local(elem.xml_node.tag)
        return tag if tag in _NATIVE else None
    return None


def _append_text(node, text):
    if not text:
        return
    if len(node):
        node[-1].tail = (node[-1].tail or '') + text
    else:
        node.text = (node.text or '') + text


def _code_node(parent, tag, elem):
    node = etree.SubElement(parent, tag)
    attributes = dict(getattr(elem, 'xml_attrib', None) or {})
    if isinstance(elem, UnknownXML):
        attributes.update(elem.xml_node.attrib)
    for name in ('id', 'rid'):
        if getattr(elem, name, None) is not None:
            attributes[name] = getattr(elem, name)
    for name in _ATTRIBUTES:
        if attributes.get(name) is not None:
            node.set(name, str(attributes[name]))
    return node


def _write_elem(node, elem):
    """Write the rich tree elem into node; True if it held a code."""
    if isinstance(elem, str):
        _append_text(node, elem)
        return False
    tag = _code_tag(elem)
    if tag is None:
        found = False
        for sub in elem.sub:
            found = _write_elem(node, sub) or found
        return found
    code = _code_node(node, tag, elem)
    if tag in _NATIVE:
        code.text = str(elem)
    elif tag == 'g':
        for sub in elem.sub:
            _write_elem(code, sub)
    return True


def _serialise(node):
    text = etree.tostring(node, encoding='unicode')
    if text.endswith('/>'):
        return ''
    return text[text.index('>') + 1 : -len('</%s>' % node.tag)]


def from_elem(elem, force=False):
    """The rich tree elem (a unit's rich_source or rich_target string) as
    XLIFF inline markup, or None if it holds no code (unless force)."""
    node = etree.Element('seg')
    if not _write_elem(node, elem) and not force:
        return None
    return _serialise(node)


def _tmx_code(parent, child, ids):
    """Write the TMX inline element child into parent as XLIFF."""
    tag = _local(child.tag)
    if tag in ('bpt', 'ept'):
        code_id = child.get('i') or child.get('x')
    elif tag in ('ph', 'it', 'ut'):
        code_id = child.get('x')
    else:
        # hi, sub or another element: its text only.
        _write_tmx(parent, child, ids)
        return
    if code_id is None:
        ids[0] += 1
        code_id = 'tmx%d' % ids[0]
    node = etree.SubElement(parent, 'ph' if tag == 'ut' else tag, id=code_id)
    if tag == 'it':
        node.set('pos', 'close' if child.get('pos') == 'end' else 'open')
    node.text = ''.join(child.itertext())


def _write_tmx(node, seg, ids):
    _append_text(node, seg.text)
    for child in seg:
        if isinstance(child.tag, str):
            _tmx_code(node, child, ids)
        _append_text(node, child.tail)


def from_tmx_seg(seg):
    """A TMX C{seg} element's text as XLIFF inline markup, or None if it
    holds no code."""
    node = etree.Element('seg')
    _write_tmx(node, seg, [0])
    if not any(_local(child.tag) in CODES for child in node.iter()):
        return None
    return _serialise(node)


def _parse(markup):
    try:
        return etree.fromstring('<seg>%s</seg>' % markup, _PARSER)
    except (etree.XMLSyntaxError, ValueError):
        return None


def _well_formed(node):
    for child in node.iterdescendants():
        tag = _local(child.tag)
        if tag not in CODES:
            return False
        if tag in _EMPTY and (child.text or len(child)):
            return False
        if tag in _NATIVE and len(child):
            return False
    return True


def _flat(text):
    return ' '.join(text.split())


def checked(markup, text):
    """markup if it is inline markup of only the kept codes whose text is
    text (ignoring spacing), else None."""
    if not markup or not isinstance(markup, str):
        return None
    node = _parse(markup)
    if node is None or not _well_formed(node):
        return None
    if _flat(''.join(node.itertext())) != _flat(text or ''):
        return None
    return markup


def pair(source_elem, target_elem):
    """(source markup, target markup) for a unit's rich source and target
    strings, or (None, None) if neither holds a code."""
    source = from_elem(source_elem)
    target = from_elem(target_elem) if target_elem is not None else None
    if source is None and target is None:
        return None, None
    if source is None:
        source = from_elem(source_elem, force=True)
    if target is None:
        target = from_elem(target_elem if target_elem is not None else StringElem(''), force=True)
    return source, target


def unit_markup(unit):
    """(source markup, target markup) of an XLIFF or TMX unit, or
    (None, None) for a unit with no inline codes or of another format."""
    from translate.storage import tmx, xliff

    if isinstance(unit, tmx.tmxunit):
        return _tmx_unit_markup(unit)
    if not isinstance(unit, xliff.xliffunit) or unit.hasplural():
        return None, None
    sources = unit.rich_source
    targets = unit.rich_target if unit.target else []
    if len(sources) != 1 or len(targets) > 1:
        return None, None
    return pair(sources[0], targets[0] if targets else None)


def seg_markup(node):
    """The inline markup of a TMX C{tuv} element's C{seg}, or None."""
    seg = next((child for child in node if child.tag == 'seg'), None)
    return from_tmx_seg(seg) if seg is not None else None


def escape_text(text):
    """text as inline markup with no codes."""
    return escape(text or '')


def text_pair(source_markup, target_markup, source, target):
    """(source markup, target markup), the side with no codes as its
    text, or (None, None) if neither has codes."""
    if source_markup is None and target_markup is None:
        return None, None
    return (
        source_markup if source_markup is not None else escape_text(source),
        target_markup if target_markup is not None else escape_text(target),
    )


def _tmx_unit_markup(unit):
    """The markup of the TMX unit's segments holding its source and
    target."""
    from translate.misc.xml_helpers import getXMLspace

    xml_space = getXMLspace(unit.xmlelement, unit._default_xml_space)
    texts = [(node, unit.getNodeText(node, xml_space)) for node in unit.getlanguageNodes()]
    source = next((node for node, text in texts if text == unit.source), None)
    target = next(
        (node for node, text in texts if text == unit.target and node is not source), None
    )
    if source is None or target is None:
        return None, None
    return text_pair(seg_markup(source), seg_markup(target), unit.source, unit.target)
