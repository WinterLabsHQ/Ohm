"""Units — v1 unit table (manual §3.1), Decimal conversions, interval arithmetic.

Pure math, no IR knowledge beyond Quantity. Deterministic: Decimal everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ohm.ir import Quantity

DIMENSIONLESS = "dimensionless"

_D = Decimal
# unit -> (dimension, factor to base unit of that dimension)
UNITS: dict[str, tuple[str, Decimal]] = {
    "V": ("voltage", _D(1)), "mV": ("voltage", _D("0.001")), "kV": ("voltage", _D(1000)),
    "A": ("current", _D(1)), "mA": ("current", _D("0.001")), "uA": ("current", _D("0.000001")),
    "ohm": ("resistance", _D(1)), "kohm": ("resistance", _D(1000)), "Mohm": ("resistance", _D(1000000)),
    "F": ("capacitance", _D(1)), "uF": ("capacitance", _D("0.000001")),
    "nF": ("capacitance", _D("1e-9")), "pF": ("capacitance", _D("1e-12")),
    "H": ("inductance", _D(1)), "uH": ("inductance", _D("0.000001")), "nH": ("inductance", _D("1e-9")),
    "W": ("power", _D(1)), "mW": ("power", _D("0.001")),
    "degC": ("temperature", _D(1)),
    "Hz": ("frequency", _D(1)), "kHz": ("frequency", _D(1000)),
    "MHz": ("frequency", _D(1000000)), "GHz": ("frequency", _D(1000000000)),
    "m": ("length", _D(1)), "mm": ("length", _D("0.001")),
    "um": ("length", _D("0.000001")), "mil": ("length", _D("0.0000254")),
    # time added for closed-form RC constants (P8); ADR-003
    "s": ("time", _D(1)), "ms": ("time", _D("0.001")),
    "us": ("time", _D("0.000001")), "ns": ("time", _D("1e-9")),
}

# dimensional algebra for * and / — only the combos closed-form physics needs;
# unknown combos yield None (unchecked, honest per D3)
_MUL: dict[tuple[str, str], str] = {
    ("voltage", "current"): "power",
    ("current", "resistance"): "voltage",
    ("resistance", "capacitance"): "time",
}
_DIV: dict[tuple[str, str], str] = {
    ("voltage", "current"): "resistance",
    ("voltage", "resistance"): "current",
    ("power", "voltage"): "current",
    ("power", "current"): "voltage",
}


def dim(unit: str) -> str | None:
    entry = UNITS.get(unit)
    return entry[0] if entry else None


def convert(value: Decimal, from_unit: str, to_unit: str) -> Decimal | None:
    """Convert between units of the same dimension; None if incompatible/unknown."""
    f, t = UNITS.get(from_unit), UNITS.get(to_unit)
    if f is None or t is None or f[0] != t[0]:
        return None
    return value * f[1] / t[1]


def mul_dim(a: str | None, b: str | None) -> str | None:
    if a == DIMENSIONLESS:
        return b
    if b == DIMENSIONLESS:
        return a
    if a is None or b is None:
        return None
    return _MUL.get((a, b)) or _MUL.get((b, a))


def div_dim(a: str | None, b: str | None) -> str | None:
    if b == DIMENSIONLESS:
        return a
    if a is None or b is None:
        return None
    if a == b:
        return DIMENSIONLESS
    return _DIV.get((a, b))


def normalize(q: Quantity) -> tuple[Quantity, str | None]:
    """Fill tol_lo/tol_hi (in q's own unit) from tol_raw. Returns (quantity, error)."""
    if dim(q.unit) is None:
        return q, f"unknown unit '{q.unit}'"
    if q.tol_raw is None or q.tol_lo is not None:
        return q, None
    tval, tunit = q.tol_raw
    if tunit is None:  # percent
        delta = q.value * tval / 100
    else:
        converted = convert(tval, tunit, q.unit)
        if converted is None:
            return q, f"tolerance unit '{tunit}' incompatible with '{q.unit}'"
        delta = converted
    return Quantity(q.value, q.unit, q.value - delta, q.value + delta, q.tol_raw), None


@dataclass(frozen=True)
class Interval:
    lo: Decimal
    hi: Decimal

    def __add__(self, o: "Interval") -> "Interval":
        return Interval(self.lo + o.lo, self.hi + o.hi)

    def __sub__(self, o: "Interval") -> "Interval":
        return Interval(self.lo - o.hi, self.hi - o.lo)

    def __mul__(self, o: "Interval") -> "Interval":
        products = [self.lo * o.lo, self.lo * o.hi, self.hi * o.lo, self.hi * o.hi]
        return Interval(min(products), max(products))

    def __truediv__(self, o: "Interval") -> "Interval":
        if o.lo <= 0 <= o.hi:
            raise ZeroDivisionError("interval divisor spans zero")
        quotients = [self.lo / o.lo, self.lo / o.hi, self.hi / o.lo, self.hi / o.hi]
        return Interval(min(quotients), max(quotients))


def interval_of(q: Quantity) -> Interval:
    if q.tol_lo is not None and q.tol_hi is not None:
        return Interval(q.tol_lo, q.tol_hi)
    return Interval(q.value, q.value)
