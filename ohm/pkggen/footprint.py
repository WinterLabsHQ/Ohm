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
    # silkscreen body outline: courtyard minus 0.25mm margin
    silk_w, silk_h = half_w - Decimal("0.25"), half_h - Decimal("0.25")
    out.append(
        f"  (fp_rect (start -{silk_w} -{silk_h}) (end {silk_w} {silk_h})"
        ' (stroke (width 0.12) (type solid)) (fill none) (layer "F.SilkS"))'
    )
    # pin-1 marker: small circle at the courtyard corner in pad 1's quadrant
    if model.pads:
        p1 = model.pads[0]
        cx = -half_w if p1.x < 0 else half_w
        cy = -half_h if p1.y < 0 else half_h
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
