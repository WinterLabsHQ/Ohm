"""P6 ★ HEADLINE: Tier-I intent checker (manual Part 2/P6, compiler design §4.3).

Catches what standard ERC cannot, using declared intent (D3):
  I001  connecting different interface types
  I002  connecting a whole interface to a non-interface endpoint
  I003  multiple drivers (output/power_out) on one net
  I005  swapped members of a `require`-related interface (SDA↔SCL, D+↔D−)
  I006  net with input/power_in endpoints and no driver (undriven) — warning

Connection-shape checks run per module (hierarchical view); driver/undriven
rules run on the flat nets, the root-cause place — every wiring path lands
there. Never mutates IR; never halts.
"""

from __future__ import annotations

from ohm import ir
from ohm.diagnostics import Diagnostic, Span
from ohm.lower import first_by_name, lower

_NO_SPAN = Span(1, 1, 1, 1)
_DRIVERS = {"output", "power_out"}
_SINKS = {"input", "power_in"}


def _d(sev, code, msg, span, path, help_=None):
    return Diagnostic(sev, code, msg, span or _NO_SPAN, file=path, help=help_)


def _classify(refparts, module, comps, mods, insts):
    """Endpoint → ("pin", comp, pin) | ("iface", iface) | ("member", iface, member) | None."""
    base = refparts[0]
    inst = insts.get(base)
    if inst is not None:
        if inst.def_ref in comps:
            return ("pin", comps[inst.def_ref], refparts[1]) if len(refparts) > 1 else None
        if inst.def_ref in mods:
            sub = mods[inst.def_ref]
            port = next((p for p in sub.ports if p.name == refparts[1]), None) if len(refparts) > 1 else None
            if port is None:
                return None
            return ("iface", port.interface) if len(refparts) == 2 else ("member", port.interface, refparts[2])
    port = next((p for p in module.ports if p.name == base), None)
    if port is not None:
        return ("iface", port.interface) if len(refparts) == 1 else ("member", port.interface, refparts[1])
    return None


def check_intent(design: ir.Design, path: str = "<string>",
                 low: ir.IRLow | None = None) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    comps = {c.name: c for c in design.components}
    mods = {m.name: m for m in design.modules}
    ifaces = {i.name: i for i in design.interfaces}
    require_members = {
        name: {m for rel in iface.requires for m in rel.members}
        for name, iface in ifaces.items()
    }

    # ---- connection-shape checks (per module) ----
    for module in design.modules:
        # (port_path, instance_path) -> {member: (pin_name, conn)} for member↔pin wiring;
        # lets a require-pair crossed onto one instance's like-named pins be caught
        member_pin_wiring: dict[tuple, dict[str, tuple]] = {}
        group_iface: dict[tuple, str] = {}
        insts = first_by_name(module.instances)
        for conn in module.connections:
            left = _classify(conn.left, module, comps, mods, insts)
            right = _classify(conn.right, module, comps, mods, insts)
            if left is None or right is None:
                continue  # resolver already diagnosed unknowns
            lk, rk = left[0], right[0]
            if lk == "iface" and rk == "iface":
                if left[1] != right[1]:
                    diags.append(_d(
                        "error", "I001",
                        f"cannot connect interface '{left[1]}' to interface '{right[1]}'",
                        conn.span, path,
                        "interfaces must be the same type; connect members explicitly to adapt"))
            elif "iface" in (lk, rk):
                other = right if lk == "iface" else left
                iface_name = left[1] if lk == "iface" else right[1]
                what = f"pin '{other[2]}'" if other[0] == "pin" else f"member '{other[2]}'"
                diags.append(_d(
                    "error", "I002",
                    f"cannot connect whole interface '{iface_name}' to {what}",
                    conn.span, path, "connect member-to-member, or interface-to-interface"))
            elif lk == "member" and rk == "member" and left[1] == right[1]:
                m1, m2 = left[2], right[2]
                req = require_members.get(left[1], set())
                if m1 != m2 and (m1 in req or m2 in req):
                    rel = next(r for r in ifaces[left[1]].requires if m1 in r.members or m2 in r.members)
                    diags.append(_d(
                        "error", "I005",
                        f"swapped interface members: '{m1}' ~ '{m2}' violates "
                        f"require {rel.kind}({', '.join(rel.members)}) on '{left[1]}'",
                        conn.span, path, f"connect '{m1}' ~ '{m1}'"))
            elif {lk, rk} == {"member", "pin"}:
                member_side, refparts_m = (left, conn.left) if lk == "member" else (right, conn.right)
                pin_side, refparts_p = (right, conn.right) if lk == "member" else (left, conn.left)
                key = (refparts_m[:-1], refparts_p[:-1])  # (port path, instance path)
                member_pin_wiring.setdefault(key, {})[member_side[2]] = (pin_side[2], conn)
                group_iface[key] = member_side[1]

        # crossed require-pair wired onto one instance's like-named pins (found by P12)
        for key, wiring in member_pin_wiring.items():
            iface = ifaces.get(group_iface[key])
            if iface is None:
                continue
            for rel in iface.requires:
                for i, a in enumerate(rel.members):
                    for b in rel.members[i + 1:]:
                        if a not in wiring or b not in wiring:
                            continue
                        pin_a, conn_a = wiring[a]
                        pin_b, conn_b = wiring[b]
                        if pin_a.lower() == b.lower() and pin_b.lower() == a.lower():
                            for m, pin, conn in ((a, pin_a, conn_a), (b, pin_b, conn_b)):
                                diags.append(_d(
                                    "error", "I005",
                                    f"swapped interface members: '{m}' is wired to pin "
                                    f"'{pin}' while require {rel.kind}({', '.join(rel.members)}) "
                                    f"pairs it with the like-named pin", conn.span, path,
                                    f"connect '{m}' to the pin named like '{m}'"))

    # ---- net-level role rules (flat view — every wiring path lands here) ----
    if low is None:  # standalone use; the driver passes its single lower() result
        low, _ = lower(design, path)
    pins_of = {c.name: first_by_name(c.pins) for c in design.components}
    part_pins = {p.ref: pins_of[p.component] for p in low.parts}
    for net in low.nets:
        noderoles = []
        for node in net.nodes:
            pin = part_pins[node.part_ref].get(node.pin)
            if pin is not None:
                noderoles.append((node, pin))
        net_span = net.conn_spans[0] if net.conn_spans else None
        related = tuple(p.span for _, p in noderoles if p.span is not None)
        drivers = [(n, p) for n, p in noderoles if p.role in _DRIVERS]
        if len(drivers) >= 2:
            names = ", ".join(f"{n.part_ref}.{p.name} ({p.role})" for n, p in drivers)
            diags.append(Diagnostic("error", "I003",
                                    f"net '{net.name}' has multiple drivers: {names}",
                                    net_span or drivers[1][1].span or _NO_SPAN,
                                    file=path, related=related))
        # inputs with zero drivers warn even through passives (series-R still floats);
        # nets touching a port are exempt — external drive possible
        sinks = [(n, p) for n, p in noderoles if p.role in _SINKS]
        if not net.ports and sinks and not drivers:
            names = ", ".join(f"{n.part_ref}.{p.name}" for n, p in sinks)
            diags.append(Diagnostic("warning", "I006",
                                    f"net '{net.name}' has no driver for its input endpoints: {names}",
                                    net_span or sinks[0][1].span or _NO_SPAN,
                                    file=path, related=related))
    return diags
