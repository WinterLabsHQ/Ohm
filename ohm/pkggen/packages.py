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

CATALOG = sorted(_GULLWING | _CHIP)

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


def _pins(kwargs: dict[str, ir.Value]) -> int:
    value = kwargs.get("pins")
    if value is None:
        return 0
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    raise PkgError(f"'pins' must be a plain integer, got '{value}'")


def build(package_type: str, kwargs: dict[str, ir.Value], name: str | None = None) -> PackageModel:
    # chip packages have fixed 2-pad geometry: pins/pitch are not accepted there
    allowed = {"density"} if package_type in _CHIP else {"pins", "pitch", "density"}
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise PkgError(f"unrecognized generate() argument(s): {', '.join(unknown)} "
                       f"({package_type} accepts: {', '.join(sorted(allowed))})")

    density = str(kwargs.get("density", "nominal"))
    if density not in _FILLETS_GULLWING:
        raise PkgError(f"unknown density '{density}' (least, nominal, most)")

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
        pads = []
        for i in range(1, half + 1):  # left column, top -> bottom
            y = (_D(2 * i - half - 1) / 2) * pitch
            pads.append(PadSpec(str(i), -center, y, length, width))
        for j in range(1, half + 1):  # right column, bottom -> top (counterclockwise)
            y = (_D(half + 1 - 2 * j) / 2) * -pitch
            pads.append(PadSpec(str(half + j), center, -y, length, width))
        pads = tuple(pads)

    max_y = max(abs(p.y) for p in pads)
    cw = (2 * center + length + _D("0.5")).quantize(_GRID)
    ch = (2 * max_y + width + _D("0.5")).quantize(_GRID)
    return PackageModel(name or f"{package_type}-{pins}", pads, cw, ch)


def build_from_spec(spec: ir.GenerateSpec, name: str | None = None) -> PackageModel:
    return build(spec.package_type, dict(spec.kwargs), name)
