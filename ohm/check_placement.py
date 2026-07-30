"""S011: two parts cannot occupy the same space.

`ohm/emit_kicad.py` claimed "No courtyard overlaps by construction (disjoint
cells)". That was true of AUTO-placed parts and only of those: a part with a
`constraint fixed(x, y)` sits exactly where the source says, and nothing checked
whether the source said something impossible. On the fmuv2 reference board it
had: a connector, a mounting hole and a fiducial declared on top of each other,
five overlapping pairs, which KiCad's DRC found and Ohm did not.

The check runs over the SAME `placement()` the emitter uses, so a board Ohm
emits and the verdict Ohm gives cannot disagree — checking the source's declared
poses separately would let the two drift.

Severity follows what Ohm actually KNOWS. A `generate(...)` package has real
computed geometry, so an overlap is a fact: error. A string-footprint part has
none — Ohm places it on a 5x5mm placeholder — so an overlap is a conflict of the
placeholder, not a proven collision: warning, saying so. The five overlaps KiCad
DRC found on fmuv2 are all of this second kind, because the board embeds that
same placeholder courtyard for stub footprints; calling them errors would be
Ohm asserting a fact about a footprint it has never read.

Limits, stated rather than hidden: courtyards are compared as axis-aligned
rectangles. A part rotated by 90/270 has its courtyard transposed; any other
angle is compared on the bounding box of the rotated rectangle, which is
conservative (it can only over-report, never miss a real overlap).
"""

from __future__ import annotations

from decimal import Decimal

from ohm import ir
from ohm.diagnostics import Diagnostic, Span

_D = Decimal


def _extent(w: Decimal, h: Decimal, rot: Decimal) -> tuple[Decimal, Decimal]:
    """Axis-aligned half-extent of a w x h courtyard turned by `rot` degrees."""
    angle = rot % 360
    if angle in (_D(90), _D(270)):
        return h / 2, w / 2
    if angle in (_D(0), _D(180)):
        return w / 2, h / 2
    # off-axis: bound it by the diagonal — over-reports, never misses
    diag = (w * w + h * h).sqrt() / 2
    return diag, diag


def check_placement(design: ir.Design, path: str = "<string>",
                    low: ir.IRLow | None = None) -> list[Diagnostic]:
    from ohm.emit_kicad import _courtyard, fixed_poses, placement

    if low is None or not low.parts:
        return []
    fixed = fixed_poses(low)
    if not fixed:
        return []  # every part auto-placed: disjoint cells by construction

    poses = placement(low)
    boxes: list[tuple[str, Decimal, Decimal, Decimal, Decimal, str, bool]] = []
    for part in low.parts:
        pose = poses.get(part.ref)
        if pose is None:
            continue
        x, y, rot, side, _locked = pose
        w, h, model = _courtyard(part.package)
        hw, hh = _extent(w, h, rot)
        boxes.append((part.ref, x, y, hw, hh, side, model is not None))

    diags: list[Diagnostic] = []
    span = Span(1, 1, 1, 1)
    for i, (ref_a, ax, ay, ahw, ahh, a_side, a_real) in enumerate(boxes):
        for ref_b, bx, by, bhw, bhh, b_side, b_real in boxes[i + 1:]:
            if a_side != b_side:
                continue  # opposite faces of the board never collide
            if ref_a not in fixed and ref_b not in fixed:
                continue  # auto cells are disjoint; do not re-litigate them
            if not (abs(ax - bx) < ahw + bhw and abs(ay - by) < ahh + bhh):
                continue
            who = " and ".join(f"'{r}'{' (fixed)' if r in fixed else ''}"
                               for r in (ref_a, ref_b))
            where = f"{ref_a} at ({ax}, {ay}), {ref_b} at ({bx}, {by})"
            if a_real and b_real:
                # both courtyards computed from real generated geometry: the
                # parts genuinely cannot both be there
                diags.append(Diagnostic(
                    "error", "S011",
                    f"courtyards overlap: {who} — parts cannot share board "
                    f"area. {where}",
                    span, file=path,
                    help="move one of the `fixed` placements, or drop the "
                         "constraint and let Ohm place the part"))
            else:
                # A string-footprint part has no geometry in the IR; Ohm places
                # it on a 5x5mm PLACEHOLDER courtyard. Calling that a collision
                # would be asserting a fact about a footprint Ohm has never
                # read — the honest report is the conflict plus the assumption.
                unknown = ", ".join(r for r, real in
                                    ((ref_a, a_real), (ref_b, b_real)) if not real)
                diags.append(Diagnostic(
                    "warning", "S011",
                    f"placement conflict: {who} overlap on Ohm's 5x5mm "
                    f"placeholder courtyard. {where}. The real land pattern of "
                    f"{unknown} is a string footprint Ohm has not read, so this "
                    "is a conflict of the PLACEHOLDER, not a proven collision",
                    span, file=path,
                    help="confirm in KiCad against the real footprint, or give "
                         "the part a generate(...) package so Ohm knows its size"))
    return diags
