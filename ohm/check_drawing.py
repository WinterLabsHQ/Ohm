"""D-tier: the DRAWING is legible — checked, not eyeballed.

Every connectivity defect found while building this emitter was caught
automatically: `tests/test_schematic_roundtrip.py` makes KiCad re-read the
schematic and compares it to Ohm's own netlist, so a wire that misses its pin
shows up as a net splitting. Every *geometric* defect was caught by rendering a
PDF and looking at it — text printed through a symbol, a frame drawn around
three other frames, a rail line running past a pin it was meant to touch. That
asymmetry is the bug: a class of defect with no gate is a class of defect the
next person rediscovers.

These are the ones that actually happened, each now an automatic check:

  D001  two pieces of text overlap
  D002  text prints over a symbol body
  D003  two symbols overlap
  D004  two elements share a uuid (undefined behaviour in KiCad)

Pure: (kicad_sch text) -> diagnostics. No I/O, no subprocess (N1). It parses the
emitted file rather than inspecting the emitter's internals, so it holds for any
`.kicad_sch` — including one a human has edited — and cannot drift from what was
actually written to disk.

Advisory by design. A drawing that overlaps is ugly, not wrong: the netlist is
proved separately and exactly. These never block a build, in the same way the
DFM tier never does.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ohm.diagnostics import Diagnostic, Span

_D = Decimal
_NO_SPAN = Span(1, 1, 1, 1)

# Rendered advance per glyph as a fraction of font size, measured off KiCad's
# stroke font at 1.27mm: ~0.8. Deliberately an UNDER-estimate — the check should
# report overlaps that are certainly real, not ones that depend on kerning.
_GLYPH_W = _D("0.8")
_SLOP = _D("0.3")  # ignore grazes this small; a 0.3mm brush is not a collision

_TEXT_RE = re.compile(
    r'\((?:label|hierarchical_label|global_label|text)\s+"((?:[^"\\]|\\.)*)"\s*'
    r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)[^)]*\)'
    r'(?:\s*\(shape[^)]*\))?'
    r'\s*(?:\(fields_autoplaced\)\s*)?'
    r'\(effects\s*\(font\s*\(size\s+([\d.]+)', re.S)
_PROP_RE = re.compile(
    r'\(property\s+"[^"]*"\s+"((?:[^"\\]|\\.)*)"\s*\(at\s+(-?[\d.]+)\s+(-?[\d.]+)'
    r'[^)]*\)\s*\(effects\s*\(font\s*\(size\s+([\d.]+)[^)]*\)\)([^)]*)\)', re.S)
_SYM_RE = re.compile(
    r'\n  \(symbol \(lib_id "([^"]+)"\) \(at (-?[\d.]+) (-?[\d.]+) (\d+)\)')
_UUID_RE = re.compile(r'\(uuid "([0-9a-f-]{36})"\)')
_JUST_RE = re.compile(r"\(justify ([a-z ]+)\)")

Box = tuple[Decimal, Decimal, Decimal, Decimal]


def _overlap(a: Box, b: Box) -> bool:
    return (a[0] + _SLOP < b[2] and a[2] - _SLOP > b[0]
            and a[1] + _SLOP < b[3] and a[3] - _SLOP > b[1])


def _text_box(text: str, x: Decimal, y: Decimal, size: Decimal,
              justify: str) -> Box:
    width = _D(len(text)) * size * _GLYPH_W
    if "right" in justify:
        x1 = x - width
    elif "left" in justify:
        x1 = x
    else:
        x1 = x - width / 2
    return (x1, y - size, x1 + width, y + size / 2)


def _sheet_body(sch: str) -> str:
    """Everything after the lib_symbols block.

    A library DEFINITION carries a reference of "U" and a value naming the part,
    at the symbol's own origin — placed nowhere. Scanning it reported every
    library entry colliding with every other, which is how a check earns being
    ignored.
    """
    start = sch.find("\n  (lib_symbols")
    if start < 0:
        return sch
    depth, i = 0, start + 3
    while i < len(sch):
        if sch[i] == "(":
            depth += 1
        elif sch[i] == ")":
            depth -= 1
            if depth == 0:
                return sch[i:]
        i += 1
    return sch


def _instances(body: str) -> list[str]:
    """Each placed symbol as its own chunk, so a part's ref/value can be
    attributed to it and not counted as colliding with the part it labels."""
    out = []
    for chunk in body.split("\n  (symbol (lib_id ")[1:]:
        # stop at the line that closes this instance, or everything drawn after
        # the last symbol on the sheet gets attributed to it
        end = chunk.find("\n  )")
        out.append("\n  (symbol (lib_id " + (chunk[:end] if end >= 0 else chunk))
    return out


def _texts(sch: str) -> list[tuple[str, Box]]:
    out = []
    for m in _TEXT_RE.finditer(sch):
        text, x, y, size = m.group(1), _D(m.group(2)), _D(m.group(3)), _D(m.group(4))
        tail = sch[m.end():m.end() + 120]
        just = jm.group(1) if (jm := _JUST_RE.search(tail)) else ""
        out.append((text, _text_box(text, x, y, size, just)))
    for m in _PROP_RE.finditer(sch):
        if "hide" in m.group(5):
            continue  # hidden properties are not drawn, so cannot collide
        text, x, y, size = m.group(1), _D(m.group(2)), _D(m.group(3)), _D(m.group(4))
        just = jm.group(1) if (jm := _JUST_RE.search(m.group(5))) else ""
        out.append((text, _text_box(text, x, y, size, just)))
    return out


def _symbols(sch: str) -> list[tuple[str, Box]]:
    """Symbol envelopes. Power ports are excluded: they are 2mm graphics that
    sit deliberately AT a pin, and counting them would make every rail a
    collision."""
    out = []
    for m in _SYM_RE.finditer(sch):
        lib, x, y = m.group(1), _D(m.group(2)), _D(m.group(3))
        if lib.startswith("ohm:PWR_"):
            continue
        hw, hh = _body_half(sch, lib, int(m.group(4)))
        out.append((lib, (x - hw, y - hh, x + hw, y + hh)))
    return out


def _body_half(sch: str, lib: str, rot: int = 0) -> tuple[Decimal, Decimal]:
    """(half-width, half-height) of a symbol's drawn BODY, from its library definition.

    The `_0_1` sub-symbol holds the graphics; `_1_1` holds the pins, which stand
    off beyond the body. Measuring both made every part that legitimately sits
    against its neighbour's pin — a rider, a series chain — read as an overlap.

    Per AXIS, not one square of the larger dimension. A 100-pin MCU is 25mm wide
    and 130mm tall; as a square it claimed a 130x130mm footprint and every label
    in the 50mm of empty sheet either side of it was reported as printing over
    it. That is most of what D002 and D003 used to report on a big sheet, and
    all of it was the envelope rather than the drawing. A turned symbol swaps
    its axes, which is why `rot` is taken — a square never had to care.
    """
    name = lib.split(":", 1)[-1]
    start = sch.find(f'(symbol "{name}_0_1"')
    if start < 0:
        return _D("7.62"), _D("7.62")
    stop = sch.find(f'(symbol "{name}_1_1"', start)
    body = sch[start:stop if stop > 0 else start + 4000]
    xs: list[Decimal] = []
    ys: list[Decimal] = []
    for m in re.finditer(r"\((?:xy|start|end) (-?[\d.]+) (-?[\d.]+)\)", body):
        xs.append(abs(_D(m.group(1))))
        ys.append(abs(_D(m.group(2))))
    if not xs:
        return _D("7.62"), _D("7.62")
    half_w, half_h = max(xs), max(ys)
    return (half_h, half_w) if rot % 180 else (half_w, half_h)


def check_drawing(sch: str, path: str = "<string>") -> list[Diagnostic]:
    """Geometric legibility of an emitted schematic sheet."""
    diags: list[Diagnostic] = []
    body = _sheet_body(sch)

    def report(code: str, message: str, help_text: str) -> None:
        diags.append(Diagnostic("warning", code, message, _NO_SPAN, file=path,
                                help=help_text))

    ids = _UUID_RE.findall(sch)
    dupes = sorted({u for u in ids if ids.count(u) > 1})
    if dupes:
        report("D004", f"{len(dupes)} uuid(s) used by more than one element",
               "KiCad's behaviour on duplicate uuids is undefined; derive each "
               "from something unique to the element, such as its coordinates")

    # a part's own reference and value sit against its body by design; only
    # text belonging to something ELSE can be said to collide with it
    owned: dict[int, list[tuple[str, Box]]] = {}
    boxes: list[tuple[str, Box]] = []
    for index, chunk in enumerate(_instances(body)):
        m = _SYM_RE.search(chunk)
        if m is None:
            continue
        lib, x, y = m.group(1), _D(m.group(2)), _D(m.group(3))
        owned[index] = _texts(chunk)
        if not lib.startswith("ohm:PWR_"):
            hw, hh = _body_half(sch, lib, int(m.group(4)))
            boxes.append((lib, (x - hw, y - hh, x + hw, y + hh)))

    texts = _texts(body)
    clashes = [(a, b) for i, (a, ab) in enumerate(texts)
               for b, bb in texts[i + 1:] if _overlap(ab, bb)]
    if clashes:
        shown = ", ".join(f"'{a}' / '{b}'" for a, b in clashes[:5])
        report("D001", f"{len(clashes)} pair(s) of text overlap: {shown}",
               "a label is bound to its wire and cannot move; give the drawing "
               "more room on that side, or move the text that is free (a "
               "property or a frame title)")

    symbols = boxes
    own_text = {t for group in owned.values() for t, _b in group}
    over_body = [(t, lib) for t, tb in texts for lib, sb in symbols
                 if _overlap(tb, sb) and t not in own_text]
    if over_body:
        shown = ", ".join(f"'{t}' over {lib.split(':')[-1]}" for t, lib in over_body[:5])
        report("D002", f"{len(over_body)} piece(s) of text print over a symbol: "
                       f"{shown}",
               "text over a symbol body hides its pin names — reserve the space "
               "the text needs when sizing that side of the part")

    collisions = [(a, b) for i, (a, ab) in enumerate(symbols)
                  for b, bb in symbols[i + 1:] if _overlap(ab, bb)]
    if collisions:
        shown = ", ".join(f"{a.split(':')[-1]} / {b.split(':')[-1]}"
                          for a, b in collisions[:5])
        report("D003", f"{len(collisions)} pair(s) of symbols overlap: {shown}",
               "two parts drawn on top of each other; the placement reserved "
               "less space than the symbols occupy")

    return diags
