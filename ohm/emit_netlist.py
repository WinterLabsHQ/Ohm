"""Own netlist emitter — KiCad-style s-expression export format (manual P4).

Deterministic: components ordered by designator's part order in IR-LOW (already
walk-ordered), nets sorted by name (done in P-LOWER). Byte-identical across runs.
"""

from __future__ import annotations

from ohm import ir
from ohm import __version__


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _value_text(v: ir.Value) -> str:
    """Canonical text for an attr value; Quantities render as authored (10kohm +/- 1%)."""
    if isinstance(v, ir.Quantity):
        s = f"{v.value}{v.unit}"
        if v.tol_raw is not None:
            tval, tunit = v.tol_raw
            s += f" +/- {tval}{tunit or '%'}"
        return s
    return str(v)


def emit_netlist(low: ir.IRLow, source: str = "<string>") -> str:
    out = ["(export (version \"E\")"]
    out.append(f"  (design (source {_q(source)}) (tool {_q('ohm ' + __version__)}))")
    out.append("  (components")
    for p in low.parts:
        if isinstance(p.package, str):
            fp = p.package
        elif isinstance(p.package, ir.GenerateSpec):
            fp = f"ohm:{p.component}"  # sibling generated .kicad_mod (P11)
        else:
            fp = ""
        attrs = dict(p.attrs)
        raw = attrs.get("value")
        value = _value_text(raw) if raw is not None else str(attrs.get("mpn", p.component))
        out.append(
            f"    (comp (ref {_q(p.designator)}) (value {_q(value)})"
            f" (footprint {_q(fp)}) (fields (field (name \"ohm_ref\") {_q(p.ref)})))"
        )
    out.append("  )")
    out.append("  (nets")
    for code, net in enumerate(low.nets, start=1):
        nodes = "".join(
            f" (node (ref {_q(n.designator)}) (pin {_q(n.pad or n.pin)}))" for n in net.nodes
        )
        out.append(f"    (net (code {_q(str(code))}) (name {_q(net.name)}){nodes})")
    out.append("  )")
    out.append(")")
    return "\n".join(out) + "\n"
