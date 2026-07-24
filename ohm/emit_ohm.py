"""Ecosystem X0 (ADR-012): `.zen` → annotatable Ohm source.

The on-ramp from the Zener ecosystem: `ohm convert design.zen` turns the flat
imported netlist into `.ohm` source a human or LLM annotates with intent
(interfaces, kinds, roles, operating points, `expects`) — which is what lights
up the checking tiers. The skeleton compiles as-is; its TODOs are the work list.

Honesty: zen carries no pad numbers. Numeric-looking pins ("1", "P2") keep
their number; everything else gets a sequential placeholder pad and a loud
TODO — placeholder pads are for compilation, never for fabrication.
"""

from __future__ import annotations

import re

from ohm import ir

_PIN_NUM = re.compile(r"^(?:P|PAD)?(\d+)$", re.IGNORECASE)
_NAME_OK = re.compile(r"^[A-Za-z_]\w*$")


def _pad_of(pin: str) -> str | None:
    m = _PIN_NUM.match(pin)
    return m.group(1) if m else None


def _safe(name: str) -> str:
    """A legal Ohm NAME for a zen ref/pin (zen allows chars Ohm's grammar doesn't)."""
    out = re.sub(r"\W", "_", name)
    return out if _NAME_OK.match(out) else f"n_{out}"


def emit_ohm(low: ir.IRLow, source: str = "<string>") -> str:
    # ---- component decls: one per distinct (component-name, pin-set) signature ----
    pins_by_part: dict[str, list[str]] = {p.ref: [] for p in low.parts}
    for net in low.nets:
        for node in net.nodes:
            if node.part_ref in pins_by_part and node.pin not in pins_by_part[node.part_ref]:
                pins_by_part[node.part_ref].append(node.pin)
    sigs: dict[tuple[str, frozenset[str]], str] = {}  # signature -> decl name
    decl_of_part: dict[str, str] = {}
    decl_pkg: dict[str, str | None] = {}
    for p in sorted(low.parts, key=lambda p: p.ref):
        sig = (p.component, frozenset(pins_by_part[p.ref]))
        if sig not in sigs:
            base = _safe(p.component)
            taken = set(sigs.values())
            name = base if base not in taken else next(
                f"{base}_{k}" for k in range(2, 999) if f"{base}_{k}" not in taken)
            sigs[sig] = name
            decl_pkg[name] = p.package if isinstance(p.package, str) else None
        decl_of_part[p.ref] = sigs[sig]

    out = ["# converted from " + source + " by `ohm convert` — a skeleton, not a sign-off:",
           "# every TODO below is intent the .zen source could not express.", ""]
    for (comp, pin_sig), name in sorted(sigs.items(), key=lambda kv: kv[1]):
        pins = sorted(pin_sig)
        numeric = {pin: _pad_of(pin) for pin in pins}
        next_pad = max([int(v) for v in numeric.values() if v] or [0]) + 1
        out.append(f"component {name}:")
        pkg = decl_pkg[name]
        out.append(f'    package = "{pkg}"' if pkg
                   else '    package = "TODO"  # TODO: no footprint in the .zen source')
        if any(v is None for v in numeric.values()):
            out.append("    # TODO: verify pad numbers against the datasheet — placeholder")
            out.append("    #       pads below compile but are NOT for fabrication")
        for pin in pins:
            pad = numeric[pin]
            if pad is None:
                pad, next_pad = str(next_pad), next_pad + 1
            out.append(f"    pin {_safe(pin)}: role=passive, pad={pad}"
                       "  # TODO: role (input/output/power_in/...)")
        if not pins:
            out.append("    pin P1: role=passive, pad=1  # TODO: no pins visible in .zen")
        out.append("")

    # ---- the module: instances + connections ----
    if not low.parts:
        out.append("# (nothing imported — the .zen file had no statically-readable parts)")
        return "\n".join(out) + "\n"
    out.append("# TODO: replace direct nets with typed interfaces (io(Power), io(I2C), ...)")
    out.append("# and declare kind conformance to enable the checking tiers.")
    out.append("module Imported:")
    for p in sorted(low.parts, key=lambda p: p.ref):
        kwargs = ", ".join(f'{_safe(k)}="{v}"' for k, v in sorted(p.attrs)
                           if isinstance(v, str))
        pkg = p.package if isinstance(p.package, str) else None
        if pkg and pkg != decl_pkg[decl_of_part[p.ref]]:
            kwargs = (kwargs + ", " if kwargs else "") + f'package="{pkg}"'
        out.append(f"    {_safe(p.ref)} = {decl_of_part[p.ref]}({kwargs})")
    for net in sorted(low.nets, key=lambda n: n.name):
        nodes = sorted((n.part_ref, n.pin) for n in net.nodes)
        first = f"{_safe(nodes[0][0])}.{_safe(nodes[0][1])}"
        for ref, pin in nodes[1:]:
            out.append(f"    {first} ~ {_safe(ref)}.{_safe(pin)}")
    return "\n".join(out) + "\n"
