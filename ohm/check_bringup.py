"""Bring-up checker (ADR-009) — catches the respin-class mistakes a correct
netlist can still contain, using Ohm's typed knowledge (interfaces, kinds,
roles, declared operating points).

The `expects` mechanism: kinds/interfaces declare requirement predicates over
their members' nets (pullup, decoupling, loadcaps, cap, termination, tie);
this pass verifies them on the flat nets. Waivers (`waive="B001,pullup"` at
instantiation) suppress a finding into an info note — visible, never silent.

  B001  open-drain net without a pull-up (automatic + `expects pullup`)
  B002  `expects tie` unsatisfied (strap/enable/gate pins)
  B003  must_connect / power_in pin left on no net (wired modules only)
  B004  declared net voltage exceeds a pin's tolerant_to (domain damage)
  B005  `expects decoupling` unsatisfied
  B006  `expects cap` unsatisfied; regulator headroom vs declared v_dropout
  B007  `expects termination` unsatisfied
  B008  `expects loadcaps` unsatisfied
  B009  Polarized part reverse-biased vs declared operating points
  B010  finding waived at instantiation (info note)
  B011  unconnected pins with no declared connection intent (info)
        — satisfied wholesale by `unused_pins = open` on the component

Pure, deterministic, O(nets×parts): no simulation, no layout data (N1).
"""

from __future__ import annotations

from decimal import Decimal

from ohm import ir
from ohm.closedform import quantity_qty
from ohm.diagnostics import Diagnostic, Span
from ohm.lower import lower
from ohm.units import Interval

_NO_SPAN = Span(1, 1, 1, 1)
_PUSHPULL = {"output", "power_out"}
_OPENDRAIN = {"open_collector", "open_drain"}
_RAILISH = {"power_in", "power_out"}
# Roles where leaving a pin unconnected is a real choice worth recording. `nc` is
# already an explicit statement, and output/bidirectional pins routinely and
# legitimately go unused on an MCU, so B011 would be pure noise on those.
_STRAPPABLE = {"passive", "input", "power_in", "ground", "open_drain", "open_collector"}

_PATTERN_CODE = {"pullup": "B001", "tie": "B002", "decoupling": "B005",
                 "cap": "B006", "termination": "B007", "loadcaps": "B008"}
_PATTERN_SEV = {"pullup": "warning", "tie": "error", "decoupling": "warning",
                "cap": "warning", "termination": "warning", "loadcaps": "warning"}

# Board/pin parasitic assumed when a crystal declares `cl` but not `c_stray`.
# 3pF is the conventional figure for a 2-layer SMD land plus MCU pin.
_C_STRAY_DEFAULT = Decimal("3e-12")
# Load caps are E-series parts and CL itself carries spread, so the check has to
# be loose enough to accept a sane BOM. +/-30% still separates 10pF from 30pF.
_LOADCAP_TOL_LO = Decimal("0.7")
_LOADCAP_TOL_HI = Decimal("1.3")


def _pf1(d: Decimal) -> str:
    if d == d.to_integral_value():
        return f"{d.quantize(Decimal(1)):f}"
    return f"{d.quantize(Decimal('0.1')):f}"


def _pf(iv: Interval) -> str:
    """Render a capacitance interval in pF — farads print as 1E-11 otherwise."""
    lo, hi = iv.lo * Decimal(10) ** 12, iv.hi * Decimal(10) ** 12
    return f"{_pf1(lo)}pF" if lo == hi else f"{_pf1(lo)}..{_pf1(hi)}pF"


def _iv(q: ir.Quantity | None) -> Interval | None:
    qty = quantity_qty(q) if q is not None else None
    return qty.iv if qty is not None else None


def _in_range(value: ir.Value | None, args: tuple[ir.Value, ...], dim: str) -> bool:
    """value within [args[0], args[1]] (either bound optional), compared in base units."""
    if not isinstance(value, ir.Quantity):
        return False
    v = quantity_qty(value)
    if v is None or v.dim != dim:
        return False
    if args:
        lo = quantity_qty(args[0]) if isinstance(args[0], ir.Quantity) else None
        if lo is not None and v.iv.hi < lo.iv.lo:
            return False
    if len(args) > 1:
        hi = quantity_qty(args[1]) if isinstance(args[1], ir.Quantity) else None
        if hi is not None and v.iv.lo > hi.iv.hi:
            return False
    return True


class _Ctx:
    """Flat-net analysis context shared by every predicate."""

    def __init__(self, design: ir.Design, low: ir.IRLow):
        self.design = design
        self.low = low
        self.comps = {c.name: c for c in design.components}
        self.kinds = {k.name: k for k in design.kinds}
        self.part_comp = {p.ref: self.comps[p.component] for p in low.parts}
        self.part_attrs = {p.ref: dict(p.attrs) for p in low.parts}
        self.pins_of = {c.name: {p.name: p for p in c.pins} for c in design.components}
        self.net_of: dict[tuple[str, str], ir.NetLow] = {}
        for net in low.nets:
            for node in net.nodes:
                self.net_of[(node.part_ref, node.pin)] = net
        # port-member roles from every interface (used to see rails through ports)
        self.member_role: dict[str, str] = {}
        for iface in design.interfaces:
            for name, _, role in iface.members:
                if role:
                    self.member_role.setdefault(name, role)
        # part_ref -> (waivers, wired?) + port path -> interface, via one walk
        self.waivers: dict[str, tuple[str, ...]] = {}
        self.wired: dict[str, bool] = {}
        self.port_iface: dict[str, str] = {}
        self.port_owner: dict[str, str] = {}  # port path -> owning instance path ("" = top)
        mods = {m.name: m for m in design.modules}
        top = mods.get(design.top) if design.top else None
        self.inst_waivers_by_path: dict[str, tuple[str, ...]] = {}
        self.inst_attrs_by_path: dict[str, dict[str, ir.Value]] = {}

        def walk(module: ir.Module, prefix: str, stack: frozenset[str]) -> None:
            has_conn = bool(module.connections)
            for port in module.ports:
                self.port_iface[f"{prefix}{port.name}"] = port.interface
                self.port_owner[f"{prefix}{port.name}"] = prefix.rstrip(".")
            for inst in module.instances:
                ref = f"{prefix}{inst.name}"
                if inst.def_ref in self.comps:
                    self.waivers[ref] = inst.waivers
                    self.wired[ref] = has_conn
                elif inst.def_ref in mods and inst.def_ref not in stack:
                    self.inst_waivers_by_path[ref] = inst.waivers
                    self.inst_attrs_by_path[ref] = dict(inst.overrides)
                    walk(mods[inst.def_ref], f"{ref}.", stack | {inst.def_ref})

        if top is not None:
            walk(top, "", frozenset({top.name}))
        # declared voltage hull per net (drivers + grounds), as in check_quant
        self.net_volt: dict[str, Interval] = {}
        for net in low.nets:
            declared: list[Interval] = []
            for node in net.nodes:
                pin = self.pins_of[self.part_comp[node.part_ref].name].get(node.pin)
                if pin is None:
                    continue
                if pin.role in ("power_out", "output", "bidirectional") and (v := _iv(pin.voltage)):
                    declared.append(v)
                elif pin.role == "ground":
                    declared.append(Interval(Decimal(0), Decimal(0)))
            if declared:
                self.net_volt[net.name] = Interval(min(v.lo for v in declared),
                                                   max(v.hi for v in declared))

    # ---------- net classification ----------
    def _pins_on(self, net: ir.NetLow):
        for node in net.nodes:
            pin = self.pins_of[self.part_comp[node.part_ref].name].get(node.pin)
            if pin is not None:
                yield node, pin

    def has_pushpull(self, net: ir.NetLow) -> bool:
        return any(p.role in _PUSHPULL for _, p in self._pins_on(net))

    def is_rail(self, net: ir.NetLow) -> bool:
        if net.name in self.net_volt:
            return True
        if any(p.role in _RAILISH for _, p in self._pins_on(net)):
            return True
        # ponytail: port label 'pwr.v' → member 'v' role via interface defs
        return any(self.member_role.get(lbl.rsplit(".", 1)[1]) in _RAILISH
                   for lbl in net.ports)

    def is_ground(self, net: ir.NetLow) -> bool:
        if any(p.role == "ground" for _, p in self._pins_on(net)):
            return True
        return any(self.member_role.get(lbl.rsplit(".", 1)[1]) == "ground"
                   for lbl in net.ports)

    def _two_pin_dim_parts(self, net: ir.NetLow, dim: str):
        """(part_ref, value, other_net) for 2-pin parts with a `value` of `dim` on net."""
        for node in net.nodes:
            comp = self.part_comp[node.part_ref]
            if len(comp.pins) != 2:
                continue
            value = self.part_attrs[node.part_ref].get("value")
            q = quantity_qty(value) if isinstance(value, ir.Quantity) else None
            if q is None or q.dim != dim:
                continue
            other_pin = next(p.name for p in comp.pins if p.name != node.pin)
            other = self.net_of.get((node.part_ref, other_pin))
            yield node.part_ref, value, other

    def resistor_to_rail(self, net: ir.NetLow, args=()) -> bool:
        return any(other is not None and self.is_rail(other)
                   and (not args or _in_range(value, args, "resistance"))
                   for _, value, other in self._two_pin_dim_parts(net, "resistance"))

    def resistor_to_gnd_or_rail(self, net: ir.NetLow) -> bool:
        return any(other is not None and (self.is_rail(other) or self.is_ground(other))
                   for _, value, other in self._two_pin_dim_parts(net, "resistance"))

    def cap_to_ground(self, net: ir.NetLow, args=()) -> bool:
        return any(other is not None and self.is_ground(other)
                   and (not args or _in_range(value, args, "capacitance"))
                   for _, value, other in self._two_pin_dim_parts(net, "capacitance"))

    def cap_sum_to_ground(self, net: ir.NetLow) -> Interval | None:
        """Total capacitance from `net` to ground. Caps in parallel add, so a
        presence test ("is there *a* cap?") cannot answer what the oscillator
        actually sees — this can."""
        total: Interval | None = None
        for _, value, other in self._two_pin_dim_parts(net, "capacitance"):
            if other is None or not self.is_ground(other):
                continue
            iv = _iv(value) if isinstance(value, ir.Quantity) else None
            if iv is not None:
                total = iv if total is None else total + iv
        return total

    def resistor_between(self, net_a: ir.NetLow, net_b: ir.NetLow, args=()) -> bool:
        return any(other is not None and other.name == net_b.name
                   and (not args or _in_range(value, args, "resistance"))
                   for _, value, other in self._two_pin_dim_parts(net_a, "resistance"))

    def tie_satisfied(self, net: ir.NetLow) -> bool:
        return (self.has_pushpull(net) or self.is_rail(net) or self.is_ground(net)
                or self.resistor_to_gnd_or_rail(net))

    def kind_contract(self, comp: ir.ComponentDef) -> list[ir.KindDef]:
        out: list[ir.KindDef] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            kd = self.kinds.get(name)
            if kd is None or name in seen:
                return
            seen.add(name)
            out.append(kd)
            for parent in kd.parents:
                add(parent)

        for kname in comp.kinds:
            add(kname)
        return out


def check_bringup(design: ir.Design, path: str = "<string>",
                  low: ir.IRLow | None = None) -> list[Diagnostic]:
    if low is None:
        low, _ = lower(design, path)
    ctx = _Ctx(design, low)
    diags: list[Diagnostic] = []

    def emit(code: str, sev: str, msg: str, span: Span | None,
             waivers: tuple[str, ...] = (), pattern: str = "") -> None:
        if code in waivers or (pattern and pattern in waivers):
            diags.append(Diagnostic("info", "B010", f"waived {code}: {msg}",
                                    span or _NO_SPAN, file=path))
        else:
            diags.append(Diagnostic(sev, code, msg, span or _NO_SPAN, file=path))

    # ---------- expectation predicates ----------
    def run_pattern(exp: ir.ExpectationDef, nets: dict[str, ir.NetLow],
                    owner: str, span: Span | None, waivers: tuple[str, ...]) -> None:
        code = _PATTERN_CODE[exp.pattern]
        sev = _PATTERN_SEV[exp.pattern]
        if exp.pattern == "termination":
            names = [m for m in exp.members if m in nets]
            if len(names) == 2:
                a, b = nets[names[0]], nets[names[1]]
                if not ctx.resistor_between(a, b, exp.args):
                    emit(code, sev, f"{owner}: expected termination resistor across "
                         f"'{names[0]}'/'{names[1]}' — none found", span, waivers, exp.pattern)
            return
        for member, net in nets.items():
            if exp.members and member not in exp.members:
                continue
            ok = {"pullup": lambda: ctx.resistor_to_rail(net, exp.args),
                  "decoupling": lambda: ctx.cap_to_ground(net, exp.args),
                  "loadcaps": lambda: ctx.cap_to_ground(net, exp.args),
                  "cap": lambda: ctx.cap_to_ground(net, exp.args),
                  "tie": lambda: ctx.tie_satisfied(net),
                  }[exp.pattern]()
            if not ok:
                what = {"pullup": "a pull-up resistor to a rail",
                        "decoupling": "a decoupling capacitor to ground",
                        "loadcaps": "a load capacitor to ground",
                        "cap": "a capacitor to ground",
                        "tie": "a defined level (rail, ground, resistor, or driver)"}[exp.pattern]
                emit(code, sev, f"{owner}: net '{net.name}' ('{member}') needs {what}",
                     span, waivers, exp.pattern)

    def loadcaps_value(part_ref: str, attrs: dict[str, ir.Value],
                       nets: dict[str, ir.NetLow], owner: str,
                       span: Span | None, waivers: tuple[str, ...]) -> None:
        """B008 second half: are the load caps the RIGHT SIZE for this crystal?

        `expects loadcaps(1pF, 100pF)` only asks whether *a* capacitor is present
        in a 100x window — every value in it passes, so a 10pF cap on an 18pF
        crystal reads as green. The relation it should be testing is not a matter
        of taste, it is the oscillator's:

            C_each = 2 x (CL - C_stray)

        with C_stray the board/pin parasitic (~3pF typical, overridable per part
        via a `c_stray` attr). Caps in parallel add, so this compares the SUM on
        each terminal, not the first one it finds.
        """
        cl_q, stray_q = attrs.get("cl"), attrs.get("c_stray")
        cl = _iv(cl_q) if isinstance(cl_q, ir.Quantity) else None
        if cl is None:
            return
        stray = _iv(stray_q) if isinstance(stray_q, ir.Quantity) \
            else Interval(_C_STRAY_DEFAULT, _C_STRAY_DEFAULT)
        if stray is None:
            return
        target = Interval(Decimal(2), Decimal(2)) * (cl - stray)
        if target.hi <= 0:
            return  # CL at or below stray: no discrete caps are correct, stay quiet
        lo, hi = target.lo * _LOADCAP_TOL_LO, target.hi * _LOADCAP_TOL_HI
        for member, net in nets.items():
            if member not in ("a", "b"):
                continue
            total = ctx.cap_sum_to_ground(net)
            if total is None or (total.hi >= lo and total.lo <= hi):
                continue
            emit("B008", "warning",
                 f"{owner}: net '{net.name}' ('{member}') has {_pf(total)} of load "
                 f"capacitance to ground, but cl={_pf(cl)} with c_stray={_pf(stray)} "
                 f"needs about {_pf(target)} (2 x (cl - c_stray)) — the oscillator "
                 f"will run off-frequency or fail to start",
                 span, waivers, "loadcaps")

    def when_ok(exp: ir.ExpectationDef, attrs: dict[str, ir.Value]) -> bool:
        return exp.when is None or bool(attrs.get(exp.when))

    # ---- kind expectations (per part, members bound via is=) ----
    for part in low.parts:
        comp = ctx.part_comp[part.ref]
        attrs = ctx.part_attrs[part.ref]
        waivers = ctx.waivers.get(part.ref, ())
        contract = ctx.kind_contract(comp)
        member_nets = {p.satisfies: ctx.net_of[(part.ref, p.name)]
                       for p in comp.pins
                       if p.satisfies and (part.ref, p.name) in ctx.net_of}
        netless_members = {p.satisfies for p in comp.pins
                           if p.satisfies and (part.ref, p.name) not in ctx.net_of}
        for kd in contract:
            for exp in kd.expects:
                if not when_ok(exp, attrs):
                    continue
                if member_nets:
                    run_pattern(exp, member_nets, f"'{part.ref}' ({kd.name})",
                                comp.span, waivers)
                    if exp.pattern == "loadcaps":
                        loadcaps_value(part.ref, attrs, member_nets,
                                       f"'{part.ref}' ({kd.name})", comp.span, waivers)
                # a tie member on NO net at all is floating by definition — but only
                # flag in wired modules (bare part-catalog instantiations stay quiet)
                if exp.pattern == "tie" and ctx.wired.get(part.ref):
                    for m in netless_members & set(exp.members or netless_members):
                        emit("B002", "error",
                             f"'{part.ref}' ({kd.name}): member '{m}' is unconnected — "
                             "a strap/enable pin may not float", comp.span, waivers, "tie")

        # ---- B006 regulator headroom (declared v_dropout + bound vin/vout) ----
        vdrop = attrs.get("v_dropout")
        if isinstance(vdrop, ir.Quantity) and "vin" in member_nets and "vout" in member_nets:
            vin = ctx.net_volt.get(member_nets["vin"].name)
            vout = ctx.net_volt.get(member_nets["vout"].name)
            drop = _iv(vdrop)
            if vin and vout and drop and vin.lo < vout.hi + drop.hi:
                emit("B006", "error",
                     f"'{part.ref}': input rail {vin.lo}V leaves no headroom for "
                     f"{vout.hi}V out + {drop.hi}V dropout", comp.span, waivers)

        # ---- B009 polarized bias (kind Polarized: member a=positive, b=negative) ----
        if any(kd.name == "Polarized" for kd in contract) \
                and "a" in member_nets and "b" in member_nets:
            va = ctx.net_volt.get(member_nets["a"].name)
            vb = ctx.net_volt.get(member_nets["b"].name)
            if va and vb and va.hi < vb.lo:  # certainly reverse-biased
                emit("B009", "error",
                     f"'{part.ref}' is polarized but reverse-biased: positive terminal at "
                     f"{va.hi}V, negative at {vb.lo}V", comp.span, waivers)

        # ---- B003 unconnected pins / pins on the wrong class of net ----
        undeclared: list[str] = []
        for pin in comp.pins:
            net = ctx.net_of.get((part.ref, pin.name))
            if pin.role == "nc":
                continue
            if net is not None:
                # A target says WHICH net class, not merely "some net". This is the
                # only way to express what a datasheet means by "RES — connect to
                # GND": wiring such a pin to a signal is as wrong as floating it.
                if isinstance(pin.must_connect, str):
                    ok = ctx.is_ground(net) if pin.must_connect == "ground" \
                        else ctx.is_rail(net)
                    if not ok:
                        emit("B003", "error",
                             f"pin '{pin.name}' of '{part.ref}' must connect to "
                             f"{pin.must_connect} but is on net '{net.name}', which is "
                             f"not a {pin.must_connect}", pin.span, waivers)
                continue
            if pin.must_connect:
                target = pin.must_connect if isinstance(pin.must_connect, str) else None
                emit("B003", "error",
                     f"pin '{pin.name}' of '{part.ref}' must connect"
                     + (f" to {target}" if target else "") + " but is on no net",
                     pin.span, waivers)
            elif pin.role == "power_in" and ctx.wired.get(part.ref):
                # ponytail: only in modules that wire anything — bare part-catalog
                # instantiations (tests, examples-in-progress) stay quiet
                emit("B003", "warning",
                     f"power pin '{pin.name}' of '{part.ref}' is on no net",
                     pin.span, waivers)
            if (pin.must_connect is None and pin.role in _STRAPPABLE
                    and str(attrs.get("unused_pins", "")) != "open"):
                undeclared.append(pin.name)

        # ---- B011 undeclared connection intent ----
        # A floating pin and a pin nobody thought about look identical to every
        # other check. On a specific, orderable part (mpn=) that is worth one info
        # per component: writing `must_connect=false` turns the omission into a
        # decision, and `must_connect=ground` catches the reserved pins whose
        # datasheets say "connect to GND" — 12 of which floated on the board that
        # motivated this.
        if undeclared and "mpn" in attrs and ctx.wired.get(part.ref):
            shown = ", ".join(undeclared[:8])
            more = f" and {len(undeclared) - 8} more" if len(undeclared) > 8 else ""
            emit("B011", "info",
                 f"'{part.ref}' ({comp.name}) leaves {len(undeclared)} unconnected pin(s) "
                 f"with no declared intent: {shown}{more}", comp.span, waivers)

    # ---- interface expectations (per port: gather ALL member nets first, so
    # multi-member patterns like termination see the whole pair) ----
    port_members: dict[str, dict[str, ir.NetLow]] = {}
    for net in low.nets:
        for lbl in net.ports:
            port_path, member = lbl.rsplit(".", 1)
            port_members.setdefault(port_path, {})[member] = net
    for port_path in sorted(port_members):
        nets_map = port_members[port_path]
        iface_name = ctx.port_iface.get(port_path)
        iface = next((i for i in design.interfaces if i.name == iface_name), None)
        if iface is None:
            continue
        owner_path = ctx.port_owner.get(port_path, "")
        attrs = ctx.inst_attrs_by_path.get(owner_path, {})
        waivers = ctx.inst_waivers_by_path.get(owner_path, ())
        for exp in iface.expects:
            if exp.when is not None and not attrs.get(exp.when):
                continue  # when-gated; top-level ports have no gate → skip
            run_pattern(exp, nets_map, f"{iface_name} port '{port_path}'",
                        iface.span, waivers)

    # ---- B001 automatic: open-drain net with no pull-up (port-exempt) ----
    seen_b001 = {d.message for d in diags if d.code == "B001"}
    for net in low.nets:
        pins = list(ctx._pins_on(net))
        if not any(p.role in _OPENDRAIN for _, p in pins):
            continue
        if net.ports or ctx.has_pushpull(net) or ctx.resistor_to_rail(net):
            continue
        msg = f"net '{net.name}' has open-drain drivers but no pull-up resistor to a rail"
        if msg not in seen_b001:
            span = net.conn_spans[0] if net.conn_spans else _NO_SPAN
            diags.append(Diagnostic("warning", "B001", msg, span, file=path))

    # ---- B004 domain damage: declared voltage vs tolerant_to ----
    for net in low.nets:
        hull = ctx.net_volt.get(net.name)
        if hull is None:
            continue
        for node, pin in ctx._pins_on(net):
            tol = _iv(pin.tolerant_to)
            if tol is not None and hull.hi > tol.lo:
                diags.append(Diagnostic(
                    "error", "B004",
                    f"net '{net.name}' reaches {hull.hi}V but pin {node.part_ref}."
                    f"{pin.name} tolerates only {tol.lo}V — damage-class mismatch "
                    "(a series resistor does not fix this)", pin.span or _NO_SPAN, file=path))
    return diags
