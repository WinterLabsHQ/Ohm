"""P11: PackageModel → KiCad 7 `.kicad_mod`. Deterministic, byte-identical."""

from __future__ import annotations

from decimal import Decimal

from ohm.pkggen.packages import PackageModel

_FP_VERSION = 20221018  # KiCad 7 board/footprint format


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
    for pad in model.pads:
        out.append(
            f'  (pad "{pad.id}" smd roundrect (at {pad.x} {pad.y}) (size {pad.w} {pad.h})'
            ' (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))'
        )
    out.append(")")
    return "\n".join(out) + "\n"
