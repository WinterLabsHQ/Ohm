"""P11: parametric package models — IPC-7351 land-pattern math (ADR-006).

Pure Decimal geometry; the one square root is rounded to 0.01mm before use
(deterministic across machines). No I/O, no acquisition — pin/geometry data is
already frozen in the source by authoring time (N1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from ohm import ir, units

_D = Decimal
_GRID = _D("0.01")


class PkgError(Exception):
    """Invalid generate(...) spec — surfaced as diagnostic S006."""


@dataclass(frozen=True)
class PadSpec:
    id: str
    x: Decimal
    y: Decimal
    w: Decimal  # pad length along X
    h: Decimal  # pad width along Y
    shape: str = "roundrect"  # or "circle" (BGA balls)


@dataclass(frozen=True)
class PackageModel:
    name: str
    pads: tuple[PadSpec, ...]
    courtyard_w: Decimal
    courtyard_h: Decimal


# density -> (toe, heel, side) fillet goals, mm (IPC-7351B)
_FILLETS_GULLWING = {
    "least": (_D("0.15"), _D("0.25"), _D("0.01")),
    "nominal": (_D("0.35"), _D("0.35"), _D("0.03")),
    "most": (_D("0.55"), _D("0.45"), _D("0.05")),
}
_FILLETS_CHIP = {
    "least": (_D("0.1"), _D(0), _D(0)),
    "nominal": (_D("0.2"), _D(0), _D("0.05")),
    "most": (_D("0.4"), _D("0.05"), _D("0.05")),
}

# family -> (default pitch, span min/max, lead len min/max, lead width min/max)
_GULLWING = {
    "SOIC": (_D("1.27"), _D("5.8"), _D("6.2"), _D("0.4"), _D("1.27"), _D("0.31"), _D("0.51")),
    "SSOP": (_D("0.65"), _D("7.4"), _D("8.2"), _D("0.55"), _D("0.95"), _D("0.22"), _D("0.38")),
    "TSSOP": (_D("0.65"), _D("6.2"), _D("6.6"), _D("0.45"), _D("0.75"), _D("0.19"), _D("0.30")),
    "SOT23": (_D("0.95"), _D("2.10"), _D("2.64"), _D("0.25"), _D("0.55"), _D("0.30"), _D("0.51")),
}

# chip size code -> (body len, body width, termination len)
_CHIP = {
    "CHIP0402": (_D("1.0"), _D("0.5"), _D("0.25")),
    "CHIP0603": (_D("1.6"), _D("0.8"), _D("0.35")),
    "CHIP0805": (_D("2.0"), _D("1.25"), _D("0.5")),
    "CHIP1206": (_D("3.2"), _D("1.6"), _D("0.5")),
}

# no-lead families -> (default pitch, terminal length T, number of terminal sides).
# body size is per-part and supplied as body=<n>mm. Lead width b defaults to pitch/2.
# QFN has terminals on 4 sides; DFN/SON (dual/small-outline no-lead) on 2 sides.
_NOLEAD = {
    "QFN": (_D("0.5"), _D("0.4"), 4),
    "DFN": (_D("0.5"), _D("0.4"), 2),
    "SON": (_D("0.5"), _D("0.4"), 2),
}

# density -> (toe, heel, side) fillet goals, mm. QFN nominal reproduces KiCad 7's
# official QFN land pattern exactly (validated vs QFN-16/32/48, P0.5mm).
_FILLETS_NOLEAD = {
    "least": (_D("0.275"), _D("0.05"), _D(0)),
    "nominal": (_D("0.375"), _D("0.1"), _D(0)),
    "most": (_D("0.475"), _D("0.15"), _D("0.05")),
}

# quad gull-wing families -> (default pitch, lead-foot min, lead-foot max). Lead
# span (tip-to-tip) is per-part, supplied as span=<n>mm; lead width b = pitch/2.
_QFP = {
    "QFP": (_D("0.5"), _D("0.3"), _D("0.75")),
}

# density -> (toe, heel, side) fillet goals, mm. QFP nominal matches KiCad 7's
# LQFP-32/48/64 land patterns within tolerance (<=0.05mm on pad center/length).
_FILLETS_QFP = {
    "least": (_D("0.2"), _D("0.15"), _D("0.01")),
    "nominal": (_D("0.3"), _D("0.25"), _D("0.03")),
    "most": (_D("0.4"), _D("0.35"), _D("0.05")),
}
_QFP_SPAN_TOL = _D("0.2")  # span min/max about the nominal lead span

# grid array. BGA = circular balls, LGA = rectangular lands. JEDEC row letters
# skip I, O, Q, S, X, Z (JESD95). Both accept an optional border=<n> for
# perimeter-only / depopulated grids (hollow center).
_GRID_FAMILIES = {"BGA": "circle", "LGA": "roundrect"}
_BGA_ALPHABET = "ABCDEFGHJKLMNPRTUVWY"

# PLCC: J-leaded chip carrier, leads on 4 sides, pin 1 centered on the top edge.
# Always P1.27; body is standardized per pin count, so only pins are needed.
# center = (per_side-1)/2 * pitch + corner gap. Matches KiCad PLCC-20/28 exactly.
_PLCC = ("PLCC",)
_PLCC_PITCH = _D("1.27")
_PLCC_CORNER_GAP = _D("1.5125")
# ponytail: JEDEC defines exactly one rectangular PLCC, so a lookup beats new
# kwargs. (top/bottom count, left/right count) — add explicit per-side kwargs if
# a second rectangular count ever shows up.
_PLCC_SIDES = {32: (7, 9)}
_PLCC_PAD_LEN = _D("1.925")  # radial (J-lead)
_PLCC_PAD_WID = _D("0.7")    # tangential

# Fixed tab packages: three signal leads on one side + a solid tab pad (pad 4).
# (id, x, y, w, h) in mm. The tab is a single rectangle (KiCad paste-subdivides
# it; a solid land is equivalent copper and imports/assembles the same).
def _lead_tab(pitch: str, lx: str, lw: str, lh: str,
              tx: str, tw: str, th: str) -> tuple:
    p = _D(pitch)
    return (
        ("1", _D(lx), -p, _D(lw), _D(lh)),
        ("2", _D(lx), _D(0), _D(lw), _D(lh)),
        ("3", _D(lx), p, _D(lw), _D(lh)),
        ("4", _D(tx), _D(0), _D(tw), _D(th)),
    )


_FIXED = {
    "SOT223": _lead_tab("2.3", "-3.15", "2", "1.5", "3.15", "2", "3.8"),
    "SOT89": _lead_tab("1.5", "-1.95", "1.3", "0.9", "2.3", "3.13", "1.73"),
    "TO252": _lead_tab("2.28", "-5.04", "2.2", "1.2", "1.26", "6.4", "5.8"),
    "TO263": _lead_tab("2.54", "-7.65", "4.6", "1.1", "1.5", "9.4", "10.8"),
}

CATALOG = sorted(set(_GULLWING) | set(_CHIP) | set(_NOLEAD) | set(_QFP)
                 | set(_GRID_FAMILIES) | set(_PLCC) | set(_FIXED))

# SOT-23 fixed pin rows (JEDEC TO-236): (side, y in pitch units); side -1=left +1=right
_SOT23_ROWS = {
    3: [(-1, -1), (-1, 1), (1, 0)],
    5: [(-1, -1), (-1, 0), (-1, 1), (1, 1), (1, -1)],
    6: [(-1, -1), (-1, 0), (-1, 1), (1, 1), (1, 0), (1, -1)],
}


def _mm(value: ir.Value | None, name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, ir.Quantity):
        conv = units.convert(value.value, value.unit, "mm")
        if conv is None:
            raise PkgError(f"'{name}' must be a length, got '{value.unit}'")
        return conv
    if isinstance(value, Decimal):
        return value  # plain number = mm
    raise PkgError(f"'{name}' must be a length quantity")


def _rms_round(cs: Decimal) -> Decimal:
    # √(Cs² + F² + P²), F = P = 0.05 — the one float; rounded to the 0.01mm grid
    val = math.sqrt(float(cs) ** 2 + 0.05**2 + 0.05**2)
    return _D(repr(val)).quantize(_GRID)


def _pad_geometry(family: str, density: str) -> tuple[Decimal, Decimal, Decimal]:
    """(pad length, pad center offset |x|, pad width) from IPC-7351 formulas."""
    _, span_min, span_max, t_min, t_max, b_min, b_max = _GULLWING[family]
    toe, heel, side = _FILLETS_GULLWING[density]
    z_max = span_max + 2 * toe
    s_max, s_min = span_min - 2 * t_max, span_max - 2 * t_min  # heel span bounds
    s_max, s_min = max(s_max, s_min), min(s_max, s_min)
    g_min = s_max - 2 * heel - _rms_round(s_max - s_min)
    length = ((z_max - g_min) / 2).quantize(_GRID)
    center = ((z_max + g_min) / 4).quantize(_GRID)
    width = (b_max + 2 * side).quantize(_GRID)
    return length, center, width


def _int(value: ir.Value | None, name: str) -> int:
    if value is None:
        return 0
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    raise PkgError(f"'{name}' must be a plain integer, got '{value}'")


def _pins(kwargs: dict[str, ir.Value]) -> int:
    return _int(kwargs.get("pins"), "pins")


def _place_quad(pins: int, pitch: Decimal, center: Decimal, radial: Decimal,
                tangential: Decimal) -> list[PadSpec]:
    """N pads on 4 sides (N/4 each), counterclockwise from pin 1 at the top-left.

    `radial` is the pad dimension pointing away from the body, `tangential` the
    one along the side. Pads on left/right sides are radial-in-X; top/bottom are
    radial-in-Y (w/h swapped), so no rotation attribute is needed.
    """
    per_side = pins // 4
    pads: list[PadSpec] = []

    def coord(i: int) -> Decimal:  # tangential offset of the i-th pad on a side (1-based)
        return (_D(2 * i - per_side - 1) / 2) * pitch

    for i in range(1, per_side + 1):  # left, top -> bottom (radial = X)
        pads.append(PadSpec(str(len(pads) + 1), -center, coord(i), radial, tangential))
    for i in range(1, per_side + 1):  # bottom, left -> right (radial = Y)
        pads.append(PadSpec(str(len(pads) + 1), coord(i), center, tangential, radial))
    for i in range(1, per_side + 1):  # right, bottom -> top (radial = X)
        pads.append(PadSpec(str(len(pads) + 1), center, -coord(i), radial, tangential))
    for i in range(1, per_side + 1):  # top, right -> left (radial = Y)
        pads.append(PadSpec(str(len(pads) + 1), -coord(i), -center, tangential, radial))
    return pads


def _place_dual(pins: int, pitch: Decimal, center: Decimal, radial: Decimal,
                tangential: Decimal) -> list[PadSpec]:
    """N pads in two columns (N/2 each), SOIC-style: pin 1 top-left, down the
    left column, then up the right column. `radial` points away from the body."""
    half = pins // 2
    pads: list[PadSpec] = []
    for i in range(1, half + 1):  # left column, top -> bottom
        y = (_D(2 * i - half - 1) / 2) * pitch
        pads.append(PadSpec(str(i), -center, y, radial, tangential))
    for j in range(1, half + 1):  # right column, bottom -> top (counterclockwise)
        y = (_D(half + 1 - 2 * j) / 2) * pitch
        pads.append(PadSpec(str(half + j), center, y, radial, tangential))
    return pads


def _build_nolead(package_type: str, kwargs: dict[str, ir.Value], density: str,
                  name: str | None) -> PackageModel:
    """QFN (4-side) / DFN / SON (2-side) no-lead land pattern (IPC-7351B).

    Lead span L = body edge; terminal length T pulls the heel inward.
    Optional square exposed/thermal pad (pad id N+1) via thermal_pad=<n>mm.
    """
    default_pitch, term, sides = _NOLEAD[package_type]
    pins = _pins(kwargs)
    if sides == 4 and (pins < 4 or pins % 4):
        raise PkgError(f"{package_type} needs a pin count that is a multiple of 4, got {pins}")
    if sides == 2 and (pins < 2 or pins % 2):
        raise PkgError(f"{package_type} needs an even pin count, got {pins}")
    pitch = _mm(kwargs.get("pitch"), "pitch") or default_pitch
    body = _mm(kwargs.get("body"), "body")
    if body is None:
        raise PkgError(f"{package_type} requires a body size, e.g. body=5mm "
                       "(the dimension across the terminal rows)")
    thermal = _mm(kwargs.get("thermal_pad"), "thermal_pad")

    toe, heel, side = _FILLETS_NOLEAD[density]
    b = pitch / 2  # lead width default (b ≈ pitch/2)
    span = body - 2 * term  # inner heel-to-heel span
    z_max = body + 2 * toe
    g_min = span - 2 * heel
    length = ((z_max - g_min) / 2).quantize(_GRID)  # radial pad length
    center = ((z_max + g_min) / 4).quantize(_GRID)   # radial pad center offset
    width = (b + 2 * side).quantize(_GRID)           # tangential pad width

    place = _place_quad if sides == 4 else _place_dual
    pads = place(pins, pitch, center, length, width)
    if thermal is not None:
        pads.append(PadSpec(str(pins + 1), _D(0), _D(0), thermal, thermal))

    cw = ch = (2 * center + length + _D("0.5")).quantize(_GRID)
    return PackageModel(name or f"{package_type}-{pins}", tuple(pads), cw, ch)


def _bga_row(idx: int) -> str:
    """JEDEC BGA row label for a 0-based row index (A, B, …, skipping I/O/Q/S/X/Z)."""
    a = _BGA_ALPHABET
    if idx < len(a):
        return a[idx]
    return a[idx // len(a) - 1] + a[idx % len(a)]


def _build_grid(package_type: str, kwargs: dict[str, ir.Value], name: str | None) -> PackageModel:
    """BGA (circular balls) / LGA (rectangular lands): a rows×cols grid, centered,
    JEDEC row/col naming. `border=<n>` keeps only the outer n rings (perimeter /
    depopulated grids). pad size = pad=<n>mm, else 0.8×ball (BGA default)."""
    shape = _GRID_FAMILIES[package_type]
    rows = _int(kwargs.get("rows"), "rows")
    cols = _int(kwargs.get("cols"), "cols")
    if rows < 1 or cols < 1:
        raise PkgError(f"{package_type} requires rows>=1 and cols>=1")
    pitch = _mm(kwargs.get("pitch"), "pitch")
    if pitch is None:
        raise PkgError(f"{package_type} requires a pitch, e.g. pitch=0.8mm")
    pad = _mm(kwargs.get("pad"), "pad")
    if pad is None:
        ball = _mm(kwargs.get("ball"), "ball")
        if ball is None:
            raise PkgError(f"{package_type} requires a pad or ball diameter, e.g. ball=0.5mm")
        pad = (ball * _D("0.8")).quantize(_GRID)
    border = _int(kwargs.get("border"), "border")  # 0 = full grid
    if border and border * 2 >= min(rows, cols):
        border = 0  # ring covers the whole grid → just a full grid

    pads: list[PadSpec] = []
    for r in range(rows):
        y = (_D(2 * r - rows + 1) / 2) * pitch
        for c in range(cols):
            if border and border <= r < rows - border and border <= c < cols - border:
                continue  # hollow center
            x = (_D(2 * c - cols + 1) / 2) * pitch
            pads.append(PadSpec(f"{_bga_row(r)}{c + 1}", x, y, pad, pad, shape))

    cw = ((cols - 1) * pitch + pad + _D("0.5")).quantize(_GRID)
    ch = ((rows - 1) * pitch + pad + _D("0.5")).quantize(_GRID)
    return PackageModel(name or f"{package_type}-{len(pads)}", tuple(pads), cw, ch)


def _build_plcc(kwargs: dict[str, ir.Value], name: str | None) -> PackageModel:
    """PLCC: J-leaded, four sides, pin 1 centered on the top edge, counterclockwise.

    Body is standardized per pin count (always P1.27), so only `pins` is needed.
    Square counts (20/28/44/52/68/84) put pins//4 on each side; PLCC-32 is the one
    JEDEC rectangular count and comes from _PLCC_SIDES.
    """
    pins = _pins(kwargs)
    if pins < 4 or pins % 4:
        raise PkgError(f"PLCC needs a pin count that is a multiple of 4, got {pins}")
    if pins in _PLCC_SIDES:
        n_tb, n_lr = _PLCC_SIDES[pins]
    else:
        n_tb = n_lr = pins // 4
        if n_tb % 2 == 0:
            raise PkgError("PLCC needs an odd number of pins per side (pin 1 is centered "
                           "on the top edge); standard PLCC = 20, 28, 32, 44, 52, 68, 84")
    pitch = _mm(kwargs.get("pitch"), "pitch") or _PLCC_PITCH
    half_tb, half_lr = (n_tb - 1) // 2, (n_lr - 1) // 2
    # each edge clears the extent of the perpendicular edges, so the top/bottom
    # offset comes from the left/right count and vice versa
    center_y = (_D(half_lr) * pitch + _PLCC_CORNER_GAP).quantize(_GRID)
    center_x = (_D(half_tb) * pitch + _PLCC_CORNER_GAP).quantize(_GRID)
    length, width = _PLCC_PAD_LEN, _PLCC_PAD_WID

    pads: list[PadSpec] = []

    def add(x: Decimal, y: Decimal, radial_y: bool) -> None:
        w, h = (width, length) if radial_y else (length, width)
        pads.append(PadSpec(str(len(pads) + 1), x, y, w, h))

    for k in range(half_tb + 1):  # top edge, center -> left (pin 1 at x=0)
        add(_D(-k) * pitch, -center_y, True)
    for k in range(n_lr):  # left edge, top -> bottom
        add(-center_x, _D(k - half_lr) * pitch, False)
    for k in range(n_tb):  # bottom edge, left -> right
        add(_D(k - half_tb) * pitch, center_y, True)
    for k in range(n_lr):  # right edge, bottom -> top
        add(center_x, _D(half_lr - k) * pitch, False)
    for k in range(half_tb, 0, -1):  # top edge, right half -> back toward center
        add(_D(k) * pitch, -center_y, True)

    cw = (2 * center_x + length + _D("0.5")).quantize(_GRID)
    ch = (2 * center_y + length + _D("0.5")).quantize(_GRID)
    return PackageModel(name or f"PLCC-{pins}", tuple(pads), cw, ch)


def _build_qfp(package_type: str, kwargs: dict[str, ir.Value], density: str,
               name: str | None) -> PackageModel:
    """QFP / quad gull-wing land pattern (IPC-7351 gull-wing math on 4 sides).

    Lead span (tip-to-tip) is per-part, supplied as span=<n>mm. Uses the same
    Zmax/Gmin gull-wing formula as SOIC, placed on four sides.
    """
    default_pitch, t_min, t_max = _QFP[package_type]
    pins = _pins(kwargs)
    if pins < 4 or pins % 4:
        raise PkgError(f"{package_type} needs a pin count that is a multiple of 4, got {pins}")
    pitch = _mm(kwargs.get("pitch"), "pitch") or default_pitch
    span = _mm(kwargs.get("span"), "span")
    if span is None:
        raise PkgError(f"{package_type} requires a lead span, e.g. span=9mm")

    toe, heel, side = _FILLETS_QFP[density]
    span_min, span_max = span - _QFP_SPAN_TOL, span + _QFP_SPAN_TOL
    b_max = pitch / 2  # lead width default (b ≈ pitch/2)
    z_max = span_max + 2 * toe
    s_max, s_min = span_min - 2 * t_max, span_max - 2 * t_min  # heel span bounds
    s_max, s_min = max(s_max, s_min), min(s_max, s_min)
    g_min = s_max - 2 * heel - _rms_round(s_max - s_min)
    length = ((z_max - g_min) / 2).quantize(_GRID)  # radial pad length
    center = ((z_max + g_min) / 4).quantize(_GRID)   # radial pad center offset
    width = (b_max + 2 * side).quantize(_GRID)       # tangential pad width

    pads = _place_quad(pins, pitch, center, length, width)
    cw = ch = (2 * center + length + _D("0.5")).quantize(_GRID)
    return PackageModel(name or f"{package_type}-{pins}", tuple(pads), cw, ch)


def build(package_type: str, kwargs: dict[str, ir.Value], name: str | None = None) -> PackageModel:
    # chip packages have fixed 2-pad geometry: pins/pitch are not accepted there
    if package_type in _CHIP:
        allowed = {"density"}
    elif package_type in _NOLEAD:
        allowed = {"pins", "pitch", "body", "thermal_pad", "density"}
    elif package_type in _QFP:
        allowed = {"pins", "pitch", "span", "density"}
    elif package_type in _GRID_FAMILIES:
        allowed = {"rows", "cols", "pitch", "ball", "pad", "border"}
    elif package_type in _PLCC:
        allowed = {"pins", "pitch"}
    elif package_type in _FIXED:
        allowed = set()
    else:
        allowed = {"pins", "pitch", "density"}
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise PkgError(f"unrecognized generate() argument(s): {', '.join(unknown)} "
                       f"({package_type} accepts: {', '.join(sorted(allowed)) or 'no arguments'})")

    if package_type in _GRID_FAMILIES:
        return _build_grid(package_type, kwargs, name)

    if package_type in _PLCC:
        return _build_plcc(kwargs, name)

    if package_type in _FIXED:
        fixed_pads = tuple(PadSpec(i, x, y, w, h) for i, x, y, w, h in _FIXED[package_type])
        xs = [x for _, x, _, w, _ in _FIXED[package_type] for x in (x - w / 2, x + w / 2)]
        ys = [y for _, _, y, _, h in _FIXED[package_type] for y in (y - h / 2, y + h / 2)]
        cw = (max(xs) - min(xs) + _D("0.5")).quantize(_GRID)
        ch = (max(ys) - min(ys) + _D("0.5")).quantize(_GRID)
        disp = {"SOT223": "SOT-223", "SOT89": "SOT-89", "TO252": "TO-252", "TO263": "TO-263"}
        return PackageModel(name or disp[package_type], fixed_pads, cw, ch)

    density = str(kwargs.get("density", "nominal"))
    if density not in _FILLETS_GULLWING:
        raise PkgError(f"unknown density '{density}' (least, nominal, most)")

    pads: tuple[PadSpec, ...]

    if package_type in _CHIP:
        body_l, body_w, term = _CHIP[package_type]
        toe, heel, side = _FILLETS_CHIP[density]
        length = (term + toe + heel).quantize(_GRID)
        center = ((body_l + toe - term - heel) / 2).quantize(_GRID)
        width = (body_w + 2 * side).quantize(_GRID)
        pads = (PadSpec("1", -center, _D(0), length, width),
                PadSpec("2", center, _D(0), length, width))
        cw = (2 * center + length + _D("0.5")).quantize(_GRID)
        ch = (width + _D("0.5")).quantize(_GRID)
        return PackageModel(name or package_type, pads, cw, ch)

    if package_type in _NOLEAD:
        return _build_nolead(package_type, kwargs, density, name)

    if package_type in _QFP:
        return _build_qfp(package_type, kwargs, density, name)

    if package_type not in _GULLWING:
        raise PkgError(
            f"package type '{package_type}' is not in the v1 catalog: {', '.join(CATALOG)}")

    pins = _pins(kwargs)
    pitch = _mm(kwargs.get("pitch"), "pitch") or _GULLWING[package_type][0]
    length, center, width = _pad_geometry(package_type, density)

    if package_type == "SOT23":
        if pins not in _SOT23_ROWS:
            raise PkgError("SOT23 supports pins=3, 5, or 6")
        pads = tuple(
            PadSpec(str(i + 1), side_ * center, _D(y) * pitch, length, width)
            for i, (side_, y) in enumerate(_SOT23_ROWS[pins])
        )
    else:
        if pins < 4 or pins % 2:
            raise PkgError(f"{package_type} needs an even pin count >= 4, got {pins}")
        half = pins // 2
        columns: list[PadSpec] = []
        for i in range(1, half + 1):  # left column, top -> bottom
            y = (_D(2 * i - half - 1) / 2) * pitch
            columns.append(PadSpec(str(i), -center, y, length, width))
        for j in range(1, half + 1):  # right column, bottom -> top (counterclockwise)
            y = (_D(half + 1 - 2 * j) / 2) * -pitch
            columns.append(PadSpec(str(half + j), center, -y, length, width))
        pads = tuple(columns)

    max_y = max(abs(p.y) for p in pads)
    cw = (2 * center + length + _D("0.5")).quantize(_GRID)
    ch = (2 * max_y + width + _D("0.5")).quantize(_GRID)
    return PackageModel(name or f"{package_type}-{pins}", pads, cw, ch)


def build_from_spec(spec: ir.GenerateSpec, name: str | None = None) -> PackageModel:
    return build(spec.package_type, dict(spec.kwargs), name)


def spec_name(spec: ir.GenerateSpec) -> str:
    """Canonical footprint name of a generate(...) spec (e.g. SOIC-8, CHIP0603).

    Identical specs share one name — and therefore one emitted .kicad_mod —
    across every part that uses them."""
    try:
        return build_from_spec(spec).name
    except PkgError:
        return spec.package_type  # unbuildable specs are S006-gated before emit
