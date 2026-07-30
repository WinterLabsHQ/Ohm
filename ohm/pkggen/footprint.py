"""P11: PackageModel → KiCad 7 `.kicad_mod`. Deterministic, byte-identical."""

from __future__ import annotations

from decimal import Decimal

from ohm.pkggen.packages import PackageModel

_FP_VERSION = 20221018  # KiCad 7 board/footprint format

_SILK_INSET = Decimal("0.25")  # body outline, in from the courtyard
_SILK_CLEAR = Decimal("0.2")  # silk-to-pad clearance (KLC F5.1)
_SILK_MIN = Decimal("0.3")  # shorter than this and the stub is just a dot


def silk_segments(model: PackageModel) -> list[tuple[Decimal, Decimal, Decimal, Decimal]]:
    """The body outline, with everything that runs over a pad removed.

    Ohm used to draw a plain rectangle at courtyard − 0.25 mm. On a chip package
    the pads reach nearly to the courtyard, so that rectangle lay across them:
    76 `silk_over_copper` violations on the fmuv2 board, and in fabrication
    silkscreen over bare copper is either clipped by the mask or ends up on the
    solderable surface. KiCad's own R_0603 draws only the two stubs BETWEEN the
    pads for exactly this reason.

    Each of the four edges is cut against the pads' x/y footprints (grown by
    `_SILK_CLEAR`), and only the surviving pieces are drawn. That is package
    agnostic: a two-pad chip keeps its top and bottom stubs, a four-side QFN
    keeps its corners, and a package whose pads cover an edge entirely simply
    has no silk there — which is the honest outcome, not a defect.
    """
    half_w, half_h = model.courtyard_w / 2, model.courtyard_h / 2
    x1, y1 = -(half_w - _SILK_INSET), -(half_h - _SILK_INSET)
    x2, y2 = half_w - _SILK_INSET, half_h - _SILK_INSET

    def _keep(lo: Decimal, hi: Decimal,
              blocked: list[tuple[Decimal, Decimal]]) -> list[tuple[Decimal, Decimal]]:
        """[lo, hi] minus the blocked intervals, in order."""
        out = []
        cursor = lo
        for blo, bhi in sorted(blocked):
            if bhi <= cursor or blo >= hi:
                continue
            if blo > cursor:
                out.append((cursor, min(blo, hi)))
            cursor = max(cursor, bhi)
            if cursor >= hi:
                break
        if cursor < hi:
            out.append((cursor, hi))
        return [(a, b) for a, b in out if b - a >= _SILK_MIN]

    segs: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
    for y in (y1, y2):  # horizontal edges: block where a pad spans this y
        blocked = [(p.x - p.w / 2 - _SILK_CLEAR, p.x + p.w / 2 + _SILK_CLEAR)
                   for p in model.pads
                   if p.y - p.h / 2 - _SILK_CLEAR <= y <= p.y + p.h / 2 + _SILK_CLEAR]
        segs += [(a, y, b, y) for a, b in _keep(x1, x2, blocked)]
    for x in (x1, x2):  # vertical edges
        blocked = [(p.y - p.h / 2 - _SILK_CLEAR, p.y + p.h / 2 + _SILK_CLEAR)
                   for p in model.pads
                   if p.x - p.w / 2 - _SILK_CLEAR <= x <= p.x + p.w / 2 + _SILK_CLEAR]
        segs += [(x, a, x, b) for a, b in _keep(y1, y2, blocked)]
    return segs


def silk_pin1(model: PackageModel) -> tuple[Decimal, Decimal] | None:
    """Pin-1 marker at the courtyard corner in pad 1's quadrant, or None when a
    pad reaches that corner and the dot would sit on copper."""
    if not model.pads:
        return None
    p1 = model.pads[0]
    cx = -model.courtyard_w / 2 if p1.x < 0 else model.courtyard_w / 2
    cy = -model.courtyard_h / 2 if p1.y < 0 else model.courtyard_h / 2
    for pad in model.pads:
        if (abs(cx - pad.x) <= pad.w / 2 + _SILK_CLEAR
                and abs(cy - pad.y) <= pad.h / 2 + _SILK_CLEAR):
            return None
    return (cx, cy)


def emit_footprint(model: PackageModel) -> str:
    half_w, half_h = model.courtyard_w / 2, model.courtyard_h / 2
    ref_y = half_h + Decimal("1.0")
    out = [
        f'(footprint "{model.name}"',
        f"  (version {_FP_VERSION})",
        '  (generator "ohm")',
        '  (layer "F.Cu")',
        "  (attr smd)",
        f'  (fp_text reference "REF**" (at 0 -{ref_y}) (layer "F.SilkS")'
        " (effects (font (size 1 1) (thickness 0.15))))",
        f'  (fp_text value "{model.name}" (at 0 {ref_y}) (layer "F.Fab")'
        " (effects (font (size 1 1) (thickness 0.15))))",
        f"  (fp_rect (start -{half_w} -{half_h}) (end {half_w} {half_h})"
        ' (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))',
    ]
    # silkscreen body outline, clipped clear of every pad (see silk_segments)
    for sx1, sy1, sx2, sy2 in silk_segments(model):
        out.append(
            f"  (fp_line (start {sx1} {sy1}) (end {sx2} {sy2})"
            ' (stroke (width 0.12) (type solid)) (layer "F.SilkS"))'
        )
    if (corner := silk_pin1(model)) is not None:
        cx, cy = corner
        out.append(
            f"  (fp_circle (center {cx} {cy}) (end {cx + Decimal('0.1')} {cy})"
            ' (stroke (width 0.12) (type solid)) (fill none) (layer "F.SilkS"))'
        )
    for pad in model.pads:
        if pad.shape == "circle":
            out.append(
                f'  (pad "{pad.id}" smd circle (at {pad.x} {pad.y}) (size {pad.w} {pad.h})'
                ' (layers "F.Cu" "F.Paste" "F.Mask"))'
            )
        else:
            out.append(
                f'  (pad "{pad.id}" smd roundrect (at {pad.x} {pad.y}) (size {pad.w} {pad.h})'
                ' (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))'
            )
    if model.body is not None:
        # KIPRJMOD keeps the reference project-relative, so the .step ships next to
        # the footprint and resolves wherever the library is installed
        out.append(f'  (model "${{KIPRJMOD}}/{model.name}.step"')
        out.append("    (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0))")
        out.append("  )")
    out.append(")")
    return "\n".join(out) + "\n"
