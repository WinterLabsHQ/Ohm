"""The emitted board has an Edge.Cuts profile — a board a fab can quote.

`tests/test_kicad_plot.py` proved an Ohm board is PRODUCIBLE: KiCad runs the
gerber and drill exporters over it to completion and every promised file arrives
whole. That is not the same claim as ORDERABLE. Layer 44 was declared in the
stackup from this emitter's first version and nothing was ever drawn on it —
`grep -rn "Edge\\.Cuts" ohm/` returned the single line of the layer table — and a
gerber package with no profile layer is a package no fab can quote, because
nothing in it says what shape to cut. The plotter does not care; the mill does.

This file is the non-KiCad half of the gate, so it runs on every machine:

  - four `gr_line`s on Edge.Cuts, forming a CLOSED rectangle (every corner is
    the start of exactly one segment and the end of exactly one other);
  - the rectangle ENCLOSES every placed part's courtyard, measured against the
    same `placement()` the footprints are emitted from, with the declared 2mm
    margin actually present on all four sides;
  - two builds of one source emit the same outline, tstamps included;
  - a design with no parts emits no outline and SAYS SO (M012) rather than
    silently shipping a board with an empty profile layer.

The KiCad half — that KiCad itself plots a Profile gerber from this geometry —
lives in `tests/test_kicad_plot.py` and is skipped without `kicad-cli`.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from ohm.check_placement import _extent
from ohm.driver import compile_source
from ohm.emit_kicad import (
    _EDGE_MARGIN,
    _courtyard,
    board_extents,
    emit_kicad_pcb,
    placement,
)

_D = Decimal
EXAMPLES = Path(__file__).parent / "examples"
GOLDEN = Path(__file__).parent / "golden"
DESIGNS = sorted(EXAMPLES.glob("*.ohm")) + sorted(GOLDEN.glob("*.ohm"))

# `(gr_line (start X Y) (end X Y) ... (layer "Edge.Cuts") ...)`, parsed rather
# than matched: the assertions below are about coordinates, and a check that only
# proved the string "Edge.Cuts" appears would pass on the layer table alone.
_EDGE_RE = re.compile(
    r'\(gr_line \(start (-?[\d.]+) (-?[\d.]+)\) \(end (-?[\d.]+) (-?[\d.]+)\)'
    r'[^\n]*\(layer "Edge\.Cuts"\)')


def segments(pcb: str) -> list[tuple[Decimal, ...]]:
    return [tuple(_D(g) for g in m.groups()) for m in _EDGE_RE.finditer(pcb)]


def compile_ok(path: Path):
    result = compile_source(path.read_text(), str(path))
    assert result.ok, [d.machine() for d in result.diagnostics]
    return result


def part_boxes(low):
    """Every placed part as (ref, x1, y1, x2, y2) — its courtyard, axis-aligned.

    The same three calls the emitter makes, so a disagreement here is a real
    disagreement about geometry rather than about how it was recomputed.
    """
    poses = placement(low)
    out = []
    for part in low.parts:
        x, y, rot, _side, _locked = poses[part.ref]
        w, h, _model = _courtyard(part.package)
        hw, hh = _extent(w, h, rot)
        out.append((part.ref, x - hw, y - hh, x + hw, y + hh))
    return out


def test_there_are_designs_to_check():
    """A parametrized gate over an empty corpus is a gate that tests nothing."""
    assert len(DESIGNS) >= 10


@pytest.mark.parametrize("design", DESIGNS, ids=lambda p: p.stem)
def test_every_board_carries_a_closed_edge_cuts_outline(design):
    pcb = emit_kicad_pcb(compile_ok(design).low, design.name)
    segs = segments(pcb)
    assert len(segs) == 4, f"expected a four-sided profile, got {len(segs)}: {segs}"

    # Closed: walk the segments in file order and require each to start where the
    # previous one ended, and the last to close back onto the first. A profile
    # that merely *has* four lines is not a profile — the mill follows the joins,
    # and an outline with a gap is an outline it cannot follow.
    for (_sx, _sy, ex, ey), (nx, ny, _ex2, _ey2) in zip(segs, segs[1:] + segs[:1]):
        assert (ex, ey) == (nx, ny), f"the outline has a gap at ({ex}, {ey}): {segs}"

    # ...and it is a rectangle rather than a degenerate zigzag: exactly two
    # distinct x and two distinct y across all eight endpoints, both with extent.
    xs = {s[0] for s in segs} | {s[2] for s in segs}
    ys = {s[1] for s in segs} | {s[3] for s in segs}
    assert len(xs) == 2 and len(ys) == 2, f"not a rectangle: xs={xs} ys={ys}"
    assert max(xs) > min(xs) and max(ys) > min(ys), "zero-area board"


@pytest.mark.parametrize("design", DESIGNS, ids=lambda p: p.stem)
def test_the_outline_encloses_every_placed_part(design):
    """Measured, not assumed: each part's courtyard against the board's edges.

    The failure message prints the offending part's extents and the board's,
    because a placement defect found through "the outline looks wrong" costs an
    hour and the same defect found through "r_led ends at x=26.245, the board
    ends at 25.9" costs a minute.
    """
    low = compile_ok(design).low
    box = board_extents(low)
    assert box is not None, f"{design.name} has parts but no derivable outline"
    bx1, by1, bx2, by2 = box

    for ref, x1, y1, x2, y2 in part_boxes(low):
        where = (f"{ref} courtyard [{x1}, {y1}]..[{x2}, {y2}] vs board "
                 f"[{bx1}, {by1}]..[{bx2}, {by2}]")
        assert x1 >= bx1 and y1 >= by1 and x2 <= bx2 and y2 <= by2, \
            f"a part is outside the board outline: {where}"


@pytest.mark.parametrize("design", DESIGNS, ids=lambda p: p.stem)
def test_the_margin_is_present_on_all_four_sides(design):
    """The board is the courtyard hull plus `_EDGE_MARGIN`, not more and not less.

    Rounding may only ever spend its 0.01mm quantum making the board LARGER
    (`board_extents` rounds outward on purpose), so each side may exceed the
    margin by less than one quantum and may never fall short of it.
    """
    low = compile_ok(design).low
    box = board_extents(low)
    assert box is not None
    bx1, by1, bx2, by2 = box
    boxes = part_boxes(low)
    quantum = _D("0.01")

    for name, got, want in (
        ("left", min(b[1] for b in boxes) - bx1, _EDGE_MARGIN),
        ("top", min(b[2] for b in boxes) - by1, _EDGE_MARGIN),
        ("right", bx2 - max(b[3] for b in boxes), _EDGE_MARGIN),
        ("bottom", by2 - max(b[4] for b in boxes), _EDGE_MARGIN),
    ):
        assert want <= got < want + quantum, \
            f"{design.name}: {name} margin is {got}mm, expected {want}mm"


def test_a_rotated_fixed_part_is_still_enclosed():
    """A 90-degree part TRANSPOSES its courtyard, and the outline must follow.

    Bounding a rotated part by its unrotated width is the classic way to get an
    outline that clips the one part whose placement the source actually stated,
    so the numbers here are written out rather than recomputed. The SOIC-8
    courtyard is 7.39 x 4.88mm; turned 90 degrees at (50, 40) it occupies
    x 47.56..52.44 and y 36.305..43.695, so with the 2mm margin the board's far
    corner is (54.44, 45.70). Ignore the rotation and the same board comes out
    (55.70, 44.44) — wrong on both axes, and short of the part on the axis along
    which the part is in fact longer.
    """
    pins = "".join("    pin P%d: role=passive, pad=%d\n" % (i, i)
                   for i in range(1, 9))
    src = (
        "component R:\n"
        "    package = generate(CHIP0603)\n"
        "    value = 1kohm\n"
        "    pin P1: role=passive, pad=1\n"
        "    pin P2: role=passive, pad=2\n"
        "component Ic:\n"
        "    package = generate(SOIC, pins=8)\n" + pins +
        "module Board:\n"
        "    u = Ic()\n"
        "    ra = R()\n"
        "    u.P5 ~ ra.P1\n"
        "    constraint fixed(u, 50mm, 40mm, 90)\n"
    )
    result = compile_source(src, "rot.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    low = result.low

    box = board_extents(low)
    assert box is not None
    bx1, by1, bx2, by2 = box
    for ref, x1, y1, x2, y2 in part_boxes(low):
        assert x1 >= bx1 and y1 >= by1 and x2 <= bx2 and y2 <= by2, \
            f"{ref} [{x1}, {y1}]..[{x2}, {y2}] escapes [{bx1}, {by1}]..[{bx2}, {by2}]"
    assert (bx2, by2) == (_D("54.44"), _D("45.70")), (bx2, by2)


def test_two_builds_emit_the_same_outline():
    """Determinism at the outline specifically — the tstamps included.

    `test_lower_emit.py::test_double_build_byte_identical` covers the whole file
    over the golden corpus; this pins the new geometry across the example corpus
    too, so an outline that ever reached for a set iteration order or a clock
    fails here with a diff a reader can see.
    """
    for design in DESIGNS:
        a = emit_kicad_pcb(compile_ok(design).low, design.name)
        b = emit_kicad_pcb(compile_ok(design).low, design.name)
        edges_a = [line for line in a.splitlines() if "Edge.Cuts" in line]
        edges_b = [line for line in b.splitlines() if "Edge.Cuts" in line]
        assert len(edges_a) == 5, edges_a  # the layer declaration plus four lines
        assert "\n".join(edges_a).encode() == "\n".join(edges_b).encode()


def test_a_partless_board_draws_no_outline_and_says_so():
    """The honest failure: no geometry, plus a diagnostic — not a guessed rectangle.

    A wired design CAN have no parts — two interface ports connected to each
    other is one — and that is exactly the case where an emitter which invented a
    default rectangle would put a profile on a gerber package enclosing nothing.
    """
    src = ("interface Pair:\n"
           "    a: signal\n"
           "    b: signal\n"
           "module top:\n"
           "    p = io(Pair)\n"
           "    q = io(Pair)\n"
           "    p ~ q\n")
    result = compile_source(src, "empty.ohm")
    assert result.low is not None and result.low.parts == ()
    assert board_extents(result.low) is None

    pcb = emit_kicad_pcb(result.low, "empty.ohm")
    assert segments(pcb) == []
    # The layer is still DECLARED — that is the stackup, and it is what made this
    # defect invisible for so long. Only the geometry is absent.
    assert '(44 "Edge.Cuts" user)' in pcb

    m012 = [d for d in result.diagnostics if d.code == "M012"]
    assert len(m012) == 1 and m012[0].severity == "warning"
    assert "not orderable" in m012[0].message
