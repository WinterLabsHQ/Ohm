"""How wide a track has to be to carry what the design says flows through it.

A netclass called "power" whose track_width equals the default is a label, not
a rule: it names the intent and then routes the rail at signal width. This turns
the intent into a number, and it takes that number from the SAME declared data
`check_quant` uses for Q002 — the `current=` on the pins that draw from the net.
No new hand-typed rating, because a hand-typed rating is what Q006 exists to
distrust.

IPC-2221A, the external-layer curve:

    A_mils2 = (I / (k * dT^b)) ** (1/c)      k = 0.048 external, 0.024 internal
    width_mils = A_mils2 / (thickness_oz * 1.378)      b = 0.44, c = 0.725

IPC-2221 is used rather than the newer IPC-2152 deliberately: 2152 needs the
board's copper area, dielectric and via field to give its (less conservative)
answer, and Ohm knows none of those. 2221 needs only current, copper weight and
temperature rise, and errs wide. A trace that is wider than it had to be costs
area; one that is narrower than it had to be is a fire.

Everything here is Decimal. The width lands on a real board, so it is not going
through a float.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

from ohm import ir
from ohm.closedform import quantity_qty

_D = Decimal

# The assumptions. They are constants because Ohm has nowhere to declare a
# stackup yet, and they are NAMED and reported (emit_kicad_pro emits M013 with
# the numbers it used) rather than buried, because a trace width computed from
# an unstated copper weight is a guess wearing a standard's name.
COPPER_OZ = _D("1")       # 1 oz/ft^2 finished outer copper — the usual default
DELTA_T_C = _D("10")      # 10 degC rise over ambient, the conservative default
_OZ_TO_MILS = _D("1.378")  # 1 oz/ft^2 = 1.378 mils of copper
_K_EXTERNAL = _D("0.048")
_B = _D("0.44")
_C = _D("0.725")
_MILS_PER_MM = _D("39.3701")

# Below this a track is a signal, not a rail, and the fab's minimum governs.
# Ohm never emits a class NARROWER than KiCad's default; sizing is only ever
# allowed to widen, so this can never quietly relax someone's rule.
MIN_TRACK_MM = _D("0.25")


def _pow(base: Decimal, exp: Decimal) -> Decimal:
    """base ** exp for positive base, via Decimal's own ln/exp (no float)."""
    return (base.ln() * exp).exp()


def trace_width_mm(amps: Decimal,
                   copper_oz: Decimal = COPPER_OZ,
                   delta_t_c: Decimal = DELTA_T_C) -> Decimal:
    """IPC-2221A external-layer width for `amps`, in mm, rounded UP to 0.05mm.

    Rounded up, never to nearest: rounding a current-carrying trace down to the
    nearer grid step is the one direction that is not safe.
    """
    if amps <= 0:
        return MIN_TRACK_MM
    area_mils2 = _pow(amps / (_K_EXTERNAL * _pow(delta_t_c, _B)), 1 / _C)
    width_mils = area_mils2 / (copper_oz * _OZ_TO_MILS)
    width_mm = width_mils / _MILS_PER_MM
    stepped = (width_mm / _D("0.05")).quantize(_D(1), rounding=ROUND_CEILING) * _D("0.05")
    return max(stepped, MIN_TRACK_MM)


def net_currents(low: ir.IRLow, design: ir.Design | None) -> dict[str, Decimal]:
    """net name -> the current the DESIGN says it carries, in amps.

    The same summation Q002 checks against: every sink pin on the net that
    declares `current=`, added up. A net where nothing declares a draw is absent
    from the result — not zero. "Nobody said" and "nobody draws" are different
    facts, and only one of them permits a default width.
    """
    if design is None:
        return {}
    comps = {c.name: c for c in design.components}
    part_comp = {p.ref: comps.get(p.component) for p in low.parts}
    sinks = {"power_in", "input", "bidirectional"}

    out: dict[str, Decimal] = {}
    for net in low.nets:
        total = _D(0)
        seen = False
        for node in net.nodes:
            comp = part_comp.get(node.part_ref)
            if comp is None:
                continue
            pin = next((p for p in comp.pins if p.name == node.pin), None)
            if pin is None or pin.role not in sinks or pin.current is None:
                continue
            qty = quantity_qty(pin.current)
            if qty is None:
                continue
            # the WORST case the declaration allows is what sizes copper
            total += qty.iv.hi
            seen = True
        if seen:
            out[net.name] = total
    return out
