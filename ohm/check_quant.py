"""P9: Tier-Q quantitative checker (manual P9; ADR-004).

Compares declared operating points against ratings, worst-case over tolerance
intervals, on the flat nets (every wiring path lands there). Ratings come
through `ratings_iface` and are frozen in the source — hermetic by construction.

  Q001  net voltage exceeds a pin's vmax
  Q002  declared current draws on a net exceed the driver's imax
  Q003  worst-case (ΔV)²/R power in a 2-pin resistive part exceeds pmax
  Q004  conflicting declared voltages on one net (incl. driver-to-ground shorts)
  Q005  25degC ambient + P_hi x theta_ja exceeds tmax (coarse thermal, ADR-004)
  Q006  a Q-check CLEARED against a rating nothing in the circuit derives (info)

Bidirectional pins count only through their declarations: with `voltage=` they
are declared sources (Q001/Q004), with `current=` they join the Q002 draw sum.

Undeclared values are unchecked, by design (D3) — never claim more.
"""

from __future__ import annotations

from decimal import Decimal

from ohm import ir
from ohm.closedform import quantity_qty
from ohm.diagnostics import Diagnostic, Span
from ohm.lower import first_by_name, lower
from ohm.ratings_iface import DeclaredRatings, RatingsProvider
from ohm.units import Interval

_NO_SPAN = Span(1, 1, 1, 1)
_ZERO = Interval(Decimal(0), Decimal(0))
_AMBIENT = Decimal(25)  # fixed 25 degC ambient for Q005 (ADR-004)
_DRIVER_ROLES = {"power_out", "output"}
# Ratings whose value is commonly set by an external component (a current-limit
# programming resistor, a feedback divider) rather than being intrinsic to the
# part. When one of these is the safe side of a comparison that PASSED, the pass
# is only as good as the literal — and nothing in Ohm relates it to the circuit.
_PROGRAMMABLE = ("imax",)
_SINK_ROLES = {"power_in", "input"}


def _iv(q: ir.Quantity | None) -> Interval | None:
    qty = quantity_qty(q) if q is not None else None
    return qty.iv if qty is not None else None


def _has_programming_part(net, part_comp, pins_of) -> bool:
    """Does the driver on this net expose a passive pin that programs its rating?

    Deliberately coarse: a `passive` pin on a part that also declares `imax` is the
    shape of every current-limit/feedback programming input. Ohm has no way to fold
    the resistor into the rating (that is the `derives` work), so the honest move is
    to say the pass rests on an unbacked number, not to guess a value.
    """
    for node in net.nodes:
        comp = part_comp[node.part_ref]
        has_rating = any(p.imax is not None for p in comp.pins)
        has_prog = any(p.role == "passive" for p in comp.pins)
        if has_rating and has_prog and len(comp.pins) >= 3:
            return True
    return False


def check_quant(design: ir.Design, path: str = "<string>",
                provider: RatingsProvider = DeclaredRatings(),
                low: ir.IRLow | None = None) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    comps = {c.name: c for c in design.components}
    if low is None:  # standalone use; the driver passes its single lower() result
        low, _ = lower(design, path)
    part_comp = {p.ref: comps[p.component] for p in low.parts}
    part_attrs = {p.ref: dict(p.attrs) for p in low.parts}  # incl. instance overrides (R9)
    pins_of = {c.name: first_by_name(c.pins) for c in design.components}
    net_voltage: dict[tuple[str, str], tuple[str, Interval]] = {}  # (part_ref, pin) -> (net, V)

    for net in low.nets:
        span = net.conn_spans[0] if net.conn_spans else _NO_SPAN
        pins = []
        for node in net.nodes:
            comp = part_comp[node.part_ref]
            pin = pins_of[comp.name].get(node.pin)
            if pin is not None:
                pins.append((node, comp, pin))

        # --- declared net voltage (drivers + ground pins) ---
        declared: list[tuple[str, Interval]] = []
        for node, _, pin in pins:
            if (pin.role in _DRIVER_ROLES or pin.role == "bidirectional") \
                    and (v := _iv(pin.voltage)) is not None:
                declared.append((f"{node.part_ref}.{pin.name}", v))
            elif pin.role == "ground":
                declared.append((f"{node.part_ref}.{pin.name}", _ZERO))
        conflict = any(
            a.hi < b.lo or b.hi < a.lo
            for i, (_, a) in enumerate(declared) for _, b in declared[i + 1:]
        )
        if conflict:
            names = ", ".join(f"{n} ({v.lo}V)" if v.lo == v.hi else f"{n} ([{v.lo},{v.hi}]V)"
                              for n, v in declared)
            diags.append(Diagnostic("error", "Q004",
                                    f"net '{net.name}' has conflicting declared voltages: {names}",
                                    span, file=path))
        elif declared:
            hull = Interval(min(v.lo for _, v in declared), max(v.hi for _, v in declared))
            for node, _, pin in pins:
                net_voltage[(node.part_ref, pin.name)] = (net.name, hull)

            # --- Q001: over-voltage against each pin's vmax rating ---
            for node, comp, pin in pins:
                vmax = _iv(provider.ratings(comp, pin, part_attrs[node.part_ref]).vmax)
                if vmax is not None and hull.hi > vmax.lo:
                    diags.append(Diagnostic(
                        "error", "Q001",
                        f"net '{net.name}' reaches {hull.hi}V but pin "
                        f"{node.part_ref}.{pin.name} is rated vmax={vmax.lo}V",
                        pin.span or span, file=path))

        # --- Q002: sum of declared draws vs the driver's imax ---
        drivers = [(node, comp, pin) for node, comp, pin in pins if pin.role in _DRIVER_ROLES]
        draws = [(node, iv) for node, _, pin in pins
                 if (pin.role in _SINK_ROLES or pin.role == "bidirectional")
                 and (iv := _iv(pin.current)) is not None]
        if len(drivers) == 1 and draws:
            node, comp, pin = drivers[0]
            imax = _iv(provider.ratings(comp, pin, part_attrs[node.part_ref]).imax)
            if imax is not None:
                total = draws[0][1]
                for _, iv in draws[1:]:
                    total = total + iv
                if total.hi > imax.lo:
                    diags.append(Diagnostic(
                        "error", "Q002",
                        f"net '{net.name}' draws up to {total.hi}A but driver "
                        f"{node.part_ref}.{pin.name} is rated imax={imax.lo}A",
                        pin.span or span, file=path))
                elif _has_programming_part(net, part_comp, pins_of):
                    # Q006: the check passed, but the number it passed against is a
                    # hand-typed literal on a part whose limit is programmed by an
                    # external resistor. On the board that motivated this, a 10k on
                    # ILIM set ~0.68A while the pin declared imax=1.5A and 1.05A of
                    # load cleared. Saying nothing here reads as coverage.
                    diags.append(Diagnostic(
                        "info", "Q006",
                        f"net '{net.name}' cleared {total.hi}A against imax={imax.lo}A "
                        f"declared on {node.part_ref}.{pin.name}; that rating is a "
                        f"literal, and this part has a passive programming pin whose "
                        f"resistor Ohm cannot relate to it — verify by hand",
                        pin.span or span, file=path))

    # --- Q003: (ΔV)²/R vs pmax for 2-pin resistive parts with known net voltages ---
    for part in low.parts:
        comp = part_comp[part.ref]
        if len(comp.pins) != 2:
            continue
        attrs = dict(part.attrs)
        value = attrs.get("value")
        r = quantity_qty(value) if isinstance(value, ir.Quantity) else None
        if r is None or r.dim != "resistance" or r.iv.lo <= 0:
            continue
        nv1 = net_voltage.get((part.ref, comp.pins[0].name))
        nv2 = net_voltage.get((part.ref, comp.pins[1].name))
        if nv1 is None or nv2 is None:
            continue
        (net1, v1), (net2, v2) = nv1, nv2
        if net1 == net2:
            continue  # both pins on one net: ΔV ≡ 0, not two decorrelated samples
        diff = v1 - v2
        dv_max = max(abs(diff.lo), abs(diff.hi))
        p_hi = dv_max * dv_max / r.iv.lo
        for pin in comp.pins:
            pmax = _iv(provider.ratings(comp, pin, part_attrs[part.ref]).pmax)
            if pmax is not None and p_hi > pmax.lo:
                diags.append(Diagnostic(
                    "error", "Q003",
                    f"part '{part.ref}' ({comp.name}) dissipates up to {p_hi}W "
                    f"(ΔV={dv_max}V across {r.iv.lo}ohm) but pmax={pmax.lo}W",
                    pin.span or _NO_SPAN, file=path))
                break  # one report per part

        # --- Q005: coarse thermal — 25degC ambient + P_hi × theta_ja vs tmax ---
        theta = attrs.get("theta_ja")
        tmax_q = attrs.get("tmax")
        tmax = quantity_qty(tmax_q) if isinstance(tmax_q, ir.Quantity) else None
        if isinstance(theta, Decimal) and tmax is not None and tmax.dim == "temperature":
            t_hi = _AMBIENT + p_hi * theta  # ponytail: ambient fixed at 25degC (ADR-004)
            if t_hi > tmax.iv.lo:
                diags.append(Diagnostic(
                    "error", "Q005",
                    f"part '{part.ref}' ({comp.name}) reaches {t_hi}degC "
                    f"(25degC ambient + {p_hi}W × theta_ja={theta}degC/W) "
                    f"but tmax={tmax.iv.lo}degC",
                    comp.span or _NO_SPAN, file=path))
    return diags
