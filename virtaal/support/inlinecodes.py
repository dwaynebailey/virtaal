#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""XLIFF and TMX inline codes as the translation memory keeps them.

The local TM keeps a unit's text as translate-toolkit gives it, and its
source and target with their inline codes beside it, written as XLIFF 1.2
inline markup: C{g}, C{x}, C{bx}, C{ex}, C{ph}, C{bpt}, C{ept} and C{it},
with their ids. A unit with codes is matched against an entry with codes
on both texts without the native code that C{ph}, C{bpt}, C{ept} and
C{it} hold (L{plain_text}), so that a TMX C{<bpt>&lt;b&gt;</bpt>} costs
nothing against an XLIFF C{<g>}. A match picked for a unit with
codes is rebuilt with the unit's own codes, mapped by kind and order with
L{abstraction.rebuild}.

Like abstraction, this needs only translate-toolkit, lxml and the
standard library, so the local TM server process can use it.
"""

import functools
import re
from collections import Counter
from xml.sax.saxutils import escape

from lxml import etree
from translate.storage.placeables import StringElem, base
from translate.storage.placeables.xliff import UnknownXML

from virtaal.support import abstraction

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


def _plain(node):
    out = [node.text or '']
    for child in node:
        if _local(child.tag) not in _NATIVE:
            out.append(_plain(child))
        out.append(child.tail or '')
    return ''.join(out)


@functools.lru_cache(maxsize=1024)
def plain_text(markup):
    """markup's text without the native code that ph, bpt, ept and it
    hold, or None if it isn't markup of the kept codes."""
    node = _parse(markup) if markup and isinstance(markup, str) else None
    if node is None or not _well_formed(node):
        return None
    return _plain(node)


def plain_source(unit):
    """The source of unit, of a format with inline codes, without the
    native code of its codes; None for a unit of another format."""
    if not holds_codes(unit):
        return None
    markup = unit_markup(unit)[0]
    text = plain_text(markup) if markup is not None else None
    return text if text is not None else unit.source


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


# Rebuilding a match with a unit's own codes

# How each code is written in the skeleton the mapping works on: as an
# open tag, a close tag or an empty tag, so that abstraction's tag classes
# are the kinds of code.
_OPENING = frozenset(('bx', 'bpt'))
_CLOSING = frozenset(('ex', 'ept'))

# Stands for "<" in a skeleton's text, so only codes are tags there.
_LT = '\ue1ff'

_TAG_MARKERS = (abstraction.TAG_OPEN, abstraction.TAG_CLOSE, abstraction.TAG_EMPTY)


def _token(prefix, kind, number):
    if kind == 'open':
        return '<%s%d>' % (prefix, number)
    if kind == 'close':
        return '</%s%d>' % (prefix, number)
    return '<%s%d/>' % (prefix, number)


def _kind(tag):
    if tag in _OPENING:
        return 'open'
    if tag in _CLOSING:
        return 'close'
    return 'empty'


def _skeleton_text(text):
    return (text or '').replace('<', _LT)


def _markup_skeleton(node, numbers):
    """node's markup as a skeleton: each code a tag named for its kind and
    id, the same in a match's source and target."""
    out = [_skeleton_text(node.text)]
    for child in node:
        tag = _local(child.tag)
        number = numbers.setdefault((tag, child.get('id')), len(numbers))
        if tag == 'g':
            out.append(_token('m', 'open', number))
            out.append(_markup_skeleton(child, numbers))
            out.append(_token('m', 'close', number))
        else:
            out.append(_token('m', _kind(tag), number))
        out.append(_skeleton_text(child.tail))
    return ''.join(out)


class _Unsupported(Exception):
    pass


def _elem_skeleton(elem, codes):
    """The unit's rich tree elem as a skeleton, each code a tag numbered
    for its place in codes."""
    if isinstance(elem, str):
        return _skeleton_text(elem)
    tag = _code_tag(elem)
    if tag is None:
        if isinstance(elem, UnknownXML):
            raise _Unsupported(elem.xml_node.tag)
        return ''.join(_elem_skeleton(sub, codes) for sub in elem.sub)
    number = len(codes)
    codes.append(elem)
    if tag != 'g':
        return _token('u', _kind(tag), number)
    inner = ''.join(_elem_skeleton(sub, codes) for sub in elem.sub)
    return _token('u', 'open', number) + inner + _token('u', 'close', number)


_RESULT_TOKEN = re.compile(r'<(/?)u(\d+)(/?)>')


def _container(code):
    return _code_tag(code) == 'g'


def _place(stack, seen, codes, found):
    """Put the code a result token stands for into the tree; False if the
    tokens don't make a tree of the unit's codes."""
    closing, number, empty = found.group(1), int(found.group(2)), found.group(3)
    if number >= len(codes):
        return False
    code = codes[number]
    if closing and _container(code):
        if len(stack) < 2 or stack[-1][0] != number:
            return False
        stack.pop()
        return True
    key = (number, bool(closing))
    if key in seen:
        return False
    seen.add(key)
    if empty or closing or not _container(code):
        stack[-1][1].sub.append(code.copy())
        return True
    copy = code.copy()
    copy.sub = []
    stack[-1][1].sub.append(copy)
    stack.append((number, copy))
    return True


def _build(skeleton, codes):
    root = StringElem()
    stack = [(None, root)]
    seen = set()
    offset = 0
    for found in _RESULT_TOKEN.finditer(skeleton):
        text = skeleton[offset : found.start()]
        if '<' in text:
            return None
        if text:
            stack[-1][1].sub.append(StringElem(text.replace(_LT, '<')))
        if not _place(stack, seen, codes, found):
            return None
        offset = found.end()
    text = skeleton[offset:]
    if '<' in text or len(stack) != 1:
        return None
    if text:
        root.sub.append(StringElem(text.replace(_LT, '<')))
    return root


def _tag_counts(placeables):
    return Counter(p.marker for p in placeables if p.marker in _TAG_MARKERS)


def _codes_node(markup):
    """The parsed markup, or None if it isn't markup of the kept codes."""
    if not markup or _LT in markup:
        return None
    node = _parse(markup)
    return node if node is not None and _well_formed(node) else None


def rebuild(source_markup, target_markup, unit_source):
    """The match target target_markup (whose source is source_markup) as
    a rich tree holding the unit's own codes from unit_source, its rich
    source string: the nth code of a kind in the match's source stands
    for the nth of that kind in the unit's. None unless the codes map
    cleanly: both sources have as many codes of each kind, and each of
    the target's has its counterpart."""
    if _LT in str(unit_source):
        return None
    source_node, target_node = _codes_node(source_markup), _codes_node(target_markup)
    codes = []
    try:
        to_skeleton = _elem_skeleton(unit_source, codes)
    except _Unsupported:
        return None
    if source_node is None or target_node is None or not codes:
        return None
    numbers = {}
    from_skeleton = _markup_skeleton(source_node, numbers)
    text = _markup_skeleton(target_node, numbers)
    from_placeables = abstraction.placeables(from_skeleton)
    to_placeables = abstraction.placeables(to_skeleton)
    if _tag_counts(from_placeables) != _tag_counts(to_placeables):
        return None
    result = abstraction.rebuild(text, from_placeables, to_placeables)
    return _build(result.text, codes)


def _kinds(markup):
    """How many codes of each kind markup holds, or None if it can't be
    read."""
    if not markup:
        return Counter()
    node = _codes_node(markup)
    if node is None:
        return None
    return _tag_counts(abstraction.placeables(_markup_skeleton(node, {})))


def holds_codes(unit):
    """Whether unit is a unit of a format with inline codes."""
    from translate.storage import tmx, xliff

    return isinstance(unit, (xliff.xliffunit, tmx.tmxunit)) and not unit.hasplural()


def codes_differing(source_markup, unit):
    """How many codes a match whose source is source_markup has that
    unit's source lacks, and the other way round; a pair counts once.
    0 if either is without markup to tell."""
    if not source_markup or not holds_codes(unit):
        return 0
    match_kinds = _kinds(source_markup)
    unit_kinds = _kinds(unit_markup(unit)[0])
    if None in (match_kinds, unit_kinds):
        return 0
    for kinds in (match_kinds, unit_kinds):
        del kinds[abstraction.TAG_CLOSE]
    return (match_kinds - unit_kinds).total() + (unit_kinds - match_kinds).total()


# Showing a match's codes


def _spans(node, offset, out):
    offset += len(node.text or '')
    for child in node:
        start = offset
        index = len(out)
        out.append(None)
        offset = _spans(child, offset, out)
        out[index] = (start, offset, _local(child.tag), child.get('id'))
        offset += len(child.tail or '')
    return offset


@functools.lru_cache(maxsize=256)
def code_spans(markup, text):
    """The codes of markup whose text is text, as (start, end, tag, id) in
    text, in document order: a code holding no text has start == end.
    None unless markup is markup of the kept codes holding exactly text."""
    node = _codes_node(markup)
    if node is None or ''.join(node.itertext()) != text:
        return None
    out = []
    _spans(node, 0, out)
    return tuple(out)


# The opening half of each paired code, which both halves are known by.
_PAIRS = {'bx': 'bx', 'ex': 'bx', 'bpt': 'bpt', 'ept': 'bpt'}


def _code_key(tag, elem):
    """A code by its kind and id: a pair's halves by its rid, if set."""
    attrib = elem.xml_node.attrib if isinstance(elem, UnknownXML) else {}
    code_id = getattr(elem, 'id', None) or attrib.get('id')
    if tag in _PAIRS:
        return _PAIRS[tag], getattr(elem, 'rid', None) or attrib.get('rid') or code_id
    return tag, code_id


def _codes(elem):
    if elem is None:
        return set()
    return {
        _code_key(tag, sub) for sub in elem.depth_first() if (tag := _code_tag(sub)) is not None
    }


def _strip(elem, keys):
    """elem's subtrees with each code in keys as its text (a g's content;
    nothing for a code with native code, which isn't text)."""
    if isinstance(elem, str):
        return [StringElem(elem)]
    tag = _code_tag(elem)
    if tag is not None and _code_key(tag, elem) in keys:
        return [part for sub in elem.sub for part in _strip(sub, keys)] if tag == 'g' else []
    if tag is not None and tag != 'g':
        return [elem.copy()]
    copy = elem.copy()
    copy.sub = [part for sub in elem.sub for part in _strip(sub, keys)]
    return [copy]


def without_codes_of(elem, other):
    """The rich tree elem, or a copy with each code that the rich tree
    other holds too (the same kind and id; both halves of a pair) as its
    text."""
    keys = _codes(elem) & _codes(other)
    if not keys:
        return elem
    root = StringElem(_strip(elem, keys))
    root.prune()
    return root


# TMX export


def _tmx_number(numbers, key):
    if key not in numbers:
        numbers[key] = len(numbers) + 1
    return str(numbers[key])


def _tmx_node(parent, child, numbers):
    tag = _local(child.tag)
    code_id = child.get('id') or ''
    node = None
    if tag == 'g':
        number = _tmx_number(numbers, ('g', code_id))
        etree.SubElement(parent, 'bpt', i=number)
        _write_markup_as_tmx(parent, child, numbers)
        node = etree.SubElement(parent, 'ept', i=number)
    elif tag in ('bpt', 'ept'):
        node = etree.SubElement(
            parent, tag, i=_tmx_number(numbers, ('bpt', child.get('rid') or code_id))
        )
    elif tag in ('bx', 'ex', 'it'):
        closing = tag == 'ex' or child.get('pos') == 'close'
        node = etree.SubElement(parent, 'it', pos='end' if closing else 'begin')
    else:
        node = etree.SubElement(parent, 'ph')
    if code_id.isdigit() and tag not in ('g', 'ept'):
        node.set('x', code_id)
    if tag in _NATIVE:
        node.text = child.text
    return node


def _write_markup_as_tmx(seg, node, numbers):
    _append_text(seg, node.text)
    for child in node:
        _tmx_node(seg, child, numbers)
        _append_text(seg, child.tail)


def write_tmx(seg, markup, numbers):
    """Replace the TMX C{seg} element's content with markup, its codes
    written as TMX C{bpt}, C{ept}, C{ph} and C{it}. numbers pairs codes
    to their TMX C{i} numbers across a translation unit's segments.
    False, leaving seg alone, if markup can't be read."""
    node = _parse(markup)
    if node is None:
        return False
    seg.text = None
    for child in list(seg):
        seg.remove(child)
    _write_markup_as_tmx(seg, node, numbers)
    return True
